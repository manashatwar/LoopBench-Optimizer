"""
Job scaffolding.

`loopbench init --job <dir>` generates a ready-to-edit job folder for optimizing
a file in an external repo, so the user focuses on configuration rather than
boilerplate:

    <dir>/
    ├── loopbench.yaml    # fill in repo, file, and pip deps
    └── test_target.py    # fill in the correctness gate + speed workload

Then:  loopbench run --config <dir>/loopbench.yaml
"""

from __future__ import annotations

from pathlib import Path

_LOOPBENCH_YAML = """\
# LoopBench job — optimize a file in an external repo (or a local path).
# Edit target.repo / target.file / sandbox.pip, fill in test_target.py, then run:
#   loopbench run --config loopbench.yaml

target:
  repo: https://github.com/OWNER/REPO      # repo to clone (or a local repo path)
  file: path/in/repo/module.py             # the file inside the repo to optimize
  evaluator: test_target.py                # this job's evaluator/test (see below)

sandbox:
  command: "pytest test_target.py -v -s -q"
  pip: []                                  # e.g. ["numpy", "pandas"] — installed in the sandbox

metric:
  name: "combined_score"                   # what to optimize (your evaluator emits it)
  threshold: 0.95                          # stop early once a candidate reaches this

constraints:
  max_iterations: 10
  max_tokens_total: 200000
  # max_token_cost_usd: 0.50               # needs pricing (see docs/defining-benchmarks.md)
  # max_runtime_seconds: 600
"""

_TEST_TARGET = '''\
"""
LoopBench evaluator/test for this job — scores each candidate of the target file.

LoopBench sets LOOPBENCH_PROGRAM_PATH to the candidate file every generation.
Fill in the two TODOs: a correctness gate and a speed workload that prints
LOOPBENCH_SPEED_MS. This file lives in your job folder, never in the target repo.
"""
import importlib.util
import os
import time
import types

import pytest


def _load() -> types.ModuleType:
    """Load the current candidate (the file under optimization)."""
    path = os.environ["LOOPBENCH_PROGRAM_PATH"]
    spec = importlib.util.spec_from_file_location("target_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load()


# ── Correctness gate ──────────────────────────────────────────────────────────
# Any failure here rejects the candidate. Compare against known-good values or a
# reference implementation.
def test_correctness(prog):
    # TODO example:
    #   assert prog.my_function(10) == 55
    raise AssertionError("TODO: add correctness assertions for your target")


# ── Speed benchmark ───────────────────────────────────────────────────────────
def test_speed(prog):
    start = time.perf_counter()
    # TODO: exercise the hot path on a large-enough workload, e.g.:
    #   prog.my_function(1_000_000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"\\nLOOPBENCH_SPEED_MS={{elapsed_ms:.4f}}")
    assert elapsed_ms < 60000


# ── Tip: scripts that run at import (competitive-programming / CLI style) ──────
# If the target executes at import (e.g. reads stdin or a data file at module
# top level), don't import it — run it as a subprocess instead:
#
#   import subprocess, sys, re
#   def test_speed():
#       proc = subprocess.run([sys.executable, os.environ["LOOPBENCH_PROGRAM_PATH"]],
#                             capture_output=True, text=True, timeout=120)
#       assert proc.returncode == 0
#       # parse the program's own timing/output, then:
#       print("LOOPBENCH_SPEED_MS=...")
'''


def write_job(job_dir: str) -> dict:
    """Scaffold a job folder. Returns paths written."""
    d = Path(job_dir)
    d.mkdir(parents=True, exist_ok=True)
    yaml_path = d / "loopbench.yaml"
    test_path = d / "test_target.py"
    yaml_path.write_text(_LOOPBENCH_YAML, encoding="utf-8")
    test_path.write_text(_TEST_TARGET, encoding="utf-8")
    return {"config": str(yaml_path), "evaluator": str(test_path), "dir": str(d)}
