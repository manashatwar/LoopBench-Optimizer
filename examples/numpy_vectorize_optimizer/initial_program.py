# EVOLVE-BLOCK-START
"""
LoopBench Demo — NumPy Vectorization Optimizer
Generation 0: a correct but slow mean-squared-error computed with a Python
for-loop over NumPy arrays (the same anti-pattern as hand-written gradient
descent / cost functions). LoopBench will vectorize it with NumPy while keeping
the result identical.

This example also exercises LoopBench's automatic dependency handling: the
sandbox detects the `numpy` import and installs it before running.
"""
import numpy as np


def mean_squared_error(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Naive O(n) Python loop over arrays (slow)."""
    total = 0.0
    n = len(y_true)
    for i in range(n):
        diff = y_true[i] - y_pred[i]
        total += diff * diff
    return total / n


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_mse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Public entry point called by the evaluator and tests."""
    return mean_squared_error(y_true, y_pred)


if __name__ == "__main__":
    import time

    rng = np.random.default_rng(0)
    a = rng.random(200_000)
    b = rng.random(200_000)
    start = time.perf_counter()
    val = run_mse(a, b)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"mse = {val:.6f}")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
