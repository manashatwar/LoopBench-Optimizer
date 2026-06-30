"""Focused tests for Docker sandbox timeout, cleanup, and image caching."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from sandbox.runner import build_sandbox_image, run_in_sandbox


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    program = tmp_path / "program.py"
    test_file = tmp_path / "test_program.py"
    program.write_text("value = 1\n", encoding="utf-8")
    test_file.write_text("def test_value(): pass\n", encoding="utf-8")
    return program, test_file


def _results_dir(command: list[str]) -> Path:
    mount = next(str(arg) for arg in command if str(arg).endswith(":/results"))
    return Path(mount[: -len(":/results")])


def test_timeout_uses_config_and_force_cleans_container(tmp_path: Path) -> None:
    program, test_file = _inputs(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(
                command,
                kwargs["timeout"],
                output="partial stdout",
                stderr="partial stderr",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=fake_run
    ):
        result = run_in_sandbox(
            str(program),
            str(test_file),
            sandbox_cfg={"timeout": 7},
        )

    run_call = next(call for call in calls if call[0][:2] == ["docker", "run"])
    stop_call = next(call for call in calls if call[0][:2] == ["docker", "stop"])
    cleanup_call = next(call for call in calls if call[0][:3] == ["docker", "rm", "-f"])
    assert run_call[1]["timeout"] == 7
    assert stop_call
    assert cleanup_call
    assert result["status"] == "timeout"
    assert result["timeout"] is True
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"


@pytest.mark.parametrize("return_code,all_passed", [(0, True), (1, False)])
def test_container_cleanup_and_logs_for_every_exit(
    tmp_path: Path,
    return_code: int,
    all_passed: bool,
) -> None:
    program, test_file = _inputs(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["docker", "run"]:
            assert kwargs["timeout"] == 120
            results_dir = _results_dir(command)
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "score.json").write_text(
                json.dumps(
                    {
                        "passed": int(all_passed),
                        "failed": int(not all_passed),
                        "all_passed": all_passed,
                        "combined_score": float(all_passed),
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command,
                return_code,
                "captured stdout",
                "captured stderr",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
        "sandbox.runner.subprocess.run", side_effect=fake_run
    ):
        result = run_in_sandbox(str(program), str(test_file))

    docker_run = next(command for command in calls if command[:2] == ["docker", "run"])
    assert "--rm" in docker_run
    assert any(str(arg).endswith(":/workspace:ro") for arg in docker_run)
    assert any(command[:3] == ["docker", "rm", "-f"] for command in calls)
    assert result["exit_code"] == return_code
    assert result["stdout"] == "captured stdout"
    assert result["stderr"] == "captured stderr"
    assert result["execution_time"] >= 0
    assert result["status"] == ("passed" if all_passed else "failed")


def test_build_image_uses_cached_image() -> None:
    cached = subprocess.CompletedProcess(
        ["docker", "image", "inspect", "loopbench-sandbox"],
        0,
        "",
        "",
    )
    with patch("sandbox.runner.subprocess.run", return_value=cached) as run:
        assert build_sandbox_image(rebuild=False)

    run.assert_called_once()
    assert run.call_args.args[0][:3] == ["docker", "image", "inspect"]


def test_build_image_on_cache_miss(tmp_path: Path) -> None:
    inspect_miss = subprocess.CompletedProcess([], 1, "", "not found")
    build_success = subprocess.CompletedProcess([], 0, "", "")

    with patch(
        "sandbox.runner.subprocess.run",
        side_effect=[inspect_miss, build_success],
    ) as run:
        assert build_sandbox_image(repo_root=str(tmp_path), rebuild=False)

    assert run.call_count == 2
    build_command = run.call_args_list[1].args[0]
    assert build_command[:2] == ["docker", "build"]
    assert "sandbox/Dockerfile.sandbox" in build_command
    assert "loopbench-sandbox" in build_command
