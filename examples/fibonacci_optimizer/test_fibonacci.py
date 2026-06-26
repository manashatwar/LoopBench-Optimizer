"""
Pytest suite for the LoopBench Fibonacci Hello World demo.

How it works:
  - The evaluator sets LOOPBENCH_PROGRAM_PATH env var to the evolved program path.
  - Tests dynamically load and call run_fibonacci() from that path.
  - Speed timing is emitted as LOOPBENCH_SPEED_MS=<value> on stdout so the
    evaluator can parse it from subprocess output.

Scoring contract:
  - All correctness tests must pass → correctness_score = 1.0 (regression gate)
  - Any failure → combined_score = 0.0 (patch is rejected)
  - Speed benchmark emits LOOPBENCH_SPEED_MS for speed_score calculation
"""
import importlib.util
import os
import time
import types
from typing import Any

# pyrefly: ignore [missing-import]
import pytest

# ── Program loader ─────────────────────────────────────────────────────────────
_PROGRAM_PATH = os.environ.get("LOOPBENCH_PROGRAM_PATH")


def _load_program() -> types.ModuleType:
    """Dynamically load the evolved program from LOOPBENCH_PROGRAM_PATH."""
    if _PROGRAM_PATH is None:
        raise RuntimeError(
            "LOOPBENCH_PROGRAM_PATH environment variable not set. "
            "Run via the evaluator or set the variable manually."
        )
    spec = importlib.util.spec_from_file_location("evolved_fib", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    """Session-scoped fixture — load the program once per test run."""
    return _load_program()


# ── Correctness tests (regression gate) ───────────────────────────────────────
class TestFibonacciCorrectness:
    """Must all pass. Any single failure zeroes the combined_score."""

    def test_fib_0(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(0) == 0

    def test_fib_1(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(1) == 1

    def test_fib_2(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(2) == 1

    def test_fib_3(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(3) == 2

    def test_fib_4(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(4) == 3

    def test_fib_5(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(5) == 5

    def test_fib_6(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(6) == 8

    def test_fib_10(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(10) == 55

    def test_fib_15(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(15) == 610

    def test_fib_20(self, prog: types.ModuleType) -> None:
        assert prog.run_fibonacci(20) == 6765

    def test_fib_negative_returns_zero(self, prog: types.ModuleType) -> None:
        """Negative inputs must return 0 (boundary contract)."""
        assert prog.run_fibonacci(-1) == 0
        assert prog.run_fibonacci(-10) == 0

    def test_fib_35_large(self, prog: types.ModuleType) -> None:
        """Larger value correctness — also exercises performance."""
        assert prog.run_fibonacci(35) == 9227465


# ── Speed benchmark (parseable output) ────────────────────────────────────────
class TestFibonacciSpeed:
    """Speed tests. Output is parsed by the evaluator."""

    def test_fib_30_speed(self, prog: types.ModuleType) -> None:
        """
        Benchmark fib(30) and emit LOOPBENCH_SPEED_MS=<value> on stdout.
        Baseline naive recursion ≈ 200-400ms.
        Target after optimization: < 5ms.
        Hard limit: 2000ms (evaluator timeout guard).
        """
        start = time.perf_counter()
        result = prog.run_fibonacci(30)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Correctness check inside speed test (belt-and-suspenders)
        assert result == 832040, f"fib(30) wrong: expected 832040, got {result}"

        # Emit parseable marker — evaluator.py greps for this line
        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")

        # Hard upper bound — prevents infinite-loop or pathological mutations
        assert elapsed_ms < 2000, (
            f"fib(30) took {elapsed_ms:.1f}ms — exceeds 2000ms hard limit"
        )
