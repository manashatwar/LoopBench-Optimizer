"""
Search strategy abstraction layer for OptimizerLoop.

Provides a pluggable interface that determines how the optimizer selects the
baseline candidate for each generation.  Concrete strategies supplied:

  AutoEscalationSearch — greedy by default, deterministically escalates to
                         restart/diversify on a plateau (the `loopbench` default)
  GreedySearch         — always picks the single best candidate
  BeamSearch           — maintains top-K and samples randomly from them
  RandomRestartSearch  — periodically reverts to the original baseline

A factory function ``create_strategy`` instantiates the correct class from a
plain-dict or dataclass-like config.

Requirements: 13.1 – 13.7
"""

from __future__ import annotations

import logging
import random
from abc import ABC, abstractmethod
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Candidate-like protocol
# ---------------------------------------------------------------------------

class _HasScore:
    """Minimal duck-type expected from a candidate object."""
    score: Optional[float]


def _score_of(candidate: Any) -> float:
    """Return a comparable float score for *candidate*.

    Handles dict candidates (OptimizerLoop stores candidates as dicts), direct
    ``.score`` attributes, and ``.metrics`` dicts (for OpenEvolve ``Program``
    objects).
    """
    # Dict candidate (OptimizerLoop stores each candidate as a dict)
    if isinstance(candidate, dict):
        score = candidate.get("score")
        if isinstance(score, (int, float)):
            return float(score)
        metrics = candidate.get("metrics")
        if isinstance(metrics, dict):
            for key in ("combined_score", "score"):
                val = metrics.get(key)
                if isinstance(val, (int, float)):
                    return float(val)
            numeric = [v for v in metrics.values() if isinstance(v, (int, float))]
            if numeric:
                return sum(numeric) / len(numeric)
        return 0.0

    # Direct score attribute (Candidate dataclass / objects)
    score = getattr(candidate, "score", None)
    if isinstance(score, (int, float)):
        return float(score)

    # OpenEvolve Program uses a metrics dict
    metrics = getattr(candidate, "metrics", None)
    if isinstance(metrics, dict):
        for key in ("combined_score", "score"):
            val = metrics.get(key)
            if isinstance(val, (int, float)):
                return float(val)
        # Fallback: average of numeric metric values
        numeric = [v for v in metrics.values() if isinstance(v, (int, float))]
        if numeric:
            return sum(numeric) / len(numeric)

    return 0.0


def _generation_of(candidate: Any) -> Optional[int]:
    """Return the candidate's generation index if available, else ``None``."""
    if isinstance(candidate, dict):
        gen = candidate.get("generation")
    else:
        gen = getattr(candidate, "generation", None)
    return gen if isinstance(gen, int) else None


# ---------------------------------------------------------------------------
# Abstract base class  (Task 8.1)
# ---------------------------------------------------------------------------

class SearchStrategy(ABC):
    """Abstract strategy for selecting the baseline candidate.

    The OptimizerLoop calls :meth:`select_baseline` after every generation to
    decide which candidate becomes the starting point for the next generation.

    Requirements: 13.1, 13.7
    """

    @abstractmethod
    def select_baseline(
        self,
        history: Sequence[Any],
        generation: int,
    ) -> Any:
        """Choose the baseline candidate for the next generation.

        Args:
            history: All candidates evaluated so far (any order).
            generation: The generation that just completed (1-based).

        Returns:
            The candidate to use as the baseline.
        """

    @abstractmethod
    def should_parallelize(self) -> bool:
        """Return True when this strategy supports parallel candidate evaluation.

        Requirements: 13.5, 13.6
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ---------------------------------------------------------------------------
# GreedySearch  (Task 8.2)
# ---------------------------------------------------------------------------

class GreedySearch(SearchStrategy):
    """Always use the single best-scoring candidate as baseline.

    The simplest strategy: greedily follows the highest score found so far.
    When multiple candidates share the identical top score the most recently
    created one is preferred (stable sort descending by score, then index).

    Requirements: 13.1, 13.7
    """

    def select_baseline(
        self,
        history: Sequence[Any],
        generation: int,
    ) -> Any:
        if not history:
            raise ValueError("select_baseline called with empty history")

        # Stable sort keeps the last-inserted candidate first among ties
        # (sorted is stable in Python, so reversing by index achieves this)
        best = max(
            reversed(list(history)),
            key=_score_of,
        )
        logger.debug(
            "GreedySearch gen=%d → baseline score=%.4f",
            generation,
            _score_of(best),
        )
        return best

    def should_parallelize(self) -> bool:
        return False  # Req 13.6: no parallelization when not beam search


# ---------------------------------------------------------------------------
# BeamSearch  (Task 8.3)
# ---------------------------------------------------------------------------

class BeamSearch(SearchStrategy):
    """Maintain the top-K candidates and explore from each.

    At each generation the strategy keeps a "beam" of the ``beam_width``
    highest-scoring candidates.  The baseline is drawn uniformly at random from
    this beam, introducing diversity while still favouring high performers.

    When ``beam_width > 1`` the strategy signals that parallel evaluation of
    multiple candidates is beneficial (Req 13.5).  When ``beam_width == 1`` it
    degenerates to greedy search but still reports ``should_parallelize=True``
    to remain consistent with the beam-search code path.

    Requirements: 13.2, 13.5, 13.6
    """

    def __init__(
        self,
        beam_width: int = 5,
        *,
        random_seed: Optional[int] = None,
    ) -> None:
        if beam_width < 1:
            raise ValueError(f"beam_width must be >= 1, got {beam_width}")
        self.beam_width = beam_width
        self._rng = random.Random(random_seed)

    def select_baseline(
        self,
        history: Sequence[Any],
        generation: int,
    ) -> Any:
        if not history:
            raise ValueError("select_baseline called with empty history")

        # Select top-K (no fewer than available)
        k = min(self.beam_width, len(history))
        top_k = sorted(history, key=_score_of)[-k:]
        chosen = self._rng.choice(top_k)
        logger.debug(
            "BeamSearch gen=%d beam_width=%d → baseline score=%.4f (from %d candidates)",
            generation,
            self.beam_width,
            _score_of(chosen),
            k,
        )
        return chosen

    def should_parallelize(self) -> bool:
        # Req 13.5: parallelize when beam search is active and hardware permits
        return True

    def __repr__(self) -> str:
        return f"BeamSearch(beam_width={self.beam_width})"


# ---------------------------------------------------------------------------
# RandomRestartSearch  (Task 8.4)
# ---------------------------------------------------------------------------

class RandomRestartSearch(SearchStrategy):
    """Periodically restart from the original baseline to escape local optima.

    Every ``restart_interval`` generations the strategy returns the *original*
    baseline (generation-0 candidate) instead of the current best.  In
    between restarts it behaves like :class:`GreedySearch`.

    Requirements: 13.3, 13.7
    """

    def __init__(self, restart_interval: int = 20) -> None:
        if restart_interval < 1:
            raise ValueError(
                f"restart_interval must be >= 1, got {restart_interval}"
            )
        self.restart_interval = restart_interval
        self._original_baseline: Optional[Any] = None

    def select_baseline(
        self,
        history: Sequence[Any],
        generation: int,
    ) -> Any:
        if not history:
            raise ValueError("select_baseline called with empty history")

        # Capture original baseline on first call
        if self._original_baseline is None:
            # The original baseline has generation 0; fall back to first entry
            gen0 = [c for c in history if getattr(c, "generation", None) == 0]
            self._original_baseline = gen0[0] if gen0 else list(history)[0]

        # On restart generations, return to original baseline
        if generation % self.restart_interval == 0:
            logger.info(
                "RandomRestartSearch: restart at generation %d → original baseline",
                generation,
            )
            return self._original_baseline

        # Otherwise: greedy selection
        best = max(reversed(list(history)), key=_score_of)
        logger.debug(
            "RandomRestartSearch gen=%d → greedy baseline score=%.4f",
            generation,
            _score_of(best),
        )
        return best

    def should_parallelize(self) -> bool:
        return False  # Req 13.6

    def __repr__(self) -> str:
        return f"RandomRestartSearch(restart_interval={self.restart_interval})"


# ---------------------------------------------------------------------------
# AutoEscalationSearch  (self-tuning, deterministic)
# ---------------------------------------------------------------------------

class AutoEscalationSearch(SearchStrategy):
    """Deterministic self-tuning strategy: greedy by default, escalate on plateau.

    Starts greedy (fastest, cheapest). When the run stops improving, it escalates
    through exploration tiers based purely on ``stall`` — the number of
    generations since the last strict improvement in best score. No extra LLM
    calls, fully reproducible:

      * ``stall < restart_patience``                     → **greedy**
        (build on the best-so-far)
      * ``restart_patience <= stall < diversify_patience`` → **restart**
        (revert to the original generation-0 baseline for a fresh mutation path)
      * ``stall >= diversify_patience``                   → **diversify**
        (rotate deterministically through the top-K candidates)

    Escalation only steers *exploration*; the OptimizerLoop still reports the
    highest-scoring candidate as best, so ``auto`` never regresses the result
    below plain greedy.

    Requirements: 13.1, 13.7
    """

    def __init__(
        self,
        restart_patience: int = 2,
        diversify_patience: int = 4,
        beam_width: int = 3,
    ) -> None:
        if restart_patience < 1:
            raise ValueError(f"restart_patience must be >= 1, got {restart_patience}")
        if diversify_patience <= restart_patience:
            raise ValueError(
                "diversify_patience must be > restart_patience (got "
                f"diversify_patience={diversify_patience}, restart_patience={restart_patience})"
            )
        if beam_width < 1:
            raise ValueError(f"beam_width must be >= 1, got {beam_width}")
        self.restart_patience = restart_patience
        self.diversify_patience = diversify_patience
        self.beam_width = beam_width
        self._original_baseline: Optional[Any] = None
        self._last_tier: Optional[str] = None

    @staticmethod
    def _stall(history: Sequence[Any]) -> int:
        """Number of entries since the last strict improvement in best score."""
        best = float("-inf")
        last_improve_idx = 0
        for i, candidate in enumerate(history):
            score = _score_of(candidate)
            if score > best:
                best = score
                last_improve_idx = i
        return (len(history) - 1) - last_improve_idx

    def select_baseline(
        self,
        history: Sequence[Any],
        generation: int,
    ) -> Any:
        if not history:
            raise ValueError("select_baseline called with empty history")

        hist = list(history)
        if self._original_baseline is None:
            gen0 = [c for c in hist if _generation_of(c) == 0]
            self._original_baseline = gen0[0] if gen0 else hist[0]

        stall = self._stall(hist)

        if stall < self.restart_patience:
            tier = "greedy"
            chosen = max(reversed(hist), key=_score_of)
        elif stall < self.diversify_patience:
            tier = "restart"
            chosen = self._original_baseline
        else:
            tier = "diversify"
            k = min(self.beam_width, len(hist))
            top_k = sorted(hist, key=_score_of)[-k:]
            idx = (stall - self.diversify_patience) % k
            chosen = top_k[idx]

        if tier != self._last_tier:
            logger.info(
                "AutoEscalationSearch gen=%d stall=%d → '%s' tier",
                generation, stall, tier,
            )
            self._last_tier = tier
        else:
            logger.debug(
                "AutoEscalationSearch gen=%d stall=%d tier=%s score=%.4f",
                generation, stall, tier, _score_of(chosen),
            )
        return chosen

    def should_parallelize(self) -> bool:
        return False

    def __repr__(self) -> str:
        return (
            f"AutoEscalationSearch(restart_patience={self.restart_patience}, "
            f"diversify_patience={self.diversify_patience}, "
            f"beam_width={self.beam_width})"
        )


# ---------------------------------------------------------------------------
# Strategy factory  (Task 8.5)
# ---------------------------------------------------------------------------

def create_strategy(config: Any) -> SearchStrategy:
    """Instantiate a :class:`SearchStrategy` from a config object or dict.

    Supported ``strategy`` values (case-insensitive):
      - ``"auto"``           → :class:`AutoEscalationSearch` (greedy, escalates on plateau)
      - ``"greedy"``         → :class:`GreedySearch`
      - ``"beam"``           → :class:`BeamSearch` (requires ``beam_width``)
      - ``"random_restart"`` → :class:`RandomRestartSearch` (requires ``restart_interval``)

    Args:
        config: Any object or dict with a ``strategy`` field and optional
                ``beam_width`` / ``restart_interval`` / ``random_seed`` fields.
                Attribute access and dict-key access are both supported.

    Returns:
        Concrete :class:`SearchStrategy` instance.

    Raises:
        ValueError: When ``strategy`` is unknown or a required parameter is missing.

    Requirements: 13.4
    """

    def _get(key: str, default: Any = None) -> Any:
        if isinstance(config, dict):
            return config.get(key, default)
        return getattr(config, key, default)

    strategy_name = (_get("strategy") or "greedy").lower().strip()

    if strategy_name == "greedy":
        instance = GreedySearch()

    elif strategy_name == "auto":
        instance = AutoEscalationSearch(
            restart_patience=int(_get("restart_patience", 2)),
            diversify_patience=int(_get("diversify_patience", 4)),
            beam_width=int(_get("beam_width", 3)),
        )

    elif strategy_name == "beam":
        beam_width = _get("beam_width")
        if beam_width is None:
            raise ValueError(
                "BeamSearch requires 'beam_width' in config"
            )
        random_seed = _get("random_seed")
        instance = BeamSearch(beam_width=int(beam_width), random_seed=random_seed)

    elif strategy_name in ("random_restart", "randomrestart"):
        restart_interval = _get("restart_interval", 20)
        instance = RandomRestartSearch(restart_interval=int(restart_interval))

    else:
        raise ValueError(
            f"Unknown search strategy '{strategy_name}'. "
            "Supported: 'auto', 'greedy', 'beam', 'random_restart'."
        )

    logger.info("Created search strategy: %r", instance)
    return instance
