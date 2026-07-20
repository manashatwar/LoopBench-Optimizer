"""
Property CP2 — No noisy acceptance (dispersion-aware speed gate).

If a candidate's dispersion band overlaps the baseline median
(``median_cand + max(stddev_cand, stddev_base) >= median_base``) it is NEVER
accepted as a new best, for arbitrary valid speed distributions.

Validates: Requirements 2.2, 2.4 (design §C1, correctness property CP2)
"""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from openevolve.optimizer_loop import accept_as_new_best


# ── Strategies over valid speed distributions (milliseconds) ─────────────────

MEDIAN = st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False)
STDDEV = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)
MIN_EFFECT = st.floats(min_value=0.0, max_value=0.9, allow_nan=False, allow_infinity=False)


def _dist(median: float, stddev: float) -> dict:
    return {"speed_ms_median": median, "speed_ms_stddev": stddev}


@given(
    median_base=MEDIAN,
    median_cand=MEDIAN,
    stddev_base=STDDEV,
    stddev_cand=STDDEV,
    min_effect=MIN_EFFECT,
)
@settings(max_examples=400, deadline=None)
def test_cp2_dispersion_overlap_never_accepted(
    median_base: float,
    median_cand: float,
    stddev_base: float,
    stddev_cand: float,
    min_effect: float,
) -> None:
    """**Property CP2** — **Validates: Requirements 2.2, 2.4**

    Whenever the candidate's dispersion band overlaps (or touches) the baseline
    median, the gate must reject it regardless of median improvement.
    """
    base = _dist(median_base, stddev_base)
    cand = _dist(median_cand, stddev_cand)

    overlaps = median_cand + max(stddev_cand, stddev_base) >= median_base
    if overlaps:
        assert accept_as_new_best(base, cand, min_effect) is False


@given(
    median_base=MEDIAN,
    median_cand=MEDIAN,
    stddev_base=STDDEV,
    stddev_cand=STDDEV,
    min_effect=st.floats(min_value=1e-3, max_value=0.9,
                         allow_nan=False, allow_infinity=False),
)
@settings(max_examples=400, deadline=None)
def test_gate_acceptance_implies_both_conditions(
    median_base: float,
    median_cand: float,
    stddev_base: float,
    stddev_cand: float,
    min_effect: float,
) -> None:
    """When the gate accepts, BOTH the effect-size and dispersion guards hold.

    This is the contrapositive companion to CP2: any accepted candidate has a
    real (>= min_effect) median improvement AND a dispersion band strictly
    below the baseline median (design §C1, Requirement 2.5, property CP1).
    """
    base = _dist(median_base, stddev_base)
    cand = _dist(median_cand, stddev_cand)

    if accept_as_new_best(base, cand, min_effect):
        rel_improvement = (median_base - median_cand) / median_base
        assert rel_improvement >= min_effect
        assert median_cand + max(stddev_cand, stddev_base) < median_base
