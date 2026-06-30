"""
Integration tests for OptimizerLoop (Task 10.7).

Tests use in-memory databases and mock heavy components (WorkspaceManager,
sandbox, LLM ensemble) so they run fast without Docker / real repos.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openevolve.optimizer_loop import OptimizerLoop


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

BASELINE_SCORE = 0.5


def _config(**overrides) -> Dict[str, Any]:
    base = {
        "repo_path": "/fake/repo",
        "target_file": "/fake/repo/program.py",
        "test_file": "/fake/repo/test_program.py",
        "max_iterations": 5,
        "patience": 3,
        "success_threshold": 0.10,
        "db_path": ":memory:",
    }
    base.update(overrides)
    return base


def _sandbox_ok(score: float = BASELINE_SCORE):
    """Return a successful sandbox result dict."""
    return {
        "status": "passed",
        "stdout": "All tests passed",
        "stderr": "",
        "exit_code": 0,
        "execution_time": 0.5,
        "combined_score": score,
        "correctness": 1.0,
        "speed_score": score,
        "all_passed": True,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "total": 1,
    }


def _sandbox_fail():
    """Return a failing sandbox result dict."""
    return {
        "status": "failed",
        "stdout": "FAILED",
        "stderr": "AssertionError",
        "exit_code": 1,
        "execution_time": 0.2,
        "combined_score": 0.0,
        "all_passed": False,
        "passed": 0,
        "failed": 1,
        "errors": 0,
        "total": 1,
    }


# ---------------------------------------------------------------------------
# Test 1: baseline is inserted with generation=0
# ---------------------------------------------------------------------------

def test_run_establishes_baseline():
    """verify baseline candidate inserted with generation=0."""
    loop = OptimizerLoop(_config(max_iterations=1, patience=1), llm_ensemble=None)

    with patch("openevolve.optimizer_loop._import_sandbox") as mock_sandbox_import:
        mock_run, mock_verify = MagicMock(return_value=_sandbox_ok()), MagicMock(return_value=True)
        mock_sandbox_import.return_value = (mock_run, mock_verify)
        # Also patch execute_generation to avoid LLM dependency
        with patch.object(loop, "execute_generation") as mock_exec:
            mock_exec.return_value = {
                "id": str(uuid.uuid4()),
                "generation": 1,
                "score": 0.4,  # worse than baseline → patience countdown
                "failed": False,
                "metrics": {"combined_score": 0.4},
                "parent_id": None,
            }
            result = loop.run()

    baseline = result["baseline_candidate"]
    assert baseline["generation"] == 0
    assert baseline["parent_id"] is None
    assert not baseline["failed"]


# ---------------------------------------------------------------------------
# Test 2: run with llm_ensemble=None completes gracefully (failure recorded)
# ---------------------------------------------------------------------------

def test_run_completes_with_no_llm():
    """run() with no LLM ensemble records failures but does not crash."""
    loop = OptimizerLoop(
        _config(max_iterations=3, patience=3), llm_ensemble=None
    )

    with patch("openevolve.optimizer_loop._import_sandbox") as mock_sandbox_import:
        mock_run = MagicMock(return_value=_sandbox_ok())
        mock_verify = MagicMock(return_value=True)
        mock_sandbox_import.return_value = (mock_run, mock_verify)

        result = loop.run()

    assert result["run_id"] is not None
    export = result["export"]
    # Baseline + 3 failed generations = 4 candidates total
    assert len(export["candidates"]) >= 1
    # All non-baseline candidates should be failures
    non_baseline = [c for c in export["candidates"] if c["generation"] > 0]
    assert all(c["failed"] for c in non_baseline)


# ---------------------------------------------------------------------------
# Test 3: early stopping stops before max_iterations
# ---------------------------------------------------------------------------

def test_early_stopping_stops_before_max():
    """Fixed-score generations trigger early stopping at patience boundary."""
    patience = 2
    max_iterations = 10
    loop = OptimizerLoop(
        _config(max_iterations=max_iterations, patience=patience),
        llm_ensemble=None,
    )

    generation_counter = {"n": 0}

    def fake_execute_gen(generation: int, baseline: Dict[str, Any]) -> Dict[str, Any]:
        generation_counter["n"] += 1
        cid = loop.db.insert_candidate(
            generation=generation,
            parent_id=baseline.get("id"),
            patch_content=f"patch-{generation}",
            score=BASELINE_SCORE,  # same as baseline → no improvement
            metrics={"combined_score": BASELINE_SCORE},
            failed=False,
        )
        candidate = loop.db.get_candidate(cid)
        loop._candidate_history.append(candidate)
        return candidate

    with patch("openevolve.optimizer_loop._import_sandbox") as mock_sandbox_import:
        mock_run = MagicMock(return_value=_sandbox_ok(BASELINE_SCORE))
        mock_verify = MagicMock(return_value=True)
        mock_sandbox_import.return_value = (mock_run, mock_verify)

        with patch.object(loop, "execute_generation", side_effect=fake_execute_gen):
            result = loop.run()

    assert result["total_generations"] == patience
    assert result["total_generations"] < max_iterations


# ---------------------------------------------------------------------------
# Test 4: generations_without_improvement resets on improvement
# ---------------------------------------------------------------------------

def test_generations_without_improvement_resets_on_improvement():
    """Counter resets when a genuinely better candidate is found."""
    patience = 3
    loop = OptimizerLoop(
        _config(max_iterations=10, patience=patience),
        llm_ensemble=None,
    )

    call_count = {"n": 0}

    def fake_execute_gen(generation: int, baseline: Dict[str, Any]) -> Dict[str, Any]:
        call_count["n"] += 1
        # Generation 1: improvement; generations 2,3,4: no improvement → stop at 4
        score = 0.8 if generation == 1 else BASELINE_SCORE
        cid = loop.db.insert_candidate(
            generation=generation,
            parent_id=baseline.get("id"),
            patch_content=f"patch-{generation}",
            score=score,
            metrics={"combined_score": score},
            failed=False,
        )
        candidate = loop.db.get_candidate(cid)
        loop._candidate_history.append(candidate)
        return candidate

    with patch("openevolve.optimizer_loop._import_sandbox") as mock_sandbox_import:
        mock_run = MagicMock(return_value=_sandbox_ok(BASELINE_SCORE))
        mock_verify = MagicMock(return_value=True)
        mock_sandbox_import.return_value = (mock_run, mock_verify)

        with patch.object(loop, "execute_generation", side_effect=fake_execute_gen):
            result = loop.run()

    # Should run: gen1 (improvement), gen2, gen3, gen4 (patience=3 non-improving)
    assert call_count["n"] == 1 + patience
    assert result["total_generations"] == 1 + patience


# ---------------------------------------------------------------------------
# Test 5: failure recorded when LLM returns no patch
# ---------------------------------------------------------------------------

def test_execute_generation_records_failure_on_no_patch():
    """When LLM returns None/empty patch, a failure candidate is recorded."""
    mock_llm = MagicMock()
    mock_llm.generate_patch = AsyncMock(return_value=None)

    loop = OptimizerLoop(_config(max_iterations=1, patience=1), llm_ensemble=mock_llm)

    # Insert a real baseline so the loop has a parent
    loop.db.create_run(run_id="test-run")
    loop._run_id = "test-run"
    baseline_cid = loop.db.insert_candidate(
        generation=0, parent_id=None, patch_content="",
        score=BASELINE_SCORE, failed=False,
    )
    baseline = loop.db.get_candidate(baseline_cid)
    loop._candidate_history.append(baseline)

    # Patch mapper to avoid real filesystem access
    with patch("openevolve.optimizer_loop._import_repo_mapper") as mock_rm_import:
        mock_mapper = MagicMock()
        mock_mapper.get_context_map.side_effect = Exception("no real repo")
        mock_rm_import.return_value = (MagicMock(return_value=mock_mapper), MagicMock())

        candidate = loop.execute_generation(1, baseline)

    assert candidate["failed"]
    assert candidate["failure_phase"] == "generate"
    assert candidate["generation"] == 1


# ---------------------------------------------------------------------------
# Test 6: db has correct generation numbers after multiple generations
# ---------------------------------------------------------------------------

def test_state_consistency_after_multiple_generations():
    """Verify db has correct generation numbers across the run."""
    n_gens = 4
    loop = OptimizerLoop(
        _config(max_iterations=n_gens, patience=n_gens + 1),  # no early stop
        llm_ensemble=None,
    )

    with patch("openevolve.optimizer_loop._import_sandbox") as mock_sandbox_import:
        mock_run = MagicMock(return_value=_sandbox_ok(BASELINE_SCORE))
        mock_verify = MagicMock(return_value=True)
        mock_sandbox_import.return_value = (mock_run, mock_verify)

        result = loop.run()

    export = result["export"]
    generations_in_db = sorted({c["generation"] for c in export["candidates"]})

    # Baseline is generation 0; rest are 1…n_gens
    assert 0 in generations_in_db
    # Each generation 1..n_gens should appear (all failed due to no LLM, but recorded)
    for g in range(1, n_gens + 1):
        assert g in generations_in_db, f"Generation {g} missing from db"

    # Parent of non-baseline candidates should reference an existing candidate
    ids_in_db = {c["id"] for c in export["candidates"]}
    for c in export["candidates"]:
        if c["generation"] > 0 and c["parent_id"] is not None:
            assert c["parent_id"] in ids_in_db, (
                f"Candidate {c['id']} has orphan parent_id={c['parent_id']}"
            )
