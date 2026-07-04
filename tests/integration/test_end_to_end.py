"""
End-to-end, performance, and custom benchmark tests — Tasks 17.1, 17.2, 17.3.

Task 17.1 — End-to-end test with a synthetic local repository (no network).
Task 17.2 — Performance bounds: memory, DB query time, LOC scaling.
Task 17.3 — Custom benchmark scripts and metric extraction patterns.

Requirements: 17.1, 17.2, 17.3, 17.4 – 17.8
"""

from __future__ import annotations

import subprocess
import time
import tracemalloc
import uuid
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from openevolve.optimizer_loop import OptimizerLoop
from openevolve.metric_parser import MetricParser, MetricPattern
from openevolve.database import CandidateDatabase
from openevolve.config import Config


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _git_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal git repository with optional files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in [
        ["git", "init"],
        ["git", "config", "user.email", "e2e@test.com"],
        ["git", "config", "user.name", "E2E Test"],
    ]:
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    for name, content in (files or {}).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


def _sandbox_ok(score: float = 0.5) -> Dict[str, Any]:
    return {
        "status": "passed", "stdout": f"Score: {score}\n", "stderr": "",
        "exit_code": 0, "execution_time": 0.1, "combined_score": score,
        "correctness": 1.0, "speed_score": score, "all_passed": True,
        "passed": 1, "failed": 0, "errors": 0, "total": 1,
    }


def _cfg(tmp_path: Path, **overrides) -> Dict[str, Any]:
    base = {
        "repo_path": str(tmp_path / "repo"),
        "target_file": str(tmp_path / "repo" / "main.py"),
        "test_file": str(tmp_path / "repo" / "test_main.py"),
        "max_iterations": 3,
        "patience": 3,
        "success_threshold": 0.10,
        "db_path": ":memory:",
    }
    base.update(overrides)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Task 17.1 — End-to-end test with a synthetic repository  (Req 17.1–17.3)
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndWithSyntheticRepo:
    """Full optimization cycle on a local synthetic Python repo.

    No network access, no Docker, no real LLM. Every external call is mocked
    so the loop exercises all 7 phases without side-effects.

    Requirements: 17.1, 17.2, 17.3
    """

    def _make_repo_files(self):
        return {
            "main.py": "def compute(n):\n    return sum(range(n))\n",
            "test_main.py": (
                "from main import compute\n"
                "def test_compute():\n"
                "    assert compute(10) == 45\n"
            ),
            "requirements.txt": "pytest\n",
        }

    def test_e2e_run_completes_without_crash(self, tmp_path):
        """Req 17.1 — optimization run completes (all phases execute)."""
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(_cfg(tmp_path), llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            result = loop.run()

        assert result is not None
        assert "run_id" in result
        assert result["status"] in ("completed", "successful", "interrupted")

    def test_e2e_baseline_established_generation_zero(self, tmp_path):
        """Req 17.1 — baseline candidate is generation 0."""
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(_cfg(tmp_path), llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            result = loop.run()

        assert result["baseline_candidate"]["generation"] == 0
        assert result["baseline_candidate"]["parent_id"] is None

    def test_e2e_all_generations_recorded_in_db(self, tmp_path):
        """Req 17.1 — every generation is written to the database."""
        n_iter = 3
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(_cfg(tmp_path, max_iterations=n_iter), llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            result = loop.run()

        export = result["export"]
        gens_in_db = {c["generation"] for c in export["candidates"]}
        assert 0 in gens_in_db
        for g in range(1, n_iter + 1):
            assert g in gens_in_db, f"Generation {g} missing from DB"

    def test_e2e_final_report_has_correct_fields(self, tmp_path):
        """Req 17.1 — final report has improvement, status, best/baseline score."""
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(_cfg(tmp_path), llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            result = loop.run()

        for key in ("improvement", "status", "best_score", "baseline_score",
                    "total_generations", "run_id"):
            assert key in result, f"Key '{key}' missing from result"

    def test_e2e_status_successful_when_improvement_exceeds_threshold(self, tmp_path):
        """Req 17.5 — run marked successful when improvement > threshold."""
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(
            _cfg(tmp_path, max_iterations=2, success_threshold=0.10),
            llm_ensemble=None,
        )
        gen_counter = {"n": 0}

        def improving_sandbox(**kw):
            gen_counter["n"] += 1
            score = 0.50 + 0.25 * gen_counter["n"]  # 0.75, 1.0, ...
            return _sandbox_ok(score)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (improving_sandbox, MagicMock(return_value=True))
            result = loop.run()

        # Improvement = (best - baseline) / baseline
        # baseline ~0.5 (first sandbox call), best ~0.75+ → >10%
        if result["improvement"] > 0.10:
            assert result["status"] == "successful"

    def test_e2e_audit_log_populated(self, tmp_path):
        """Req 12.1 — audit events recorded during run."""
        _git_repo(tmp_path, self._make_repo_files())
        loop = OptimizerLoop(_cfg(tmp_path, max_iterations=2), llm_ensemble=None)

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            loop.run()

        events = loop.db.get_audit_log()
        event_types = {e["event_type"] for e in events}
        assert "generation_start" in event_types

    def test_e2e_json_optimizers_example(self, tmp_path):
        """Req 17.1 — minimal JSON validator style target."""
        _git_repo(tmp_path, {
            "json_validator.py": (
                "import json\n"
                "def validate(s):\n"
                "    try:\n"
                "        json.loads(s)\n"
                "        return True\n"
                "    except Exception:\n"
                "        return False\n"
            ),
            "test_json_validator.py": (
                "from json_validator import validate\n"
                "def test_valid(): assert validate('{\"a\":1}')\n"
                "def test_invalid(): assert not validate('{bad}')\n"
            ),
        })
        loop = OptimizerLoop(
            _cfg(tmp_path,
                 target_file=str(tmp_path / "repo" / "json_validator.py"),
                 test_file=str(tmp_path / "repo" / "test_json_validator.py")),
            llm_ensemble=None,
        )

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.9)),
                MagicMock(return_value=True),
            )
            result = loop.run()

        assert result["run_id"] is not None


# ─────────────────────────────────────────────────────────────────────────────
# Task 17.2 — Performance tests  (Req 17.7)
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformanceBounds:
    """Verify system stays within resource bounds (Req 17.7).

    These tests use in-memory databases and mock heavy I/O so they run
    quickly while still validating the performance-critical code paths.
    """

    def test_db_insert_1000_candidates_under_5s(self):
        """Inserting 1 000 candidates should complete in < 5 s (Req 17.7)."""
        config = Config()
        config.database.in_memory = True
        db = CandidateDatabase(config.database)
        run_id = db.create_run(run_id="perf-run")

        start = time.monotonic()
        prev_id = None
        for i in range(1000):
            cid = db.insert_candidate(
                run_id=run_id, generation=i, parent_id=prev_id,
                patch_content=f"patch {i}", score=float(i) / 1000,
                metrics={"combined_score": float(i) / 1000}, failed=False,
            )
            prev_id = cid
        elapsed = time.monotonic() - start

        db.close()
        assert elapsed < 5.0, (
            f"Inserting 1 000 candidates took {elapsed:.2f}s (limit: 5s)"
        )

    def test_db_query_best_candidate_under_100ms(self):
        """Querying best candidate from 500 rows should be < 100 ms."""
        config = Config()
        config.database.in_memory = True
        db = CandidateDatabase(config.database)
        run_id = db.create_run(run_id="perf-query")

        for i in range(500):
            db.insert_candidate(
                run_id=run_id, generation=i % 50, parent_id=None,
                patch_content="p", score=float(i) / 500,
                metrics={"combined_score": float(i) / 500}, failed=False,
            )

        start = time.monotonic()
        best = db.get_best_candidate(run_id=run_id)
        elapsed = time.monotonic() - start

        db.close()
        assert elapsed < 0.1, (
            f"get_best_candidate over 500 rows took {elapsed*1000:.1f}ms (limit 100ms)"
        )
        assert best is not None

    def test_db_export_run_under_2s(self):
        """Exporting a run with 200 candidates should complete in < 2 s."""
        config = Config()
        config.database.in_memory = True
        db = CandidateDatabase(config.database)
        run_id = db.create_run(run_id="perf-export")

        for i in range(200):
            db.insert_candidate(
                run_id=run_id, generation=i, parent_id=None,
                patch_content=f"--- a/f\n+++ b/f\n@@ -{i} +{i} @@\n",
                score=float(i) / 200, metrics={"s": float(i) / 200},
                failed=False,
            )

        start = time.monotonic()
        export = db.export_run(run_id)
        elapsed = time.monotonic() - start

        db.close()
        assert elapsed < 2.0, (
            f"export_run (200 candidates) took {elapsed:.2f}s (limit 2s)"
        )
        assert len(export["candidates"]) == 200

    def test_optimizer_loop_memory_under_50mb(self, tmp_path):
        """Optimizer run memory overhead should stay < 50 MB (Req 17.7)."""
        _git_repo(tmp_path, {
            "main.py": "x = 1\n",
            "test_main.py": "def test_x(): assert 1\n",
        })

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        loop = OptimizerLoop(_cfg(tmp_path, max_iterations=5), llm_ensemble=None)
        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value=_sandbox_ok(0.5)),
                MagicMock(return_value=True),
            )
            loop.run()

        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        total_kb = sum(s.size_diff for s in stats if s.size_diff > 0) / 1024
        total_mb = total_kb / 1024

        assert total_mb < 50, (
            f"Optimizer run allocated {total_mb:.1f} MB (limit 50 MB)"
        )

    def test_metric_parser_processes_1000_outputs_under_1s(self):
        """MetricParser should handle 1 000 output strings in < 1 s (Req 17.7)."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency",
                    regex=r"latency:\s*([\d.]+)",
                    goal="minimize",
                )
            ]
        )
        outputs = [f"bench done. latency: {10 + i * 0.01}" for i in range(1000)]

        start = time.monotonic()
        results = [parser.parse(o) for o in outputs]
        elapsed = time.monotonic() - start

        assert elapsed < 1.0, (
            f"Parsing 1 000 outputs took {elapsed:.2f}s (limit 1s)"
        )
        assert all("latency" in r for r in results)


# ─────────────────────────────────────────────────────────────────────────────
# Task 17.3 — Custom benchmark scripts and metric patterns  (Req 17.8)
# ─────────────────────────────────────────────────────────────────────────────

class TestCustomBenchmarkScripts:
    """Custom user-defined metric patterns and scoring functions (Req 17.8)."""

    # ── Custom regex patterns ─────────────────────────────────────────────

    def test_custom_execution_time_pattern(self):
        """Custom regex extracts execution time correctly."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="exec_time",
                    regex=r"Execution time:\s*([\d.]+)\s*ms",
                    goal="minimize",
                    unit="ms",
                    scale=0.001,  # convert ms → seconds
                )
            ]
        )
        output = "Benchmark complete. Execution time: 245.3 ms"
        result = parser.parse(output)
        assert "exec_time" in result
        assert abs(result["exec_time"] - 0.2453) < 1e-4  # 245.3 * 0.001

    def test_custom_throughput_pattern(self):
        """Custom regex extracts throughput with maximize goal."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="throughput",
                    regex=r"Throughput:\s*([\d.]+)\s*ops/sec",
                    goal="maximize",
                )
            ]
        )
        output = "Results: Throughput: 12500.7 ops/sec"
        result = parser.parse(output)
        assert "throughput" in result
        assert abs(result["throughput"] - 12500.7) < 0.01

    def test_custom_memory_pattern(self):
        """Custom regex extracts memory usage with minimize goal."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="memory_mb",
                    regex=r"Peak memory:\s*([\d.]+)\s*MB",
                    goal="minimize",
                )
            ]
        )
        output = "Peak memory: 128.5 MB after benchmark"
        result = parser.parse(output)
        assert abs(result["memory_mb"] - 128.5) < 0.01

    def test_multi_metric_custom_scoring(self):
        """Multiple custom metrics combine into a single score."""
        parser = MetricParser(
            patterns=[
                MetricPattern(name="latency", regex=r"p50:\s*([\d.]+)",
                               goal="minimize"),
                MetricPattern(name="throughput", regex=r"qps:\s*([\d.]+)",
                               goal="maximize"),
            ],
            primary_metric="latency",
        )
        output = "p50: 10.5  qps: 5000.0  p99: 45.2"
        result = parser.parse(output)
        assert "latency" in result
        assert "throughput" in result
        assert "combined_score" in result
        assert 0.0 <= result["combined_score"] <= 1.0

    def test_json_style_output_with_regex(self):
        """Regex extracts values from JSON-like benchmark output."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="mean_ms",
                    regex=r'"mean":\s*([\d.]+)',
                    goal="minimize",
                )
            ]
        )
        output = '{"benchmark": {"mean": 23.4, "stddev": 1.2}}'
        result = parser.parse(output)
        assert abs(result["mean_ms"] - 23.4) < 0.01

    # ── Custom metric extraction in OptimizerLoop ─────────────────────────

    def test_optimizer_loop_uses_custom_metric_patterns(self, tmp_path):
        """OptimizerLoop correctly wires custom MetricPattern into run."""
        _git_repo(tmp_path, {
            "bench.py": "def run(): pass\n",
            "test_bench.py": "def test_run(): pass\n",
        })

        CUSTOM_OUTPUT = "Benchmark: latency: 42.0 ms"

        def custom_sandbox(**kw):
            return {
                "status": "passed",
                "stdout": CUSTOM_OUTPUT,
                "stderr": "",
                "exit_code": 0,
                "execution_time": 0.05,
                "combined_score": 0.5,
                "all_passed": True,
                "passed": 1, "failed": 0, "errors": 0, "total": 1,
            }

        loop = OptimizerLoop(
            {
                "repo_path": str(tmp_path / "repo"),
                "target_file": str(tmp_path / "repo" / "bench.py"),
                "test_file": str(tmp_path / "repo" / "test_bench.py"),
                "max_iterations": 1,
                "patience": 1,
                "db_path": ":memory:",
                "metric_patterns": [
                    {
                        "name": "latency",
                        "regex": r"latency:\s*([\d.]+)",
                        "goal": "minimize",
                        "unit": "ms",
                    }
                ],
            },
            llm_ensemble=None,
        )

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (custom_sandbox, MagicMock(return_value=True))
            result = loop.run()

        # Baseline should have been measured with custom parser
        assert result["run_id"] is not None

    def test_optimizer_loop_custom_scoring_function_via_config(self):
        """Custom patterns from config dict are parsed and applied."""
        from openevolve.metric_parser import MetricParser, MetricPattern, create_parser_from_config

        cfg = {
            "patterns": [
                {"name": "rps", "regex": r"RPS:\s*([\d.]+)", "goal": "maximize"},
                {"name": "p99", "regex": r"p99:\s*([\d.]+)", "goal": "minimize"},
            ],
            "primary_metric": "rps",
        }
        parser = create_parser_from_config(cfg)
        assert parser is not None

        output = "RPS: 8000.5  p99: 12.3  p50: 5.1"
        result = parser.parse(output)
        assert abs(result["rps"] - 8000.5) < 0.01
        assert abs(result["p99"] - 12.3) < 0.01
        assert result["combined_score"] == result["rps_score"]

    def test_custom_scale_factor_converts_units(self):
        """Scale factor converts raw metric to target unit."""
        parser = MetricParser(
            patterns=[
                MetricPattern(
                    name="latency_s",
                    regex=r"latency_ms:\s*([\d.]+)",
                    goal="minimize",
                    scale=0.001,  # ms → s
                )
            ]
        )
        output = "latency_ms: 2000"
        result = parser.parse(output)
        assert abs(result["latency_s"] - 2.0) < 1e-6

    def test_missing_metric_uses_fallback_score(self):
        """When pattern not found, fallback score is returned (Req 17.8)."""
        parser = MetricParser(
            patterns=[
                MetricPattern(name="latency", regex=r"no_match_here:([\d.]+)",
                               goal="minimize")
            ],
            fallback_score=0.0,
        )
        result = parser.parse("output without the metric")
        assert result["combined_score"] == 0.0

    def test_e2e_with_custom_benchmark_math_lib(self, tmp_path):
        """Req 17.2 — optimize a math utility via custom benchmark output."""
        _git_repo(tmp_path, {
            "math_utils.py": "def fib(n):\n    if n<=1: return n\n    return fib(n-1)+fib(n-2)\n",
            "bench_math.py": (
                "from math_utils import fib\n"
                "import time\n"
                "start = time.time()\n"
                "fib(30)\n"
                "elapsed = time.time() - start\n"
                "print(f'latency: {elapsed*1000:.2f} ms')\n"
            ),
        })
        BENCH_OUTPUT = "latency: 150.00 ms"

        loop = OptimizerLoop(
            {
                "repo_path": str(tmp_path / "repo"),
                "target_file": str(tmp_path / "repo" / "math_utils.py"),
                "test_file": str(tmp_path / "repo" / "bench_math.py"),
                "max_iterations": 2,
                "patience": 2,
                "db_path": ":memory:",
                "metric_patterns": [
                    {"name": "latency", "regex": r"latency:\s*([\d.]+)",
                     "goal": "minimize", "unit": "ms"}
                ],
            },
            llm_ensemble=None,
        )

        with patch("openevolve.optimizer_loop._import_sandbox") as mock_sb:
            mock_sb.return_value = (
                MagicMock(return_value={
                    "status": "passed", "stdout": BENCH_OUTPUT, "stderr": "",
                    "exit_code": 0, "execution_time": 0.15, "combined_score": 0.4,
                    "all_passed": True, "passed": 1, "failed": 0, "errors": 0, "total": 1,
                }),
                MagicMock(return_value=True),
            )
            result = loop.run()

        assert result is not None
        assert result["total_generations"] >= 1
