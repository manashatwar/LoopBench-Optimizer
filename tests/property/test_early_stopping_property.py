"""
Property 5: Early Stopping Trigger Precision

For any optimization run with configured patience parameter P, when exactly P
consecutive generations occur without improvement in the best candidate score,
the system SHALL terminate immediately in the next iteration without continuing
to the maximum generation count.

Validates: Requirements 7.6
"""

from __future__ import annotations

import uuid
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from hypothesis import given, settings, strategies as st

from openevolve.optimizer_loop import OptimizerLoop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(patience: int, max_iterations: int) -> Dict[str, Any]:
    return {
        "repo_path": "/fake/repo",
        "target_file": "/fake/repo/program.py",
        "test_file": "/fake/repo/test_program.py",
        "max_iterations": max_iterations,
        "patience": patience,
        "success_threshold": 0.10,
        "db_path": ":memory:",
    }


def _baseline_candidate(score: float = 0.5) -> Dict[str, Any]:
    return {
        "id": "baseline-0",
        "generation": 0,
        "parent_id": None,
        "score": score,
        "failed": False,
        "metrics": {"combined_score": score},
    }


def _make_non_improving_candidate(generation: int, score: float = 0.5) -> Dict[str, Any]:
    """Return a candidate with the same score as baseline (no improvement)."""
    return {
        "id": f"gen-{generation}-{uuid.uuid4().hex[:6]}",
        "generation": generation,
        "parent_id": "baseline-0",
        "score": score,
        "failed": False,
        "metrics": {"combined_score": score},
    }


# ---------------------------------------------------------------------------
# Property 5
# ---------------------------------------------------------------------------

@given(
    patience=st.integers(min_value=1, max_value=10),
    max_iterations=st.integers(min_value=20, max_value=50),
)
@settings(max_examples=50, deadline=None)
def test_property_5_early_stopping_trigger_precision(
    patience: int,
    max_iterations: int,
) -> None:
    """
    **Validates: Requirements 7.6**

    Property 5: system terminates after exactly P consecutive generations
    without improvement.

    Approach:
    - Establish baseline with score=0.5
    - Mock execute_generation to always return score=0.5 (no improvement)
    - Verify run() terminates before max_iterations
    - Verify exactly 'patience' non-improving generations caused the stop
    - Verify total_generations <= patience + 1 (baseline + patience gens)
    """
    config = _make_config(patience, max_iterations)
    loop = OptimizerLoop(config, llm_ensemble=None)

    BASELINE_SCORE = 0.5
    generations_executed: list[int] = []

    baseline = _baseline_candidate(score=BASELINE_SCORE)

    def fake_establish_baseline() -> Dict[str, Any]:
        # Insert baseline into real db so select_baseline can use it
        cid = loop.db.insert_candidate(
            generation=0,
            parent_id=None,
            patch_content="",
            score=BASELINE_SCORE,
            metrics={"combined_score": BASELINE_SCORE},
            failed=False,
        )
        candidate = loop.db.get_candidate(cid)
        # Override id to match our helper
        candidate["id"] = "baseline-0"
        loop._candidate_history.append(candidate)
        return candidate

    def fake_execute_generation(
        generation: int, baseline_candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        generations_executed.append(generation)
        cand = _make_non_improving_candidate(generation, score=BASELINE_SCORE)
        # Insert into real db for history tracking
        cid = loop.db.insert_candidate(
            generation=generation,
            parent_id=baseline_candidate.get("id", "baseline-0"),
            patch_content=f"patch-{generation}",
            score=BASELINE_SCORE,
            metrics={"combined_score": BASELINE_SCORE},
            failed=False,
        )
        cand["id"] = loop.db.get_candidate(cid)["id"]
        loop._candidate_history.append(cand)
        return cand

    loop.db.create_run(
        run_id="test-run",
        target_repo="/fake/repo",
    )
    loop._run_id = "test-run"

    with patch.object(loop, "establish_baseline", side_effect=fake_establish_baseline), \
         patch.object(loop, "execute_generation", side_effect=fake_execute_generation):
        result = loop.run()

    # Property assertions
    total = result["total_generations"]

    # 1. run() terminates before max_iterations
    assert total < max_iterations, (
        f"Expected early stop before max_iterations={max_iterations}, "
        f"but ran {total} generations (patience={patience})"
    )

    # 2. Exactly 'patience' non-improving generations caused the stop
    assert len(generations_executed) == patience, (
        f"Expected exactly {patience} non-improving generations, "
        f"but executed {len(generations_executed)} (max_iter={max_iterations})"
    )

    # 3. Total generations does not exceed patience
    assert total <= patience, (
        f"total_generations={total} should be <= patience={patience}"
    )
