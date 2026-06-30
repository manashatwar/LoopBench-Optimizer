"""
RepositoryScanner: Recursive file tree traversal with configurable ignore patterns.

Uses Python stdlib (pathlib + fnmatch) — no external dependencies required.
Implements Requirements 1.1, 1.2, 1.3, 1.5, 9.1, 9.4.
"""

import fnmatch
import logging
import time
from pathlib import Path
from typing import Dict, List

from openevolve.repo_mapper.models import FileNode, RepoMapperConfig, RepositoryMap

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default ignore patterns (Requirement 1.3)
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_PATTERNS: List[str] = [
    # Version control
    ".git",
    ".svn",
    ".hg",
    # Python artifacts
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    "dist",
    "build",
    ".eggs",
    # Virtual environments
    ".venv",
    "venv",
    "env",
    "ENV",
    # IDE / editor
    ".vscode",
    ".idea",
    "*.swp",
    "*.swo",
    "*~",
    # JavaScript dependencies
    "node_modules",
    "bower_components",
    "vendor",
    # Binary / compiled artifacts
    "*.o",
    "*.so",
    "*.dylib",
    "*.dll",
    # Logs and temporary files
    "*.log",
    "*.tmp",
    ".DS_Store",
]


class RepositoryScanner:
    """Scans a repository and builds a FileNode tree.

    Uses ``pathlib`` for traversal and ``fnmatch`` for pattern matching —
    no third-party dependencies required.

    Attributes:
        config: Configuration controlling depth, ignore patterns, etc.
        _all_ignore_patterns: Combined list of default + user-supplied patterns.

    Example::

        config = RepoMapperConfig()
        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(Path("/path/to/repo"))
        print(repo_map.to_tree_string())
    """

    def __init__(self, config: RepoMapperConfig) -> None:
        """Initialise scanner with configuration.

        Args:
            config: ``RepoMapperConfig`` controlling scanning behaviour.
        """
        self.config = config
        # Merge user patterns with defaults (user patterns take precedence)
        self._all_ignore_patterns: List[str] = (
            list(config.ignore_patterns) + DEFAULT_IGNORE_PATTERNS
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, repo_path: Path) -> RepositoryMap:
        """Recursively scan a repository and return a :class:`RepositoryMap`.

        Args:
            repo_path: Absolute path to the repository root.

        Returns:
            :class:`RepositoryMap` containing every non-ignored file/directory
            up to ``config.max_traversal_depth``.

        Raises:
            ValueError: If ``repo_path`` does not exist (Requirement 9.4).
        """
        repo_path = repo_path.resolve()
        if not repo_path.exists():
            raise ValueError(
                f"Repository path does not exist: {repo_path}"
            )
        if not repo_path.is_dir():
            raise ValueError(
                f"Repository path is not a directory: {repo_path}"
            )

        logger.info("Scanning repository: %s", repo_path)

        root_stat = repo_path.stat()
        root_node = FileNode(
            path=Path("."),
            absolute_path=repo_path,
            is_dir=True,
            size_bytes=0,
            modified_time=root_stat.st_mtime,
            depth=0,
        )

        files: Dict[Path, FileNode] = {}
        self._traverse(repo_path, repo_path, files, depth=0)

        repo_map = RepositoryMap(
            repo_path=repo_path,
            root_node=root_node,
            files=files,
            scan_timestamp=time.time(),
        )

        logger.info(
            "Scan complete: %d files/directories found in %s",
            len(files),
            repo_path,
        )
        return repo_map

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _traverse(
        self,
        repo_root: Path,
        current_dir: Path,
        files: Dict[Path, FileNode],
        depth: int,
    ) -> None:
        """Recursively traverse *current_dir* and populate *files*.

        Args:
            repo_root: Absolute path to repository root (for computing relative paths).
            current_dir: Directory currently being traversed.
            files: Accumulator dict mapping relative_path -> FileNode.
            depth: Current depth (root = 0).
        """
        # Enforce depth limit (Requirement 1.5)
        if depth >= self.config.max_traversal_depth:
            logger.debug(
                "Depth limit (%d) reached at %s; truncating traversal.",
                self.config.max_traversal_depth,
                current_dir,
            )
            return

        try:
            entries = list(current_dir.iterdir())
        except PermissionError as exc:
            # Requirement 9.1: log warning and continue
            logger.warning("Permission denied reading directory %s: %s", current_dir, exc)
            return
        except OSError as exc:
            logger.warning("OS error reading directory %s: %s", current_dir, exc)
            return

        for entry in sorted(entries):  # sorted for deterministic output
            # Compute relative path for pattern matching
            try:
                relative = entry.relative_to(repo_root)
            except ValueError:
                # Should not happen, but guard anyway (e.g. symlink outside root)
                logger.debug("Skipping entry outside repo root: %s", entry)
                continue

            # Apply ignore patterns
            if self._should_ignore(entry, relative):
                logger.debug("Ignoring: %s", relative)
                continue

            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as exc:
                # Requirement 9.1: log warning, skip entry
                logger.warning("Cannot stat %s: %s", entry, exc)
                continue

            is_symlink = entry.is_symlink()
            # is_dir: must be a real directory, not a symlink-to-dir
            is_dir = entry.is_dir() and not is_symlink

            node = FileNode(
                path=relative,
                absolute_path=entry,
                is_dir=is_dir,
                size_bytes=entry_stat.st_size,
                modified_time=entry_stat.st_mtime,
                depth=depth + 1,
            )

            files[relative] = node

            # Recurse into real directories (skip symlinks to avoid loops)
            if is_dir:
                self._traverse(repo_root, entry, files, depth + 1)

    def _should_ignore(self, absolute_path: Path, relative_path: Path) -> bool:
        """Return ``True`` if *absolute_path* / *relative_path* matches any ignore pattern.

        Matching is performed against:
        - The bare filename / directory name (``entry.name``)
        - The relative path string (for patterns like ``dist/``)
        - Each part of the relative path

        Args:
            absolute_path: Full path to the file/directory.
            relative_path: Path relative to the repository root.

        Returns:
            ``True`` if the path should be excluded from the scan.
        """
        name = absolute_path.name
        relative_str = str(relative_path)

        for pattern in self._all_ignore_patterns:
            # Strip trailing slash from directory patterns (e.g. ".git/" -> ".git")
            clean_pattern = pattern.rstrip("/")

            # Match against filename alone
            if fnmatch.fnmatch(name, clean_pattern):
                return True

            # Match against relative path string (handles nested patterns)
            if fnmatch.fnmatch(relative_str, clean_pattern):
                return True

            # Match any component in the path (e.g. "node_modules" anywhere)
            for part in relative_path.parts:
                if fnmatch.fnmatch(part, clean_pattern):
                    return True

        return False
