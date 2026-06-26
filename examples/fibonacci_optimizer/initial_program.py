# EVOLVE-BLOCK-START
"""
LoopBench Hello World — Fibonacci Optimizer
Generation 0: naive recursive implementation (intentionally slow).
The evolutionary loop will rewrite the fibonacci() function.
"""


def fibonacci(n: int) -> int:
    """
    Naive recursive Fibonacci — no memoization, exponential time complexity O(2^n).
    This is the starting point; LoopBench will optimize it.

    Args:
        n: Position in the Fibonacci sequence (0-indexed)

    Returns:
        The nth Fibonacci number
    """
    if n <= 0:
        return 0
    if n == 1:
        return 1
    return fibonacci(n - 1) + fibonacci(n - 2)


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_fibonacci(n: int) -> int:
    """
    Public entry point called by the evaluator and tests.
    This wrapper is intentionally outside the EVOLVE-BLOCK so it
    always exists regardless of what the LLM does to fibonacci().
    """
    return fibonacci(n)


if __name__ == "__main__":
    import time

    start = time.perf_counter()
    result = run_fibonacci(30)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"fib(30) = {result}")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
    print(f"Time: {elapsed_ms:.1f}ms")
