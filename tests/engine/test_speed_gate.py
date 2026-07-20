"""
Unit tests for the confidence-based (dispersion-aware) speed gate.

Covers the pure gate helper ``accept_as_new_best`` plus its integration into
OptimizerLoop best-selection (``_is_new_best``) and config passthrough of
``metric.min_effect`` (design §C1, Requirements 2 and 10).
"""

from __future__ import annotations

from typing import Any, Dict

from openevolve.optimizer_loop import (
    DEFAULT_MIN_EFFECT,
    OptimizerLoop,
    accept_as_new_best,
)


def _dist(median: float, stddev: float = 0.0) -> Dict[str, Any]:
    return {"speed_ms_median": median, "speed_ms_stddev": stddev}


def _config(**overrides) -> Dict[str, Any]:
    base = {
        "repo_path": "/fake/repo",
        "target_file": "/fake/repo/program.py",
        "test_file": "/fake/repo/test_program.py",
        "max_iterations": 5,
        "patience": 3,
        "db_path": ":memory:",
    }
    base.update(overrides)
    return base


# ── accept_as_new_best: pure gate math ───────────────────────────────────────

def test_clear_win_is_accepted():
    """Large median improvement with tight variance clears both conditions."""
    base = _dist(100.0, 2.0)
    cand = _dist(80.0, 2.0)  # 20% faster; 80 + 2 = 82 < 100
    assert accept_as_new_best(base, cand, 0.03) is True


def test_noise_only_below_min_effect_is_rejected():
    """A sub-min_effect median improvement is rejected (condition a fails)."""
    base = _dist(100.0, 1.0)
    cand = _dist(98.0, 1.0)  # only 2% < 3% min_effect
    assert accept_as_new_best(base, cand, 0.03) is False


def test_dispersion_overlap_is_rejected_even_with_big_median_gain():
    """A real median gain is rejected when the dispersion band overlaps (CP2)."""
    base = _dist(100.0, 20.0)
    cand = _dist(90.0, 5.0)  # 10% faster but 90 + max(5,20)=110 >= 100
    assert accept_as_new_best(base, cand, 0.03) is False


def test_boundary_band_touching_median_is_rejected():
    """Equality (band exactly touches baseline median) is a rejection."""
    base = _dist(100.0, 0.0)
    cand = _dist(90.0, 10.0)  # 90 + 10 == 100 -> not strictly below
    assert accept_as_new_best(base, cand, 0.03) is False


def test_repeats_one_reduces_to_median_improvement():
    """With stddev=0 (repeats=1) the gate is just median improvement >= min_effect."""
    base = _dist(100.0, 0.0)
    assert accept_as_new_best(base, _dist(96.0, 0.0), 0.03) is True   # 4% >= 3%
    assert accept_as_new_best(base, _dist(98.0, 0.0), 0.03) is False  # 2% < 3%


def test_min_effect_boundary_is_inclusive():
    """Exactly min_effect improvement is accepted (>= comparison)."""
    base = _dist(100.0, 0.0)
    cand = _dist(97.0, 0.0)  # exactly 3%
    assert accept_as_new_best(base, cand, 0.03) is True


def test_speed_ms_used_as_median_fallback():
    """The back-compat ``speed_ms`` field is honored when median is absent."""
    base = {"speed_ms": 100.0}
    cand = {"speed_ms": 80.0}
    assert accept_as_new_best(base, cand, 0.03) is True


def test_missing_median_returns_false():
    """No usable median on either side -> gate declines (caller falls back)."""
    assert accept_as_new_best({}, _dist(80.0), 0.03) is False
    assert accept_as_new_best(_dist(100.0), {}, 0.03) is False
    assert accept_as_new_best(None, None, 0.03) is False


def test_non_positive_baseline_median_returns_false():
    assert accept_as_new_best(_dist(0.0), _dist(0.0), 0.03) is False


# ── OptimizerLoop._is_new_best integration ───────────────────────────────────

def _candidate(score: float, metrics: Dict[str, Any], failed: bool = False) -> Dict[str, Any]:
    return {"id": "c", "score": score, "metrics": metrics, "failed": failed}


def test_is_new_best_applies_gate_when_distribution_present():
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    best = _candidate(0.5, {"combined_score": 0.5, **_dist(100.0, 2.0)})
    # Clear win: faster median, tight variance, higher combined_score.
    win = _candidate(0.7, {"combined_score": 0.7, **_dist(80.0, 2.0)})
    assert loop._is_new_best(best, win, 0.7, 0.5) is True

    # Noise-only: median barely moves -> gate rejects even though score rose.
    noise = _candidate(0.51, {"combined_score": 0.51, **_dist(99.0, 2.0)})
    assert loop._is_new_best(best, noise, 0.51, 0.5) is False


def test_is_new_best_rejects_failed_candidate():
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    best = _candidate(0.5, {"combined_score": 0.5, **_dist(100.0, 2.0)})
    failed = _candidate(0.9, {"combined_score": 0.9, **_dist(10.0, 1.0)}, failed=True)
    assert loop._is_new_best(best, failed, 0.9, 0.5) is False


def test_is_new_best_falls_back_to_score_without_distribution():
    """No speed distribution -> plain score comparison (backward compatible)."""
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    best = _candidate(0.5, {"combined_score": 0.5})
    better = _candidate(0.51, {"combined_score": 0.51})
    worse = _candidate(0.49, {"combined_score": 0.49})
    assert loop._is_new_best(best, better, 0.51, 0.5) is True
    assert loop._is_new_best(best, worse, 0.49, 0.5) is False


def test_is_new_best_falls_back_for_non_speed_metric():
    """A custom (non-speed) metric bypasses the speed gate."""
    loop = OptimizerLoop(_config(metric_name="accuracy"), llm_ensemble=None)
    best = _candidate(0.5, {"accuracy": 0.5, **_dist(100.0, 2.0)})
    # Distribution shows no improvement, but the metric is accuracy -> score wins.
    cand = _candidate(0.9, {"accuracy": 0.9, **_dist(100.0, 2.0)})
    assert loop._is_new_best(best, cand, 0.9, 0.5) is True


# ── Config passthrough ────────────────────────────────────────────────────────

def test_min_effect_defaults_to_three_percent():
    loop = OptimizerLoop(_config(), llm_ensemble=None)
    assert loop.min_effect == DEFAULT_MIN_EFFECT == 0.03


def test_min_effect_override_from_config():
    loop = OptimizerLoop(_config(min_effect=0.10), llm_ensemble=None)
    assert loop.min_effect == 0.10


def test_min_effect_invalid_falls_back_to_default():
    loop = OptimizerLoop(_config(min_effect="not-a-number"), llm_ensemble=None)
    assert loop.min_effect == DEFAULT_MIN_EFFECT


def test_min_effect_governs_loop_gate():
    """A configured min_effect changes the acceptance boundary end-to-end."""
    strict = OptimizerLoop(_config(min_effect=0.10), llm_ensemble=None)
    base = _candidate(0.5, {"combined_score": 0.5, **_dist(100.0, 0.0)})
    cand = _candidate(0.55, {"combined_score": 0.55, **_dist(95.0, 0.0)})  # 5% faster
    # 5% clears the default 3% but not a stricter 10% threshold.
    assert strict._is_new_best(base, cand, 0.55, 0.5) is False
    lenient = OptimizerLoop(_config(min_effect=0.03), llm_ensemble=None)
    assert lenient._is_new_best(base, cand, 0.55, 0.5) is True
