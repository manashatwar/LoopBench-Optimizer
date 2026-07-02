"""
Pytest suite for the LoopBench Prime Counter example.

How it works:
  - The evaluator sets LOOPBENCH_PROGRAM_PATH to the evolved program path.
  - Tests dynamically load and call run_count_primes() from that path.
  - Speed timing is emitted as LOOPBENCH_SPEED_MS=<value> on stdout so the
    evaluator can parse it from subprocess output.

Scoring contract:
  - All correctness tests must pass → correctness_score = 1.0 (regression gate)
  - Any failure → combined_score = 0.0 (patch is rejected)
  - Speed benchmark emits LOOPBENCH_SPEED_MS for speed_score calculation
"""
import importlib.util
import os
import time
import types

# pyrefly: ignore [missing-import]
import pytest

_PROGRAM_PATH = os.environ.get("LOOPBENCH_PROGRAM_PATH")


def _load_program() -> types.ModuleType:
    """Dynamically load the evolved program from LOOPBENCH_PROGRAM_PATH."""
    if _PROGRAM_PATH is None:
        raise RuntimeError(
            "LOOPBENCH_PROGRAM_PATH environment variable not set. "
            "Run via the evaluator or set the variable manually."
        )
    spec = importlib.util.spec_from_file_location("evolved_primes", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    """Session-scoped fixture — load the program once per test run."""
    return _load_program()


# ── Correctness tests (regression gate) ───────────────────────────────────────
class TestPrimeCounterCorrectness:
    """Must all pass. Any single failure zeroes the combined_score."""

    def test_below_2_is_zero(self, prog: types.ModuleType) -> None:
        assert prog.run_count_primes(0) == 0
        assert prog.run_count_primes(1) == 0
        assert prog.run_count_primes(2) == 0

    def test_small_bounds(self, prog: types.ModuleType) -> None:
        # primes < 3 -> {2}
        assert prog.run_count_primes(3) == 1
        # primes < 10 -> {2,3,5,7}
        assert prog.run_count_primes(10) == 4
        # primes < 11 -> {2,3,5,7} (11 excluded, upper bound is exclusive)
        assert prog.run_count_primes(11) == 4
        # primes < 12 -> {2,3,5,7,11}
        assert prog.run_count_primes(12) == 5

    def test_negative_returns_zero(self, prog: types.ModuleType) -> None:
        assert prog.run_count_primes(-1) == 0
        assert prog.run_count_primes(-100) == 0

    def test_hundred(self, prog: types.ModuleType) -> None:
        # There are 25 primes below 100.
        assert prog.run_count_primes(100) == 25

    def test_thousand(self, prog: types.ModuleType) -> None:
        # There are 168 primes below 1000.
        assert prog.run_count_primes(1000) == 168

    def test_ten_thousand(self, prog: types.ModuleType) -> None:
        # There are 1229 primes below 10000.
        assert prog.run_count_primes(10_000) == 1229


# ── Speed benchmark (parseable output) ────────────────────────────────────────
class TestPrimeCounterSpeed:
    """Speed test. Output is parsed by the evaluator."""

    def test_fifty_thousand_speed(self, prog: types.ModuleType) -> None:
        """
        Benchmark count_primes(50_000) and emit LOOPBENCH_SPEED_MS on stdout.
        Baseline naive trial division ≈ 100-300ms.
        Target after optimization (sieve): < 10ms.
        Hard limit: 5000ms (evaluator timeout guard).
        """
        start = time.perf_counter()
        result = prog.run_count_primes(50_000)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # There are 5133 primes below 50000.
        assert result == 5133, f"count_primes(50000) wrong: expected 5133, got {result}"

        # Emit parseable marker — evaluator.py greps for this line
        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")

        # Hard upper bound — prevents pathological mutations
        assert elapsed_ms < 5000, (
            f"count_primes(50000) took {elapsed_ms:.1f}ms — exceeds 5000ms hard limit"
        )
