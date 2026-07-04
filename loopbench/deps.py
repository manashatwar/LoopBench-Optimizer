"""
Dependency detection for the sandbox.

Real repos import third-party packages (numpy, pandas, sklearn, …) that are not
in the base sandbox image. This module figures out what to `pip install` so the
sandbox can run the target's code, from three sources (in priority order):

  1. An explicit list passed by the user (`--pip` / `sandbox.pip`).
  2. A `requirements.txt` at the repo root (authoritative).
  3. Top-level imports scanned from the target file (heuristic, stdlib-filtered).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Optional

# import name -> PyPI package name (only where they differ)
_IMPORT_TO_PYPI = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "skimage": "scikit-image",
    "OpenSSL": "pyOpenSSL",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "Crypto": "pycryptodome",
    "google": "google-api-python-client",
    "matplotlib": "matplotlib",
}

# Third-party packages we never want the sandbox to install (headless / heavy /
# irrelevant to correctness). matplotlib is dropped because plotting is not a
# performance concern and pulls a large stack.
_SKIP = {"matplotlib"}


def _stdlib_names() -> set:
    names = set(getattr(sys, "stdlib_module_names", set()))
    # A few always-present names not always listed.
    names.update({"__future__", "typing_extensions"})
    return names


def _parse_requirements(path: Path) -> List[str]:
    pkgs: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        pkgs.append(line)
    return pkgs


# Directories that never contain the repo's own dependency-bearing source.
_SKIP_DIRS = {".venv", "venv", "env", "site-packages", "__pycache__", ".git",
              "node_modules", ".tox", "build", "dist", ".mypy_cache", ".pytest_cache"}

# Local module names collected from the repo so we don't treat a sibling module
# (e.g. `import utils`) as a PyPI dependency.
def _local_module_names(repo_path: Path) -> set:
    names = set()
    for p in repo_path.rglob("*.py"):
        if any(part in _SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        names.add(p.stem)
        # a package dir (has __init__.py) contributes its directory name
        if p.name == "__init__.py":
            names.add(p.parent.name)
    return names


def _imports_from_source(source: str, stdlib: set, local: set) -> List[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(tree):
        mods = []
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue  # relative import — internal to the repo
            if node.module:
                mods = [node.module]
        for mod in mods:
            top = mod.split(".")[0]
            if not top or top in stdlib or top in local:
                continue
            pkg = _IMPORT_TO_PYPI.get(top, top)
            if pkg not in _SKIP:
                out.append(pkg)
    return out


def scan_imports(target_file: Path) -> List[str]:
    """Return third-party PyPI package names imported by a single Python file."""
    try:
        source = Path(target_file).read_text(encoding="utf-8")
    except OSError:
        return []
    seen = set()
    out = []
    for pkg in _imports_from_source(source, _stdlib_names(), set()):
        if pkg not in seen:
            seen.add(pkg)
            out.append(pkg)
    return out


def scan_repo_imports(repo_path: Path, max_files: int = 3000) -> List[str]:
    """Scan every Python file in the repo and union their third-party imports.

    This mirrors how the Repo Context Mapper treats the whole repository — the
    target file may import a sibling module that itself pulls in a third-party
    package, so single-file scanning is not enough.
    """
    repo_path = Path(repo_path)
    stdlib = _stdlib_names()
    local = _local_module_names(repo_path)
    seen = set()
    out: List[str] = []
    count = 0
    for p in repo_path.rglob("*.py"):
        if any(part in _SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        count += 1
        if count > max_files:
            break
        try:
            source = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for pkg in _imports_from_source(source, stdlib, local):
            if pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
    return sorted(out)


def _parse_pyproject(path: Path) -> List[str]:
    """Extract dependencies from pyproject.toml (PEP 621 and Poetry)."""
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    pkgs: List[str] = []
    # PEP 621: [project] dependencies = ["numpy>=1.22", ...]
    project = data.get("project", {})
    for dep in project.get("dependencies", []) or []:
        if isinstance(dep, str) and dep.strip():
            pkgs.append(dep.strip())
    # Poetry: [tool.poetry.dependencies] { numpy = "^1.22", python = "..." }
    poetry = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
    for name in poetry:
        if name.lower() != "python":
            pkgs.append(name)  # poetry version specs aren't pip syntax — drop them

    # Filter skips and de-dup.
    out, seen = [], set()
    for p in pkgs:
        base = p.split("==")[0].split(">")[0].split("<")[0].split("~")[0].strip()
        if base in _SKIP or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def resolve_deps_with_source(
    repo_path: Path,
    target_file: Optional[Path] = None,
    explicit: Optional[List[str]] = None,
) -> tuple:
    """Resolve pip packages and report where they came from.

    Priority (authoritative first): explicit > requirements.txt > pyproject.toml
    > imports scanned across the repo. Returns (packages, source_label).
    """
    if explicit is not None:
        # An explicit list is authoritative — honor it verbatim (no _SKIP filter).
        # An *empty* explicit list means "install nothing" (do NOT fall back to
        # scanning the repo). ``None`` alone means "not specified → auto-detect".
        pkgs = [p for p in explicit if p]
        return pkgs, ("explicit --pip/config" if pkgs else "explicit (no deps)")

    repo_path = Path(repo_path)

    req = repo_path / "requirements.txt"
    if req.exists():
        pkgs = [p for p in _parse_requirements(req)
                if p.split("==")[0].split(">")[0].strip() not in _SKIP]
        if pkgs:
            return pkgs, "requirements.txt"

    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        pkgs = _parse_pyproject(pyproject)
        if pkgs:
            return pkgs, "pyproject.toml"

    pkgs = scan_repo_imports(repo_path)
    if pkgs:
        return pkgs, "scanned imports (best-effort)"
    if target_file is not None:
        pkgs = scan_imports(Path(target_file))
        if pkgs:
            return pkgs, "scanned imports (best-effort)"
    return [], "none"


def detect_python_deps(
    repo_path: Path,
    target_file: Optional[Path] = None,
    explicit: Optional[List[str]] = None,
) -> List[str]:
    """Resolve the pip packages the sandbox should install (see
    :func:`resolve_deps_with_source`). Returns a de-duplicated list."""
    pkgs, _ = resolve_deps_with_source(repo_path, target_file, explicit)
    return pkgs
