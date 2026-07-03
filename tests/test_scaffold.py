"""Tests for the external-repo job scaffold (loopbench/scaffold.py)."""
import ast

import yaml

from loopbench.scaffold import write_job


def test_writes_both_files(tmp_path):
    paths = write_job(str(tmp_path / "job"))
    cfg = tmp_path / "job" / "loopbench.yaml"
    ev = tmp_path / "job" / "test_target.py"
    assert cfg.exists() and ev.exists()
    assert paths["config"].endswith("loopbench.yaml")
    assert paths["evaluator"].endswith("test_target.py")


def test_yaml_is_valid_and_has_external_target(tmp_path):
    write_job(str(tmp_path / "job"))
    data = yaml.safe_load((tmp_path / "job" / "loopbench.yaml").read_text(encoding="utf-8"))
    assert "repo" in data["target"]
    assert "file" in data["target"]
    assert data["target"]["evaluator"] == "test_target.py"
    assert "command" in data["sandbox"]
    assert "pip" in data["sandbox"]
    assert "metric" in data and "constraints" in data


def test_evaluator_is_valid_python_with_markers(tmp_path):
    write_job(str(tmp_path / "job"))
    text = (tmp_path / "job" / "test_target.py").read_text(encoding="utf-8")
    ast.parse(text)  # must be valid Python
    assert "LOOPBENCH_PROGRAM_PATH" in text
    assert "LOOPBENCH_SPEED_MS" in text
    assert "def test_correctness" in text
    assert "def test_speed" in text


def test_creates_nested_dir(tmp_path):
    write_job(str(tmp_path / "a" / "b" / "job"))
    assert (tmp_path / "a" / "b" / "job" / "loopbench.yaml").exists()


def test_check_accepts_scaffolded_external_job(tmp_path):
    """`loopbench check` must validate an external-repo job (no evaluator.py)."""
    import argparse

    from loopbench.cli import _cmd_check

    write_job(str(tmp_path / "job"))
    args = argparse.Namespace(config=str(tmp_path / "job" / "loopbench.yaml"))
    assert _cmd_check(args) == 0

