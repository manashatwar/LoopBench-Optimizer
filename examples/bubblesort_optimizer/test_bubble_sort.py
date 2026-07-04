"""
Pytest suite for the LoopBench Bubble Sort example.

Scoring contract:
  - All correctness tests must pass → correctness gate holds (patch kept).
  - Any failure → combined_score = 0.0 (patch rejected).
  - The speed benchmark prints LOOPBENCH_SPEED_MS=<value> for the speed score.

The optimizer may replace the O(n^2) algorithm with anything faster, as long as
run_sort() still returns a NEW ascending list and does not mutate its input.
"""
import importlib.util
import os
import random
import time
import types

import pytest

_PROGRAM_PATH = os.environ["LOOPBENCH_PROGRAM_PATH"]


def _load_program() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("evolved_sort", _PROGRAM_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def prog() -> types.ModuleType:
    return _load_program()


# ── Correctness gate (must match sorted()) ────────────────────────────────────
class TestBubbleSortCorrectness:
    def test_random_lists(self, prog: types.ModuleType) -> None:
        rng = random.Random(1234)
        for _ in range(50):
            data = [rng.randint(-1000, 1000) for _ in range(rng.randint(0, 80))]
            assert prog.run_sort(data) == sorted(data)

    def test_edge_cases(self, prog: types.ModuleType) -> None:
        assert prog.run_sort([]) == []
        assert prog.run_sort([42]) == [42]
        assert prog.run_sort([5, 5, 5, 5]) == [5, 5, 5, 5]        # duplicates
        assert prog.run_sort([9, 7, 5, 3, 1]) == [1, 3, 5, 7, 9]  # reverse
        assert prog.run_sort([1, 2, 3, 4, 5]) == [1, 2, 3, 4, 5]  # already sorted

    def test_does_not_mutate_input(self, prog: types.ModuleType) -> None:
        original = [3, 1, 2, 5, 4]
        snapshot = list(original)
        prog.run_sort(original)
        assert original == snapshot, "run_sort must not mutate the caller's list"

    def test_preserves_multiset(self, prog: types.ModuleType) -> None:
        rng = random.Random(99)
        data = [rng.randint(0, 50) for _ in range(300)]
        out = prog.run_sort(data)
        assert sorted(out) == sorted(data)


# ── Speed benchmark (parseable output) ────────────────────────────────────────
class TestBubbleSortSpeed:
    def test_large_random_speed(self, prog: types.ModuleType) -> None:
        """
        Sort a large random list and emit LOOPBENCH_SPEED_MS.
        Baseline (bubble sort, O(n^2)) is hundreds of ms; an O(n log n)
        replacement drops it to well under a millisecond.
        """
        rng = random.Random(2024)
        workload = [rng.randint(0, 1_000_000) for _ in range(2500)]
        start = time.perf_counter()
        result = prog.run_sort(workload)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert result == sorted(workload)
        print(f"\nLOOPBENCH_SPEED_MS={elapsed_ms:.4f}")
        assert elapsed_ms < 30000, f"sort took {elapsed_ms:.1f}ms — exceeds hard limit"
