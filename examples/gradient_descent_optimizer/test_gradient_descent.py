"""
Pytest suite for the LoopBench Gradient Descent demo.

Correctness is checked two ways, independent of the implementation:
  1. evaluate_cost / evaluate_gradient must match vectorized reference formulas
     for random inputs (this is the real gate — it holds for any correct
     implementation, looped or vectorized).
  2. A full descent must converge near the known ground-truth line.

A speed test times a large-array descent and emits LOOPBENCH_SPEED_MS.
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
    spec = importlib.util.spec_from_file_location("evolved_gd", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load_program()


def _data(n=1500, m=2.5, b=1.0, seed=1):
    rng = np.random.default_rng(seed)
    xs = rng.random(n)
    x = np.ones((n, 2))
    x[:, 0] = xs
    y = m * xs + b + rng.normal(0, 0.01, n)
    return x, y


def _ref_cost(x, y, params):
    pred = params[0] * x[:, 0] + params[1]
    return float(np.mean((y - pred) ** 2))


def _ref_grad(x, y, params):
    err = y - (params[0] * x[:, 0] + params[1])
    return np.array([np.mean(-2.0 * x[:, 0] * err), np.mean(-2.0 * err)])


class TestCorrectness:
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_cost_matches_reference(self, prog, seed):
        x, y = _data(n=300, seed=seed)
        params = np.array([1.3, -0.4])
        assert prog.evaluate_cost(x, y, params) == pytest.approx(_ref_cost(x, y, params), rel=1e-9)

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_gradient_matches_reference(self, prog, seed):
        x, y = _data(n=300, seed=seed)
        params = np.array([0.7, 0.2])
        got = np.asarray(prog.evaluate_gradient(x, y, params), dtype=float)
        exp = _ref_grad(x, y, params)
        assert got == pytest.approx(exp, rel=1e-9, abs=1e-12)

    def test_descent_converges(self, prog):
        x, y = _data(n=1500, m=2.5, b=1.0, seed=7)
        params, cost = prog.run_gradient_descent(x, y, [0.0, 0.0], alpha=0.5, iterations=400)
        assert params[0] == pytest.approx(2.5, abs=0.15)
        assert params[1] == pytest.approx(1.0, abs=0.15)
        assert cost < 0.01


class TestSpeed:
    def test_descent_speed(self, prog):
        x, y = _data(n=4000, seed=11)
        reps = 3
        start = time.perf_counter()
        for _ in range(reps):
            prog.run_gradient_descent(x, y, [0.0, 0.0], alpha=0.5, iterations=150)
        elapsed_ms = ((time.perf_counter() - start) / reps) * 1000
        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
        assert elapsed_ms < 20000
