# EVOLVE-BLOCK-START
"""
LoopBench Example — Prime Counter Optimizer
Generation 0: naive trial-division primality test (intentionally slow).
The evolutionary loop will rewrite count_primes() into a faster algorithm
(e.g. the Sieve of Eratosthenes) while keeping every correctness test green.
"""


def count_primes(n: int) -> int:
    """
    Count the number of primes strictly less than n using naive trial division.
    This is O(n * sqrt(n)) — deliberately slow. LoopBench will optimize it.

    Args:
        n: Upper bound (exclusive).

    Returns:
        The count of primes p with 2 <= p < n.
    """
    if n <= 2:
        return 0

    def _is_prime(x: int) -> bool:
        if x < 2:
            return False
        i = 2
        while i * i <= x:
            if x % i == 0:
                return False
            i += 1
        return True

    total = 0
    for candidate in range(2, n):
        if _is_prime(candidate):
            total += 1
    return total


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_count_primes(n: int) -> int:
    """
    Public entry point called by the evaluator and tests.
    Kept outside the EVOLVE-BLOCK so it always exists regardless of
    what the LLM does to count_primes().
    """
    return count_primes(n)


if __name__ == "__main__":
    import time

    start = time.perf_counter()
    result = run_count_primes(50_000)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"primes below 50000 = {result}")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
    print(f"Time: {elapsed_ms:.1f}ms")
