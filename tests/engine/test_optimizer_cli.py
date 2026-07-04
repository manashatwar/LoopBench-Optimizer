"""
Integration tests for the optimizer CLI — Task 13.7.

Tests cover:
  - init command (Task 13.1)
  - run command argument parsing and validation (Task 13.2)
  - progress display (Task 13.3)
  - atomic output on completion (Task 13.4)
  - resume command (Task 13.5)
  - export command (Task 13.6)

Requirements: 9.1 – 9.8, 15.6, 15.7
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
import yaml

from openevolve.cli import (
    _opt_cmd_export as _cmd_export,
    _opt_cmd_init as _cmd_init,
    _opt_cmd_resume as _cmd_resume,
    _opt_cmd_run as _cmd_run,
    _fmt_elapsed as _format_elapsed,
    _print_run_summary as _print_summary,
    optimizer_write_partial_results as _write_partial_results,
    optimizer_write_results_atomic as _write_results_atomic,
    _build_optimizer_parser as build_parser,
    optimizer_print_progress as print_progress,
)
from openevolve.config_validator import REQUIRED_SECTIONS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "output": None,
        "config": None,
        "run_id": None,
        "db": None,
        "format": "json",
        "max_iterations": None,
        "metric": None,
        "log_level": "INFO",
        "name": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_result(
    score: float = 0.65,
    baseline: float = 0.50,
    status: str = "successful",
    run_id: str = "test-run",
) -> Dict[str, Any]:
    improvement = (score - baseline) / max(abs(baseline), 1e-9)
    return {
        "run_id": run_id,
        "status": status,
        "improvement": improvement,
        "improvement_pct": round(improvement * 100, 2),
        "best_score": score,
        "baseline_score": baseline,
        "total_generations": 5,
        "confidence_warning": status != "successful",
        "best_candidate": {
            "id": "best-1", "generation": 5, "score": score,
            "patch_content": "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-old\n+new\n",
            "metrics": {"combined_score": score}, "failed": False,
        },
        "baseline_candidate": {
            "id": "baseline-0", "generation": 0, "score": baseline,
            "patch_content": "", "metrics": {"combined_score": baseline}, "failed": False,
        },
        "export": {
            "run": {"id": run_id, "status": status, "target_repo": "/repo"},
            "candidates": [],
            "audit_log": [],
        },
    }


# ---------------------------------------------------------------------------
# Task 13.1 — init command
# ---------------------------------------------------------------------------

class TestInitCommand:
    def test_creates_file(self, tmp_path):
        out = str(tmp_path / "optimizer.yaml")
        ns = _ns(output=out)
        rc = _cmd_init(ns)
        assert rc == 0
        assert Path(out).exists()

    def test_created_file_is_valid_yaml(self, tmp_path):
        out = str(tmp_path / "optimizer.yaml")
        _cmd_init(_ns(output=out))
        with open(out, encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
        assert isinstance(parsed, dict)

    def test_file_has_all_6_sections(self, tmp_path):
        out = str(tmp_path / "optimizer.yaml")
        _cmd_init(_ns(output=out))
        with open(out, encoding="utf-8") as f:
            content = f.read()
        # Substitute env-var placeholders before parsing
        content = content.replace("${OPENAI_API_KEY}", "stub").replace("${GITHUB_TOKEN}", "stub")
        parsed = yaml.safe_load(content)
        for section in REQUIRED_SECTIONS:
            assert section in parsed, f"Template missing section '{section}'"

    def test_default_output_filename(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        ns = _ns(output="optimizer.yaml")
        rc = _cmd_init(ns)
        assert rc == 0
        assert (tmp_path / "optimizer.yaml").exists()

    def test_parser_init_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["init", "--output", "test.yaml"])
        assert args.command == "init"
        assert args.output == "test.yaml"


# ---------------------------------------------------------------------------
# Task 13.2 — run command argument parsing and validation
# ---------------------------------------------------------------------------

class TestRunCommandParsing:
    def test_parser_run_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([
            "run", "--config", "optimizer.yaml",
            "--max-iterations", "50",
            "--output", "results/",
        ])
        assert args.command == "run"
        assert args.config == "optimizer.yaml"
        assert args.max_iterations == 50
        assert args.output == "results/"

    def test_run_without_config_accepted(self):
        """Config is optional (repo_path can come from CLI override)."""
        parser = build_parser()
        args = parser.parse_args(["run"])
        assert args.config is None

    def test_run_validates_missing_repo_path(self, tmp_path, capsys):
        """Missing repo_path causes validation error (Req 9.2)."""
        ns = _ns(config=None, output=str(tmp_path))
        rc = _cmd_run(ns)
        assert rc != 0
        captured = capsys.readouterr()
        assert "repo_path" in captured.err or "repo_path" in captured.out

    def test_run_validates_missing_target_file(self, tmp_path, capsys):
        cfg_path = tmp_path / "optimizer.yaml"
        cfg_path.write_text(yaml.dump({"repo_path": "/fake"}), encoding="utf-8")
        ns = _ns(config=str(cfg_path), output=str(tmp_path))
        rc = _cmd_run(ns)
        assert rc != 0

    def test_cli_max_iterations_overrides_config(self, tmp_path):
        """CLI --max-iterations overrides YAML search.max_iterations (Req 15.6)."""
        from openevolve.cli import _load_merge_config as _load_and_merge_config, _build_opt_config as _build_optimizer_config

        cfg = {
            "repo_path": "/fake", "target_file": "/fake/p.py",
            "test_file": "/fake/t.py",
            "search": {"max_iterations": 100, "patience": 5, "strategy": "greedy"},
            "metrics": {"success_threshold": 0.1, "patterns": []},
            "database": {"path": ":memory:"},
        }
        cfg_path = tmp_path / "optimizer.yaml"
        cfg_path.write_text(yaml.dump(cfg), encoding="utf-8")

        # Load then apply CLI override (as _cmd_run does internally)
        raw = _load_and_merge_config(str(cfg_path), {})
        raw.setdefault("search", {})["max_iterations"] = 25  # CLI wins
        opt_cfg = _build_optimizer_config(raw)

        assert opt_cfg["max_iterations"] == 25, (
            "CLI --max-iterations should override config value"
        )


# ---------------------------------------------------------------------------
# Task 13.3 — progress display
# ---------------------------------------------------------------------------

class TestProgressDisplay:
    def test_print_progress_contains_run_id(self, capsys):
        print_progress(
            run_id="abc123",
            generation=5,
            max_iterations=50,
            best_score=0.8,
            baseline_score=0.5,
            current_score=0.78,
            elapsed=120.0,
        )
        out = capsys.readouterr().out
        assert "abc123" in out

    def test_print_progress_contains_generation(self, capsys):
        print_progress(
            run_id="r", generation=10, max_iterations=50,
            best_score=0.5, baseline_score=0.5, current_score=None, elapsed=60.0,
        )
        out = capsys.readouterr().out
        assert "10/50" in out

    def test_print_progress_shows_improvement_pct(self, capsys):
        print_progress(
            run_id="r", generation=3, max_iterations=10,
            best_score=0.75, baseline_score=0.50, current_score=0.75, elapsed=30.0,
        )
        out = capsys.readouterr().out
        assert "50.0%" in out or "+50" in out

    def test_print_progress_shows_elapsed(self, capsys):
        print_progress(
            run_id="r", generation=1, max_iterations=5,
            best_score=0.5, baseline_score=0.5, current_score=None, elapsed=3661.0,
        )
        out = capsys.readouterr().out
        assert "1h" in out  # 3661s = 1h 1m 1s

    def test_print_progress_shows_recent_candidates(self, capsys):
        candidates = [
            {"generation": 1, "score": 0.6, "failed": False},
            {"generation": 2, "score": 0.4, "failed": True, "failure_phase": "apply"},
        ]
        print_progress(
            run_id="r", generation=2, max_iterations=10,
            best_score=0.6, baseline_score=0.5, current_score=0.4, elapsed=60.0,
            recent_candidates=candidates,
        )
        out = capsys.readouterr().out
        assert "✓" in out  # successful candidate
        assert "✗" in out  # failed candidate

    def test_format_elapsed_hours(self):
        assert "1h" in _format_elapsed(3700)

    def test_format_elapsed_minutes(self):
        result = _format_elapsed(190)
        assert "3m" in result

    def test_format_elapsed_seconds(self):
        result = _format_elapsed(45)
        assert "45s" in result


# ---------------------------------------------------------------------------
# Task 13.4 — atomic output on completion
# ---------------------------------------------------------------------------

class TestAtomicOutput:
    def test_write_results_atomic_creates_file(self, tmp_path, capsys):
        result = _make_result()
        success = _write_results_atomic(result, tmp_path)
        assert success
        assert (tmp_path / "results.json").exists()

    def test_write_results_atomic_file_is_valid_json(self, tmp_path, capsys):
        result = _make_result()
        _write_results_atomic(result, tmp_path)
        with open(tmp_path / "results.json", encoding="utf-8") as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_write_results_atomic_prints_summary(self, tmp_path, capsys):
        result = _make_result(status="successful")
        _write_results_atomic(result, tmp_path)
        out = capsys.readouterr().out
        assert "SUCCESSFUL" in out or "successful" in out.lower()

    def test_write_partial_results_writes_file(self, tmp_path):
        result = _make_result()
        _write_partial_results(result, tmp_path)
        assert (tmp_path / "partial_results.json").exists()

    def test_write_partial_results_skips_when_no_baseline(self, tmp_path):
        result = {"baseline_candidate": None, "export": {}}
        _write_partial_results(result, tmp_path)
        assert not (tmp_path / "partial_results.json").exists()

    def test_print_summary_successful(self, capsys):
        _print_summary(_make_result(status="successful"))
        out = capsys.readouterr().out
        assert "SUCCESSFUL" in out
        assert "🎉" in out

    def test_print_summary_completed(self, capsys):
        _print_summary(_make_result(status="completed"))
        out = capsys.readouterr().out
        assert "COMPLETED" in out

    def test_print_summary_confidence_warning(self, capsys):
        result = _make_result(status="completed")
        result["confidence_warning"] = True
        _print_summary(result)
        out = capsys.readouterr().out
        assert "threshold" in out.lower() or "⚠️" in out


# ---------------------------------------------------------------------------
# Task 13.5 — resume command
# ---------------------------------------------------------------------------

class TestResumeCommand:
    def test_resume_nonexistent_run(self, tmp_path, capsys):
        db_path = str(tmp_path / "optimizer.db")
        # Create empty db
        from openevolve.database import CandidateDatabase
        db = CandidateDatabase(db_path)
        db.close()

        ns = _ns(run_id="nonexistent", db=db_path, output=str(tmp_path))
        rc = _cmd_resume(ns)
        assert rc != 0
        captured = capsys.readouterr()
        assert "not found" in captured.err or "not found" in captured.out

    def test_resume_completed_run_is_noop(self, tmp_path, capsys):
        from openevolve.database import CandidateDatabase
        db_path = str(tmp_path / "optimizer.db")
        db = CandidateDatabase(db_path)
        run_id = db.create_run(run_id="done-run", status="completed")
        db.complete_run(run_id, status="completed")
        db.close()

        ns = _ns(run_id=run_id, db=db_path, output=str(tmp_path))
        rc = _cmd_resume(ns)
        assert rc == 0

    def test_parser_resume_subcommand(self):
        parser = build_parser()
        args = parser.parse_args(["resume", "--run-id", "abc"])
        assert args.command == "resume"
        assert args.run_id == "abc"


# ---------------------------------------------------------------------------
# Task 13.6 — export command
# ---------------------------------------------------------------------------

class TestExportCommand:
    def _create_run_in_db(self, db_path: str, run_id: str) -> None:
        from openevolve.database import CandidateDatabase
        db = CandidateDatabase(db_path)
        db.create_run(run_id=run_id, target_repo="/test/repo")
        db.insert_candidate(
            run_id=run_id, generation=0, parent_id=None,
            patch_content="", score=0.5, failed=False,
        )
        db.complete_run(run_id, status="completed", final_improvement=0.15)
        db.close()

    def test_export_json(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = "export-run-1"
        self._create_run_in_db(db_path, run_id)
        out_path = str(tmp_path / f"{run_id}.json")
        ns = _ns(run_id=run_id, db=db_path, format="json", output=out_path)
        rc = _cmd_export(ns)
        assert rc == 0
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        assert "run" in data

    def test_export_markdown(self, tmp_path):
        db_path = str(tmp_path / "optimizer.db")
        run_id = "export-run-2"
        self._create_run_in_db(db_path, run_id)
        out_path = str(tmp_path / f"{run_id}.md")
        ns = _ns(run_id=run_id, db=db_path, format="markdown", output=out_path)
        rc = _cmd_export(ns)
        assert rc == 0
        text = Path(out_path).read_text(encoding="utf-8")
        assert "# Optimization Run" in text

    def test_export_nonexistent_run(self, tmp_path, capsys):
        db_path = str(tmp_path / "optimizer.db")
        from openevolve.database import CandidateDatabase
        db = CandidateDatabase(db_path)
        db.close()
        ns = _ns(run_id="no-such-run", db=db_path, format="json",
                 output=str(tmp_path / "out.json"))
        rc = _cmd_export(ns)
        assert rc != 0

    def test_export_unknown_format(self, tmp_path, capsys):
        db_path = str(tmp_path / "optimizer.db")
        from openevolve.database import CandidateDatabase
        db = CandidateDatabase(db_path)
        run_id = db.create_run(run_id="r")
        db.close()
        ns = _ns(run_id=run_id, db=db_path, format="xml", output=str(tmp_path / "out.xml"))
        rc = _cmd_export(ns)
        assert rc != 0

    def test_parser_export_subcommand(self):
        parser = build_parser()
        args = parser.parse_args([
            "export", "--run-id", "abc123",
            "--format", "json", "--output", "out.json",
        ])
        assert args.command == "export"
        assert args.run_id == "abc123"
        assert args.format == "json"
        assert args.output == "out.json"


# ---------------------------------------------------------------------------
# Parser smoke tests
# ---------------------------------------------------------------------------

class TestParser:
    def test_init_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["init"])
        assert args.output == "optimizer.yaml"

    def test_run_long_form(self):
        parser = build_parser()
        args = parser.parse_args([
            "run",
            "--config", "c.yaml",
            "--max-iterations", "100",
            "--metric", "latency",
            "--output", "out/",
        ])
        assert args.config == "c.yaml"
        assert args.max_iterations == 100
        assert args.metric == "latency"
        assert args.output == "out/"

    def test_resume_requires_run_id(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["resume"])  # --run-id missing

    def test_export_requires_run_id(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["export"])  # --run-id missing
