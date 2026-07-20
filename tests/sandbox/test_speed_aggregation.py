"""Tests for K-run speed aggregation and distribution parsing (design §C1).

These exercise the host-side reference aggregation (which mirrors what the
sandbox entrypoint computes inside the container) plus the runner's parsing of
the distribution fields from score.json — without requiring Docker.
"""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from sandbox.runner import (
    _normalize_distribution_fields,
    aggregate_speed_samples,
    run_in_sandbox,
)


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    program = tmp_path / "program.py"
    test_file = tmp_path / "test_program.py"
    program.write_text("value = 1\n", encoding="utf-8")
    test_file.write_text("def test_value(): pass\n", encoding="utf-8")
    return program, test_file


def _results_dir(command: list[str]) -> Path:
    mount = next(str(arg) for arg in command if str(arg).endswith(":/results"))
    return Path(mount[: -len(":/results")])


# ── Aggregation math ─────────────────────────────────────────────────────────

def test_known_multi_run_median_mean_stddev():
    # 100, 200, 300 -> median 200, mean 200, sample stddev 100.
    agg = aggregate_speed_samples([100.0, 200.0, 300.0])
    assert agg["speed_ms_median"] == 200.0
    assert agg["speed_ms_mean"] == 200.0
    assert agg["speed_ms_stddev"] == 100.0
    assert agg["speed_ms_samples"] == [100.0, 200.0, 300.0]
    assert agg["runs"] == 3
    # speed_ms is the median for back-compat.
    assert agg["speed_ms"] == 200.0


def test_even_count_median_is_midpoint_average():
    # 10, 20, 30, 40 -> median 25, mean 25, stddev ~12.9099.
    agg = aggregate_speed_samples([10.0, 20.0, 30.0, 40.0])
    assert agg["speed_ms_median"] == 25.0
    assert agg["speed_ms_mean"] == 25.0
    assert agg["speed_ms_stddev"] == 12.9099
    assert agg["runs"] == 4


def test_repeats_one_reproduces_single_shot_behavior():
    # A single kept run: distribution collapses to that value with zero spread.
    agg = aggregate_speed_samples([42.0])
    assert agg["speed_ms"] == 42.0
    assert agg["speed_ms_median"] == 42.0
    assert agg["speed_ms_mean"] == 42.0
    assert agg["speed_ms_stddev"] == 0.0
    assert agg["speed_ms_samples"] == [42.0]
    assert agg["runs"] == 1


def test_no_samples_yields_none_distribution():
    agg = aggregate_speed_samples([])
    assert agg["speed_ms"] is None
    assert agg["speed_ms_median"] is None
    assert agg["speed_ms_mean"] is None
    assert agg["speed_ms_stddev"] is None
    assert agg["speed_ms_samples"] == []
    assert agg["runs"] == 0


def test_aggregation_ignores_non_numeric_and_none():
    agg = aggregate_speed_samples([100.0, None, "not-a-number", 300.0])
    assert agg["speed_ms_samples"] == [100.0, 300.0]
    assert agg["speed_ms_median"] == 200.0
    assert agg["runs"] == 2


# ── Distribution backfill for older / generic score.json ─────────────────────

def test_normalize_backfills_from_single_speed_ms():
    # Legacy score.json with only speed_ms -> distribution derived, speed_ms kept.
    score = {"speed_ms": 55.0, "correctness": 1.0}
    _normalize_distribution_fields(score)
    assert score["speed_ms"] == 55.0
    assert score["speed_ms_median"] == 55.0
    assert score["speed_ms_stddev"] == 0.0
    assert score["speed_ms_samples"] == [55.0]
    assert score["runs"] == 1


def test_normalize_preserves_container_distribution():
    score = {
        "speed_ms": 200.0,
        "speed_ms_median": 200.0,
        "speed_ms_mean": 210.0,
        "speed_ms_stddev": 15.0,
        "speed_ms_samples": [190.0, 200.0, 230.0],
        "runs": 3,
    }
    _normalize_distribution_fields(score)
    # Container-provided values are untouched (mean != median stays as given).
    assert score["speed_ms_mean"] == 210.0
    assert score["speed_ms_samples"] == [190.0, 200.0, 230.0]
    assert score["runs"] == 3


def test_normalize_handles_missing_speed_gracefully():
    score = {"correctness": 1.0}
    _normalize_distribution_fields(score)
    assert score["speed_ms_samples"] == []
    assert score["runs"] == 0
    assert score["speed_ms_median"] is None


# ── Runner wiring: repeats env + parsing distribution from score.json ─────────

def test_repeats_config_passed_as_env_and_distribution_parsed(tmp_path: Path):
    program, test_file = _inputs(tmp_path)

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            results_dir = _results_dir(command)
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "score.json").write_text(
                json.dumps(
                    {
                        "passed": 1,
                        "failed": 0,
                        "all_passed": True,
                        "speed_ms": 200.0,
                        "speed_ms_median": 200.0,
                        "speed_ms_mean": 200.0,
                        "speed_ms_stddev": 100.0,
                        "speed_ms_samples": [100.0, 200.0, 300.0],
                        "runs": 3,
                        "combined_score": 0.5,
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "out", "err")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=fake_run
    ) as run:
        result = run_in_sandbox(
            str(program),
            str(test_file),
            sandbox_cfg={"repeats": 5},
        )

    docker_run = next(
        call.args[0] for call in run.call_args_list if call.args[0][:2] == ["docker", "run"]
    )
    joined = " ".join(str(a) for a in docker_run)
    assert "LOOPBENCH_REPEATS=5" in joined

    # Distribution fields flow through to the returned result.
    assert result["speed_ms_median"] == 200.0
    assert result["speed_ms_stddev"] == 100.0
    assert result["speed_ms_samples"] == [100.0, 200.0, 300.0]
    assert result["runs"] == 3


def test_default_repeats_is_one(tmp_path: Path):
    program, test_file = _inputs(tmp_path)

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            results_dir = _results_dir(command)
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "score.json").write_text(
                json.dumps(
                    {
                        "passed": 1,
                        "failed": 0,
                        "all_passed": True,
                        "speed_ms": 42.0,
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0, "out", "err")
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=fake_run
    ) as run:
        result = run_in_sandbox(str(program), str(test_file))

    docker_run = next(
        call.args[0] for call in run.call_args_list if call.args[0][:2] == ["docker", "run"]
    )
    joined = " ".join(str(a) for a in docker_run)
    assert "LOOPBENCH_REPEATS=1" in joined
    # Legacy single-value score.json is normalized into a one-sample distribution.
    assert result["speed_ms"] == 42.0
    assert result["speed_ms_median"] == 42.0
    assert result["runs"] == 1
