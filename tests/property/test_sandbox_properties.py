"""Property tests for Docker sandbox output capture requirements."""

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hypothesis import given, settings, strategies as st

from sandbox.runner import run_in_sandbox, verify_output_streams


STREAM_TEXT = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=200,
)


def _sandbox_inputs(root: Path) -> tuple[Path, Path]:
    program = root / "program.py"
    test_file = root / "test_program.py"
    program.write_text("value = 1\n", encoding="utf-8")
    test_file.write_text("def test_value(): pass\n", encoding="utf-8")
    return program, test_file


def _write_score_from_command(command: list[str], *, all_passed: bool = True) -> None:
    for argument in command:
        argument_text = str(argument)
        if argument_text.endswith(":/results"):
            results_dir = Path(argument_text[: -len(":/results")])
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "score.json").write_text(
                json.dumps(
                    {
                        "passed": 1 if all_passed else 0,
                        "failed": 0 if all_passed else 1,
                        "errors": 0,
                        "total": 1,
                        "combined_score": 1.0 if all_passed else 0.0,
                        "all_passed": all_passed,
                    }
                ),
                encoding="utf-8",
            )
            return


@given(stdout=STREAM_TEXT, stderr=STREAM_TEXT)
@settings(max_examples=25, deadline=None)
def test_property_output_stream_capture_completeness(stdout: str, stderr: str) -> None:
    """Property 1: both captured streams permit metric loading."""
    with TemporaryDirectory() as tmpdir:
        program, test_file = _sandbox_inputs(Path(tmpdir))

        def fake_run(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                _write_score_from_command(command)
                return subprocess.CompletedProcess(command, 0, stdout, stderr)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
            "sandbox.runner.subprocess.run", side_effect=fake_run
        ):
            result = run_in_sandbox(str(program), str(test_file))

        assert verify_output_streams(result["stdout"], result["stderr"])
        assert result["stdout"] == stdout
        assert result["stderr"] == stderr
        assert result["status"] == "passed"
        assert result["combined_score"] == 1.0


@given(captured_stream=STREAM_TEXT, missing_stdout=st.booleans())
@settings(max_examples=25, deadline=None)
def test_property_output_stream_capture_error_handling(
    captured_stream: str,
    missing_stdout: bool,
) -> None:
    """Property 2: a missing stream fails before metric extraction."""
    with TemporaryDirectory() as tmpdir:
        program, test_file = _sandbox_inputs(Path(tmpdir))
        stdout = None if missing_stdout else captured_stream
        stderr = captured_stream if missing_stdout else None

        def fake_run(command, **kwargs):
            if command[:2] == ["docker", "run"]:
                _write_score_from_command(command)
                return subprocess.CompletedProcess(command, 0, stdout, stderr)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch("sandbox.runner.build_sandbox_image", return_value=True), patch(
            "sandbox.runner.subprocess.run", side_effect=fake_run
        ), patch("sandbox.runner.json.load") as metric_loader:
            result = run_in_sandbox(str(program), str(test_file))

        assert not verify_output_streams(stdout, stderr)
        assert result["status"] == "output_capture_failed"
        assert result["all_passed"] is False
        assert result["error"]
        metric_loader.assert_not_called()
