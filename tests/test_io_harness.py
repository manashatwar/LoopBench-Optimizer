"""Tests for the LoopBench run-mode I/O harness generator (loopbench/io_harness.py)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from loopbench.io_harness import (
    generate_io_test_file,
    load_io_cases,
    maybe_build_io_harness,
    resolve_io_tests_path,
)


def _write_cases(tmp_path: Path, cases) -> Path:
    p = tmp_path / "io_tests.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    return p


class TestLoadIoCases:
    def test_valid_cases(self, tmp_path: Path):
        p = _write_cases(tmp_path, [
            {"name": "a", "input": "1\n", "output": "YES"},
            {"input": "2\n", "output": "NO"},
        ])
        cases = load_io_cases(str(p))
        assert len(cases) == 2
        assert cases[0]["name"] == "a"
        assert cases[1]["name"] == "case1"  # default name filled in
        assert cases[1]["input"] == "2\n"

    def test_rejects_empty_list(self, tmp_path: Path):
        p = _write_cases(tmp_path, [])
        with pytest.raises(ValueError):
            load_io_cases(str(p))

    def test_rejects_missing_keys(self, tmp_path: Path):
        p = _write_cases(tmp_path, [{"input": "x"}])  # no output
        with pytest.raises(ValueError):
            load_io_cases(str(p))


class TestResolvePrecedence:
    def test_explicit_wins(self, tmp_path: Path):
        target = tmp_path / "prog.py"
        target.write_text("print(1)")
        path, reason = resolve_io_tests_path("/explicit/path.json", target)
        assert path == "/explicit/path.json"
        assert "explicit" in reason

    def test_autodetect_stem_io_json(self, tmp_path: Path):
        target = tmp_path / "prog.py"
        target.write_text("print(1)")
        io = tmp_path / "prog.io.json"
        io.write_text("[]")
        path, reason = resolve_io_tests_path(None, target)
        assert path == str(io)

    def test_none_when_absent(self, tmp_path: Path):
        target = tmp_path / "prog.py"
        target.write_text("print(1)")
        path, _ = resolve_io_tests_path(None, target)
        assert path is None


class TestGenerateAndRun:
    def test_generated_file_contains_cases_and_marker(self, tmp_path: Path):
        out = tmp_path / "_loopbench_io_test.py"
        generate_io_test_file([{"name": "c0", "input": "hi\n", "output": "HI"}], str(out))
        text = out.read_text(encoding="utf-8")
        assert "LOOPBENCH_SPEED_MS" in text
        assert "subprocess" in text
        assert "hi" in text

    def test_maybe_build_returns_none_without_tests(self, tmp_path: Path):
        target = tmp_path / "prog.py"
        target.write_text("print(1)")
        assert maybe_build_io_harness(None, target, tmp_path / "out") is None

    def test_harness_passes_against_correct_program(self, tmp_path: Path):
        """End-to-end: generate a harness and run it against a matching stdin program."""
        prog = tmp_path / "echo_upper.py"
        prog.write_text(
            "import sys\n"
            "for line in sys.stdin.read().splitlines():\n"
            "    print(line.upper())\n"
        )
        cases_file = _write_cases(tmp_path, [
            {"name": "one", "input": "abc\n", "output": "ABC"},
            {"name": "two", "input": "ab\ncd\n", "output": "AB\nCD"},
        ])
        info = maybe_build_io_harness(str(cases_file), prog, tmp_path / "harness")
        assert info is not None
        assert info["n_cases"] == 2

        env = {"LOOPBENCH_PROGRAM_PATH": str(prog)}
        import os
        env = {**os.environ, **env}
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", info["test_path"], "-q", "-s"],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert "LOOPBENCH_SPEED_MS=" in (proc.stdout + proc.stderr)
        assert proc.returncode == 0, proc.stdout + proc.stderr
