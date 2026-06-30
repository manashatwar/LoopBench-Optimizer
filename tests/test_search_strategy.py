"""
Unit tests for openevolve/search_strategy.py — Tasks 8.1 – 8.6.

Covers:
  - SearchStrategy ABC enforcement
  - GreedySearch selection (including tie-breaking)
  - BeamSearch top-K sampling and parallelization flag
  - RandomRestartSearch restart interval and greedy fallback
  - create_strategy() factory for all three types
  - Edge cases and error paths

Requirements: 13.1 – 13.7
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pytest

from openevolve.search_strategy import (
    BeamSearch,
    GreedySearch,
    RandomRestartSearch,
    SearchStrategy,
    _score_of,
    create_strategy,
)


# ---------------------------------------------------------------------------
# Helpers / fake candidate types
# ---------------------------------------------------------------------------

@dataclass
class SimpleCandidate:
    """Minimal candidate with a direct .score attribute."""
    id: int
    score: Optional[float]
    generation: int = 1


@dataclass
class ProgramCandidate:
    """OpenEvolve-style candidate with a metrics dict (no direct .score)."""
    id: int
    metrics: Dict[str, float] = field(default_factory=dict)
    generation: int = 1


def _history(scores, *, start_id=0, generation=1):
    """Create a list of SimpleCandidate objects with the given scores."""
    return [SimpleCandidate(id=start_id + i, score=s, generation=generation)
            for i, s in enumerate(scores)]


# ---------------------------------------------------------------------------
# _score_of helper
# ---------------------------------------------------------------------------

class TestScoreOf:
    def test_direct_score_attribute(self):
        c = SimpleCandidate(id=0, score=0.75)
        assert _score_of(c) == pytest.approx(0.75)

    def test_metrics_combined_score(self):
        c = ProgramCandidate(id=0, metrics={"combined_score": 0.9, "other": 1.0})
        assert _score_of(c) == pytest.approx(0.9)

    def test_metrics_score_key(self):
        c = ProgramCandidate(id=0, metrics={"score": 0.55})
        assert _score_of(c) == pytest.approx(0.55)

    def test_metrics_fallback_average(self):
        c = ProgramCandidate(id=0, metrics={"a": 0.4, "b": 0.6})
        assert _score_of(c) == pytest.approx(0.5)

    def test_no_score_returns_zero(self):
        class Dummy:
            pass
        assert _score_of(Dummy()) == 0.0

    def test_none_score_returns_zero(self):
        c = SimpleCandidate(id=0, score=None)
        assert _score_of(c) == 0.0


# ---------------------------------------------------------------------------
# Task 8.1 — SearchStrategy is abstract
# ---------------------------------------------------------------------------

class TestSearchStrategyABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            SearchStrategy()  # type: ignore[abstract]

    def test_subclass_must_implement_select_baseline(self):
        class Partial(SearchStrategy):
            def should_parallelize(self):
                return False
        with pytest.raises(TypeError):
            Partial()

    def test_subclass_must_implement_should_parallelize(self):
        class Partial(SearchStrategy):
            def select_baseline(self, history, generation):
                return history[0]
        with pytest.raises(TypeError):
            Partial()

    def test_full_subclass_instantiates(self):
        class Full(SearchStrategy):
            def select_baseline(self, history, generation):
                return history[0]
            def should_parallelize(self):
                return False
        assert isinstance(Full(), SearchStrategy)


# ---------------------------------------------------------------------------
# Task 8.2 — GreedySearch
# ---------------------------------------------------------------------------

class TestGreedySearch:
    def test_selects_highest_score(self):
        gs = GreedySearch()
        history = _history([0.3, 0.9, 0.1, 0.7])
        best = gs.select_baseline(history, generation=1)
        assert _score_of(best) == pytest.approx(0.9)

    def test_selects_last_inserted_on_tie(self):
        """When scores are equal the most recently inserted candidate wins."""
        gs = GreedySearch()
        c1 = SimpleCandidate(id=1, score=0.5, generation=1)
        c2 = SimpleCandidate(id=2, score=0.5, generation=2)
        c3 = SimpleCandidate(id=3, score=0.5, generation=3)
        result = gs.select_baseline([c1, c2, c3], generation=1)
        assert result.id == 3  # last in list wins on tie

    def test_works_with_single_candidate(self):
        gs = GreedySearch()
        history = _history([0.42])
        assert gs.select_baseline(history, generation=1).score == pytest.approx(0.42)

    def test_works_with_program_candidate(self):
        gs = GreedySearch()
        history = [
            ProgramCandidate(id=0, metrics={"combined_score": 0.2}),
            ProgramCandidate(id=1, metrics={"combined_score": 0.8}),
        ]
        best = gs.select_baseline(history, generation=2)
        assert best.id == 1

    def test_should_not_parallelize(self):
        assert GreedySearch().should_parallelize() is False

    def test_raises_on_empty_history(self):
        with pytest.raises((ValueError, Exception)):
            GreedySearch().select_baseline([], generation=1)

    def test_deterministic_across_calls(self):
        gs = GreedySearch()
        h = _history([0.1, 0.9, 0.5])
        r1 = gs.select_baseline(h, generation=1)
        r2 = gs.select_baseline(h, generation=1)
        assert r1.id == r2.id


# ---------------------------------------------------------------------------
# Task 8.3 — BeamSearch
# ---------------------------------------------------------------------------

class TestBeamSearch:
    def test_always_selects_from_top_k(self):
        """baseline must be one of the top-K scores."""
        bs = BeamSearch(beam_width=3, random_seed=0)
        history = _history([0.1, 0.2, 0.9, 0.8, 0.7, 0.3])
        top3_scores = {0.9, 0.8, 0.7}
        for _ in range(50):
            result = bs.select_baseline(history, generation=1)
            assert _score_of(result) in top3_scores

    def test_beam_width_1_equals_greedy(self):
        bs = BeamSearch(beam_width=1, random_seed=42)
        history = _history([0.3, 0.95, 0.1])
        result = bs.select_baseline(history, generation=1)
        assert _score_of(result) == pytest.approx(0.95)

    def test_introduces_diversity_over_many_calls(self):
        """With beam_width=3 across many calls, at least 2 distinct candidates appear."""
        bs = BeamSearch(beam_width=3, random_seed=7)
        history = _history([0.9, 0.8, 0.7, 0.1, 0.2])
        chosen_ids = {bs.select_baseline(history, generation=1).id for _ in range(100)}
        # Must explore more than just the single best
        assert len(chosen_ids) >= 2

    def test_beam_width_larger_than_history(self):
        """beam_width > len(history) should not raise; use all candidates."""
        bs = BeamSearch(beam_width=100, random_seed=0)
        history = _history([0.5, 0.6])
        result = bs.select_baseline(history, generation=1)
        assert result is not None

    def test_should_parallelize_true(self):
        assert BeamSearch(beam_width=3).should_parallelize() is True

    def test_should_parallelize_beam_width_1(self):
        # Even beam_width=1 returns True (still beam-search code path)
        assert BeamSearch(beam_width=1).should_parallelize() is True

    def test_invalid_beam_width_raises(self):
        with pytest.raises(ValueError):
            BeamSearch(beam_width=0)
        with pytest.raises(ValueError):
            BeamSearch(beam_width=-1)

    def test_raises_on_empty_history(self):
        with pytest.raises((ValueError, Exception)):
            BeamSearch(beam_width=3).select_baseline([], generation=1)

    def test_repr(self):
        assert "BeamSearch" in repr(BeamSearch(beam_width=5))
        assert "5" in repr(BeamSearch(beam_width=5))

    def test_reproducible_with_seed(self):
        """Same seed → same sequence of selections."""
        h = _history([0.9, 0.85, 0.8, 0.1])
        ids_a = [BeamSearch(beam_width=3, random_seed=42).select_baseline(h, g).id
                 for g in range(1, 11)]
        ids_b = [BeamSearch(beam_width=3, random_seed=42).select_baseline(h, g).id
                 for g in range(1, 11)]
        assert ids_a == ids_b


# ---------------------------------------------------------------------------
# Task 8.4 — RandomRestartSearch
# ---------------------------------------------------------------------------

class TestRandomRestartSearch:
    def _gen0_history(self):
        """History with a generation-0 baseline plus several others."""
        baseline = SimpleCandidate(id=0, score=0.5, generation=0)
        others = [SimpleCandidate(id=i, score=0.5 + i * 0.05, generation=i)
                  for i in range(1, 6)]
        return [baseline] + others

    def test_restarts_at_interval(self):
        rrs = RandomRestartSearch(restart_interval=5)
        history = self._gen0_history()
        # Prime the strategy
        rrs.select_baseline(history, generation=1)
        # Generation 5 is the restart
        result = rrs.select_baseline(history, generation=5)
        assert result.generation == 0

    def test_greedy_between_restarts(self):
        rrs = RandomRestartSearch(restart_interval=10)
        history = self._gen0_history()
        rrs.select_baseline(history, generation=1)  # prime
        result = rrs.select_baseline(history, generation=3)
        # Should be greedy best (highest score in history)
        assert _score_of(result) == pytest.approx(max(_score_of(c) for c in history))

    def test_restart_at_every_interval(self):
        rrs = RandomRestartSearch(restart_interval=3)
        history = self._gen0_history()
        rrs.select_baseline(history, generation=1)  # prime
        for gen in [3, 6, 9, 12]:
            result = rrs.select_baseline(history, generation=gen)
            assert result.generation == 0, f"Expected restart at gen={gen}"

    def test_no_restart_on_non_interval(self):
        rrs = RandomRestartSearch(restart_interval=5)
        history = self._gen0_history()
        rrs.select_baseline(history, generation=1)
        for gen in [1, 2, 3, 4, 6, 7, 8, 9]:
            result = rrs.select_baseline(history, generation=gen)
            assert result.generation != 0 or _score_of(result) == pytest.approx(
                _score_of(max(history, key=_score_of))
            )

    def test_should_not_parallelize(self):
        assert RandomRestartSearch().should_parallelize() is False

    def test_invalid_restart_interval_raises(self):
        with pytest.raises(ValueError):
            RandomRestartSearch(restart_interval=0)

    def test_raises_on_empty_history(self):
        rrs = RandomRestartSearch(restart_interval=5)
        with pytest.raises((ValueError, Exception)):
            rrs.select_baseline([], generation=1)

    def test_fallback_when_no_gen0(self):
        """If no generation-0 candidate exists, uses first in history."""
        rrs = RandomRestartSearch(restart_interval=5)
        history = _history([0.3, 0.7, 0.9])  # all generation=1
        rrs.select_baseline(history, generation=1)
        result = rrs.select_baseline(history, generation=5)
        # Should be first candidate (fallback)
        assert result.id == history[0].id

    def test_repr(self):
        assert "RandomRestartSearch" in repr(RandomRestartSearch(restart_interval=10))
        assert "10" in repr(RandomRestartSearch(restart_interval=10))


# ---------------------------------------------------------------------------
# Task 8.5 — create_strategy factory
# ---------------------------------------------------------------------------

class TestCreateStrategy:
    # ── dict-based configs ────────────────────────────────────────────────

    def test_greedy_from_dict(self):
        s = create_strategy({"strategy": "greedy"})
        assert isinstance(s, GreedySearch)

    def test_beam_from_dict(self):
        s = create_strategy({"strategy": "beam", "beam_width": 4})
        assert isinstance(s, BeamSearch)
        assert s.beam_width == 4

    def test_random_restart_from_dict(self):
        s = create_strategy({"strategy": "random_restart", "restart_interval": 15})
        assert isinstance(s, RandomRestartSearch)
        assert s.restart_interval == 15

    def test_random_restart_default_interval(self):
        s = create_strategy({"strategy": "random_restart"})
        assert isinstance(s, RandomRestartSearch)
        assert s.restart_interval == 20

    def test_case_insensitive(self):
        assert isinstance(create_strategy({"strategy": "GREEDY"}), GreedySearch)
        assert isinstance(create_strategy({"strategy": "Beam", "beam_width": 2}), BeamSearch)

    def test_missing_strategy_defaults_to_greedy(self):
        s = create_strategy({})
        assert isinstance(s, GreedySearch)

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown search strategy"):
            create_strategy({"strategy": "genetic"})

    def test_beam_without_beam_width_raises(self):
        with pytest.raises(ValueError, match="beam_width"):
            create_strategy({"strategy": "beam"})

    # ── attribute-based configs (dataclass/namespace style) ───────────────

    def test_greedy_from_object(self):
        class Cfg:
            strategy = "greedy"
        assert isinstance(create_strategy(Cfg()), GreedySearch)

    def test_beam_from_object(self):
        class Cfg:
            strategy = "beam"
            beam_width = 6
            random_seed = None
        s = create_strategy(Cfg())
        assert isinstance(s, BeamSearch)
        assert s.beam_width == 6

    def test_random_restart_from_object(self):
        class Cfg:
            strategy = "random_restart"
            restart_interval = 8
        s = create_strategy(Cfg())
        assert isinstance(s, RandomRestartSearch)
        assert s.restart_interval == 8

    # ── strategy actually works after creation ─────────────────────────────

    def test_created_greedy_selects_best(self):
        gs = create_strategy({"strategy": "greedy"})
        h = _history([0.1, 0.9, 0.5])
        assert _score_of(gs.select_baseline(h, 1)) == pytest.approx(0.9)

    def test_created_beam_selects_from_top_k(self):
        bs = create_strategy({"strategy": "beam", "beam_width": 2})
        h = _history([0.9, 0.85, 0.1, 0.2])
        for _ in range(30):
            result = bs.select_baseline(h, 1)
            assert _score_of(result) in {0.9, 0.85}

    def test_created_random_restart_reverts(self):
        rrs = create_strategy({"strategy": "random_restart", "restart_interval": 5})
        history = [SimpleCandidate(id=0, score=0.5, generation=0),
                   SimpleCandidate(id=1, score=0.9, generation=1)]
        rrs.select_baseline(history, generation=1)
        result = rrs.select_baseline(history, generation=5)
        assert result.generation == 0


# ---------------------------------------------------------------------------
# Task 8.1 — Requirement 13.6 parallelization contract
# ---------------------------------------------------------------------------

class TestParallelizationContract:
    """Req 13.5: parallelize only for BeamSearch.
    Req 13.6: never parallelize when beam search is not active."""

    def test_greedy_no_parallelize(self):
        assert GreedySearch().should_parallelize() is False

    def test_random_restart_no_parallelize(self):
        assert RandomRestartSearch().should_parallelize() is False

    def test_beam_does_parallelize(self):
        assert BeamSearch(beam_width=3).should_parallelize() is True

    def test_factory_greedy_no_parallelize(self):
        s = create_strategy({"strategy": "greedy"})
        assert s.should_parallelize() is False

    def test_factory_random_restart_no_parallelize(self):
        s = create_strategy({"strategy": "random_restart"})
        assert s.should_parallelize() is False

    def test_factory_beam_does_parallelize(self):
        s = create_strategy({"strategy": "beam", "beam_width": 4})
        assert s.should_parallelize() is True
