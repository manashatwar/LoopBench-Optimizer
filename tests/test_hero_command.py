"""
Tests for the LoopBench hero command (loopbench run --target ... --metric ...).

These tests cover argument routing, auto-detection helpers, and artifact
wiring without requiring a live LLM (the full run is validated manually).
"""

import argparse
from pathlib import Path

import pytest

from loopbench import hero
from loopbench.cli import build_parser

REPO_ROOT = Path(__file__).parent.parent.resolve()
FIB_DIR = REPO_ROOT / "examples" / "fibonacci_optimizer"


# ── argument parsing / routing ────────────────────────────────────────────────
class TestArgParsing:
    def test_target_flag_parses(self):
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--target", "https://github.com/u/r", "--metric", "latency"]
        )
        assert args.target == "https://github.com/u/r"
        assert args.metric == "latency"

    def test_config_is_optional(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--target", "/some/path"])
        assert args.config is None
        assert args.target == "/some/path"

    def test_metric_defaults_to_combined_score(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--target", "/some/path"])
        assert args.metric == "combined_score"

    def test_target_file_and_test_command(self):
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--target", "/r", "--target-file", "src/main.py",
             "--test-command", "pytest -q"]
        )
        assert args.target_file == "src/main.py"
        assert args.test_command == "pytest -q"


# ── neither --config nor --target → error ─────────────────────────────────────
class TestBackwardCompat:
    def test_no_target_no_config_errors(self):
        from loopbench.cli import _cmd_run
        args = argparse.Namespace(
            target=None, config=None, output=None, iterations=None,
            target_score=None, log_level="INFO",
        )
        assert _cmd_run(args) == 1


# ── helpers ───────────────────────────────────────────────────────────────────
class TestHelpers:
    def test_is_url(self):
        assert hero._is_url("https://github.com/u/r")
        assert hero._is_url("git@github.com:u/r.git")
        assert not hero._is_url("/local/path")
        assert not hero._is_url("C:\\repo")

    def test_default_llm_cfg_shape(self):
        cfg = hero._default_llm_cfg()
        assert cfg["api_key"] == "${GEMINI_API_KEY}"
        assert cfg["models"] and cfg["models"][0]["name"]
        assert cfg["api_base"]  # provider-agnostic: driven by LLM_API_BASE env

    def test_autodetect_target_file_fibonacci(self):
        # The fibonacci example has initial_program.py
        found = hero._autodetect_target_file(FIB_DIR, "python")
        assert found is not None
        assert found.name == "initial_program.py"

    def test_autodetect_test_file_fibonacci(self):
        target = FIB_DIR / "initial_program.py"
        found = hero._autodetect_test_file(FIB_DIR, target)
        assert found is not None
        assert found.name == "test_fibonacci.py"


# ── artifact writers (no LLM needed) ──────────────────────────────────────────
class TestArtifactWriters:
    def test_write_test_log(self, tmp_path):
        result = {
            "run_id": "abc123",
            "status": "completed",
            "baseline_score": 0.30,
            "best_score": 0.90,
            "best_candidate": {
                "stdout": "LOOPBENCH_SPEED_MS=5.0\n13 passed",
                "stderr": "",
                "exit_code": 0,
                "failed": False,
                "failure_phase": None,
            },
            "baseline_candidate": {},
        }
        path = hero._write_test_log(tmp_path, result)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "abc123" in content
        assert "13 passed" in content
        assert "STDOUT" in content

    def test_write_test_log_falls_back_to_baseline(self, tmp_path):
        result = {
            "run_id": "r",
            "status": "completed",
            "baseline_score": 0.3,
            "best_score": 0.3,
            "best_candidate": {},  # no output
            "baseline_candidate": {
                "stdout": "baseline output here",
                "stderr": "",
                "exit_code": 0,
                "failed": False,
            },
        }
        path = hero._write_test_log(tmp_path, result)
        assert "baseline output here" in path.read_text(encoding="utf-8")
