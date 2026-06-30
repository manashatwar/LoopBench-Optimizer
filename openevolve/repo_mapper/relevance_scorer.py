"""
RelevanceScorer: Score repository files by relevance to an optimization target.

Task 3.4 — THE SECRET SAUCE. Custom logic for understanding which files
matter most when optimising a specific target file.

Scoring formula (weights from design.md §5):
    total = 0.50 * direct_import
          + 0.30 * directory_proximity
          + 0.15 * reverse_import
          + 0.05 * name_similarity

Component definitions:
    direct_import   — hop distance from target's import graph
                      1.0 (direct), 0.8 (2-hop), 0.5 (3-hop), 0.0 (unreachable)
    directory_proximity — tree distance
                      1.0 (same dir), 0.8 (parent/child), 0.5 (sibling dir),
                      0.2 (same top-level dir), 0.0 (elsewhere)
    reverse_import  — does this file import the target?
                      0.6 (yes), 0.0 (no)
    name_similarity — SequenceMatcher ratio on file stems

All scores clamped to [0.0, 1.0].

Implements Requirements: 2.4, 2.5, 2.6, 2.7
"""

import logging
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional

from openevolve.repo_mapper.models import (
    FileDescriptor,
    ImportGraph,
    RelevanceScore,
    RepoMapperConfig,
    RepositoryMap,
)

logger = logging.getLogger(__name__)


class RelevanceScorer:
    """Score all repository files by relevance to a target optimization file.

    This is **the value proposition** of the repo-mapper: understanding which
    files are relevant to an optimization target, not just which files exist.

    Attributes:
        config: :class:`~models.RepoMapperConfig` controlling scoring behaviour.

    Weights::

        WEIGHT_DIRECT_IMPORT      = 0.50
        WEIGHT_DIRECTORY_PROXIMITY = 0.30
        WEIGHT_REVERSE_IMPORT     = 0.15
        WEIGHT_NAME_SIMILARITY    = 0.05

    Example::

        scorer = RelevanceScorer(config)
        scores = scorer.score_files(
            target_file=Path("src/main.py"),
            repo_map=repo_map,
            import_graph=graph,
        )
        # scores[0] is the most relevant file
        for s in scores[:5]:
            print(f"{s.file_path}  total={s.total_score:.3f}")
    """

    # Scoring weights — must sum to 1.0
    WEIGHT_DIRECT_IMPORT: float = 0.50
    WEIGHT_DIRECTORY_PROXIMITY: float = 0.30
    WEIGHT_REVERSE_IMPORT: float = 0.15
    WEIGHT_NAME_SIMILARITY: float = 0.05

    def __init__(self, config: RepoMapperConfig) -> None:
        """Initialise with configuration.

        Args:
            config: ``RepoMapperConfig`` instance.
        """
        self.config = config

    # ------------------------------------------------------------------
    # Public API (Task 3.4)
    # ------------------------------------------------------------------

    def score_files(
        self,
        target_file: Path,
        repo_map: RepositoryMap,
        import_graph: ImportGraph,
        descriptors: Optional[Dict[Path, FileDescriptor]] = None,
    ) -> List[RelevanceScore]:
        """Score every non-target file in the repository.

        Args:
            target_file: Relative path (from repo root) of the file being
                optimised.  May also be an absolute path — it will be
                converted to a relative path automatically.
            repo_map: Repository structure returned by :class:`RepositoryScanner`.
            import_graph: Dependency graph returned by :class:`ImportAnalyzer`.
            descriptors: Optional mapping of ``rel_path -> FileDescriptor``.
                Reserved for future extensions (not used in scoring currently).

        Returns:
            List of :class:`~models.RelevanceScore` objects, sorted by
            ``total_score`` descending.  The target file itself is excluded.
        """
        # Normalise target to a relative path
        target = self._normalise_path(target_file, repo_map)

        scores: List[RelevanceScore] = []

        for rel_path, node in repo_map.files.items():
            # Skip directories and the target file itself
            if node.is_dir or rel_path == target:
                continue
            score = self._score_single(rel_path, target, import_graph)
            scores.append(score)

        # Sort descending by total score
        scores.sort(key=lambda s: s.total_score, reverse=True)

        logger.debug(
            "RelevanceScorer: scored %d files for target %s; "
            "top score=%.3f",
            len(scores),
            target,
            scores[0].total_score if scores else 0.0,
        )
        return scores

    # ------------------------------------------------------------------
    # Private: single-file scoring
    # ------------------------------------------------------------------

    def _score_single(
        self,
        file_path: Path,
        target: Path,
        import_graph: ImportGraph,
    ) -> RelevanceScore:
        """Compute a :class:`RelevanceScore` for one file."""
        direct = self._score_direct_imports(file_path, target, import_graph)
        reverse = self._score_reverse_imports(file_path, target, import_graph)
        proximity = self._score_directory_proximity(file_path, target)
        name_sim = self._score_name_similarity(file_path, target)

        total = (
            self.WEIGHT_DIRECT_IMPORT * direct
            + self.WEIGHT_DIRECTORY_PROXIMITY * proximity
            + self.WEIGHT_REVERSE_IMPORT * reverse
            + self.WEIGHT_NAME_SIMILARITY * name_sim
        )
        total = max(0.0, min(1.0, total))

        return RelevanceScore(
            file_path=file_path,
            total_score=total,
            direct_import_score=direct,
            reverse_import_score=reverse,
            directory_proximity_score=proximity,
            name_similarity_score=name_sim,
        )

    # ------------------------------------------------------------------
    # Component scorers (Task 3.4)
    # ------------------------------------------------------------------

    def _score_direct_imports(
        self,
        file_path: Path,
        target: Path,
        graph: ImportGraph,
    ) -> float:
        """Score based on import-graph hop distance from *target* to *file_path*.

        - 1.0  if target directly imports file_path (1 hop)
        - 0.8  if reachable in 2 hops
        - 0.5  if reachable in 3 hops
        - 0.0  otherwise

        Uses BFS so that the cheapest hop is always found first.

        Requirement 2.4
        """
        visited: set[Path] = {target}
        frontier = graph.get_direct_imports(target)

        # 1 hop — direct
        if file_path in frontier:
            return 1.0

        # 2 hops
        visited.update(frontier)
        next_frontier: set[Path] = set()
        for mid in frontier:
            neighbours = graph.get_direct_imports(mid) - visited
            if file_path in neighbours:
                return 0.8
            next_frontier.update(neighbours)

        # 3 hops
        visited.update(next_frontier)
        for mid2 in next_frontier:
            if file_path in graph.get_direct_imports(mid2) - visited:
                return 0.5

        return 0.0

    def _score_directory_proximity(
        self,
        file_path: Path,
        target: Path,
    ) -> float:
        """Score based on directory-tree distance.

        - 1.0  same directory
        - 0.8  direct parent–child relationship
        - 0.5  sibling directories (same grandparent)
        - 0.2  same top-level directory
        - 0.0  completely different subtree

        Requirement 2.5
        """
        fp = file_path.parent
        tp = target.parent

        # Same directory
        if fp == tp:
            return 1.0

        # Parent / child: one is an ancestor of the other
        try:
            target.relative_to(file_path.parent)  # target is inside file's dir
            return 0.8
        except ValueError:
            pass
        try:
            file_path.relative_to(target.parent)  # file is inside target's dir
            return 0.8
        except ValueError:
            pass

        # Sibling: same grandparent directory
        if fp.parent == tp.parent and fp != tp:
            return 0.5

        # Same top-level directory component
        if file_path.parts and target.parts and file_path.parts[0] == target.parts[0]:
            return 0.2

        return 0.0

    def _score_reverse_imports(
        self,
        file_path: Path,
        target: Path,
        graph: ImportGraph,
    ) -> float:
        """Score if *file_path* imports *target* (reverse dependency).

        - 0.6 if file_path is a reverse importer of target
        - 0.0 otherwise

        Requirement 2.6
        """
        reverse_importers = graph.get_reverse_imports(target)
        return 0.6 if file_path in reverse_importers else 0.0

    def _score_name_similarity(
        self,
        file_path: Path,
        target: Path,
    ) -> float:
        """Score based on filename stem similarity using SequenceMatcher.

        Perfect match → 1.0; completely different names → 0.0.

        Requirement 2.7
        """
        a = file_path.stem.lower()
        b = target.stem.lower()
        if a == b:
            return 1.0
        ratio = SequenceMatcher(None, a, b).ratio()
        return float(ratio)

    # ------------------------------------------------------------------
    # Private: path normalisation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_path(target_file: Path, repo_map: RepositoryMap) -> Path:
        """Convert *target_file* to a path relative to ``repo_map.repo_path``.

        Args:
            target_file: Absolute or relative path to the target file.
            repo_map: Repository map (provides ``repo_path``).

        Returns:
            Relative :class:`Path` from the repository root.
        """
        if target_file.is_absolute():
            try:
                return target_file.relative_to(repo_map.repo_path)
            except ValueError:
                # Absolute path outside repo — return as-is and let scoring
                # produce zeros (no matches)
                return target_file
        return target_file
