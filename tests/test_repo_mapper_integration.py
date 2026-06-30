"""
Integration tests for RepoContextMapper (Task 5.1).

Verifies that the existing openevolve/repo_mapper/ module meets
Requirements 2.1 by running the mapper against a small synthetic git
repository built in a temporary directory.  No network access, no LLM.
"""

import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from openevolve.repo_mapper.mapper import ContextBuildError, RepoContextMapper
from openevolve.repo_mapper.models import ContextMap, RepoMapperConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    """Run ``git init`` so the scanner has a valid VCS root."""
    subprocess.run(
        ["git", "init"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    # Minimal git config so later git operations don't complain
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        check=True,
        capture_output=True,
    )


def _make_test_repo() -> tuple[tempfile.TemporaryDirectory, Path]:
    """
    Create a temporary git repository with a handful of Python files.

    Returns (tmpdir_handle, repo_path) – caller owns the TemporaryDirectory.
    """
    tmpdir = tempfile.TemporaryDirectory()
    repo_path = Path(tmpdir.name) / "repo"
    repo_path.mkdir()

    _init_git_repo(repo_path)

    # target file – imports utils
    (repo_path / "main.py").write_text(
        """\
\"\"\"Main entry point.\"\"\"
from utils import helper

def run():
    return helper()
""",
        encoding="utf-8",
    )

    # utility module – imported by main.py
    (repo_path / "utils.py").write_text(
        """\
\"\"\"Utility helpers.\"\"\"


def helper():
    \"\"\"Return a constant.\"\"\"
    return 42
""",
        encoding="utf-8",
    )

    # config module – related by directory proximity
    (repo_path / "config.py").write_text(
        """\
\"\"\"Configuration constants.\"\"\"

MAX_RETRIES = 3
TIMEOUT = 30
""",
        encoding="utf-8",
    )

    return tmpdir, repo_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_repo():
    """Provide a (tmpdir_handle, repo_path) pair for the module."""
    tmpdir, repo_path = _make_test_repo()
    yield repo_path
    tmpdir.cleanup()


@pytest.fixture(scope="module")
def mapper():
    """RepoContextMapper with caching disabled so tests are independent."""
    config = RepoMapperConfig(token_budget=2000, enable_cache=False)
    return RepoContextMapper(config)


@pytest.fixture(scope="module")
def context_map(mapper, test_repo):
    """Pre-built ContextMap for main.py – reused across tests in this module."""
    target = test_repo / "main.py"
    return mapper.get_context_map(test_repo, target)


# ---------------------------------------------------------------------------
# 5.1-A: Return type
# ---------------------------------------------------------------------------

class TestContextMapReturnType:
    """get_context_map() returns a valid ContextMap."""

    def test_returns_context_map(self, context_map):
        assert isinstance(context_map, ContextMap)

    def test_target_file_relative(self, context_map, test_repo):
        # target_file must be relative to repo root
        assert not context_map.target_file.is_absolute()
        assert context_map.target_file == Path("main.py")


# ---------------------------------------------------------------------------
# 5.1-B: target_descriptor is populated
# ---------------------------------------------------------------------------

class TestTargetDescriptor:
    """ContextMap.target_descriptor is a non-trivial FileDescriptor."""

    def test_target_descriptor_not_none(self, context_map):
        assert context_map.target_descriptor is not None

    def test_target_descriptor_path(self, context_map):
        assert context_map.target_descriptor.file_path == Path("main.py")

    def test_target_descriptor_has_role(self, context_map):
        assert context_map.target_descriptor.role != ""

    def test_target_descriptor_has_summary(self, context_map):
        assert context_map.target_descriptor.summary.strip() != ""

    def test_target_descriptor_has_loc(self, context_map):
        assert context_map.target_descriptor.loc > 0


# ---------------------------------------------------------------------------
# 5.1-C: relevant_files structure
# ---------------------------------------------------------------------------

class TestRelevantFiles:
    """ContextMap.relevant_files is a list of (path, descriptor, score) tuples."""

    def test_relevant_files_is_list(self, context_map):
        assert isinstance(context_map.relevant_files, list)

    def test_relevant_files_tuple_structure(self, context_map):
        for item in context_map.relevant_files:
            assert len(item) == 3, "Each entry must be a 3-tuple (path, descriptor, score)"
            path, descriptor, score = item
            assert isinstance(path, Path)
            assert hasattr(descriptor, "file_path")
            assert isinstance(score, float)

    def test_relevant_files_scores_in_range(self, context_map):
        for _, _, score in context_map.relevant_files:
            assert 0.0 <= score <= 1.0, f"Score {score} is out of [0, 1]"

    def test_relevant_files_sorted_by_score_desc(self, context_map):
        scores = [s for _, _, s in context_map.relevant_files]
        assert scores == sorted(scores, reverse=True)

    def test_target_not_in_relevant_files(self, context_map):
        """The target file itself should not appear in relevant_files."""
        relevant_paths = [p for p, _, _ in context_map.relevant_files]
        assert context_map.target_file not in relevant_paths


# ---------------------------------------------------------------------------
# 5.1-D: repository_tree
# ---------------------------------------------------------------------------

class TestRepositoryTree:
    """ContextMap.repository_tree is a non-empty string."""

    def test_repository_tree_is_string(self, context_map):
        assert isinstance(context_map.repository_tree, str)

    def test_repository_tree_non_empty(self, context_map):
        assert context_map.repository_tree.strip() != ""

    def test_repository_tree_contains_target(self, context_map):
        assert "main.py" in context_map.repository_tree


# ---------------------------------------------------------------------------
# 5.1-E: token_count
# ---------------------------------------------------------------------------

class TestTokenCount:
    """ContextMap.token_count is positive and within the configured budget."""

    def test_token_count_positive(self, context_map):
        assert context_map.token_count > 0

    def test_token_count_within_budget(self, context_map):
        budget = 2000
        assert context_map.token_count <= budget


# ---------------------------------------------------------------------------
# 5.1-F: to_prompt_section() content
# ---------------------------------------------------------------------------

class TestToPromptSection:
    """to_prompt_section() returns a well-formed string."""

    def test_returns_string(self, context_map):
        prompt = context_map.to_prompt_section()
        assert isinstance(prompt, str)

    def test_contains_repo_context_header(self, context_map):
        assert "## Repository Context" in context_map.to_prompt_section()

    def test_contains_target_file_header(self, context_map):
        assert "### Target File" in context_map.to_prompt_section()

    def test_contains_target_filename(self, context_map):
        assert "main.py" in context_map.to_prompt_section()

    def test_contains_file_structure(self, context_map):
        assert "### File Structure" in context_map.to_prompt_section()


# ---------------------------------------------------------------------------
# 5.1-G: ContextBuildError for out-of-repo target
# ---------------------------------------------------------------------------

class TestOutOfRepoTarget:
    """Supplying a target outside the repo raises ContextBuildError."""

    def test_raises_context_build_error(self, mapper, test_repo):
        outside = Path(tempfile.gettempdir()) / "outside.py"
        outside.write_text("# outside", encoding="utf-8")
        try:
            with pytest.raises(ContextBuildError):
                mapper.get_context_map(test_repo, outside)
        finally:
            outside.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 5.1-H: Caching – second call is at least as fast (or doesn't error)
# ---------------------------------------------------------------------------

class TestCaching:
    """Second call with same args completes without error (caching path)."""

    def test_second_call_returns_valid_context(self, test_repo):
        config = RepoMapperConfig(token_budget=2000, enable_cache=True)
        cached_mapper = RepoContextMapper(config)
        target = test_repo / "main.py"

        first = cached_mapper.get_context_map(test_repo, target)
        second = cached_mapper.get_context_map(test_repo, target)

        assert isinstance(second, ContextMap)
        assert second.target_file == first.target_file

    def test_second_call_is_not_slower_by_large_margin(self, test_repo):
        """Cache should make second call comparable or faster (not 10× slower)."""
        config = RepoMapperConfig(token_budget=2000, enable_cache=True)
        cached_mapper = RepoContextMapper(config)
        target = test_repo / "main.py"

        t0 = time.perf_counter()
        cached_mapper.get_context_map(test_repo, target)
        first_time = time.perf_counter() - t0

        t0 = time.perf_counter()
        cached_mapper.get_context_map(test_repo, target)
        second_time = time.perf_counter() - t0

        # Allow the second call to be at most 10× slower than first
        # (in practice it should be faster due to cache hit)
        assert second_time <= max(first_time * 10, 5.0), (
            f"Second call ({second_time:.3f}s) was far slower than "
            f"first ({first_time:.3f}s)"
        )
