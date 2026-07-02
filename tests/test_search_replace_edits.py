"""
Tests for Aider-style SEARCH/REPLACE edit-block parsing and application
in openevolve.optimizer_loop.
"""

from openevolve.optimizer_loop import (
    _apply_search_replace,
    _parse_search_replace_blocks,
)

ORIGINAL = (
    "def fib(n):\n"
    "    if n <= 0:\n"
    "        return 0\n"
    "    return fib(n-1) + fib(n-2)\n"
)


def _block(search: str, replace: str) -> str:
    return (
        "<<<<<<< SEARCH\n"
        f"{search}\n"
        "=======\n"
        f"{replace}\n"
        ">>>>>>> REPLACE\n"
    )


class TestParse:
    def test_single_block(self):
        text = _block("a = 1", "a = 2")
        blocks = _parse_search_replace_blocks(text)
        assert blocks == [("a = 1", "a = 2")]

    def test_multiple_blocks(self):
        text = _block("a = 1", "a = 2") + "\nsome prose\n" + _block("b = 3", "b = 4")
        blocks = _parse_search_replace_blocks(text)
        assert blocks == [("a = 1", "a = 2"), ("b = 3", "b = 4")]

    def test_block_inside_code_fence(self):
        text = "```\n" + _block("x", "y") + "```\n"
        blocks = _parse_search_replace_blocks(text)
        assert blocks == [("x", "y")]

    def test_no_blocks(self):
        assert _parse_search_replace_blocks("just prose") == []


class TestApply:
    def test_exact_replacement(self):
        blocks = [("    return fib(n-1) + fib(n-2)",
                   "    a, b = 0, 1\n    for _ in range(2, n+1):\n        a, b = b, a+b\n    return b")]
        new, err = _apply_search_replace(ORIGINAL, blocks)
        assert err is None
        assert "a, b = b, a+b" in new
        assert "fib(n-1)" not in new

    def test_fuzzy_trailing_whitespace(self):
        # SEARCH matches the file line but has extra TRAILING spaces
        blocks = [("        return 0   ", "        return 0  # fixed")]
        new, err = _apply_search_replace(ORIGINAL, blocks)
        assert err is None
        assert "# fixed" in new

    def test_no_match_returns_error(self):
        blocks = [("this text is not in the file", "whatever")]
        new, err = _apply_search_replace(ORIGINAL, blocks)
        assert err is not None
        assert new == ORIGINAL

    def test_empty_blocks_error(self):
        new, err = _apply_search_replace(ORIGINAL, [])
        assert err is not None

    def test_no_change_error(self):
        blocks = [("    return 0", "    return 0")]
        new, err = _apply_search_replace(ORIGINAL, blocks)
        assert err is not None  # produced no change


class TestEndToEnd:
    def test_parse_then_apply(self):
        response = (
            "Here is the fix:\n"
            + _block("        return 0", "        return 0  # base case")
        )
        blocks = _parse_search_replace_blocks(response)
        new, err = _apply_search_replace(ORIGINAL, blocks)
        assert err is None
        assert "# base case" in new
