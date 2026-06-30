"""
Tests for openevolve/repo_manager.py — Tasks 16.1, 16.2, 16.3, 16.4.

Task 16.1 — clone_repository()
Task 16.2 — detect_language(), detect_test_framework()
Task 16.3 — detect_dependencies(), generate_dockerfile()
Task 16.4 — integration tests (this file)

Requirements: 10.1 – 10.6
"""

from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

import pytest

from openevolve.repo_manager import (
    DependencyInfo,
    RepoInfo,
    FrameworkInfo,
    clone_repository,
    detect_dependencies,
    detect_language,
    detect_test_framework,
    generate_dockerfile,
    setup_repository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    """Create a minimal git repository with optional files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    for name, content in (files or {}).items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--allow-empty"],
        cwd=repo, check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# Task 16.1 — clone_repository
# ---------------------------------------------------------------------------

class TestCloneRepository:
    def test_clone_local_repo(self, tmp_path):
        """Clone a local bare repo (no network required)."""
        source = _make_repo(tmp_path, {"main.py": "x = 1\n"})
        dest = tmp_path / "clone"
        result = clone_repository(str(source), dest)
        assert result.exists()
        assert (result / "main.py").exists()

    def test_clone_returns_path(self, tmp_path):
        source = _make_repo(tmp_path, {})
        dest = tmp_path / "clone"
        result = clone_repository(str(source), dest)
        assert isinstance(result, Path)
        assert result.is_dir()

    def test_clone_creates_parent_dirs(self, tmp_path):
        source = _make_repo(tmp_path, {})
        dest = tmp_path / "deep" / "nested" / "clone"
        clone_repository(str(source), dest)
        assert dest.exists()

    def test_clone_empty_url_raises(self, tmp_path):
        with pytest.raises(ValueError):
            clone_repository("", tmp_path / "dest")

    def test_clone_invalid_url_raises(self, tmp_path):
        with pytest.raises(RuntimeError, match="git clone failed"):
            clone_repository(
                "https://github.com/this-does-not-exist-zzzzzz/no-repo.git",
                tmp_path / "dest",
                timeout=15,
            )

    def test_clone_git_not_found_raises(self, tmp_path, monkeypatch):
        """Simulate git not being on PATH."""
        monkeypatch.setenv("PATH", "")
        with pytest.raises(RuntimeError, match="git executable not found"):
            clone_repository("https://github.com/x/y.git", tmp_path / "dest")

    def test_clone_with_depth_1(self, tmp_path):
        """Shallow clone should succeed."""
        source = _make_repo(tmp_path, {"code.py": "pass\n"})
        dest = tmp_path / "shallow"
        clone_repository(str(source), dest, depth=1)
        assert dest.exists()

    def test_error_message_contains_troubleshooting(self, tmp_path):
        """Error raised on failure includes troubleshooting hints (Req 10.4)."""
        with pytest.raises(RuntimeError) as exc_info:
            clone_repository("https://not-a-valid-host-xyz.com/repo.git",
                             tmp_path / "dest", timeout=5)
        msg = str(exc_info.value)
        assert "Troubleshooting" in msg or "clone" in msg.lower()


# ---------------------------------------------------------------------------
# Task 16.2 — detect_language
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def test_detects_python_via_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
        assert detect_language(tmp_path) == "python"

    def test_detects_python_via_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        assert detect_language(tmp_path) == "python"

    def test_detects_python_via_setup_py(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
        assert detect_language(tmp_path) == "python"

    def test_detects_javascript_via_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"x"}', encoding="utf-8")
        assert detect_language(tmp_path) == "javascript"

    def test_detects_go_via_go_mod(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com\n", encoding="utf-8")
        assert detect_language(tmp_path) == "go"

    def test_detects_rust_via_cargo_toml(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        assert detect_language(tmp_path) == "rust"

    def test_detects_by_file_extension(self, tmp_path):
        (tmp_path / "main.py").write_text("pass\n", encoding="utf-8")
        (tmp_path / "utils.py").write_text("pass\n", encoding="utf-8")
        assert detect_language(tmp_path) == "python"

    def test_defaults_to_python_for_empty_dir(self, tmp_path):
        assert detect_language(tmp_path) == "python"

    def test_returns_string(self, tmp_path):
        result = detect_language(tmp_path)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Task 16.2 — detect_test_framework
# ---------------------------------------------------------------------------

class TestDetectTestFramework:
    def test_detects_pytest_via_pytest_ini(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert info.name == "pytest"
        assert "pytest" in info.test_command

    def test_detects_pytest_via_conftest(self, tmp_path):
        (tmp_path / "conftest.py").write_text("", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert info.name == "pytest"

    def test_detects_pytest_via_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert info.name == "pytest"

    def test_detects_pytest_via_tests_dir(self, tmp_path):
        (tmp_path / "tests").mkdir()
        info = detect_test_framework(tmp_path)
        assert info.name == "pytest"

    def test_detects_go_test(self, tmp_path):
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert info.name == "go-test"
        assert "go test" in info.test_command

    def test_detects_cargo_test(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert info.name == "cargo"

    def test_detects_benchmark_variant(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
        (tmp_path / "requirements.txt").write_text("pytest-benchmark\n", encoding="utf-8")
        info = detect_test_framework(tmp_path)
        assert "benchmark" in info.test_command

    def test_returns_test_framework_info(self, tmp_path):
        info = detect_test_framework(tmp_path)
        assert isinstance(info, FrameworkInfo)
        assert isinstance(info.name, str)
        assert isinstance(info.test_command, str)
        assert isinstance(info.test_glob, str)

    def test_defaults_to_pytest(self, tmp_path):
        info = detect_test_framework(tmp_path)
        assert info.name == "pytest"


# ---------------------------------------------------------------------------
# Task 16.3 — detect_dependencies
# ---------------------------------------------------------------------------

class TestDetectDependencies:
    def test_detects_requirements_txt(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert info.requirements_txt is not None
        assert info.requirements_txt.name == "requirements.txt"

    def test_detects_pyproject_toml(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert info.pyproject_toml is not None

    def test_detects_setup_py(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert info.setup_py is not None

    def test_has_python_deps_true_when_files_present(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert info.has_python_deps is True

    def test_has_python_deps_false_when_nothing(self, tmp_path):
        info = detect_dependencies(tmp_path)
        assert info.has_python_deps is False

    def test_install_command_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert "requirements.txt" in info.install_command

    def test_install_command_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
        info = detect_dependencies(tmp_path)
        assert "pip install" in info.install_command

    def test_returns_dependency_info(self, tmp_path):
        info = detect_dependencies(tmp_path)
        assert isinstance(info, DependencyInfo)


# ---------------------------------------------------------------------------
# Task 16.3 — generate_dockerfile
# ---------------------------------------------------------------------------

class TestGenerateDockerfile:
    def _make_fw(self, cmd="pytest -v"):
        return FrameworkInfo(
            name="pytest", test_command=cmd,
            test_glob="test_*.py", config_file="pytest.ini",
        )

    def _make_dep(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("requests\n", encoding="utf-8")
        return detect_dependencies(tmp_path)

    def test_returns_string(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw())
        assert isinstance(content, str)

    def test_has_from_line(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw())
        assert content.startswith("FROM python:")

    def test_has_workdir(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw())
        assert "WORKDIR" in content

    def test_has_copy_requirements(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw())
        assert "requirements.txt" in content

    def test_has_pip_install(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw())
        assert "pip install" in content

    def test_has_cmd_with_test_command(self, tmp_path):
        dep = self._make_dep(tmp_path)
        content = generate_dockerfile(tmp_path, dep, self._make_fw("pytest --benchmark-only -v"))
        assert "CMD" in content
        assert "pytest" in content

    def test_writes_file_when_output_path_given(self, tmp_path):
        dep = self._make_dep(tmp_path)
        out = tmp_path / "Dockerfile.test"
        generate_dockerfile(tmp_path, dep, self._make_fw(), output_path=out)
        assert out.exists()

    def test_custom_base_image(self, tmp_path):
        dep = DependencyInfo()
        content = generate_dockerfile(
            tmp_path, dep, self._make_fw(), base_image="python:3.12-slim"
        )
        assert "python:3.12-slim" in content


# ---------------------------------------------------------------------------
# Task 16.4 — integration: setup_repository
# ---------------------------------------------------------------------------

class TestSetupRepository:
    def test_setup_returns_repo_info(self, tmp_path):
        source = _make_repo(tmp_path, {
            "main.py": "x = 1\n",
            "requirements.txt": "requests\n",
            "tests/test_x.py": "def test_x(): pass\n",
        })
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest, generate_dockerfile_if_missing=True)
        assert isinstance(info, RepoInfo)

    def test_setup_detects_python(self, tmp_path):
        source = _make_repo(tmp_path, {"setup.py": "from setuptools import setup\n"})
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest)
        assert info.primary_language == "python"

    def test_setup_detects_test_framework(self, tmp_path):
        source = _make_repo(tmp_path, {
            "pytest.ini": "[pytest]\n",
            "tests/test_x.py": "def test_x(): pass\n",
        })
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest)
        assert info.test_framework == "pytest"
        assert "pytest" in info.test_command

    def test_setup_generates_dockerfile(self, tmp_path):
        source = _make_repo(tmp_path, {"requirements.txt": "requests\n"})
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest, generate_dockerfile_if_missing=True)
        assert info.has_dockerfile
        assert (dest / "Dockerfile.test").exists()

    def test_setup_lists_dependency_files(self, tmp_path):
        source = _make_repo(tmp_path, {"requirements.txt": "requests\n"})
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest)
        assert any("requirements.txt" in f for f in info.dependency_files)

    def test_setup_detects_makefile(self, tmp_path):
        source = _make_repo(tmp_path, {"Makefile": "test:\n\tpytest\n"})
        dest = tmp_path / "setup_clone"
        info = setup_repository(str(source), dest)
        assert info.has_makefile

    def test_setup_invalid_url_raises_with_guidance(self, tmp_path):
        with pytest.raises(RuntimeError) as exc_info:
            setup_repository(
                "https://github.com/no-user-xyz/no-repo-xyz.git",
                tmp_path / "dest",
                generate_dockerfile_if_missing=False,
            )
        assert "Troubleshooting" in str(exc_info.value) or "clone failed" in str(exc_info.value)
