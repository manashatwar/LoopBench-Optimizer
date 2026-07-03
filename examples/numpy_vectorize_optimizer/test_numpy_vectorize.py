"""
Pytest suite for the LoopBench NumPy Vectorization demo.

Correctness is checked against numpy's own MSE within a tight tolerance, and a
speed test times a large-array computation and emits LOOPBENCH_SPEED_MS.
Requires numpy — LoopBench's sandbox installs it automatically.
"""
import importlib.util
import os
import time
import types

# pyrefly: ignore [missing-import]
import numpy as np
# pyrefly: ignore [missing-import]
import pytest

_PROGRAM_PATH = os.environ.get("LOOPBENCH_PROGRAM_PATH")


def _load_program() -> types.ModuleType:
    if _PROGRAM_PATH is None:
        raise RuntimeError("LOOPBENCH_PROGRAM_PATH environment variable not set.")
    spec = importlib.util.spec_from_file_location("evolved_mse", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load_program()


class TestCorrectness:
    @pytest.mark.parametrize("n", [1, 2, 10, 1000])
    def test_matches_numpy(self, prog: types.ModuleType, n: int) -> None:
        rng = np.random.default_rng(n)
        a = rng.random(n)
        b = rng.random(n)
        expected = float(np.mean((a - b) ** 2))
        assert prog.run_mse(a, b) == pytest.approx(expected, rel=1e-9, abs=1e-12)

    def test_zero_when_equal(self, prog: types.ModuleType) -> None:
        a = np.arange(100, dtype=float)
        assert prog.run_mse(a, a) == pytest.approx(0.0, abs=1e-12)


class TestSpeed:
    def test_large_array_speed(self, prog: types.ModuleType) -> None:
        rng = np.random.default_rng(42)
        a = rng.random(200_000)
        b = rng.random(200_000)
        expected = float(np.mean((a - b) ** 2))
        assert prog.run_mse(a, b) == pytest.approx(expected, rel=1e-9)

        reps = 5
        start = time.perf_counter()
        for _ in range(reps):
            prog.run_mse(a, b)
        elapsed_ms = ((time.perf_counter() - start) / reps) * 1000
        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
        assert elapsed_ms < 10000
