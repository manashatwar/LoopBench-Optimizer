"""
Benchmark scaffolding.

For an arbitrary target file, the user supplies a benchmark that lives in their
own workspace (NOT inside the cloned target repo) and is passed to LoopBench via
`--benchmark`. This module writes a ready-to-edit template so users don't start
from a blank file.

The generated benchmark:
  * loads the file under optimization from LOOPBENCH_PROGRAM_PATH,
  * has a correctness test (the gate — fill in real assertions),
  * has a speed test that prints LOOPBENCH_SPEED_MS.
"""

from __future__ import annotations

from pathlib import Path

_BENCHMARK_TEMPLATE = '''"""
LoopBench benchmark for optimizing a target file.

Run:
    loopbench run --target <repo-or-path> --target-file <file> \\
        --benchmark {benchmark_name} [--pip "numpy ..."]

LoopBench sets LOOPBENCH_PROGRAM_PATH to the candidate file each generation.
Fill in the correctness assertions and the speed workload below.
"""
import importlib.util
import os
import time
import types

import pytest


def _load():
    """Load the current candidate file (set by LoopBench)."""
    path = os.environ["LOOPBENCH_PROGRAM_PATH"]
    spec = importlib.util.spec_from_file_location("target_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load()


# ── Correctness gate ──────────────────────────────────────────────────────────
# TODO: assert the target's function(s) return the RIGHT answers. Any failure
# here rejects the candidate. Compare against a known-good reference or values.
def test_correctness(prog):
    # example:
    #   assert prog.my_function(10) == 55
    raise AssertionError("TODO: write correctness assertions for your target")


# ── Speed benchmark ───────────────────────────────────────────────────────────
# TODO: exercise the hot path on a large-enough workload, then print the marker.
def test_speed(prog):
    start = time.perf_counter()
    # TODO: call the target's hot function here, e.g.:
    #   prog.my_function(1_000_000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"\\nLOOPBENCH_SPEED_MS={{elapsed_ms:.4f}}")
    assert elapsed_ms < 60000
'''


def write_benchmark_template(path: str) -> str:
    """Write a benchmark template to *path*. Returns the path written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_BENCHMARK_TEMPLATE.format(benchmark_name=p.name), encoding="utf-8")
    return str(p)
