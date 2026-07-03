# EVOLVE-BLOCK-START
"""
LoopBench Demo — Longest Palindromic Substring Optimizer
Based on the CodeChef "PRINCESS" problem (detect palindromic substrings).

Generation 0: a correct but deliberately naive O(n^3) solution — it checks
every substring and tests each for being a palindrome. LoopBench will rewrite
longest_palindrome() to a faster approach (expand-around-center O(n^2), or
Manacher's algorithm O(n)) while keeping every correctness test green.
"""


def longest_palindrome(s: str) -> str:
    """Return the longest palindromic substring of s (naive O(n^3))."""
    n = len(s)
    if n < 2:
        return s

    best = s[0] if n >= 1 else ""
    # Check every substring s[i:j] and keep the longest palindrome.
    for i in range(n):
        for j in range(i + 1, n + 1):
            sub = s[i:j]
            if len(sub) > len(best) and sub == sub[::-1]:
                best = sub
    return best


# EVOLVE-BLOCK-END


# ── Fixed section (never mutated) ─────────────────────────────────────────────
def run_longest_palindrome(s: str) -> str:
    """Public entry point called by the evaluator and tests."""
    return longest_palindrome(s)


def has_palindrome_substring(s: str) -> bool:
    """True if s contains a palindromic substring of length > 1 (the PRINCESS
    problem's YES/NO question). Implemented on top of the evolved function."""
    return len(run_longest_palindrome(s)) > 1


if __name__ == "__main__":
    import time

    sample = ("abacabadabacaba" * 8) + "xyzzyx"
    start = time.perf_counter()
    result = run_longest_palindrome(sample)
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"longest palindrome length = {len(result)}")
    print(f"LOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
    print(f"Time: {elapsed_ms:.1f}ms")
