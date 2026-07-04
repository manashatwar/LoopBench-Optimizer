"""
LoopBench evaluator for a Vyper crowdfunding contract — optimizes GAS.

LoopBench sets LOOPBENCH_PROGRAM_PATH to the candidate .vy source every
generation. Correctness is a hard gate: any failing test rejects the candidate
(score 0.0). The optimized quantity is gas: we measure the gas of a
participate() call (the hot path) and feed it to LoopBench as the speed metric,
scaled so that lower gas => higher score.

Requires: vyper + titanoboa (installed in the sandbox via sandbox.pip).
"""

import os

import boa

_PATH = os.environ["LOOPBENCH_PROGRAM_PATH"]  # the evolved crowdfund.vy

GOAL = 10**18        # 1 ETH funding goal
TIMELIMIT = 3600     # 1 hour campaign


def _deploy():
    """Compile + deploy a fresh contract; returns (contract, beneficiary)."""
    beneficiary = boa.env.generate_address("beneficiary")
    with open(_PATH) as fh:
        source = fh.read()
    contract = boa.loads(source, beneficiary, GOAL, TIMELIMIT)
    return contract, beneficiary


# ── Correctness gate ─────────────────────────────────────────────────────────

def test_participate_accumulates_funds():
    c, _ = _deploy()
    alice = boa.env.generate_address("alice")
    boa.env.set_balance(alice, 5 * 10**18)
    with boa.env.prank(alice):
        c.participate(value=3 * 10**17)
        c.participate(value=2 * 10**17)
    assert boa.env.get_balance(c.address) == 5 * 10**17


def test_finalize_transfers_to_beneficiary():
    c, beneficiary = _deploy()
    donor = boa.env.generate_address("donor")
    boa.env.set_balance(donor, 2 * 10**18)
    with boa.env.prank(donor):
        c.participate(value=GOAL)
    boa.env.time_travel(seconds=TIMELIMIT + 1)
    c.finalize()
    assert boa.env.get_balance(beneficiary) == GOAL
    assert boa.env.get_balance(c.address) == 0


def test_refund_returns_funds_below_goal():
    c, _ = _deploy()
    bob = boa.env.generate_address("bob")
    boa.env.set_balance(bob, 2 * 10**18)
    with boa.env.prank(bob):
        c.participate(value=4 * 10**17)  # below goal
    boa.env.time_travel(seconds=TIMELIMIT + 1)
    before = boa.env.get_balance(bob)
    with boa.env.prank(bob):
        c.refund()
    assert boa.env.get_balance(bob) == before + 4 * 10**17


def test_deadline_blocks_late_participation():
    c, _ = _deploy()
    late = boa.env.generate_address("late")
    boa.env.set_balance(late, 10**18)
    boa.env.time_travel(seconds=TIMELIMIT + 1)
    raised = False
    with boa.env.prank(late):
        try:
            c.participate(value=10**17)
        except Exception:
            raised = True
    assert raised, "participate() must revert after the deadline"


# ── The metric being optimized: gas of the hot path (participate) ────────────

def test_speed_gas():
    c, _ = _deploy()
    alice = boa.env.generate_address("alice_gas")
    boa.env.set_balance(alice, 10 * 10**18)
    with boa.env.prank(alice):
        c.participate(value=10**17)
        gas_used = c._computation.get_gas_used()
    # Lower gas -> lower "ms" -> higher speed_score. Divide by 1000 so the
    # sandbox's exp(-ms/150) score has a usable gradient at ~40k-70k gas.
    print(f"\nLOOPBENCH_SPEED_MS={gas_used / 1000.0:.4f}")
    print(f"GAS_USED={gas_used}")
    assert gas_used > 0
