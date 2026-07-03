"""
Pytest suite for the LoopBench Longest-Palindrome demo (CodeChef PRINCESS).

Correctness is checked two ways, both implementation-agnostic:
  1. The length of the returned longest palindrome must match a reference
     expand-around-center implementation, AND the returned string must itself
     be a palindrome and a substring of the input.
  2. has_palindrome_substring() must match the PRINCESS YES/NO answer.

A speed test times a large input and emits LOOPBENCH_SPEED_MS.
"""
import importlib.util
import os
import time
import types

# pyrefly: ignore [missing-import]
import pytest

_PROGRAM_PATH = os.environ.get("LOOPBENCH_PROGRAM_PATH")


def _load_program() -> types.ModuleType:
    if _PROGRAM_PATH is None:
        raise RuntimeError("LOOPBENCH_PROGRAM_PATH environment variable not set.")
    spec = importlib.util.spec_from_file_location("evolved_palindrome", _PROGRAM_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load spec from {_PROGRAM_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load_program()


def _ref_longest_len(s: str) -> int:
    """Reference: length of the longest palindromic substring (expand-around-center)."""
    if len(s) < 2:
        return len(s)
    best = 1
    for center in range(len(s)):
        for left, right in ((center, center), (center, center + 1)):
            while left >= 0 and right < len(s) and s[left] == s[right]:
                left -= 1
                right += 1
            best = max(best, right - left - 1)
    return best


def _big_input() -> str:
    return ("abcracecarxyznoonwowdeed" * 18) + "xyzzyx"


# ── Correctness gate ──────────────────────────────────────────────────────────
class TestLongestPalindromeCorrectness:
    @pytest.mark.parametrize("s", [
        "", "a", "ab", "aa", "aba", "abba", "abc",
        "babba", "racecar", "abacabad", "forgeeksskeegfor",
        "banana", "noon", "level", "xyzzyx", "abcde",
        "aaaa", "aabbaa", "12321", "a man a plan",
    ])
    def test_length_matches_reference(self, prog: types.ModuleType, s: str) -> None:
        result = prog.run_longest_palindrome(s)
        assert len(result) == _ref_longest_len(s)
        # The returned value must genuinely be a palindromic substring of s.
        assert result == result[::-1]
        assert result in s

    @pytest.mark.parametrize("s,expected", [
        ("ab", False),        # PRINCESS example 1 -> NO
        ("babba", True),      # PRINCESS example 2 -> YES
        ("abc", False),
        ("aab", True),
        ("a", False),
        ("", False),
    ])
    def test_princess_yes_no(self, prog: types.ModuleType, s: str, expected: bool) -> None:
        assert prog.has_palindrome_substring(s) is expected

    def test_big_input_matches_reference(self, prog: types.ModuleType) -> None:
        s = _big_input()
        assert len(prog.run_longest_palindrome(s)) == _ref_longest_len(s)


# ── Speed benchmark ───────────────────────────────────────────────────────────
class TestLongestPalindromeSpeed:
    def test_speed(self, prog: types.ModuleType) -> None:
        s = _big_input()
        expected_len = _ref_longest_len(s)
        assert len(prog.run_longest_palindrome(s)) == expected_len  # warm-up + check

        iterations = 3
        start = time.perf_counter()
        for _ in range(iterations):
            prog.run_longest_palindrome(s)
        elapsed_ms = ((time.perf_counter() - start) / iterations) * 1000

        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
        assert elapsed_ms < 10000, f"took {elapsed_ms:.1f}ms — exceeds 10s limit"
