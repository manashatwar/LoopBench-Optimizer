"""
Repository manager for OptimizerLoop.

Handles cloning, language detection, test-framework detection, and
dependency-installation for external GitHub repositories.

Tasks 16.1, 16.2, 16.3
Requirements: 10.1 – 10.6
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RepoInfo:
    """Metadata discovered after cloning a repository."""
    local_path: Path
    url: str
    primary_language: str = "python"
    test_framework: str = "pytest"
    test_command: str = "pytest"
    dependency_files: List[str] = field(default_factory=list)
    has_dockerfile: bool = False
    has_makefile: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Task 16.1 — clone_repository  (Req 10.1, 10.3)
# ─────────────────────────────────────────────────────────────────────────────

def clone_repository(
    url: str,
    destination: Path,
    *,
    branch: Optional[str] = None,
    depth: Optional[int] = 1,
    auth_token: Optional[str] = None,
    ssh_key_path: Optional[str] = None,
    timeout: int = 120,
) -> Path:
    """Clone a GitHub repository to *destination*.

    Supports HTTPS (with optional token) and SSH (with optional key).

    Args:
        url:          Repository URL (HTTPS or SSH).
        destination:  Local path where the repo should be cloned.
        branch:       Branch/tag to clone (default: default branch).
        depth:        Shallow clone depth (default 1; None = full clone).
        auth_token:   Personal access token for private HTTPS repos (Req 10.3).
        ssh_key_path: Path to SSH private key for SSH URLs (Req 10.3).
        timeout:      Command timeout in seconds.

    Returns:
        Resolved local path of the cloned repository.

    Raises:
        ValueError:  If the URL is empty or malformed.
        RuntimeError: If ``git clone`` fails — includes stdout/stderr for
                      troubleshooting guidance (Req 10.4).
    """
    if not url or not url.strip():
        raise ValueError("Repository URL must not be empty.")

    dest = Path(destination).resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    # ── Build clone URL with token if HTTPS ───────────────────────────────
    clone_url = url.strip()
    if auth_token and clone_url.startswith("https://"):
        # Inject token: https://<token>@github.com/...
        clone_url = clone_url.replace("https://", f"https://{auth_token}@", 1)

    # ── Build git command ──────────────────────────────────────────────────
    cmd: List[str] = ["git", "clone"]
    if depth is not None:
        cmd += ["--depth", str(depth)]
    if branch:
        cmd += ["--branch", branch]
    cmd += [clone_url, str(dest)]

    # ── Environment for SSH key ────────────────────────────────────────────
    env = os.environ.copy()
    if ssh_key_path:
        env["GIT_SSH_COMMAND"] = (
            f"ssh -i {ssh_key_path} -o StrictHostKeyChecking=no"
        )

    logger.info("Cloning %s → %s", url, dest)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git clone timed out after {timeout}s.\n"
            "Troubleshooting: check network connectivity or increase --timeout."
        )
    except FileNotFoundError:
        raise RuntimeError(
            "git executable not found. Please install Git and ensure it is on PATH."
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}).\n"
            f"URL: {url}\n"
            f"stdout: {result.stdout[-500:]}\n"
            f"stderr: {result.stderr[-500:]}\n\n"
            "Troubleshooting hints:\n"
            "  • For private repos, supply auth_token or ssh_key_path.\n"
            "  • Verify the URL is correct: try opening it in a browser.\n"
            "  • Ensure you have network access to github.com."
        )

    logger.info("Cloned successfully → %s", dest)
    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Task 16.2 — detect_language  (Req 10.2)
# ─────────────────────────────────────────────────────────────────────────────

# File-extension → language mapping (ordered by priority)
_LANG_EXTENSIONS: Dict[str, str] = {
    ".py":    "python",
    ".js":    "javascript",
    ".ts":    "typescript",
    ".go":    "go",
    ".rs":    "rust",
    ".java":  "java",
    ".cpp":   "cpp",
    ".cc":    "cpp",
    ".c":     "c",
    ".rb":    "ruby",
    ".scala": "scala",
    ".kt":    "kotlin",
    ".swift": "swift",
    ".r":     "r",
    ".jl":    "julia",
}

# Config-file patterns that indicate a specific language
_LANG_INDICATORS: List[tuple[str, str]] = [
    ("pyproject.toml",   "python"),
    ("setup.py",         "python"),
    ("setup.cfg",        "python"),
    ("requirements.txt", "python"),
    ("Pipfile",          "python"),
    ("package.json",     "javascript"),
    ("tsconfig.json",    "typescript"),
    ("go.mod",           "go"),
    ("Cargo.toml",       "rust"),
    ("pom.xml",          "java"),
    ("build.gradle",     "java"),
    ("Gemfile",          "ruby"),
]


def detect_language(repo_path: Path) -> str:
    """Identify the primary programming language of a repository.

    Strategy (in order):
    1. Look for well-known config files (pyproject.toml, package.json, etc.).
    2. Count source files by extension across the top 3 directory levels.
    3. Fall back to ``"python"`` if uncertain.

    Args:
        repo_path: Local path to the cloned repository.

    Returns:
        Language name as a lowercase string (e.g. ``"python"``).

    Requirements: 10.2
    """
    root = Path(repo_path)

    # Strategy 1 — config file indicators
    for filename, lang in _LANG_INDICATORS:
        if (root / filename).exists():
            logger.debug("Language detected via config file '%s': %s", filename, lang)
            return lang

    # Strategy 2 — count source files by extension (max depth 3)
    counts: Dict[str, int] = {}
    for depth in range(1, 4):
        pattern = "/".join(["*"] * depth)
        for fpath in root.glob(pattern):
            if fpath.is_file():
                ext = fpath.suffix.lower()
                lang = _LANG_EXTENSIONS.get(ext)
                if lang:
                    counts[lang] = counts.get(lang, 0) + 1

    if counts:
        best = max(counts, key=counts.__getitem__)
        logger.debug("Language detected by file count: %s (%d files)", best, counts[best])
        return best

    logger.debug("Language detection uncertain, defaulting to 'python'")
    return "python"


# ─────────────────────────────────────────────────────────────────────────────
# Task 16.2 — detect_test_framework  (Req 10.5)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FrameworkInfo:
    """Discovered test framework configuration."""
    name: str            # "pytest" | "unittest" | "custom" | ...
    test_command: str    # command to run tests
    test_glob: str       # glob pattern for test files
    config_file: str     # config file that triggered detection


# Ordered checks: (config_file, framework_name, test_command, test_glob)
_FRAMEWORK_CHECKS: List[tuple[str, str, str, str]] = [
    ("pytest.ini",        "pytest",   "pytest -v",           "test_*.py"),
    ("setup.cfg",         "pytest",   "pytest -v",           "test_*.py"),  # may contain [tool:pytest]
    ("pyproject.toml",    "pytest",   "pytest -v",           "test_*.py"),  # may contain [tool.pytest...]
    ("tox.ini",           "pytest",   "pytest -v",           "test_*.py"),
    ("conftest.py",       "pytest",   "pytest -v",           "test_*.py"),
    ("jest.config.js",    "jest",     "npx jest",            "*.test.js"),
    ("jest.config.ts",    "jest",     "npx jest",            "*.test.ts"),
    ("vitest.config.ts",  "vitest",   "npx vitest",          "*.test.ts"),
    ("go.mod",            "go-test",  "go test ./...",       "*_test.go"),
    ("Cargo.toml",        "cargo",    "cargo test",          "*_test.rs"),
    ("build.gradle",      "gradle",   "gradle test",         "*Test.java"),
    ("pom.xml",           "maven",    "mvn test",            "*Test.java"),
    ("Makefile",          "make",     "make test",           ""),
]


def detect_test_framework(repo_path: Path) -> FrameworkInfo:
    """Discover the test framework used by a repository.

    Checks for well-known config files in priority order.  When ``pytest``
    indicators are found the command is further validated to use
    ``--benchmark-only`` if ``pytest-benchmark`` is detected.

    Falls back to ``unittest`` for Python repos with ``test/`` directory,
    and a generic ``make test`` for repos with a Makefile.

    Args:
        repo_path: Local path to the cloned repository.

    Returns:
        :class:`FrameworkInfo` with name, command, glob, and config file.

    Requirements: 10.5
    """
    root = Path(repo_path)

    for cfg_file, name, cmd, glob in _FRAMEWORK_CHECKS:
        if (root / cfg_file).exists():
            # Refine pytest command if benchmark plugin present
            if name == "pytest" and _has_benchmark_dependency(root):
                cmd = "pytest --benchmark-only -v"
            logger.debug("Test framework detected via '%s': %s", cfg_file, name)
            return FrameworkInfo(
                name=name, test_command=cmd, test_glob=glob, config_file=cfg_file
            )

    # Fallback: look for test directories
    if any((root / d).is_dir() for d in ("tests", "test")):
        logger.debug("Test framework: pytest (tests/ directory found)")
        return FrameworkInfo(
            name="pytest", test_command="pytest -v",
            test_glob="test_*.py", config_file="tests/",
        )

    # Last resort
    logger.debug("Test framework: unknown, defaulting to pytest")
    return FrameworkInfo(
        name="pytest", test_command="pytest -v",
        test_glob="test_*.py", config_file="",
    )


def _has_benchmark_dependency(root: Path) -> bool:
    """Return True if pytest-benchmark is listed in any dependency file."""
    files_to_check = [
        root / "requirements.txt",
        root / "requirements-dev.txt",
        root / "pyproject.toml",
        root / "setup.cfg",
    ]
    for f in files_to_check:
        if f.exists():
            try:
                content = f.read_text(encoding="utf-8", errors="ignore")
                if "pytest-benchmark" in content:
                    return True
            except OSError:
                pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Task 16.3 — dependency detection + Dockerfile patching  (Req 10.6)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DependencyInfo:
    """Dependency files found in the repository."""
    requirements_txt: Optional[Path] = None
    pyproject_toml: Optional[Path] = None
    setup_py: Optional[Path] = None
    package_json: Optional[Path] = None
    cargo_toml: Optional[Path] = None
    go_mod: Optional[Path] = None

    @property
    def has_python_deps(self) -> bool:
        return any([self.requirements_txt, self.pyproject_toml, self.setup_py])

    @property
    def install_command(self) -> str:
        """Return the best pip install command for this project."""
        if self.pyproject_toml:
            return "pip install -e .[dev] --quiet 2>/dev/null || pip install -e . --quiet"
        if self.requirements_txt:
            return f"pip install -r {self.requirements_txt.name} --quiet"
        if self.setup_py:
            return "pip install -e . --quiet"
        return ""


def detect_dependencies(repo_path: Path) -> DependencyInfo:
    """Scan *repo_path* for dependency declaration files.

    Args:
        repo_path: Local path to the cloned repository.

    Returns:
        :class:`DependencyInfo` with resolved paths to all found files.

    Requirements: 10.6
    """
    root = Path(repo_path)
    info = DependencyInfo()

    for name, attr in [
        ("requirements.txt",  "requirements_txt"),
        ("requirements-dev.txt", "requirements_txt"),   # prefer dev if present
        ("pyproject.toml",    "pyproject_toml"),
        ("setup.py",          "setup_py"),
        ("package.json",      "package_json"),
        ("Cargo.toml",        "cargo_toml"),
        ("go.mod",            "go_mod"),
    ]:
        candidate = root / name
        if candidate.exists():
            setattr(info, attr, candidate)

    logger.debug("Dependencies found: %s", info)
    return info


def generate_dockerfile(
    repo_path: Path,
    dep_info: DependencyInfo,
    framework_info: FrameworkInfo,
    *,
    base_image: str = "python:3.11-slim",
    output_path: Optional[Path] = None,
) -> str:
    """Generate a Dockerfile that installs dependencies and runs tests.

    Task 16.3 — Req 10.6

    The generated file:
    - Uses *base_image* as the base.
    - Copies dependency declaration files first (layer-caching benefit).
    - Runs the appropriate ``install_command``.
    - Sets the default CMD to the discovered test command.

    Args:
        repo_path:      Local path to the repository (for context only).
        dep_info:       Discovered dependency files.
        framework_info: Discovered test framework.
        base_image:     Docker base image (default: ``python:3.11-slim``).
        output_path:    If provided, write the Dockerfile to this path.

    Returns:
        Dockerfile content as a string.
    """
    lines = [
        f"FROM {base_image}",
        "WORKDIR /workspace",
        "",
    ]

    # Copy dependency files first for better layer caching
    dep_files: List[str] = []
    if dep_info.requirements_txt:
        dep_files.append(dep_info.requirements_txt.name)
    if dep_info.pyproject_toml:
        dep_files.append("pyproject.toml")
    if dep_info.setup_py:
        dep_files.append("setup.py")
        dep_files.append("setup.cfg")  # may accompany setup.py

    if dep_files:
        # COPY only the files that exist
        existing = [f for f in dep_files if (Path(repo_path) / f).exists()]
        if existing:
            lines.append(f"COPY {' '.join(existing)} ./")
            if dep_info.has_python_deps:
                install_cmd = dep_info.install_command
                lines += [
                    f"RUN {install_cmd} || true",
                    "",
                ]

    # Copy everything else
    lines += [
        "COPY . .",
        "",
        f'CMD ["{framework_info.test_command.split()[0]}"]'
        if " " not in framework_info.test_command
        else f"CMD {_shell_list(framework_info.test_command)}",
    ]

    content = "\n".join(lines) + "\n"

    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(content, encoding="utf-8")

    return content


def _shell_list(cmd: str) -> str:
    """Convert a shell command string to a JSON array for CMD."""
    import shlex
    parts = shlex.split(cmd)
    quoted = ", ".join(f'"{p}"' for p in parts)
    return f"[{quoted}]"


# ─────────────────────────────────────────────────────────────────────────────
# High-level convenience: full repo setup
# ─────────────────────────────────────────────────────────────────────────────

def setup_repository(
    url: str,
    local_dir: Path,
    *,
    branch: Optional[str] = None,
    auth_token: Optional[str] = None,
    ssh_key_path: Optional[str] = None,
    generate_dockerfile_if_missing: bool = True,
) -> RepoInfo:
    """Clone a repository and gather all metadata needed by OptimizerLoop.

    Combines Tasks 16.1 – 16.3 into one call:
    1. Clone (Task 16.1)
    2. Detect language and test framework (Task 16.2)
    3. Detect dependencies; optionally generate Dockerfile (Task 16.3)

    Args:
        url:            Repository URL.
        local_dir:      Target directory for the clone.
        branch:         Optional branch to check out.
        auth_token:     HTTPS auth token for private repos.
        ssh_key_path:   SSH key for SSH URLs.
        generate_dockerfile_if_missing: Auto-generate Dockerfile.test if absent.

    Returns:
        Populated :class:`RepoInfo`.
    """
    dest = clone_repository(
        url, local_dir,
        branch=branch,
        auth_token=auth_token,
        ssh_key_path=ssh_key_path,
    )

    language    = detect_language(dest)
    fw_info     = detect_test_framework(dest)
    dep_info    = detect_dependencies(dest)

    # Gather dependency file names
    dep_files = [
        str(p.relative_to(dest))
        for p in [dep_info.requirements_txt, dep_info.pyproject_toml, dep_info.setup_py]
        if p is not None
    ]

    # Generate a Dockerfile if none exists
    dockerfile_path = dest / "Dockerfile.test"
    has_dockerfile = (dest / "Dockerfile").exists() or dockerfile_path.exists()
    if generate_dockerfile_if_missing and not has_dockerfile:
        generate_dockerfile(dest, dep_info, fw_info, output_path=dockerfile_path)
        has_dockerfile = True
        logger.info("Auto-generated Dockerfile.test at %s", dockerfile_path)

    info = RepoInfo(
        local_path=dest,
        url=url,
        primary_language=language,
        test_framework=fw_info.name,
        test_command=fw_info.test_command,
        dependency_files=dep_files,
        has_dockerfile=has_dockerfile,
        has_makefile=(dest / "Makefile").exists(),
    )
    logger.info(
        "Repository ready: language=%s framework=%s cmd='%s' deps=%s",
        info.primary_language, info.test_framework, info.test_command, dep_files,
    )
    return info
