# EVOLVE-BLOCK-START
"""
LoopBench Demo — Gradient Descent Optimizer
Adapted from a classic hand-written linear-regression gradient descent
(OmkarPathak/Python-Programs). The cost and gradient are computed with Python
for-loops over NumPy arrays — correct but slow. LoopBench will vectorize them
with NumPy while keeping the numerical results identical.

Also exercises automatic dependency handling: the sandbox detects `numpy` and
installs it before running.
"""
import numpy as np


def evaluate_cost(x: np.ndarray, y: np.ndarray, params: np.ndarray) -> float:
    """Mean squared error of a line params=[m, b] over points (naive loop)."""
    total = 0.0
    n = len(y)
    for i in range(n):
        pred = params[0] * x[i, 0] + params[1]
        total += (y[i] - pred) ** 2
    return total / n


def evaluate_gradient(x: np.ndarray, y: np.ndarray, params: np.ndarray) -> np.ndarray:
    """Gradient of the MSE w.r.t. [m, b] (naive loop)."""
    m_grad = 0.0
    b_grad = 0.0
    n = len(y)
    for i in range(n):
        err = y[i] - (params[0] * x[i, 0] + params[1])
        m_grad += -(2.0 / n) * x[i, 0] * err
        b_grad += -(2.0 / n) * err
    return np.array([m_grad, b_grad])


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_gradient_descent(x, y, init_params, alpha=0.1, iterations=200):
    """Fit a line to (x, y) via gradient descent. Returns (params, final_cost)."""
    params = np.array(init_params, dtype=float)
    for _ in range(iterations):
        grad = evaluate_gradient(x, y, params)
        params = params - alpha * grad
    return params, evaluate_cost(x, y, params)


def _make_data(n=2000, m=2.5, b=1.0, seed=0):
    rng = np.random.default_rng(seed)
    xs = rng.random(n)
    x = np.ones((n, 2))
    x[:, 0] = xs
    y = m * xs + b + rng.normal(0, 0.01, n)
    return x, y


if __name__ == "__main__":
    import time

    x, y = _make_data()
    start = time.perf_counter()
    params, cost = run_gradient_descent(x, y, [0.0, 0.0], alpha=0.5, iterations=200)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"fit m={params[0]:.3f} b={params[1]:.3f} cost={cost:.6f}")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
