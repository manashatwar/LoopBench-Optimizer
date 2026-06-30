"""
Parser interface for import and structure extraction.

Task 1.3: Provides a unified interface for extracting imports and code structure
from Python files using the ast module (stdlib - no external dependencies needed).

This is a thin wrapper that:
1. Uses Python's ``ast`` module for reliable import extraction
2. Falls back to regex for partially-parseable or encoding-unusual files
3. Extracts class/function structure for file summarisation

Implements Requirements: 2.1, 2.2, 3.1, 3.2, 3.3, 8.1, 8.4
"""

import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Output data structures
# ---------------------------------------------------------------------------

@dataclass
class ImportInfo:
    """A single extracted import statement."""

    module: str          # The module being imported (e.g. "os.path", "numpy")
    names: List[str]     # Specific names imported (e.g. ["join"]) or [] for bare imports
    is_relative: bool    # True for "from . import X" style relative imports
    level: int           # Number of leading dots for relative imports (0 = absolute)
    line_number: int     # Source line number


@dataclass
class ClassInfo:
    """A single class definition extracted from a file."""

    name: str
    bases: List[str]
    docstring: Optional[str]
    methods: List[str]
    line_number: int


@dataclass
class FunctionInfo:
    """A top-level function definition extracted from a file."""

    name: str
    args: List[str]
    return_annotation: Optional[str]
    docstring: Optional[str]
    is_async: bool
    line_number: int


@dataclass
class FileStructure:
    """Complete structural information extracted from a file."""

    file_path: Path
    module_docstring: Optional[str]
    imports: List[ImportInfo] = field(default_factory=list)
    classes: List[ClassInfo] = field(default_factory=list)
    functions: List[FunctionInfo] = field(default_factory=list)
    parse_error: Optional[str] = None   # Set if AST parsing failed

    @property
    def is_parseable(self) -> bool:
        """Return True if the file was successfully parsed."""
        return self.parse_error is None


# ---------------------------------------------------------------------------
# Regex fallback patterns (used when AST parsing fails)
# ---------------------------------------------------------------------------

_RE_IMPORT = re.compile(
    r"^\s*(?:from\s+(\.+\S*)\s+import\s+(.+)|import\s+(.+))$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_imports(file_path: Path) -> List[ImportInfo]:
    """Extract import statements from a Python file.

    First attempts AST-based extraction for accuracy. Falls back to regex
    scanning if the file cannot be parsed (e.g. syntax errors, encoding issues).

    Args:
        file_path: Path to the Python source file.

    Returns:
        List of :class:`ImportInfo` objects (empty on total failure).
    """
    source = _read_source(file_path)
    if source is None:
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
        return _extract_imports_ast(tree)
    except SyntaxError as exc:
        logger.warning("Syntax error in %s (line %s): %s — using regex fallback", file_path, exc.lineno, exc)
        return _extract_imports_regex(source)
    except Exception as exc:
        logger.warning("Unexpected error parsing %s: %s", file_path, exc)
        return []


def extract_structure(file_path: Path) -> FileStructure:
    """Extract structural information (classes, functions, docstring) from a Python file.

    Args:
        file_path: Path to the Python source file.

    Returns:
        :class:`FileStructure` with all extracted information.
        On parse failure, ``parse_error`` is set and classes/functions are empty.
    """
    source = _read_source(file_path)
    if source is None:
        return FileStructure(
            file_path=file_path,
            module_docstring=None,
            parse_error="Could not read file",
        )

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError as exc:
        logger.warning("Cannot parse structure of %s: %s", file_path, exc)
        return FileStructure(
            file_path=file_path,
            module_docstring=_extract_docstring_regex(source),
            imports=_extract_imports_regex(source),
            parse_error=str(exc),
        )
    except Exception as exc:
        logger.warning("Unexpected error parsing %s: %s", file_path, exc)
        return FileStructure(
            file_path=file_path,
            module_docstring=None,
            parse_error=str(exc),
        )

    module_docstring = ast.get_docstring(tree)
    imports = _extract_imports_ast(tree)
    classes = _extract_classes(tree)
    functions = _extract_functions(tree)

    return FileStructure(
        file_path=file_path,
        module_docstring=module_docstring,
        imports=imports,
        classes=classes,
        functions=functions,
    )


# ---------------------------------------------------------------------------
# Private: AST-based extraction
# ---------------------------------------------------------------------------

def _read_source(file_path: Path) -> Optional[str]:
    """Read file contents with UTF-8 and Latin-1 fallback.

    Requirement 9.3: attempt UTF-8 with error replacement.
    """
    encodings = ["utf-8", "utf-8-sig", "latin-1"]
    for enc in encodings:
        try:
            return file_path.read_text(encoding=enc, errors="replace")
        except OSError as exc:
            logger.warning("Cannot read %s with encoding %s: %s", file_path, enc, exc)
    return None


def _extract_imports_ast(tree: ast.Module) -> List[ImportInfo]:
    """Walk an AST module and collect all import nodes."""
    results: List[ImportInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                results.append(
                    ImportInfo(
                        module=alias.name,
                        names=[],
                        is_relative=False,
                        level=0,
                        line_number=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [alias.name for alias in node.names]
            level = node.level or 0
            results.append(
                ImportInfo(
                    module=module,
                    names=names,
                    is_relative=level > 0,
                    level=level,
                    line_number=node.lineno,
                )
            )
    return results


def _extract_classes(tree: ast.Module) -> List[ClassInfo]:
    """Extract top-level class definitions from an AST module."""
    results: List[ClassInfo] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            bases = []
            for base in node.bases:
                try:
                    bases.append(ast.unparse(base))
                except Exception:
                    bases.append("?")
            methods = [
                n.name
                for n in ast.iter_child_nodes(node)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            results.append(
                ClassInfo(
                    name=node.name,
                    bases=bases,
                    docstring=ast.get_docstring(node),
                    methods=methods,
                    line_number=node.lineno,
                )
            )
    return results


def _extract_functions(tree: ast.Module) -> List[FunctionInfo]:
    """Extract top-level function definitions from an AST module."""
    results: List[FunctionInfo] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Collect argument names
            args = [arg.arg for arg in node.args.args]

            # Return annotation
            return_ann: Optional[str] = None
            if node.returns is not None:
                try:
                    return_ann = ast.unparse(node.returns)
                except Exception:
                    return_ann = "?"

            results.append(
                FunctionInfo(
                    name=node.name,
                    args=args,
                    return_annotation=return_ann,
                    docstring=ast.get_docstring(node),
                    is_async=isinstance(node, ast.AsyncFunctionDef),
                    line_number=node.lineno,
                )
            )
    return results


# ---------------------------------------------------------------------------
# Private: Regex fallback
# ---------------------------------------------------------------------------

def _extract_imports_regex(source: str) -> List[ImportInfo]:
    """Lightweight regex-based import extraction for unparseable files.

    Accuracy is lower than AST-based extraction but covers most cases.
    """
    results: List[ImportInfo] = []
    for i, line in enumerate(source.splitlines(), start=1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith("#"):
            continue

        # "from X import Y" / "from .. import Y"
        m_from = re.match(r"from\s+(\.+\S*|\S+)\s+import\s+(.+)", line_stripped)
        if m_from:
            module = m_from.group(1)
            names_raw = m_from.group(2).split(",")
            names = [n.strip().split(" as ")[0].strip() for n in names_raw]
            level = len(module) - len(module.lstrip("."))
            is_relative = level > 0
            results.append(
                ImportInfo(
                    module=module.lstrip("."),
                    names=names,
                    is_relative=is_relative,
                    level=level,
                    line_number=i,
                )
            )
            continue

        # "import X"
        m_import = re.match(r"import\s+(.+)", line_stripped)
        if m_import:
            for part in m_import.group(1).split(","):
                module = part.strip().split(" as ")[0].strip()
                if module:
                    results.append(
                        ImportInfo(
                            module=module,
                            names=[],
                            is_relative=False,
                            level=0,
                            line_number=i,
                        )
                    )

    return results


def _extract_docstring_regex(source: str) -> Optional[str]:
    """Try to extract the module-level docstring using a simple regex.

    Only works for docstrings at the very start of the file (no leading code).
    """
    m = re.match(r'\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', source, re.DOTALL)
    if m:
        docstring = m.group(1) or m.group(2)
        return docstring.strip() or None
    return None
