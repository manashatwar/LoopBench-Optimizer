"""Tests for the baseline profiler (design §C2, requirement R4).

The pstats parser and formatter are pure and run without Docker. The runner
profile-path tests mock the profiling container run so no Docker is required.
"""

import cProfile
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from openevolve.profiler import (
    DEFAULT_MAX_HOTSPOTS,
    format_hotspots,
    parse_command_output,
    parse_pstats,
)
from sandbox.runner import run_in_sandbox


# ── Fixtures / helpers ───────────────────────────────────────────────────────

def _synthetic_stats() -> dict:
    """A synthetic pstats mapping: (cc, nc, tottime, cumtime, callers)."""
    return {
        ("slow.py", 10, "slow"): (1, 1, 0.50, 0.90, {}),
        ("mid.py", 20, "mid"): (2, 2, 0.30, 0.35, {}),
        ("fast.py", 30, "fast"): (5, 5, 0.10, 0.12, {}),
        ("tiny.py", 40, "tiny"): (9, 9, 0.01, 0.01, {}),
    }


def _busy_work() -> int:
    total = 0
    for value in range(50000):
        total += value % 7
    return total


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    program = tmp_path / "program.py"
    test_file = tmp_path / "test_program.py"
    program.write_text("value = 1\n", encoding="utf-8")
    test_file.write_text("def test_value(): pass\n", encoding="utf-8")
    return program, test_file


def _results_dir(command: list[str]) -> Path:
    mount = next(str(arg) for arg in command if str(arg).endswith(":/results"))
    return Path(mount[: -len(":/results")])


def _score_run(command, **kwargs):
    """Fake a scoring docker run that writes a minimal score.json."""
    if command[:2] == ["docker", "run"]:
        results_dir = _results_dir(command)
        results_dir.mkdir(parents=True, exist_ok=True)
        (results_dir / "score.json").write_text(
            json.dumps(
                {"passed": 1, "failed": 0, "all_passed": True, "speed_ms": 42.0}
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "out", "err")
    return subprocess.CompletedProcess(command, 0, "", "")


# ── Pure parser: ordering + truncation ───────────────────────────────────────

def test_parser_truncates_to_max_hotspots():
    hotspots = parse_pstats(_synthetic_stats(), max_hotspots=2)
    assert len(hotspots) == 2


def test_parser_orders_by_self_time_descending():
    hotspots = parse_pstats(_synthetic_stats(), max_hotspots=DEFAULT_MAX_HOTSPOTS)
    tottimes = [h["tottime"] for h in hotspots]
    assert tottimes == sorted(tottimes, reverse=True)
    # Highest self-time function comes first.
    assert hotspots[0]["function"] == "slow.py:10(slow)"
    assert hotspots[0]["tottime"] == 0.5


def test_parser_never_exceeds_max_hotspots_on_real_profile():
    profile = cProfile.Profile()
    profile.enable()
    _busy_work()
    profile.disable()

    hotspots = parse_pstats(profile, max_hotspots=3)
    assert len(hotspots) <= 3
    tottimes = [h["tottime"] for h in hotspots]
    assert tottimes == sorted(tottimes, reverse=True)
    # Each record carries the compact fields we embed in the prompt.
    for hotspot in hotspots:
        assert "function" in hotspot
        assert "tottime" in hotspot


# ── Pure parser: graceful failure on empty / garbage input ───────────────────

def test_parser_empty_dict_yields_empty_list():
    assert parse_pstats({}) == []


def test_parser_none_yields_empty_list():
    assert parse_pstats(None) == []


def test_parser_garbage_string_yields_empty_list():
    # A non-path string makes pstats.Stats raise internally -> empty list.
    assert parse_pstats("this is not a profile dump") == []


def test_parser_garbage_bytes_yields_empty_list():
    assert parse_pstats(b"\x00\x01not-a-marshal-dump") == []


def test_command_output_parser_handles_garbage_gracefully():
    assert parse_command_output("no numbers here\njust words") == []
    assert parse_command_output("") == []
    assert parse_command_output(None) == []


def test_command_output_parser_orders_and_truncates():
    text = "\n".join(
        [
            "profile report",
            "0.10  mod::b_inner",
            "0.50  mod::a_hot",
            "0.30  mod::c_mid",
            "garbage line with no leading number",
        ]
    )
    hotspots = parse_command_output(text, max_hotspots=2)
    assert len(hotspots) == 2
    assert hotspots[0]["function"] == "mod::a_hot"
    assert hotspots[0]["tottime"] == 0.5
    assert hotspots[1]["function"] == "mod::c_mid"


# ── Formatter: compact + deterministic ───────────────────────────────────────

def test_format_hotspots_is_deterministic():
    hotspots = [
        {"function": "slow.py:10(slow)", "tottime": 0.5, "cumtime": 0.9, "ncalls": 1},
        {"function": "mid.py:20(mid)", "tottime": 0.3, "cumtime": 0.35, "ncalls": 2},
    ]
    expected = (
        "Baseline profile — top hotspots by self-time:\n"
        "  1. slow.py:10(slow)  self=0.500000s  cum=0.900000s  calls=1\n"
        "  2. mid.py:20(mid)  self=0.300000s  cum=0.350000s  calls=2"
    )
    assert format_hotspots(hotspots) == expected
    # Stable across repeated calls.
    assert format_hotspots(hotspots) == format_hotspots(hotspots)


def test_format_hotspots_empty_is_empty_string():
    assert format_hotspots([]) == ""
    assert format_hotspots(None) == ""


# ── Runner: profiling is OFF by default (exactly as today) ───────────────────

def test_profile_off_by_default_no_profiling(tmp_path: Path):
    program, test_file = _inputs(tmp_path)

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=_score_run
    ), patch("sandbox.runner._collect_hotspots") as collect:
        result = run_in_sandbox(str(program), str(test_file))

    # No profiling run happened and no hotspots key is added.
    collect.assert_not_called()
    assert "hotspots" not in result


def test_profile_falsy_explicit_no_profiling(tmp_path: Path):
    program, test_file = _inputs(tmp_path)

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=_score_run
    ), patch("sandbox.runner._collect_hotspots") as collect:
        result = run_in_sandbox(
            str(program), str(test_file), sandbox_cfg={"profile": False}
        )

    collect.assert_not_called()
    assert "hotspots" not in result


# ── Runner: profiling ON flows hotspots into the result ──────────────────────

def test_profile_on_attaches_hotspots(tmp_path: Path):
    program, test_file = _inputs(tmp_path)
    controlled = [
        {"function": "slow.py:10(slow)", "tottime": 0.5, "cumtime": 0.9, "ncalls": 1},
    ]

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=_score_run
    ), patch(
        "sandbox.runner._collect_hotspots", return_value=controlled
    ) as collect:
        result = run_in_sandbox(
            str(program), str(test_file), sandbox_cfg={"profile": True}
        )

    collect.assert_called_once()
    assert result["hotspots"] == controlled
    # Scoring result is preserved unchanged alongside the hotspots.
    assert result["all_passed"] is True
    assert result["speed_ms"] == 42.0


def test_profile_on_graceful_when_profiler_unavailable(tmp_path: Path):
    """If the profiling container can't run, hotspots is an empty list (R4.4)."""
    program, test_file = _inputs(tmp_path)

    def run_with_missing_profile_docker(command, **kwargs):
        # Score run succeeds; the profile run hits a missing Docker binary.
        joined = " ".join(str(a) for a in command)
        if "cProfile" in joined:
            raise FileNotFoundError("docker not found")
        return _score_run(command, **kwargs)

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=run_with_missing_profile_docker
    ):
        result = run_in_sandbox(
            str(program), str(test_file), sandbox_cfg={"profile": True}
        )

    assert result["hotspots"] == []
    assert result["all_passed"] is True


def test_collect_hotspots_uses_profile_command_override(tmp_path: Path):
    """A configured profile_command drives the generic (non-Python) path."""
    program, test_file = _inputs(tmp_path)
    captured = {}

    def fake_run(command, **kwargs):
        joined = " ".join(str(a) for a in command)
        if "my-profiler" in joined:
            captured["profile_cmd"] = joined
            return subprocess.CompletedProcess(
                command, 0, "0.42  native::hot_loop\n0.10  native::cold\n", ""
            )
        return _score_run(command, **kwargs)

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=fake_run
    ):
        result = run_in_sandbox(
            str(program),
            str(test_file),
            sandbox_cfg={"profile": True, "profile_command": "my-profiler run"},
        )

    assert "my-profiler run" in captured["profile_cmd"]
    assert result["hotspots"][0]["function"] == "native::hot_loop"
    assert result["hotspots"][0]["tottime"] == 0.42
