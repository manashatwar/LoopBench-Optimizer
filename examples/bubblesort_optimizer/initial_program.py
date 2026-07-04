# EVOLVE-BLOCK-START
"""
LoopBench example — Bubble Sort optimizer.

Generation 0: a naive O(n^2) bubble sort (intentionally slow). The evolutionary
loop will rewrite bubble_sort() into something faster (e.g. an O(n log n) sort)
while keeping the public behavior identical: return a NEW ascending list and
never mutate the caller's input.
"""


def bubble_sort(data):
    """Naive bubble sort — O(n^2). Returns a new sorted (ascending) list."""
    arr = list(data)
    n = len(arr)
    for i in range(n):
        for j in range(n - 1, i, -1):
            if arr[j] < arr[j - 1]:
                arr[j], arr[j - 1] = arr[j - 1], arr[j]
    return arr


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_sort(data):
    """Public entry point called by the tests. Returns a new ascending list."""
    return bubble_sort(data)


if __name__ == "__main__":
    import random
    import time

    sample = [random.randint(0, 10_000) for _ in range(2000)]
    start = time.perf_counter()
    run_sort(sample)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
