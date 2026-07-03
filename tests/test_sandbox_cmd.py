"""Tests for custom sandbox command resolution (sandbox/runner.py)."""
from sandbox.runner import _resolve_test_cmd


def test_default_when_no_command():
    cmd = _resolve_test_cmd({}, "/workspace/test_x.py")
    assert cmd == "pytest /workspace/test_x.py -v -s -q --tb=short"


def test_default_when_none_cfg():
    cmd = _resolve_test_cmd(None, "/workspace/test_x.py")
    assert cmd.startswith("pytest /workspace/test_x.py")


def test_bare_pytest_falls_back_to_default():
    # A bare "pytest" (e.g. detected framework name) is not runnable on its own.
    cmd = _resolve_test_cmd({"test_command": "pytest"}, "/workspace/test_x.py")
    assert cmd == "pytest /workspace/test_x.py -v -s -q --tb=short"


def test_custom_command_is_honored():
    cmd = _resolve_test_cmd({"test_command": "pytest bench.py --benchmark-only"}, "/workspace/test_x.py")
    assert cmd == "pytest bench.py --benchmark-only"


def test_non_pytest_command_is_honored():
    cmd = _resolve_test_cmd({"test_command": "python benchmark.py"}, "/workspace/test_x.py")
    assert cmd == "python benchmark.py"


def test_whitespace_command_falls_back():
    cmd = _resolve_test_cmd({"test_command": "   "}, "/workspace/test_x.py")
    assert cmd.startswith("pytest /workspace/test_x.py")
