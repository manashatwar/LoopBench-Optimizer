"""
ImportAnalyzer: Build import dependency graphs from repository Python files.

Task 2.2 + 2.3 — Uses the parser_interface (ast-based) for import extraction
and a simplified heuristic for import resolution (80/20 rule: no full Python
import machinery, just path matching against files present in the repo).

Design decisions (from tasks.md):
- Tool-based extraction: uses parser_interface.extract_imports() (not raw AST loops)
- Simple resolution: If import maps to a .py file in the repo → resolved. Otherwise → external.
- Skip complex edge cases: virtualenvs, editable installs, namespace packages → all "external"
- Error handling: unparseable/unreadable files → log warning and skip (Req 9.2)
- Circular imports: handled naturally since we don't recurse — we analyse each file once

Implements Requirements: 2.1, 2.2, 2.3, 8.1, 8.2, 8.3, 9.2, 9.3, 9.5
"""

import logging
from pathlib import Path
from typing import List, Optional

from openevolve.repo_mapper.models import (
    ImportGraph,
    ImportRelation,
    RepoMapperConfig,
    RepositoryMap,
)
from openevolve.repo_mapper.parser_interface import ImportInfo, extract_imports

logger = logging.getLogger(__name__)


class ImportAnalyzer:
    """Builds an import dependency graph from repository Python files.

    Uses :func:`~openevolve.repo_mapper.parser_interface.extract_imports` for
    reliable import extraction, then applies a fast heuristic resolver that
    maps module names to files present in the ``RepositoryMap``.

    The resolver deliberately does **not** attempt to replicate the full Python
    import machinery (no ``sys.path`` manipulation, no virtualenv inspection,
    no namespace package traversal).  It achieves ~80 % accuracy with ~20 %
    of the effort and that is *good enough* for relevance scoring.

    Attributes:
        config: ``RepoMapperConfig`` controlling analysis behaviour.

    Example::

        scanner = RepositoryScanner(config)
        repo_map = scanner.scan(Path("/my/repo"))

        analyzer = ImportAnalyzer(config)
        graph = analyzer.analyze(repo_map)

        # Which files does "src/main.py" directly import?
        deps = graph.get_direct_imports(Path("src/main.py"))
    """

    def __init__(self, config: RepoMapperConfig) -> None:
        """Initialise the analyzer.

        Args:
            config: ``RepoMapperConfig`` instance.
        """
        self.config = config

    # ------------------------------------------------------------------
    # Public API (Task 2.2)
    # ------------------------------------------------------------------

    def analyze(
        self,
        repo_map: RepositoryMap,
        target_file: Optional[Path] = None,
    ) -> ImportGraph:
        """Build an import graph for every Python file in the repository.

        Iterates over all ``.py`` files in *repo_map*, extracts their import
        statements using the parser interface, resolves them to files in the
        repository (when possible), and records every relationship in an
        :class:`~openevolve.repo_mapper.models.ImportGraph`.

        Args:
            repo_map: Repository structure from :class:`RepositoryScanner`.
            target_file: Optional hint (relative path) for the file being
                optimised.  Not used in the current implementation but kept
                for API compatibility with future optimisations.

        Returns:
            :class:`~openevolve.repo_mapper.models.ImportGraph` with all
            intra-repository import relationships populated.
        """
        graph = ImportGraph()

        python_files = [
            (rel_path, node)
            for rel_path, node in repo_map.files.items()
            if not node.is_dir and rel_path.suffix == ".py"
        ]

        logger.info(
            "ImportAnalyzer: analysing %d Python files in %s",
            len(python_files),
            repo_map.repo_path,
        )

        for rel_path, node in python_files:
            self._analyze_file(rel_path, node.absolute_path, repo_map, graph)

        logger.info(
            "ImportAnalyzer: graph complete — %d relations across %d files",
            len(graph.relations),
            len(graph.get_all_files()),
        )
        return graph

    # ------------------------------------------------------------------
    # Private: per-file analysis (Task 2.2)
    # ------------------------------------------------------------------

    def _analyze_file(
        self,
        rel_path: Path,
        abs_path: Path,
        repo_map: RepositoryMap,
        graph: ImportGraph,
    ) -> None:
        """Extract imports from one file and add relations to *graph*.

        Requirement 9.2: log warning and skip on parse/IO error.
        """
        try:
            imports: List[ImportInfo] = extract_imports(abs_path)
        except Exception as exc:
            # Requirement 9.2: log and skip
            logger.warning(
                "ImportAnalyzer: failed to extract imports from %s: %s",
                rel_path,
                exc,
            )
            return

        for imp in imports:
            try:
                relation = self._build_relation(imp, rel_path, repo_map)
                graph.add_relation(relation)
            except Exception as exc:
                logger.debug(
                    "ImportAnalyzer: error building relation for %s from %s: %s",
                    imp.module,
                    rel_path,
                    exc,
                )

    def _build_relation(
        self,
        imp: ImportInfo,
        source_file: Path,
        repo_map: RepositoryMap,
    ) -> ImportRelation:
        """Convert a raw :class:`~parser_interface.ImportInfo` into an
        :class:`~models.ImportRelation`.

        Args:
            imp: Extracted import information.
            source_file: Relative path of the file containing the import.
            repo_map: Repository map for resolution.

        Returns:
            :class:`ImportRelation` with ``target_file`` populated when
            the module resolves to a file in the repository.
        """
        resolved = self._resolve_import(
            imp.module, source_file, repo_map, imp.level
        )
        import_type = "relative" if imp.is_relative else "absolute"

        # Guard: skip self-imports (a file resolving to itself)
        if resolved is not None and resolved == source_file:
            logger.debug(
                "ImportAnalyzer: ignoring self-import in %s (module %s)",
                source_file,
                imp.module,
            )
            resolved = None

        return ImportRelation(
            source_file=source_file,
            target_module=imp.module,
            target_file=resolved,
            import_type=import_type,
            line_number=imp.line_number,
        )

    # ------------------------------------------------------------------
    # Private: simplified import resolution (Task 2.3 — 80/20 rule)
    # ------------------------------------------------------------------

    def _resolve_import(
        self,
        module: str,
        source_file: Path,
        repo_map: RepositoryMap,
        level: int = 0,
    ) -> Optional[Path]:
        """Resolve a module name to a file path in the repository.

        **Strategy (80/20 rule)**:

        1. *Relative imports* (``level > 0``): climb *level - 1* directories
           from the source file's package, then look for ``module.py`` or
           ``module/__init__.py``.
        2. *Absolute imports*: try ``module/path.py`` and
           ``module/path/__init__.py`` at repo root level.
        3. *Partial match*: if the full dotted path misses, try only the
           first component (common for ``from package.sub import thing``).
        4. If nothing matches → return ``None`` (external or unresolvable).

        No ``sys.path`` manipulation, no virtualenv inspection.

        Args:
            module: Module name string (dots already stripped of leading dots).
            source_file: Relative path of the file doing the import.
            repo_map: Repository map to check candidates against.
            level: Relative import level (number of leading dots).

        Returns:
            Relative path (from repo root) of the resolved file, or ``None``.
        """
        if level > 0:
            return self._resolve_relative(module, source_file, repo_map, level)
        return self._resolve_absolute(module, repo_map)

    def _resolve_relative(
        self,
        module: str,
        source_file: Path,
        repo_map: RepositoryMap,
        level: int,
    ) -> Optional[Path]:
        """Resolve a relative import.

        ``level=1`` → same package as source_file.
        ``level=2`` → parent package.
        etc.
        """
        # Start at source file's directory and climb (level - 1) times
        anchor: Path = source_file.parent
        for _ in range(level - 1):
            if anchor == Path(".") or anchor == Path(""):
                break
            anchor = anchor.parent

        if not module:
            # "from . import something" — anchor is the package dir
            candidate_init = anchor / "__init__.py"
            if candidate_init in repo_map.files:
                return candidate_init
            return None

        # Convert module dots to path separators
        sub_path = module.replace(".", "/")

        # Try as .py file
        candidate_py = anchor / (sub_path + ".py")
        if candidate_py in repo_map.files:
            return candidate_py

        # Try as package __init__.py
        candidate_init = anchor / sub_path / "__init__.py"
        if candidate_init in repo_map.files:
            return candidate_init

        return None

    def _resolve_absolute(
        self,
        module: str,
        repo_map: RepositoryMap,
    ) -> Optional[Path]:
        """Resolve an absolute import against repo-root-relative paths."""
        if not module:
            return None

        sub_path = module.replace(".", "/")

        # 1. Direct .py file at repo root
        candidate_py = Path(sub_path + ".py")
        if candidate_py in repo_map.files:
            return candidate_py

        # 2. Package __init__.py at repo root
        candidate_init = Path(sub_path) / "__init__.py"
        if candidate_init in repo_map.files:
            return candidate_init

        # 3. Try first component only (handles "from pkg.sub import X" where pkg is a dir)
        parts = sub_path.split("/")
        if len(parts) > 1:
            top = parts[0]
            top_py = Path(top + ".py")
            if top_py in repo_map.files:
                return top_py
            top_init = Path(top) / "__init__.py"
            if top_init in repo_map.files:
                return top_init

        # 4. Check if module name appears as any suffix of existing paths
        #    (handles repos where source lives in a subdirectory like "src/")
        for rel_path in repo_map.files:
            if rel_path.is_dir():
                continue
            # e.g. module "utils" should match "src/utils.py"
            if rel_path.stem == module and rel_path.suffix == ".py":
                return rel_path
            # e.g. module "pkg.utils" → stem "utils" in any "pkg/" subdir
            last_part = sub_path.split("/")[-1]
            if rel_path.stem == last_part and rel_path.suffix == ".py":
                # Only accept if the parent directory name matches the preceding parts
                if len(parts) > 1:
                    expected_parent = parts[-2]
                    if rel_path.parent.name == expected_parent:
                        return rel_path

        return None  # External / stdlib / third-party
