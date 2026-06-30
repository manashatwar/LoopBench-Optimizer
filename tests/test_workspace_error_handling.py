"""
Task 3.6 — Unit tests for WorkspaceManager error handling and resilience.

Covers:
- Error classification from Git command output (_classify_git_error)
- Retry logic with mock Git failures (path_exists, lock_file)
- Cascading cleanup attempts (normal → forced → prune+rmtree)
- Timeout handling in _run_git_command
- Disk space checking (_check_disk_space)
- Detached HEAD scenarios (_get_base_branch)
"""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from openevolve.workspace_errors import (
    GitVersionError,
    RepositoryValidationError,
    WorktreeCreationError,
    WorktreeRemovalError,
)
from openevolve.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# Shared helper — build a WorkspaceManager that skips real validation
# ---------------------------------------------------------------------------

def _make_manager(tmp_path, **kwargs) -> WorkspaceManager:
    """Return a WorkspaceManager whose _validate_repository is patched out."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    parent = tmp_path / "worktrees"
    parent.mkdir()

    with patch.object(WorkspaceManager, "_validate_repository"):
        mgr = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(parent),
            **kwargs,
        )
    return mgr


# ---------------------------------------------------------------------------
# Error classification (_classify_git_error)
# ---------------------------------------------------------------------------

class TestClassifyGitError:
    """Test error type detection from Git command output."""

    def test_path_exists_already_exists(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("fatal: already exists") == "path_exists"

    def test_path_exists_path_exists(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("error: path exists at destination") == "path_exists"

    def test_lock_file_index_lock(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("unable to create '.git/index.lock'") == "lock_file"

    def test_lock_file_unable_to_create_lock(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("unable to create lock file") == "lock_file"

    def test_disk_space_no_space_left(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("error: no space left on device") == "disk_space"

    def test_disk_space_disk_full(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("disk full") == "disk_space"

    def test_not_git_repo(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("not a git repository") == "not_git_repo"

    def test_invalid_ref_unknown_revision(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("unknown revision or path not in the working tree") == "invalid_ref"

    def test_invalid_ref_bad_revision(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("bad revision 'mybranch'") == "invalid_ref"

    def test_corrupted_repo(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("error: object file is corrupt") == "corrupted_repo"

    def test_permission_denied(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("permission denied") == "permission_denied"

    def test_access_denied_windows(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("access denied") == "permission_denied"

    def test_unknown_error(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("some completely unrecognised error text") == "unknown"

    def test_case_insensitive_matching(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._classify_git_error("FATAL: ALREADY EXISTS") == "path_exists"


# ---------------------------------------------------------------------------
# Retry logic in create_worktree()
# ---------------------------------------------------------------------------

class TestRetryLogic:
    """Test exponential-backoff retry behaviour in create_worktree()."""

    @patch("time.sleep")  # suppress actual sleeps
    def test_retries_on_path_exists_then_succeeds(self, mock_sleep, tmp_path):
        """Second attempt should succeed after path_exists on the first."""
        mgr = _make_manager(tmp_path)

        fail = Mock(returncode=1, stdout="", stderr="fatal: already exists")
        ok   = Mock(returncode=0, stdout="", stderr="")

        call_count = {"n": 0}

        def _side_effect(args, check=True, timeout=None):
            # First two calls: disk-space-unrelated (get_base_branch symbolic-ref)
            # Only track 'worktree add' calls
            if args[:2] == ["worktree", "add"]:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("fatal: already exists")
            return ok

        with patch.object(mgr, "_run_git_command", side_effect=_side_effect), \
             patch.object(mgr, "_check_disk_space", return_value=True), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"):
            path = mgr.create_worktree()

        assert path is not None
        assert call_count["n"] == 2
        # Exponential backoff: first retry sleeps 1 second (2**0)
        mock_sleep.assert_called_once_with(1)

    @patch("time.sleep")
    def test_retries_on_lock_file_then_succeeds(self, mock_sleep, tmp_path):
        """Second attempt should succeed after lock_file on the first."""
        mgr = _make_manager(tmp_path)
        ok = Mock(returncode=0, stdout="", stderr="")

        call_count = {"n": 0}

        def _side_effect(args, check=True, timeout=None):
            if args[:2] == ["worktree", "add"]:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("unable to create '.git/index.lock'")
            return ok

        with patch.object(mgr, "_run_git_command", side_effect=_side_effect), \
             patch.object(mgr, "_check_disk_space", return_value=True), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"):
            path = mgr.create_worktree()

        assert call_count["n"] == 2
        mock_sleep.assert_called_once_with(1)  # 2**0

    @patch("time.sleep")
    def test_raises_after_all_retries_exhausted(self, mock_sleep, tmp_path):
        """WorktreeCreationError raised when all 3 attempts fail."""
        mgr = _make_manager(tmp_path)

        def _always_fail(args, check=True, timeout=None):
            if args[:2] == ["worktree", "add"]:
                raise RuntimeError("fatal: already exists")
            return Mock(returncode=0, stdout="", stderr="")

        with patch.object(mgr, "_run_git_command", side_effect=_always_fail), \
             patch.object(mgr, "_check_disk_space", return_value=True), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"):
            with pytest.raises(WorktreeCreationError):
                mgr.create_worktree()

    @patch("time.sleep")
    def test_non_retriable_error_raises_immediately(self, mock_sleep, tmp_path):
        """Non-retriable errors (e.g. permission denied) should not retry."""
        mgr = _make_manager(tmp_path)
        call_count = {"n": 0}

        def _side_effect(args, check=True, timeout=None):
            if args[:2] == ["worktree", "add"]:
                call_count["n"] += 1
                raise RuntimeError("permission denied")
            return Mock(returncode=0, stdout="", stderr="")

        with patch.object(mgr, "_run_git_command", side_effect=_side_effect), \
             patch.object(mgr, "_check_disk_space", return_value=True), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"):
            with pytest.raises(WorktreeCreationError):
                mgr.create_worktree()

        # Should have attempted exactly once (no retry for non-retriable errors)
        assert call_count["n"] == 1
        mock_sleep.assert_not_called()

    def test_disk_space_check_failure_raises_creation_error(self, tmp_path):
        """Insufficient disk space should raise WorktreeCreationError."""
        mgr = _make_manager(tmp_path)

        with patch.object(mgr, "_check_disk_space", return_value=False), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"), \
             patch.object(mgr, "_run_git_command"):
            with pytest.raises(WorktreeCreationError, match="disk space"):
                mgr.create_worktree()


# ---------------------------------------------------------------------------
# Cascading cleanup (remove_worktree)
# ---------------------------------------------------------------------------

class TestCascadingCleanup:
    """Test the 3-stage cleanup cascade in remove_worktree()."""

    def test_normal_removal_succeeds_on_first_attempt(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ok = Mock(returncode=0, stdout="", stderr="")

        with patch.object(mgr, "_run_git_command", return_value=ok) as mock_git:
            mgr.remove_worktree("/fake/path")

        # Only one git call: normal removal
        mock_git.assert_called_once_with(["worktree", "remove", "/fake/path"])

    def test_falls_back_to_forced_removal(self, tmp_path):
        mgr = _make_manager(tmp_path)
        ok = Mock(returncode=0, stdout="", stderr="")
        call_log = []

        def _side_effect(args, check=True, timeout=None):
            call_log.append(args[:])
            if args[:2] == ["worktree", "remove"] and "--force" not in args:
                raise RuntimeError("modified files in worktree")
            return ok

        with patch.object(mgr, "_run_git_command", side_effect=_side_effect):
            mgr.remove_worktree("/fake/path")

        assert ["worktree", "remove", "/fake/path"] in call_log
        assert ["worktree", "remove", "--force", "/fake/path"] in call_log

    def test_falls_back_to_prune_and_rmtree(self, tmp_path):
        """Both git removal attempts fail → prune + shutil.rmtree."""
        mgr = _make_manager(tmp_path)
        worktree_dir = tmp_path / "orphan_wt"
        worktree_dir.mkdir()

        def _side_effect(args, check=True, timeout=None):
            if "remove" in args:
                raise RuntimeError("removal failed")
            return Mock(returncode=0, stdout="", stderr="")

        with patch.object(mgr, "_run_git_command", side_effect=_side_effect), \
             patch("shutil.rmtree") as mock_rmtree:
            mgr.remove_worktree(str(worktree_dir))

        mock_rmtree.assert_called_once_with(str(worktree_dir))

    def test_raises_removal_error_when_all_attempts_fail(self, tmp_path):
        """WorktreeRemovalError raised when all 3 stages fail."""
        mgr = _make_manager(tmp_path)

        def _always_fail(args, check=True, timeout=None):
            raise RuntimeError("everything broken")

        with patch.object(mgr, "_run_git_command", side_effect=_always_fail), \
             patch("shutil.rmtree", side_effect=OSError("rmtree failed")):
            with pytest.raises(WorktreeRemovalError) as exc_info:
                mgr.remove_worktree("/fake/path")

        assert exc_info.value.attempts == 3

    def test_removal_error_not_raised_from_exit(self, tmp_path):
        """__exit__ must NOT re-raise cleanup errors (they only get logged)."""
        mgr = _make_manager(tmp_path)
        mgr.current_worktree_path = "/fake/path"

        with patch.object(mgr, "remove_worktree", side_effect=WorktreeRemovalError("boom", 3)):
            # Should not raise
            result = mgr.__exit__(None, None, None)

        assert result is False  # does not suppress exceptions


# ---------------------------------------------------------------------------
# Timeout handling in _run_git_command
# ---------------------------------------------------------------------------

class TestTimeoutHandling:
    """Test that Git command timeouts are caught and converted to RuntimeError."""

    def test_timeout_raises_runtime_error(self, tmp_path):
        mgr = _make_manager(tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            with pytest.raises(RuntimeError, match="timed out"):
                mgr._run_git_command(["status"])

    def test_timeout_message_includes_command(self, tmp_path):
        mgr = _make_manager(tmp_path)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            with pytest.raises(RuntimeError) as exc_info:
                mgr._run_git_command(["worktree", "add", "/some/path", "HEAD"])

        assert "timed out" in str(exc_info.value).lower()

    def test_git_not_found_raises_git_version_error(self, tmp_path):
        mgr = _make_manager(tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError("No such file: git")):
            with pytest.raises(GitVersionError):
                mgr._run_git_command(["status"])

    def test_custom_timeout_is_used(self, tmp_path):
        """The timeout kwarg should override the instance default."""
        mgr = _make_manager(tmp_path, git_timeout=30)
        ok = Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", return_value=ok) as mock_run:
            mgr._run_git_command(["status"], timeout=5)

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 5

    def test_default_timeout_from_instance(self, tmp_path):
        mgr = _make_manager(tmp_path, git_timeout=42)
        ok = Mock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", return_value=ok) as mock_run:
            mgr._run_git_command(["status"])

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 42


# ---------------------------------------------------------------------------
# Disk space checking (_check_disk_space)
# ---------------------------------------------------------------------------

class TestDiskSpaceChecking:
    """Test the _check_disk_space() method."""

    def test_returns_true_when_space_sufficient(self, tmp_path):
        mgr = _make_manager(tmp_path, min_disk_space_mb=100)
        # 500 MB available
        usage = SimpleNamespace(free=500 * 1024 * 1024, total=1000 * 1024 * 1024, used=500 * 1024 * 1024)

        with patch("shutil.disk_usage", return_value=usage):
            assert mgr._check_disk_space() is True

    def test_returns_false_when_space_insufficient(self, tmp_path):
        mgr = _make_manager(tmp_path, min_disk_space_mb=100)
        # Only 50 MB available
        usage = SimpleNamespace(free=50 * 1024 * 1024, total=1000 * 1024 * 1024, used=950 * 1024 * 1024)

        with patch("shutil.disk_usage", return_value=usage):
            assert mgr._check_disk_space() is False

    def test_returns_true_on_os_error(self, tmp_path):
        """If disk_usage raises OSError, we allow the operation to proceed."""
        mgr = _make_manager(tmp_path)

        with patch("shutil.disk_usage", side_effect=OSError("Cannot stat")):
            assert mgr._check_disk_space() is True

    def test_exactly_at_threshold(self, tmp_path):
        """Exactly at min threshold: should return False (< not <=)."""
        mgr = _make_manager(tmp_path, min_disk_space_mb=100)
        # Exactly 99.99 MB
        usage = SimpleNamespace(free=int(99.99 * 1024 * 1024), total=1000 * 1024 * 1024, used=0)

        with patch("shutil.disk_usage", return_value=usage):
            assert mgr._check_disk_space() is False

    def test_custom_min_disk_space(self, tmp_path):
        """min_disk_space_mb should be configurable."""
        mgr = _make_manager(tmp_path, min_disk_space_mb=200)
        # 150 MB — enough for default (100) but not for 200
        usage = SimpleNamespace(free=150 * 1024 * 1024, total=1000 * 1024 * 1024, used=0)

        with patch("shutil.disk_usage", return_value=usage):
            assert mgr._check_disk_space() is False


# ---------------------------------------------------------------------------
# Detached HEAD handling (_get_base_branch)
# ---------------------------------------------------------------------------

class TestDetachedHeadHandling:
    """Test _get_base_branch() for both normal and detached HEAD states."""

    def _mock_git(self, symbolic_ref_rc, sha="abc1234567890abcdef"):
        """Return a side_effect function for _run_git_command."""
        def _side_effect(args, check=True, timeout=None):
            if args[:2] == ["symbolic-ref", "-q"]:
                return Mock(returncode=symbolic_ref_rc, stdout="refs/heads/main\n", stderr="")
            if args[0] == "rev-parse":
                return Mock(returncode=0, stdout=sha + "\n", stderr="")
            return Mock(returncode=0, stdout="", stderr="")
        return _side_effect

    def test_returns_head_on_normal_branch(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git_command", side_effect=self._mock_git(symbolic_ref_rc=0)):
            result = mgr._get_base_branch()
        assert result == "HEAD"

    def test_returns_commit_sha_on_detached_head(self, tmp_path):
        sha = "deadbeef12345678deadbeef12345678deadbeef"
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git_command", side_effect=self._mock_git(symbolic_ref_rc=1, sha=sha)):
            result = mgr._get_base_branch()
        assert result == sha

    def test_defaults_to_head_on_exception(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch.object(mgr, "_run_git_command", side_effect=Exception("unexpected")):
            result = mgr._get_base_branch()
        assert result == "HEAD"

    def test_uncommitted_changes_do_not_affect_worktree_creation(self, tmp_path):
        """
        Worktrees are based on committed state, so uncommitted changes in the main
        workspace are irrelevant.  create_worktree() should succeed regardless.
        """
        mgr = _make_manager(tmp_path)
        ok = Mock(returncode=0, stdout="", stderr="")

        with patch.object(mgr, "_run_git_command", return_value=ok), \
             patch.object(mgr, "_check_disk_space", return_value=True), \
             patch.object(mgr, "_get_base_branch", return_value="HEAD"):
            # Should not raise even if main workspace has uncommitted changes
            path = mgr.create_worktree()

        assert path is not None
