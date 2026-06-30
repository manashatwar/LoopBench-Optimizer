"""
FileAnalyzer: Extract concise descriptors from Python source files.

Task 3.2 — Implements the FileAnalyzer class that uses parser_interface
(AST-based) for structure extraction and applies custom role-inference
heuristics plus summary generation.

Key design choices (from design.md §4):
- Parsing: delegates to parser_interface.extract_structure() — no raw AST here
- Role inference: filename + structure heuristics (90 % accuracy, no ML)
- Summary: docstring → class/function list → filename fallback, truncated to
  config.max_file_descriptor_length
- Robustness: gracefully handles parse failures (Req 9.3)

Implements Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 8.4, 8.5, 8.6
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from openevolve.repo_mapper.models import FileDescriptor, RepoMapperConfig
from openevolve.repo_mapper.parser_interface import FileStructure, extract_structure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role inference constants
# ---------------------------------------------------------------------------

# Names / stems that imply a specific role regardless of content
_ROLE_BY_STEM: Dict[str, str] = {
    "__main__": "main",
    "__init__": "init",
    "setup": "config",
    "conftest": "test",
    "settings": "config",
    "config": "config",
    "conf": "config",
    "configuration": "config",
    "constants": "config",
    "exceptions": "utility",
    "errors": "utility",
    "utils": "utility",
    "helpers": "utility",
    "util": "utility",
    "helper": "utility",
}

# Pattern-based role inference (checked in order)
_ROLE_PATTERNS = [
    # Test files
    (re.compile(r"^test_|_test$"), "test"),
    # Model files
    (re.compile(r"model|schema|entity|record", re.IGNORECASE), "model"),
    # Interface / API files
    (re.compile(r"interface|api|client|handler|endpoint|view", re.IGNORECASE), "interface"),
    # Evaluator files (common in OpenEvolve)
    (re.compile(r"evaluat", re.IGNORECASE), "utility"),
]


class FileAnalyzer:
    """Extract concise :class:`~models.FileDescriptor` objects from Python files.

    Uses :func:`~parser_interface.extract_structure` for AST-based extraction,
    then applies heuristic role inference and summary generation.  Falls back
    gracefully when a file cannot be parsed (Requirement 9.3).

    Attributes:
        config: :class:`~models.RepoMapperConfig` controlling max descriptor length.

    Example::

        config = RepoMapperConfig()
        analyzer = FileAnalyzer(config)
        descriptor = analyzer.analyze_file(
            absolute_path=Path("/repo/src/utils.py"),
            relative_path=Path("src/utils.py"),
        )
        print(descriptor.to_string())
    """

    def __init__(self, config: RepoMapperConfig) -> None:
        """Initialise the analyzer.

        Args:
            config: ``RepoMapperConfig`` instance.
        """
        self.config = config

    # ------------------------------------------------------------------
    # Public API (Task 3.2)
    # ------------------------------------------------------------------

    def analyze_file(
        self,
        absolute_path: Path,
        relative_path: Path,
    ) -> FileDescriptor:
        """Generate a :class:`FileDescriptor` for a single file.

        Strategy:

        1. Call ``extract_structure()`` (parser interface) to get classes,
           functions, docstring.
        2. Infer role from filename + structure heuristics.
        3. Build summary from docstring / class-function list / fallback.
        4. Truncate summary to ``config.max_file_descriptor_length``.
        5. Count lines of code.

        Args:
            absolute_path: Full filesystem path to the file.
            relative_path: Path relative to the repository root.

        Returns:
            :class:`FileDescriptor` — never raises, falls back on parse error.
        """
        structure = self._get_structure(absolute_path)
        role = self._infer_role(relative_path, structure)
        summary = self._build_summary(structure, relative_path)
        summary = self._truncate(summary)
        loc = self._count_loc(absolute_path)
        has_main = self._detect_main(absolute_path, structure)

        class_names = [c.name for c in structure.classes] if structure else []
        func_names = [f.name for f in structure.functions] if structure else []

        return FileDescriptor(
            file_path=relative_path,
            role=role,
            summary=summary,
            classes=class_names,
            functions=func_names,
            has_main=has_main,
            loc=loc,
        )

    def analyze_many(
        self,
        files: Dict[Path, Path],  # rel_path -> abs_path
    ) -> Dict[Path, FileDescriptor]:
        """Analyse multiple files, returning a mapping of rel_path -> descriptor.

        Requirement 9.6: analysis continues even if individual files fail.

        Args:
            files: Dict mapping relative path → absolute path.

        Returns:
            Dict mapping relative path → :class:`FileDescriptor`.
        """
        results: Dict[Path, FileDescriptor] = {}
        for rel_path, abs_path in files.items():
            try:
                results[rel_path] = self.analyze_file(abs_path, rel_path)
            except Exception as exc:
                logger.warning(
                    "FileAnalyzer: failed to analyze %s: %s", rel_path, exc
                )
        return results

    # ------------------------------------------------------------------
    # Private: structure extraction (delegates to parser_interface)
    # ------------------------------------------------------------------

    def _get_structure(self, absolute_path: Path) -> Optional[FileStructure]:
        """Extract structure using parser_interface, returning None on error."""
        try:
            return extract_structure(absolute_path)
        except Exception as exc:
            # Requirement 9.3: log and return None (graceful degradation)
            logger.warning(
                "FileAnalyzer: cannot extract structure from %s: %s",
                absolute_path,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # Private: role inference (Task 3.2 / Requirement 8.6)
    # ------------------------------------------------------------------

    def _infer_role(
        self,
        relative_path: Path,
        structure: Optional[FileStructure],
    ) -> str:
        """Infer the file's role from its name and structure.

        Priority order:
        1. Exact stem match in ``_ROLE_BY_STEM`` table
        2. Path-component checks (e.g. file is inside a ``tests/`` directory)
        3. Regex patterns on the stem
        4. Structure-based heuristics (multiple classes → model, etc.)
        5. Default: ``"utility"``

        Args:
            relative_path: Relative path used for stem / part inspection.
            structure: Parsed structure (may be ``None`` on parse failure).

        Returns:
            Role string.
        """
        stem = relative_path.stem.lower()
        name = relative_path.name

        # 1. Exact stem lookup
        if stem in _ROLE_BY_STEM:
            return _ROLE_BY_STEM[stem]

        # 2. File is inside a directory that implies a role
        parts_lower = [p.lower() for p in relative_path.parts[:-1]]
        if any(p in ("tests", "test") for p in parts_lower):
            return "test"

        # 3. Regex patterns on stem
        for pattern, role in _ROLE_PATTERNS:
            if pattern.search(stem):
                return role

        # 4. Structure-based heuristics (only if parsed)
        if structure and structure.is_parseable:
            num_classes = len(structure.classes)
            num_functions = len(structure.functions)
            # Many classes → model / data structure file
            if num_classes >= 3:
                return "model"
            # Single class with mostly abstract methods / pass → interface
            if num_classes == 1 and num_functions == 0:
                cls = structure.classes[0]
                if any(
                    m.startswith("__") or m == "abstractmethod"
                    for m in cls.methods
                ):
                    return "interface"

        # 5. Default
        return "utility"

    # ------------------------------------------------------------------
    # Private: summary generation
    # ------------------------------------------------------------------

    def _build_summary(
        self,
        structure: Optional[FileStructure],
        relative_path: Path,
    ) -> str:
        """Build a concise human-readable description of the file.

        Priority:
        1. Module-level docstring (if available)
        2. First-class docstring (if module has one class)
        3. Synthesised from class/function names
        4. Filename-based fallback

        Args:
            structure: Parsed structure (may be ``None``).
            relative_path: Relative path for filename-based fallback.

        Returns:
            Summary string (not yet truncated).
        """
        if structure is not None:
            # 1. Module docstring
            if structure.module_docstring:
                first_line = structure.module_docstring.strip().splitlines()[0]
                if len(first_line) > 20:
                    return first_line

            # 2. First class docstring (if exactly one class)
            if len(structure.classes) == 1 and structure.classes[0].docstring:
                first_line = structure.classes[0].docstring.strip().splitlines()[0]
                if len(first_line) > 20:
                    return first_line

            # 3. Synthesised from structure
            parts = []
            if structure.classes:
                names = ", ".join(c.name for c in structure.classes[:3])
                parts.append(f"Defines {names}")
            if structure.functions:
                names = ", ".join(f.name for f in structure.functions[:3])
                suffix = " and more" if len(structure.functions) > 3 else ""
                parts.append(f"provides {names}{suffix}")
            if parts:
                return ". ".join(parts) + "."

        # 4. Fallback: name-based description
        stem = relative_path.stem.replace("_", " ").replace("-", " ")
        return f"{stem.capitalize()} module."

    def _truncate(self, text: str) -> str:
        """Truncate *text* to ``config.max_file_descriptor_length`` characters."""
        limit = self.config.max_file_descriptor_length
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    # ------------------------------------------------------------------
    # Private: auxiliary helpers
    # ------------------------------------------------------------------

    def _count_loc(self, absolute_path: Path) -> int:
        """Count non-empty lines in the file (approximate LOC)."""
        try:
            content = absolute_path.read_text(encoding="utf-8", errors="replace")
            return sum(1 for line in content.splitlines() if line.strip())
        except OSError:
            return 0

    def _detect_main(
        self,
        absolute_path: Path,
        structure: Optional[FileStructure],
    ) -> bool:
        """Return True if the file has an ``if __name__ == '__main__'`` guard."""
        # Fast check: look for the pattern in raw source
        try:
            source = absolute_path.read_text(encoding="utf-8", errors="replace")
            return '__name__' in source and '__main__' in source
        except OSError:
            return False
