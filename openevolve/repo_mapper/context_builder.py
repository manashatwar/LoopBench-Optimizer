"""
ContextBuilder: Generate token-budget-aware context maps for LLM prompts.

Task 5.2 — CUSTOM LOGIC (CRITICAL FOR LLM EFFECTIVENESS).
Precise token control ensures we include maximum relevant context without
overwhelming the LLM prompt.

Implements Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from openevolve.repo_mapper.models import (
    ContextMap,
    FileDescriptor,
    RelevanceScore,
    RepoMapperConfig,
    RepositoryMap,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Build context maps within configured token budgets.

    This is **critical for LLM effectiveness** — if context exceeds the token
    budget, the LLM either truncates or becomes less effective. The builder
    intelligently selects which files to include and how much detail to show.

    Attributes:
        config: :class:`~models.RepoMapperConfig` controlling token budget
            and file selection.

    Strategy:
        1. Always include target file (reserve tokens for this)
        2. Add relevant files in descending score order
        3. Stop when token budget exhausted
        4. Truncate descriptions if needed
        5. Generate filtered tree showing only included files

    Example:
        >>> builder = ContextBuilder(config)
        >>> context = builder.build(
        ...     target_file=Path("src/main.py"),
        ...     repo_map=repo_map,
        ...     scored_files=scores[:20],
        ...     descriptors=descriptors,
        ... )
        >>> print(context.to_prompt_section())
    """

    def __init__(self, config: RepoMapperConfig) -> None:
        """Initialise with configuration.

        Args:
            config: ``RepoMapperConfig`` instance with ``token_budget`` set.
        """
        self.config = config

    # ------------------------------------------------------------------
    # Public API (Task 5.2)
    # ------------------------------------------------------------------

    def build(
        self,
        target_file: Path,
        repo_map: RepositoryMap,
        scored_files: List[RelevanceScore],
        descriptors: Dict[Path, FileDescriptor],
    ) -> ContextMap:
        """Build a context map within the configured token budget.

        Args:
            target_file: Relative path of the file being optimised.
            repo_map: Repository structure from :class:`RepositoryScanner`.
            scored_files: Files sorted by relevance (descending), from
                :class:`RelevanceScorer`.
            descriptors: Mapping ``rel_path -> FileDescriptor``, from
                :class:`FileAnalyzer`.

        Returns:
            :class:`~models.ContextMap` formatted for LLM prompt insertion,
            guaranteed to fit within ``config.token_budget``.

        Raises:
            ValueError: If ``target_file`` not found in ``descriptors``.
        """
        # Normalise target to relative path
        target = self._normalise_path(target_file, repo_map)

        if target not in descriptors:
            raise ValueError(
                f"Target file {target} not found in descriptors. "
                "Did you forget to analyse it?"
            )

        target_descriptor = descriptors[target]

        # Reserve tokens for target file descriptor (always include)
        target_token_budget = int(self.config.token_budget * 0.7)
        target_descriptor = self._fit_descriptor_to_tokens(
            target_descriptor, target_token_budget
        )
        target_tokens = self._estimate_tokens(target_descriptor.to_string())
        available = self.config.token_budget - target_tokens

        if target_tokens > self.config.token_budget:
            logger.warning(
                "Target descriptor exceeds token budget (%d > %d). "
                "Truncating to fit.",
                target_tokens,
                self.config.token_budget,
            )
            target_descriptor = self._fit_descriptor_to_tokens(
                target_descriptor, self.config.token_budget - 50
            )
            target_tokens = self._estimate_tokens(target_descriptor.to_string())
            available = max(0, self.config.token_budget - target_tokens)

        # Sort by relevance (descending) — Property 10
        ranked = sorted(
            scored_files,
            key=lambda s: s.total_score,
            reverse=True,
        )

        # Greedily add relevant files until budget or max count exhausted
        relevant: List[Tuple[Path, FileDescriptor, float]] = []
        included_paths: Set[Path] = {target}
        max_files = self.config.max_relevant_files

        for score_obj in ranked:
            if len(relevant) >= max_files:
                break
            if score_obj.file_path not in descriptors:
                continue
            if score_obj.file_path == target:
                continue

            desc = descriptors[score_obj.file_path]
            text = desc.to_string(include_score=score_obj.total_score)
            tokens = self._estimate_tokens(text)

            # Reserve ~100 tokens for tree overhead
            if tokens + 100 > available:
                logger.debug(
                    "Token budget exhausted after %d files. "
                    "Remaining budget: %d tokens.",
                    len(relevant),
                    available,
                )
                break

            relevant.append((score_obj.file_path, desc, score_obj.total_score))
            included_paths.add(score_obj.file_path)
            available -= tokens

        # Build filtered tree
        tree_str = self._build_filtered_tree(repo_map, included_paths, target)
        tree_tokens = self._estimate_tokens(tree_str)

        # Final token count (target + relevant files + tree)
        total_tokens = (
            self._estimate_tokens(target_descriptor.to_string())
            + sum(
                self._estimate_tokens(d.to_string(include_score=s))
                for _, d, s in relevant
            )
            + tree_tokens
        )

        logger.info(
            "ContextMap built: target=%s, relevant_files=%d, tokens=%d/%d",
            target,
            len(relevant),
            total_tokens,
            self.config.token_budget,
        )

        return ContextMap(
            target_file=target,
            target_descriptor=target_descriptor,
            relevant_files=relevant,
            repository_tree=tree_str,
            token_count=total_tokens,
        )

    # ------------------------------------------------------------------
    # Token estimation (Task 5.2)
    # ------------------------------------------------------------------

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string.

        Uses rough heuristic: 1 token ≈ 4 characters (configurable via
        ``estimate_tokens_per_char``).

        Args:
            text: Input text.

        Returns:
            Estimated token count.
        """
        return int(len(text) * self.config.estimate_tokens_per_char)

    # ------------------------------------------------------------------
    # Filtered tree generation (Task 5.2)
    # ------------------------------------------------------------------

    def _build_filtered_tree(
        self,
        repo_map: RepositoryMap,
        relevant_files: Set[Path],
        target_file: Optional[Path] = None,
    ) -> str:
        """Build tree showing only relevant files and their ancestors.

        Args:
            repo_map: Full repository structure.
            relevant_files: Set of file paths to include.

        Returns:
            Indented tree string (similar to ``RepositoryMap.to_tree_string()``
            but filtered).

        Example output::

            repo_root/
              src/
                main.py  <- Target
                utils.py
              tests/
                test_main.py
        """
        # Collect all ancestor directories of relevant files
        all_paths: Set[Path] = set(relevant_files)
        for file_path in relevant_files:
            # Add all parents up to repo root
            current = file_path.parent
            while current != Path("."):
                all_paths.add(current)
                if not current.parts:
                    break
                current = current.parent

        # Sort by path for consistent ordering
        sorted_paths = sorted(all_paths, key=lambda p: (len(p.parts), str(p)))

        lines: List[str] = [f"{repo_map.repo_path.name or 'root'}/"]

        for path in sorted_paths:
            node = repo_map.files.get(path)
            if node is None:
                # Directory not explicitly in files dict — infer it
                depth = len(path.parts)
                indent = "  " * depth
                name = path.name + "/"
                lines.append(f"{indent}{name}")
            else:
                indent = "  " * node.depth
                name = node.path.name
                if node.is_dir:
                    name += "/"
                if not node.is_dir and node.path in relevant_files:
                    if target_file is not None and node.path == target_file:
                        name += "  <- Target"
                    else:
                        name += "  <- Relevant"
                lines.append(f"{indent}{name}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Descriptor truncation helper (Task 5.2)
    # ------------------------------------------------------------------

    def _truncate_descriptor(
        self,
        descriptor: FileDescriptor,
        max_tokens: int,
    ) -> FileDescriptor:
        """Truncate a FileDescriptor summary to fit within token budget.

        Args:
            descriptor: Original descriptor.
            max_tokens: Maximum tokens allowed.

        Returns:
            New :class:`FileDescriptor` with truncated summary.
        """
        max_chars = max(1, int(max_tokens / self.config.estimate_tokens_per_char))

        if len(descriptor.summary) > max_chars:
            truncated_summary = descriptor.summary[: max_chars - 3] + "..."
        else:
            truncated_summary = descriptor.summary

        return FileDescriptor(
            file_path=descriptor.file_path,
            role=descriptor.role,
            summary=truncated_summary,
            classes=descriptor.classes,
            functions=descriptor.functions,
            has_main=descriptor.has_main,
            loc=descriptor.loc,
        )

    def _fit_descriptor_to_tokens(
        self,
        descriptor: FileDescriptor,
        max_tokens: int,
    ) -> FileDescriptor:
        """Shrink a descriptor until its ``to_string()`` fits within ``max_tokens``."""
        if max_tokens <= 0:
            return FileDescriptor(
                file_path=descriptor.file_path,
                role=descriptor.role,
                summary="...",
                loc=descriptor.loc,
            )

        fitted = descriptor
        if self._estimate_tokens(fitted.to_string()) <= max_tokens:
            return fitted

        # Drop optional structure fields first
        if fitted.classes or fitted.functions or fitted.has_main:
            fitted = FileDescriptor(
                file_path=fitted.file_path,
                role=fitted.role,
                summary=fitted.summary,
                loc=fitted.loc,
            )

        if self._estimate_tokens(fitted.to_string()) <= max_tokens:
            return fitted

        # Truncate summary to fit remaining budget
        header_tokens = self._estimate_tokens(
            f"**{fitted.file_path.name}** [role: {fitted.role}, loc: {fitted.loc}]\n"
        )
        summary_budget = max(1, max_tokens - header_tokens)
        return self._truncate_descriptor(fitted, summary_budget)

    # ------------------------------------------------------------------
    # Path normalisation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_path(target_file: Path, repo_map: RepositoryMap) -> Path:
        """Convert absolute path to relative (if needed)."""
        if target_file.is_absolute():
            try:
                return target_file.relative_to(repo_map.repo_path)
            except ValueError:
                return target_file
        return target_file
