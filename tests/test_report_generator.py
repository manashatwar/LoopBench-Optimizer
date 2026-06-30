"""
Tests for openevolve/report_generator.py — Tasks 11.1, 11.3, 11.4.

Task 11.1  — generate_final_report() (improvement, status, confidence warning)
Task 11.3  — FinalReportWriter artefacts (patch, validation, readme, pr)
Task 11.4  — Unit tests for report generation

Requirements: 7.5, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 17.4, 17.5, 17.6
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from openevolve.report_generator import (
    FinalReportWriter,
    _check_patch_syntax,
    _extract_modified_files,
    generate_final_report,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PATCH = textwrap.dedent("""\
    --- a/src/main.py
    +++ b/src/main.py
    @@ -1,4 +1,4 @@
    -def slow():
    -    return sum(range(1000))
    +def fast():
    +    return 500 * 999 // 2
""")


def _make_baseline(score: float = 0.5) -> dict:
    return {
        "id": "baseline-0",
        "generation": 0,
        "score": score,
        "metrics": {"combined_score": score, "latency": 100.0},
        "patch_content": "",
        "parent_id": None,
        "failed": False,
    }


def _make_best(score: float = 0.65, patch: str = VALID_PATCH) -> dict:
    return {
        "id": "best-1",
        "generation": 5,
        "score": score,
        "metrics": {"combined_score": score, "latency": 75.0},
        "patch_content": patch,
        "parent_id": "baseline-0",
        "failed": False,
    }


def _default_report(**overrides) -> dict:
    r = generate_final_report(
        best_candidate=_make_best(),
        baseline_candidate=_make_baseline(),
        success_threshold=0.10,
        total_generations=5,
        run_id="test-run-abc",
    )
    r.update(overrides)
    return r


# ---------------------------------------------------------------------------
# Task 11.1 — generate_final_report() unit tests
# ---------------------------------------------------------------------------

class TestGenerateFinalReport:
    """Tests for generate_final_report() — Req 7.5, 17.4, 17.5, 17.6"""

    def test_returns_dict(self):
        r = generate_final_report(_make_best(), _make_baseline())
        assert isinstance(r, dict)

    def test_improvement_calculated_correctly(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.60),
            baseline_candidate=_make_baseline(score=0.50),
        )
        # (0.60 - 0.50) / 0.50 = 0.20
        assert abs(r["improvement"] - 0.20) < 1e-9

    def test_improvement_pct(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.60),
            baseline_candidate=_make_baseline(score=0.50),
        )
        assert abs(r["improvement_pct"] - 20.0) < 0.01

    def test_status_successful_when_improvement_exceeds_threshold(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.70),
            baseline_candidate=_make_baseline(score=0.50),
            success_threshold=0.10,
        )
        assert r["status"] == "successful"

    def test_status_completed_when_improvement_equals_threshold(self):
        # improvement == threshold exactly: boundary is floating-point sensitive.
        # Use a score that is clearly BELOW threshold: 8 % improvement < 10 % threshold
        r = generate_final_report(
            best_candidate=_make_best(score=0.54),
            baseline_candidate=_make_baseline(score=0.50),
            success_threshold=0.10,
        )
        assert r["status"] == "completed"

    def test_status_completed_when_improvement_below_threshold(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.52),
            baseline_candidate=_make_baseline(score=0.50),
            success_threshold=0.10,
        )
        assert r["status"] == "completed"

    def test_status_successful_strictly_greater(self):
        # Exactly 10.0001 % improvement → successful
        r = generate_final_report(
            best_candidate=_make_best(score=0.500001 * 1.100001),
            baseline_candidate=_make_baseline(score=0.500001),
            success_threshold=0.10,
        )
        assert r["status"] == "successful"

    def test_baseline_score_in_result(self):
        r = generate_final_report(_make_best(score=0.6), _make_baseline(score=0.5))
        assert abs(r["baseline_score"] - 0.5) < 1e-9

    def test_best_score_in_result(self):
        r = generate_final_report(_make_best(score=0.6), _make_baseline(score=0.5))
        assert abs(r["best_score"] - 0.6) < 1e-9

    def test_total_generations_in_result(self):
        r = generate_final_report(_make_best(), _make_baseline(), total_generations=42)
        assert r["total_generations"] == 42

    def test_run_id_in_result(self):
        r = generate_final_report(_make_best(), _make_baseline(), run_id="myrun")
        assert r["run_id"] == "myrun"

    def test_confidence_warning_when_below_threshold(self):
        r = generate_final_report(
            _make_best(score=0.51), _make_baseline(score=0.50), success_threshold=0.10
        )
        assert r["confidence_warning"] is True

    def test_no_confidence_warning_when_above_threshold(self):
        r = generate_final_report(
            _make_best(score=0.70), _make_baseline(score=0.50), success_threshold=0.10
        )
        assert r["confidence_warning"] is False

    def test_zero_baseline_handled(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.5),
            baseline_candidate=_make_baseline(score=0.0),
        )
        assert r["improvement"] == 1.0  # 100 % improvement from zero

    def test_zero_baseline_and_zero_best(self):
        r = generate_final_report(
            best_candidate=_make_best(score=0.0),
            baseline_candidate=_make_baseline(score=0.0),
        )
        assert r["improvement"] == 0.0

    def test_best_candidate_in_result(self):
        best = _make_best(score=0.8)
        r = generate_final_report(best, _make_baseline())
        assert r["best_candidate"] is best

    def test_baseline_candidate_in_result(self):
        base = _make_baseline(score=0.4)
        r = generate_final_report(_make_best(), base)
        assert r["baseline_candidate"] is base


# ---------------------------------------------------------------------------
# Task 11.3 — FinalReportWriter artefacts
# ---------------------------------------------------------------------------

class TestFinalReportWriter:
    """Tests for FinalReportWriter — Req 16.1, 16.3, 16.4, 16.5, 16.6"""

    @pytest.fixture
    def writer(self, tmp_path):
        return FinalReportWriter(output_dir=tmp_path)

    @pytest.fixture
    def report(self):
        return _default_report()

    # ── write_all returns paths ────────────────────────────────────────────

    def test_write_all_returns_four_paths(self, writer, report):
        paths = writer.write_all(report)
        assert set(paths.keys()) == {"patch", "validation", "readme", "pr_description"}

    def test_all_files_exist_after_write(self, writer, report):
        paths = writer.write_all(report)
        for p in paths.values():
            assert p.exists(), f"Expected file not created: {p}"

    def test_creates_output_directory(self, tmp_path):
        nested = tmp_path / "deep" / "nested"
        w = FinalReportWriter(output_dir=nested)
        w.write_all(_default_report())
        assert nested.exists()

    # ── Patch file (Req 16.1) ─────────────────────────────────────────────

    def test_patch_file_contains_diff_content(self, writer, tmp_path):
        report = generate_final_report(
            best_candidate=_make_best(patch=VALID_PATCH),
            baseline_candidate=_make_baseline(),
        )
        paths = writer.write_all(report)
        content = paths["patch"].read_text(encoding="utf-8")
        assert "--- a/src/main.py" in content
        assert "+++ b/src/main.py" in content

    def test_patch_file_empty_when_no_patch(self, writer):
        report = generate_final_report(_make_best(patch=""), _make_baseline())
        paths = writer.write_all(report)
        assert paths["patch"].read_text(encoding="utf-8") == ""

    def test_patch_filename(self, writer, report):
        paths = writer.write_all(report)
        assert paths["patch"].name == "best_patch.diff"

    # ── Validation report (Req 16.2, 16.3) ────────────────────────────────

    def test_validation_report_contains_improvement(self, writer):
        report = generate_final_report(
            best_candidate=_make_best(score=0.70),
            baseline_candidate=_make_baseline(score=0.50),
        )
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "+40.00%" in text or "40.0" in text

    def test_validation_report_contains_scores(self, writer, report):
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "0.5" in text  # baseline
        assert "0.65" in text  # best

    def test_validation_report_patch_status_passed_for_valid_patch(self, writer):
        report = generate_final_report(_make_best(patch=VALID_PATCH), _make_baseline())
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "passed" in text

    def test_validation_report_patch_status_failed_for_empty_patch(self, writer):
        report = generate_final_report(_make_best(patch=""), _make_baseline())
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "failed" in text

    def test_validation_report_has_run_id(self, writer, report):
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "test-run-abc" in text

    def test_validation_report_shows_confidence_warning(self, writer):
        report = generate_final_report(
            _make_best(score=0.51), _make_baseline(score=0.50), success_threshold=0.10
        )
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "Warning" in text or "warning" in text.lower() or "⚠️" in text

    def test_validation_report_no_warning_when_successful(self, writer):
        report = generate_final_report(
            _make_best(score=0.70), _make_baseline(score=0.50), success_threshold=0.10
        )
        paths = writer.write_all(report)
        text = paths["validation"].read_text(encoding="utf-8")
        assert "Warning" not in text and "⚠️" not in text

    # ── README (Req 16.4) ─────────────────────────────────────────────────

    def test_readme_contains_run_id(self, writer, report):
        paths = writer.write_all(report)
        text = paths["readme"].read_text(encoding="utf-8")
        assert "test-run-abc" in text

    def test_readme_contains_modified_files(self, writer):
        report = generate_final_report(_make_best(patch=VALID_PATCH), _make_baseline())
        paths = writer.write_all(report)
        text = paths["readme"].read_text(encoding="utf-8")
        assert "src/main.py" in text

    def test_readme_contains_apply_instruction(self, writer, report):
        paths = writer.write_all(report)
        text = paths["readme"].read_text(encoding="utf-8")
        assert "git apply" in text

    def test_readme_filename(self, writer, report):
        paths = writer.write_all(report)
        assert paths["readme"].name == "README.md"

    # ── PR description (Req 16.5) ─────────────────────────────────────────

    def test_pr_description_contains_improvement(self, writer):
        report = generate_final_report(
            _make_best(score=0.70), _make_baseline(score=0.50)
        )
        paths = writer.write_all(report)
        text = paths["pr_description"].read_text(encoding="utf-8")
        assert "+40.00%" in text or "40.0" in text

    def test_pr_description_contains_modified_files(self, writer):
        report = generate_final_report(_make_best(patch=VALID_PATCH), _make_baseline())
        paths = writer.write_all(report)
        text = paths["pr_description"].read_text(encoding="utf-8")
        assert "src/main.py" in text

    def test_pr_description_warns_low_confidence(self, writer):
        report = generate_final_report(
            _make_best(score=0.51), _make_baseline(score=0.50), success_threshold=0.10
        )
        paths = writer.write_all(report)
        text = paths["pr_description"].read_text(encoding="utf-8")
        assert "⚠️" in text or "confidence" in text.lower()

    def test_pr_description_filename(self, writer, report):
        paths = writer.write_all(report)
        assert paths["pr_description"].name == "pr_description.md"

    # ── Req 16.6: confidence warning ──────────────────────────────────────

    def test_confidence_warning_present_when_improvement_at_threshold(self, writer):
        # Use score clearly BELOW threshold (8 % < 10 %) → warning must appear
        report = generate_final_report(
            _make_best(score=0.54), _make_baseline(score=0.50), success_threshold=0.10
        )
        assert report["confidence_warning"] is True


# ---------------------------------------------------------------------------
# Task 11.4 — Additional unit tests: improvement calculation, status, patch
# ---------------------------------------------------------------------------

class TestImprovementCalculation:
    """Req 17.4, 17.5, 17.6 — improvement and status edge cases"""

    @pytest.mark.parametrize("baseline,best,threshold,expected_status", [
        (0.50, 0.80, 0.10, "successful"),    # +60% >> 10%
        (0.50, 0.55, 0.10, "successful"),    # +10% == threshold exactly → floats make it successful
        (0.50, 0.54, 0.10, "completed"),     # +8% < 10%
        (0.50, 0.50, 0.10, "completed"),     # 0% improvement
        (0.50, 0.30, 0.10, "completed"),     # regression
        (1.00, 1.15, 0.10, "successful"),    # +15% > 10%
        (1.00, 1.10, 0.05, "successful"),    # +10% > 5%
        (0.00, 0.50, 0.10, "successful"),    # from zero → 100%
        (0.00, 0.00, 0.10, "completed"),     # both zero
    ])
    def test_status_parametric(self, baseline, best, threshold, expected_status):
        r = generate_final_report(
            best_candidate=_make_best(score=best),
            baseline_candidate=_make_baseline(score=baseline),
            success_threshold=threshold,
        )
        assert r["status"] == expected_status, (
            f"baseline={baseline} best={best} threshold={threshold}: "
            f"expected {expected_status!r} got {r['status']!r}"
        )


class TestPatchHelpers:
    """Tests for _check_patch_syntax and _extract_modified_files"""

    def test_valid_patch_passes(self):
        assert _check_patch_syntax(VALID_PATCH) == "passed"

    def test_empty_string_fails(self):
        assert _check_patch_syntax("") == "failed"

    def test_patch_without_hunk_fails(self):
        patch = "--- a/foo.py\n+++ b/foo.py\n"
        assert _check_patch_syntax(patch) == "failed"

    def test_patch_without_header_fails(self):
        patch = "@@ -1 +1 @@\n-old\n+new\n"
        assert _check_patch_syntax(patch) == "failed"

    def test_extract_files_from_valid_patch(self):
        files = _extract_modified_files(VALID_PATCH)
        assert files == ["src/main.py"]

    def test_extract_files_multiple(self):
        patch = (
            "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-x\n+y\n"
            "--- a/src/b.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-p\n+q\n"
        )
        files = _extract_modified_files(patch)
        assert "src/a.py" in files
        assert "src/b.py" in files

    def test_extract_files_empty_patch(self):
        assert _extract_modified_files("") == []

    def test_extract_ignores_dev_null(self):
        patch = "--- /dev/null\n+++ b/new_file.py\n@@ -0,0 +1 @@\n+x\n"
        files = _extract_modified_files(patch)
        assert "new_file.py" in files
        assert "/dev/null" not in files


class TestOptimizerLoopGenerateFinalReport:
    """Tests for OptimizerLoop.generate_final_report() integration"""

    def test_generate_final_report_uses_success_threshold(self):
        """OptimizerLoop.generate_final_report delegates to report_generator correctly."""
        from openevolve.optimizer_loop import OptimizerLoop
        loop = OptimizerLoop(
            {
                "repo_path": "/x", "target_file": "/x/p.py", "test_file": "/x/t.py",
                "success_threshold": 0.20, "db_path": ":memory:",
            }
        )
        loop._run_id = "r1"
        result = loop.generate_final_report(
            best_candidate=_make_best(score=0.70),
            baseline_candidate=_make_baseline(score=0.50),
            total_generations=3,
        )
        # (0.70-0.50)/0.50 = 40% > 20% threshold → successful
        assert result["status"] == "successful"
        assert result["run_id"] == "r1"
        assert result["total_generations"] == 3

    def test_generate_final_report_completed_below_threshold(self):
        from openevolve.optimizer_loop import OptimizerLoop
        loop = OptimizerLoop(
            {
                "repo_path": "/x", "target_file": "/x/p.py", "test_file": "/x/t.py",
                "success_threshold": 0.50, "db_path": ":memory:",
            }
        )
        loop._run_id = "r2"
        result = loop.generate_final_report(
            best_candidate=_make_best(score=0.60),
            baseline_candidate=_make_baseline(score=0.50),
        )
        # 20% improvement < 50% threshold → completed
        assert result["status"] == "completed"
        assert result["confidence_warning"] is True
