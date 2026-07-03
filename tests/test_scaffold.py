"""Tests for the benchmark scaffold (loopbench/scaffold.py)."""
from pathlib import Path

from loopbench.scaffold import write_benchmark_template


def test_writes_template(tmp_path: Path):
    out = tmp_path / "bench.py"
    write_benchmark_template(str(out))
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    # Must be a valid, importable-shaped pytest benchmark referencing the env var.
    assert "LOOPBENCH_PROGRAM_PATH" in text
    assert "LOOPBENCH_SPEED_MS" in text
    assert "def test_correctness" in text
    assert "def test_speed" in text
    assert "bench.py" in text  # benchmark name interpolated into the docstring


def test_creates_parent_dirs(tmp_path: Path):
    out = tmp_path / "nested" / "dir" / "b.py"
    write_benchmark_template(str(out))
    assert out.exists()


def test_generated_template_is_valid_python(tmp_path: Path):
    import ast
    out = tmp_path / "bench.py"
    write_benchmark_template(str(out))
    ast.parse(out.read_text(encoding="utf-8"))  # raises if the template is malformed
