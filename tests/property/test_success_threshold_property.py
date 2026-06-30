"""
Property 6: Success Threshold Differentiation

For any completed optimization run with a configured success threshold T, the
system SHALL mark the run as "Successful" if and only if the final improvement
percentage STRICTLY exceeds T, and SHALL mark runs with improvement ≤ T as
"Completed" but not "Successful" in the final report.

Validates: Requirements 17.5, 17.6
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from openevolve.report_generator import generate_final_report


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

POSITIVE_SCORE = st.floats(min_value=0.001, max_value=100.0,
                            allow_nan=False, allow_infinity=False)

THRESHOLD = st.floats(min_value=0.001, max_value=1.0,
                       allow_nan=False, allow_infinity=False)


def _make_candidate(score: float) -> dict:
    return {
        "id": "c0",
        "generation": 0,
        "score": score,
        "metrics": {"combined_score": score},
        "patch_content": "",
        "parent_id": None,
        "failed": False,
    }


# ---------------------------------------------------------------------------
# Property 6 tests
# ---------------------------------------------------------------------------

@given(baseline=POSITIVE_SCORE, best=POSITIVE_SCORE, threshold=THRESHOLD)
@settings(max_examples=200, deadline=None)
def test_property_6_success_threshold_differentiation(
    baseline: float, best: float, threshold: float
) -> None:
    """
    **Property 6: Success Threshold Differentiation**
    **Validates: Requirements 17.5, 17.6**

    For ANY (baseline, best, threshold):
    - improvement = (best - baseline) / |baseline|
    - status == "successful"  iff  improvement > threshold
    - status == "completed"   iff  improvement <= threshold
    """
    report = generate_final_report(
        best_candidate=_make_candidate(best),
        baseline_candidate=_make_candidate(baseline),
        success_threshold=threshold,
    )

    improvement = report["improvement"]
    status = report["status"]

    if improvement > threshold:
        assert status == "successful", (
            f"improvement={improvement:.6f} > threshold={threshold:.6f} "
            f"but status={status!r} (expected 'successful')"
        )
    else:
        assert status == "completed", (
            f"improvement={improvement:.6f} <= threshold={threshold:.6f} "
            f"but status={status!r} (expected 'completed')"
        )


@given(baseline=POSITIVE_SCORE, threshold=THRESHOLD)
@settings(max_examples=100, deadline=None)
def test_property_6_equal_scores_always_completed(
    baseline: float, threshold: float
) -> None:
    """When best == baseline (0 % improvement), status is always 'completed'."""
    report = generate_final_report(
        best_candidate=_make_candidate(baseline),
        baseline_candidate=_make_candidate(baseline),
        success_threshold=threshold,
    )
    assert report["status"] == "completed"
    assert abs(report["improvement"]) < 1e-9


@given(baseline=POSITIVE_SCORE, threshold=THRESHOLD)
@settings(max_examples=100, deadline=None)
def test_property_6_regression_always_completed(
    baseline: float, threshold: float
) -> None:
    """When best < baseline (regression), status is always 'completed'."""
    # Use a score clearly worse than baseline
    worse = max(0.0, baseline * 0.5)
    report = generate_final_report(
        best_candidate=_make_candidate(worse),
        baseline_candidate=_make_candidate(baseline),
        success_threshold=threshold,
    )
    assert report["status"] == "completed"


@given(threshold=THRESHOLD)
@settings(max_examples=50, deadline=None)
def test_property_6_confidence_warning_iff_not_successful(
    threshold: float,
) -> None:
    """confidence_warning is True iff status is 'completed'."""
    for best, expected_status in [
        (1.0 + threshold * 1.5, "successful"),  # clearly above threshold
        (1.0, "completed"),                      # no improvement
    ]:
        report = generate_final_report(
            best_candidate=_make_candidate(best),
            baseline_candidate=_make_candidate(1.0),
            success_threshold=threshold,
        )
        if report["status"] == "successful":
            assert report["confidence_warning"] is False, (
                f"successful run should not have confidence_warning"
            )
        else:
            assert report["confidence_warning"] is True, (
                f"completed run should have confidence_warning"
            )
