"""Tests for the generic (non-Python) scorer path (design §C3, R7, CP5).

The generic scorer computes ``score.json`` for non-pytest commands using only
shell + awk — no ``python3`` — so non-Python images can be scored. These tests
prove CP5: the shell/awk scorer produces the SAME score as the python scorer
for identical inputs.

Two layers of coverage:
  1. A pure-Python reference (``_generic_score``) encoding the exact formula is
     always unit-tested against known outputs and the aggregation helper.
  2. When ``sh`` and ``awk`` are on PATH (e.g. CI Linux), the real
     ``sandbox/score_generic.sh`` is invoked via subprocess and asserted equal
     to the reference. On hosts without those tools (typically Windows dev
     boxes) that portion is skipped.
"""

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from sandbox.runner import _generic_score, aggregate_speed_samples

_SCRIPT = Path(__file__).resolve().parents[2] / "sandbox" / "score_generic.sh"
_HAVE_SHELL = shutil.which("sh") is not None and shutil.which("awk") is not None

# Input tables shared by the reference tests and the shell-equivalence test.
# (samples, cmd_exit)
_CASES = [
    ([100.0, 200.0, 300.0], 0),   # multiple samples, passing
    ([10.0, 20.0, 30.0, 40.0], 0),  # even count -> midpoint median
    ([42.0], 0),                  # single sample -> stddev 0
    ([], 0),                      # no samples, passing -> combined == correctness
    ([100.0, 200.0, 300.0], 1),   # multiple samples, failing -> correctness 0
    ([42.0], 1),                  # single sample, failing
    ([], 1),                      # no samples, failing
]


# ── Pure-Python reference: formula + rounding ────────────────────────────────

def test_multi_sample_distribution_and_scores():
    score = _generic_score([100.0, 200.0, 300.0], cmd_exit=0)
    assert score["passed"] == 1
    assert score["failed"] == 0
    assert score["correctness"] == 1.0
    assert score["all_passed"] is True
    assert score["cmd_exit"] == 0
    # Distribution mirrors aggregate_speed_samples.
    assert score["speed_ms"] == 200.0
    assert score["speed_ms_median"] == 200.0
    assert score["speed_ms_mean"] == 200.0
    assert score["speed_ms_stddev"] == 100.0
    assert score["speed_ms_samples"] == [100.0, 200.0, 300.0]
    assert score["runs"] == 3
    # speed_score = exp(-median/150), combined = correctness * speed_score.
    expected_speed_score = round(math.exp(-200.0 / 150.0), 6)
    assert score["speed_score"] == expected_speed_score
    assert score["combined_score"] == expected_speed_score


def test_even_count_median_is_midpoint_average():
    score = _generic_score([10.0, 20.0, 30.0, 40.0], cmd_exit=0)
    assert score["speed_ms_median"] == 25.0
    assert score["speed_ms_mean"] == 25.0
    assert score["speed_ms_stddev"] == 12.9099
    assert score["runs"] == 4


def test_single_sample_has_zero_stddev():
    score = _generic_score([42.0], cmd_exit=0)
    assert score["speed_ms"] == 42.0
    assert score["speed_ms_median"] == 42.0
    assert score["speed_ms_mean"] == 42.0
    assert score["speed_ms_stddev"] == 0.0
    assert score["speed_ms_samples"] == [42.0]
    assert score["runs"] == 1
    assert score["speed_score"] == round(math.exp(-42.0 / 150.0), 6)


def test_no_samples_scores_on_correctness_alone():
    score = _generic_score([], cmd_exit=0)
    assert score["speed_ms"] is None
    assert score["speed_ms_median"] is None
    assert score["speed_ms_mean"] is None
    assert score["speed_ms_stddev"] is None
    assert score["speed_ms_samples"] == []
    assert score["runs"] == 0
    assert score["speed_score"] == 0.0
    # With no speed marker, a passing run scores on correctness alone.
    assert score["correctness"] == 1.0
    assert score["combined_score"] == 1.0


def test_nonzero_exit_yields_zero_correctness():
    score = _generic_score([100.0, 200.0, 300.0], cmd_exit=1)
    assert score["passed"] == 0
    assert score["failed"] == 1
    assert score["all_passed"] is False
    assert score["correctness"] == 0.0
    # correctness == 0 -> speed_score 0 and combined 0 even with speed present.
    assert score["speed_score"] == 0.0
    assert score["combined_score"] == 0.0
    # Distribution is still reported.
    assert score["speed_ms_median"] == 200.0
    assert score["runs"] == 3


def test_no_samples_failing_combined_is_zero():
    score = _generic_score([], cmd_exit=1)
    assert score["correctness"] == 0.0
    assert score["speed_ms"] is None
    # combined_score == correctness (0.0) when there is no speed marker.
    assert score["combined_score"] == 0.0


def test_reference_distribution_matches_aggregate_helper():
    for samples, _exit in _CASES:
        agg = aggregate_speed_samples(samples)
        score = _generic_score(samples, cmd_exit=0)
        for field in (
            "speed_ms",
            "speed_ms_median",
            "speed_ms_mean",
            "speed_ms_stddev",
            "speed_ms_samples",
            "runs",
        ):
            assert score[field] == agg[field]


# ── Shell/awk equivalence (CP5) — runs where sh + awk exist ──────────────────

def _run_shell_scorer(tmp_path: Path, samples, cmd_exit: int) -> dict:
    exit_file = tmp_path / "exit_code"
    samples_file = tmp_path / "speed_samples.txt"
    score_file = tmp_path / "score.json"
    exit_file.write_text(f"{cmd_exit}\n", encoding="utf-8")
    samples_file.write_text(
        "".join(f"{v}\n" for v in samples), encoding="utf-8"
    )
    subprocess.run(
        ["sh", str(_SCRIPT), str(exit_file), str(samples_file), str(score_file)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(score_file.read_text(encoding="utf-8"))


def _assert_scores_equal(shell: dict, ref: dict) -> None:
    assert set(shell) == set(ref)
    for key, expected in ref.items():
        actual = shell[key]
        if isinstance(expected, list):
            assert len(actual) == len(expected)
            for a, e in zip(actual, expected):
                assert a == pytest.approx(e, abs=1e-4)
        elif isinstance(expected, bool) or expected is None:
            assert actual == expected
        elif isinstance(expected, float):
            assert actual == pytest.approx(expected, abs=1e-6)
        else:
            assert actual == expected


@pytest.mark.skipif(not _HAVE_SHELL, reason="sh/awk not available on this host")
@pytest.mark.parametrize("samples,cmd_exit", _CASES)
def test_shell_scorer_equals_python_reference(tmp_path: Path, samples, cmd_exit):
    shell_score = _run_shell_scorer(tmp_path, samples, cmd_exit)
    ref_score = _generic_score(samples, cmd_exit)
    _assert_scores_equal(shell_score, ref_score)


@pytest.mark.skipif(not _HAVE_SHELL, reason="sh/awk not available on this host")
def test_shell_scorer_emits_valid_json_with_nulls(tmp_path: Path):
    # No samples -> null distribution fields must still be valid JSON.
    score = _run_shell_scorer(tmp_path, [], cmd_exit=0)
    assert score["speed_ms"] is None
    assert score["speed_ms_samples"] == []
    assert score["runs"] == 0
    assert score["combined_score"] == 1.0
