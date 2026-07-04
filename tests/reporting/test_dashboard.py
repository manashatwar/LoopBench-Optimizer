"""
Tests for the Dashboard — Task 15.7.

Covers:
  - data.json generation from a real run (Task 15.6)
  - Static HTML contains all required UI sections (Task 15.1 – 15.5)
  - CLI dashboard command creates correct output files
  - Candidate detail data structure
  - Chart export button present (Task 15.5)

Requirements: 11.1 – 11.6, 9.1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from openevolve.cli import _opt_cmd_dashboard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "run_id": None,
        "db": None,
        "port": 8080,
        "open_browser": False,
        "docs_dir": "docs",
        "no_server": True,   # default: no server in tests
        "log_level": "INFO",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _create_test_run(db_path: str, run_id: str = "dash-test-run") -> str:
    from openevolve.database import CandidateDatabase
    db = CandidateDatabase(db_path)
    db.create_run(run_id=run_id, target_repo="https://github.com/test/repo",
                  success_threshold=0.1)

    PATCH = "--- a/src/main.py\n+++ b/src/main.py\n@@ -1 +1 @@\n-old\n+new\n"

    baseline_id = db.insert_candidate(
        run_id=run_id, generation=0, parent_id=None,
        patch_content="", score=0.50, failed=False,
        metrics={"combined_score": 0.50},
        applied=True, tested=True, exit_code=0,
        stdout="baseline output", stderr="",
    )
    gen1_id = db.insert_candidate(
        run_id=run_id, generation=1, parent_id=baseline_id,
        patch_content=PATCH, score=0.70, failed=False,
        metrics={"combined_score": 0.70},
        applied=True, tested=True, exit_code=0,
        stdout="gen1 output", stderr="",
    )
    db.insert_candidate(
        run_id=run_id, generation=2, parent_id=gen1_id,
        patch_content="", score=0.0, failed=True,
        failure_phase="apply", error_message="patch apply failed",
        metrics={}, applied=False, tested=False,
    )
    db.complete_run(run_id, status="successful", final_improvement=0.40)
    db.close()
    return run_id


# ---------------------------------------------------------------------------
# Task 15.6 — data.json generation
# ---------------------------------------------------------------------------

class TestDataJsonGeneration:
    def test_creates_data_json(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        ns = _make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir)
        rc = _opt_cmd_dashboard(ns)
        assert rc == 0
        assert (tmp_path / "docs" / "data.json").exists()

    def test_data_json_is_valid_json(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_data_json_has_run_key(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        assert "run" in data
        assert data["run"]["id"] == run_id

    def test_data_json_has_candidates(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        assert "candidates" in data
        assert len(data["candidates"]) == 3  # baseline + 2 generations

    def test_data_json_has_best_candidate(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        assert "best_candidate" in data
        best = data["best_candidate"]
        assert best is not None
        assert best.get("score", 0) >= 0.70

    def test_data_json_has_audit_log(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        assert "audit_log" in data

    def test_nonexistent_run_returns_error(self, tmp_path, capsys):
        db_path = str(tmp_path / "optimizer.db")
        from openevolve.database import CandidateDatabase
        CandidateDatabase(db_path).close()
        docs_dir = str(tmp_path / "docs")
        rc = _opt_cmd_dashboard(_make_ns(run_id="no-such-run", db=db_path, docs_dir=docs_dir))
        assert rc != 0

    def test_no_run_id_no_crash(self, tmp_path, capsys):
        docs_dir = str(tmp_path / "docs")
        # No run_id, no data.json — should warn but not crash
        rc = _opt_cmd_dashboard(_make_ns(run_id=None, db=None, docs_dir=docs_dir))
        assert rc == 0  # graceful


# ---------------------------------------------------------------------------
# Task 15.1 — static HTML structure
# ---------------------------------------------------------------------------

class TestStaticHtml:
    @pytest.fixture
    def html(self):
        return (Path(__file__).resolve().parents[2] / "docs" / "index.html").read_text(encoding="utf-8")

    def test_html_file_exists(self):
        assert (Path(__file__).resolve().parents[2] / "docs" / "index.html").exists()

    def test_html_has_react_cdn(self, html):
        assert "react" in html.lower()
        assert "unpkg.com" in html

    def test_html_has_recharts_cdn(self, html):
        assert "recharts" in html.lower()

    def test_html_has_root_div(self, html):
        assert 'id="root"' in html

    # Task 15.2 — chart components present in JS
    def test_html_has_line_chart(self, html):
        assert "LineChart" in html

    def test_html_has_scatter_chart(self, html):
        assert "ScatterChart" in html

    def test_html_has_green_red_coloring(self, html):
        """Req 11.3: successful=green (#4ade80), failed=red (#f87171)."""
        assert "#4ade80" in html   # green for success
        assert "#f87171" in html   # red for failure

    def test_html_applies_red_always(self, html):
        """Req 11.3: red applied to failed candidates even early in run."""
        assert "failed" in html and "#f87171" in html

    def test_html_has_best_score_trajectory(self, html):
        assert "runningBest" in html or "Running Best" in html

    # Task 15.3 — candidate detail view
    def test_html_has_candidate_detail(self, html):
        assert "CandidateDetail" in html or "detail-panel" in html

    def test_html_has_patch_display(self, html):
        assert "DiffView" in html or "diff-block" in html

    def test_html_has_metrics_display(self, html):
        assert "metrics" in html.lower()

    def test_html_has_stdout_stderr_display(self, html):
        assert "stdout" in html
        assert "stderr" in html

    # Task 15.4 — auto-refresh
    def test_html_has_refresh_param(self, html):
        assert "refresh" in html

    def test_html_has_auto_refresh_logic(self, html):
        assert "setInterval" in html or "REFRESH_S" in html

    def test_html_shows_last_updated(self, html):
        assert "lastUpdated" in html or "Updated" in html

    # Task 15.5 — PNG export
    def test_html_has_png_export(self, html):
        assert "html2canvas" in html or "Export PNG" in html or "exportPng" in html

    def test_html_has_export_button(self, html):
        assert "Export PNG" in html

    # Two-mode detection
    def test_html_detects_live_mode(self, html):
        assert "IS_LIVE" in html or "localhost" in html

    def test_html_detects_static_mode(self, html):
        assert "data.json" in html

    def test_html_has_github_pages_mention(self, html):
        assert "GitHub Pages" in html


# ---------------------------------------------------------------------------
# Task 15.6 — CLI dashboard parser
# ---------------------------------------------------------------------------

class TestDashboardParser:
    def test_parser_has_dashboard_command(self):
        from openevolve.cli import _build_optimizer_parser
        parser = _build_optimizer_parser()
        args = parser.parse_args(["dashboard", "--run-id", "abc", "--no-server"])
        assert args.command == "dashboard"
        assert args.run_id == "abc"
        assert args.no_server is True

    def test_parser_dashboard_port_default(self):
        from openevolve.cli import _build_optimizer_parser
        parser = _build_optimizer_parser()
        args = parser.parse_args(["dashboard", "--no-server"])
        assert args.port == 8080

    def test_parser_dashboard_docs_dir_default(self):
        from openevolve.cli import _build_optimizer_parser
        parser = _build_optimizer_parser()
        args = parser.parse_args(["dashboard", "--no-server"])
        assert args.docs_dir == "docs"


# ---------------------------------------------------------------------------
# Task 15.7 — candidate data structure
# ---------------------------------------------------------------------------

class TestCandidateDataStructure:
    def test_candidates_have_required_fields(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        for c in data["candidates"]:
            assert "id" in c
            assert "generation" in c
            assert "failed" in c
            assert "score" in c or c.get("failed")

    def test_failed_candidates_have_failure_phase(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        failed = [c for c in data["candidates"] if c.get("failed")]
        assert len(failed) >= 1
        for c in failed:
            assert c.get("failure_phase") is not None

    def test_successful_candidates_have_patch(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = _create_test_run(db_path)
        docs_dir = str(tmp_path / "docs")
        _opt_cmd_dashboard(_make_ns(run_id=run_id, db=db_path, docs_dir=docs_dir))
        with open(tmp_path / "docs" / "data.json", encoding="utf-8") as f:
            data = json.load(f)
        passed = [c for c in data["candidates"] if not c.get("failed") and c.get("generation", 0) > 0]
        assert len(passed) >= 1
        for c in passed:
            assert "patch_content" in c
