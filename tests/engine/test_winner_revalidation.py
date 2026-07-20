"""
Tests for final winner revalidation (design §C1, Requirement 3, CP3).

Covers:
  * the pure hold-check helper ``revalidation_holds`` (single-sourced with the
    loop's ``accept_as_new_best`` gate);
  * the orchestration method ``OptimizerLoop._revalidate_winner`` (M sandbox
    re-runs, NO LLM calls) with the sandbox + workspace mocked (no Docker/git);
  * run() status logic — a holding winner stays "successful", a noisy/regressed
    winner is downgraded (CP3), and ``--no-revalidate`` skips revalidation
    entirely (no extra sandbox runs).
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

from openevolve.optimizer_loop import (
    OptimizerLoop,
    accept_as_new_best,
    revalidation_holds,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _config(**overrides) -> Dict[str, Any]:
    base = {
        "repo_path": "/fake/repo",
        "target_file": "/fake/repo/program.py",
        "test_file": "/fake/repo/test_program.py",
        "max_iterations": 1,
        "patience": 1,
        "success_threshold": 0.10,
        "db_path": ":memory:",
    }
    base.update(overrides)
    return base


def _dist(median: float, stddev: float = 0.0) -> Dict[str, Any]:
    return {"speed_ms_median": median, "speed_ms_stddev": stddev}


def _sandbox_dist(median: float, stddev: float, exit_code: int = 0,
                  score: float = 0.7) -> Dict[str, Any]:
    """A sandbox result dict carrying a speed distribution."""
    return {
        "status": "passed" if exit_code == 0 else "failed",
        "stdout": "All tests passed" if exit_code == 0 else "FAILED",
        "stderr": "",
        "exit_code": exit_code,
        "execution_time": 0.5,
        "combined_score": score if exit_code == 0 else 0.0,
        "correctness": 1.0 if exit_code == 0 else 0.0,
        "speed_score": score if exit_code == 0 else 0.0,
        "speed_ms": median,
        "speed_ms_median": median,
        "speed_ms_stddev": stddev,
        "speed_ms_samples": [median, median, median],
        "runs": 3,
    }


def _mock_wm(success: bool = True):
    """Build a mocked WorkspaceManager class whose context manager yields a path."""
    wm = MagicMock()
    wm.__enter__.return_value = "/fake/worktree"
    wm.__exit__.return_value = False
    apply_result = MagicMock()
    apply_result.success = success
    apply_result.error_output = "" if success else "patch conflict"
    wm.apply_patch.return_value = apply_result
    return MagicMock(return_value=wm), wm


def _winning_gen(loop: OptimizerLoop, median_cand: float = 80.0,
                 stddev_cand: float = 2.0, score: float = 0.9):
    """Fake execute_generation inserting an improving candidate with a patch."""
    def fake(generation: int, baseline: Dict[str, Any]) -> Dict[str, Any]:
        metrics = {
            "combined_score": score,
            "speed_ms": median_cand,
            "speed_ms_median": median_cand,
            "speed_ms_stddev": stddev_cand,
        }
        cid = loop.db.insert_candidate(
            generation=generation,
            parent_id=baseline.get("id"),
            patch_content="diff --git a/program.py b/program.py\n@@ -1 +1 @@\n-x\n+y\n",
            score=score,
            metrics=metrics,
            failed=False,
        )
        candidate = loop.db.get_candidate(cid)
        loop._candidate_history.append(candidate)
        return candidate
    return fake


# ---------------------------------------------------------------------------
# Pure hold-check helper — revalidation_holds
# ---------------------------------------------------------------------------

def test_revalidation_holds_true_when_gain_persists():
    """A re-measured distribution that still clears the gate holds."""
    assert revalidation_holds(_dist(100.0, 2.0), _dist(80.0, 2.0), 0.03) is True


def test_revalidation_holds_false_when_gain_evaporates():
    """A re-measured median that barely moves fails the min_effect check."""
    assert revalidation_holds(_dist(100.0, 2.0), _dist(99.0, 5.0), 0.03) is False


def test_revalidation_holds_false_on_dispersion_overlap():
    """A real median gain that is swamped by noise does not hold (CP2/CP3)."""
    assert revalidation_holds(_dist(100.0, 20.0), _dist(90.0, 5.0), 0.03) is False


def test_revalidation_holds_false_on_missing_distribution():
    """A failed/absent re-measurement never holds (caller downgrades)."""
    assert revalidation_holds(_dist(100.0, 2.0), {"speed_ms_median": None}, 0.03) is False
    assert revalidation_holds(_dist(100.0, 2.0), None, 0.03) is False


def test_revalidation_holds_is_single_sourced_with_accept_gate():
    """The hold-check reuses accept_as_new_best — no duplicated math."""
    base, reval = _dist(100.0, 3.0), _dist(85.0, 4.0)
    for min_effect in (0.03, 0.05, 0.20):
        assert (
            revalidation_holds(base, reval, min_effect)
            == accept_as_new_best(base, reval, min_effect)
        )


# ---------------------------------------------------------------------------
# Orchestration — _revalidate_winner (M sandbox runs, no LLM)
# ---------------------------------------------------------------------------

def test_revalidate_winner_orchestrates_m_runs_and_makes_no_llm_calls():
    """M-run re-measurement reuses run_in_sandbox with repeats=M, no LLM calls."""
    mock_llm = MagicMock()
    mock_llm.generate_patch = AsyncMock(return_value="patch")
    loop = OptimizerLoop(_config(revalidate_runs=7), llm_ensemble=mock_llm)
    winner = {"id": "w", "patch_content": "diff --git a/p b/p\n@@\n-x\n+y\n"}

    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch("openevolve.optimizer_loop._import_workspace_manager") as mwi:
        mock_run = MagicMock(return_value=_sandbox_dist(80.0, 2.0))
        msi.return_value = (mock_run, MagicMock(return_value=True))
        wm_cls, _ = _mock_wm()
        mwi.return_value = wm_cls

        dist = loop._revalidate_winner(winner)

    assert dist is not None
    assert dist["speed_ms_median"] == 80.0
    assert dist["speed_ms_stddev"] == 2.0
    # Single sandbox invocation configured for M repeats.
    assert mock_run.call_count == 1
    assert mock_run.call_args.kwargs["sandbox_cfg"]["repeats"] == 7
    # No LLM calls during revalidation (R3.2).
    mock_llm.generate_patch.assert_not_called()


def test_revalidate_winner_returns_none_without_patch():
    """The baseline winner (no patch) is not revalidated."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    assert loop._revalidate_winner({"id": "b", "patch_content": ""}) is None
    assert loop._revalidate_winner({"id": "b"}) is None


def test_revalidate_winner_returns_none_when_patch_reapply_fails():
    """A patch that no longer applies aborts revalidation without running."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    winner = {"id": "w", "patch_content": "diff --git a/p b/p\n@@\n-x\n+y\n"}
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch("openevolve.optimizer_loop._import_workspace_manager") as mwi:
        mock_run = MagicMock(return_value=_sandbox_dist(80.0, 2.0))
        msi.return_value = (mock_run, MagicMock(return_value=True))
        wm_cls, _ = _mock_wm(success=False)
        mwi.return_value = wm_cls

        dist = loop._revalidate_winner(winner)

    assert dist is None
    mock_run.assert_not_called()


def test_revalidate_winner_correctness_regression_fails_hold_check():
    """If the winner fails correctness on re-run, the gain does not hold."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    winner = {"id": "w", "patch_content": "diff --git a/p b/p\n@@\n-x\n+y\n"}
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch("openevolve.optimizer_loop._import_workspace_manager") as mwi:
        mock_run = MagicMock(return_value=_sandbox_dist(80.0, 2.0, exit_code=1))
        msi.return_value = (mock_run, MagicMock(return_value=True))
        wm_cls, _ = _mock_wm()
        mwi.return_value = wm_cls

        dist = loop._revalidate_winner(winner)

    assert dist is not None
    assert dist["speed_ms_median"] is None
    assert revalidation_holds(_dist(100.0, 2.0), dist, 0.03) is False


# ---------------------------------------------------------------------------
# run() status logic
# ---------------------------------------------------------------------------

def test_holding_winner_stays_successful():
    """A winner whose gain holds under re-measurement keeps status successful."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch.object(loop, "execute_generation", side_effect=_winning_gen(loop)), \
         patch.object(loop, "_revalidate_winner",
                      return_value=_dist(80.0, 2.0)) as reval:
        msi.return_value = (MagicMock(return_value=_sandbox_dist(100.0, 2.0)),
                            MagicMock(return_value=True))
        result = loop.run()

    reval.assert_called_once()
    assert result["status"] == "successful"
    assert result["revalidation"]["performed"] is True
    assert result["revalidation"]["held"] is True


def test_noisy_winner_is_downgraded():
    """A winner whose re-measured gain evaporates is downgraded (CP3)."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch.object(loop, "execute_generation", side_effect=_winning_gen(loop)), \
         patch.object(loop, "_revalidate_winner",
                      return_value=_dist(99.0, 5.0)):
        msi.return_value = (MagicMock(return_value=_sandbox_dist(100.0, 2.0)),
                            MagicMock(return_value=True))
        result = loop.run()

    assert result["status"] == "revalidation_failed"
    assert result["status"] != "successful"
    assert result["revalidation"]["performed"] is True
    assert result["revalidation"]["held"] is False
    # The persisted run status is downgraded too.
    run_row = result["export"]["run"]
    assert run_row["status"] == "revalidation_failed"


def test_no_revalidate_skips_revalidation_and_extra_sandbox_runs():
    """--no-revalidate skips revalidation; status decided as before; no extra runs."""
    loop = OptimizerLoop(_config(revalidate=False), llm_ensemble=None)
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch.object(loop, "execute_generation", side_effect=_winning_gen(loop)), \
         patch.object(loop, "_revalidate_winner") as reval:
        mock_run = MagicMock(return_value=_sandbox_dist(100.0, 2.0))
        msi.return_value = (mock_run, MagicMock(return_value=True))
        result = loop.run()

    reval.assert_not_called()
    assert result["status"] == "successful"
    assert result["revalidation"]["performed"] is False
    # Only the baseline sandbox run happened — no revalidation re-measurement.
    assert mock_run.call_count == 1


def test_run_revalidation_orchestrates_real_sandbox_path_no_llm():
    """End-to-end: real _revalidate_winner path (mocked sandbox/git) uses no LLM."""
    mock_llm = MagicMock()
    mock_llm.generate_patch = AsyncMock(return_value="patch")
    mock_llm.usage_totals = MagicMock(return_value={
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "api_calls": 0,
    })
    loop = OptimizerLoop(_config(revalidate_runs=5), llm_ensemble=mock_llm)
    with patch("openevolve.optimizer_loop._import_sandbox") as msi, \
         patch("openevolve.optimizer_loop._import_workspace_manager") as mwi, \
         patch.object(loop, "execute_generation", side_effect=_winning_gen(loop)):
        mock_run = MagicMock(side_effect=[_sandbox_dist(100.0, 2.0),
                                          _sandbox_dist(80.0, 2.0)])
        msi.return_value = (mock_run, MagicMock(return_value=True))
        wm_cls, _ = _mock_wm()
        mwi.return_value = wm_cls
        result = loop.run()

    assert result["status"] == "successful"
    assert result["revalidation"]["held"] is True
    # baseline run + one M-repeat revalidation run.
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[1].kwargs["sandbox_cfg"]["repeats"] == 5
    # execute_generation is faked, so any LLM call would come from revalidation.
    mock_llm.generate_patch.assert_not_called()
