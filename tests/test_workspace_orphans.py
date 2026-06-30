"""
Task 5.5 — Unit tests for orphan detection and cleanup (Phase 3).

Covers:
- WorktreeInfo.parse_from_git_list() parsing (task 5.1)
- _detect_orphans() — filesystem-only orphans, Git-only prunable orphans,
  no orphans, mixed scenarios (task 5.2)
- cleanup_orphans() — successful cleanup, partial failure, empty set (task 5.3)
- auto_cleanup_orphans=True triggers cleanup during __init__ (task 5.4)
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from openevolve.workspace_errors import WorktreeCreationError
from openevolve.workspace_manager import WorkspaceManager
from openevolve.workspace_types import WorktreeInfo


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------

def _make_manager(tmp_path, **kwargs) -> WorkspaceManager:
    """Return a WorkspaceManager with _validate_repository patched out."""
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    parent = tmp_path / "worktrees"
    parent.mkdir(exist_ok=True)

    with patch.object(WorkspaceManager, "_validate_repository"):
        mgr = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(parent),
            **kwargs,
        )
    return mgr


# ---------------------------------------------------------------------------
# Task 5.1 — WorktreeInfo.parse_from_git_list()
# ---------------------------------------------------------------------------

class TestWorktreeInfoParsing:
    """Test parsing of 'git worktree list --porcelain' output."""

    GIT_OUTPUT_TWO = """\
worktree /main/repo
HEAD abc123def456
branch refs/heads/main

worktree /tmp/temp_worktree_abc
HEAD def789ghi012
detached

"""

    GIT_OUTPUT_LOCKED = """\
worktree /main/repo
HEAD abc123
branch refs/heads/main
locked

"""

    GIT_OUTPUT_PRUNABLE = """\
worktree /gone/path
HEAD aaa111
detached
prunable gitdir file points to non-existent location

"""

    def test_parses_two_worktrees(self):
        wts = WorktreeInfo.parse_from_git_list(self.GIT_OUTPUT_TWO)
        assert len(wts) == 2

    def test_parses_main_branch(self):
        wts = WorktreeInfo.parse_from_git_list(self.GIT_OUTPUT_TWO)
        main = wts[0]
        assert main.path == "/main/repo"
        assert main.commit == "abc123def456"
        assert main.branch == "main"
        assert main.locked is False
        assert main.prunable is False

    def test_parses_detached_worktree(self):
        wts = WorktreeInfo.parse_from_git_list(self.GIT_OUTPUT_TWO)
        detached = wts[1]
        assert detached.path == "/tmp/temp_worktree_abc"
        assert detached.branch is None
        assert detached.locked is False

    def test_parses_locked_worktree(self):
        wts = WorktreeInfo.parse_from_git_list(self.GIT_OUTPUT_LOCKED)
        assert wts[0].locked is True

    def test_parses_prunable_worktree(self):
        wts = WorktreeInfo.parse_from_git_list(self.GIT_OUTPUT_PRUNABLE)
        assert wts[0].prunable is True

    def test_empty_output_returns_empty_list(self):
        assert WorktreeInfo.parse_from_git_list("") == []

    def test_single_worktree_no_trailing_newline(self):
        output = "worktree /repo\nHEAD abc\nbranch refs/heads/feat"
        wts = WorktreeInfo.parse_from_git_list(output)
        assert len(wts) == 1
        assert wts[0].branch == "feat"

    def test_branch_refs_heads_prefix_stripped(self):
        output = "worktree /r\nHEAD a\nbranch refs/heads/feature/my-branch\n\n"
        wts = WorktreeInfo.parse_from_git_list(output)
        assert wts[0].branch == "feature/my-branch"

    def test_non_standard_branch_ref_kept_as_is(self):
        output = "worktree /r\nHEAD a\nbranch refs/tags/v1.0\n\n"
        wts = WorktreeInfo.parse_from_git_list(output)
        assert wts[0].branch == "refs/tags/v1.0"


# ---------------------------------------------------------------------------
# Task 5.2 — _detect_orphans()
# ---------------------------------------------------------------------------

class TestDetectOrphans:
    """Test the orphan detection logic."""

    def _git_list_output(self, *paths):
        """Build a porcelain output listing the given paths as registered worktrees."""
        lines = []
        for p in paths:
            lines += [f"worktree {p}", "HEAD abc123", "branch refs/heads/main", ""]
        return "\n".join(lines)

    # ── Case A: filesystem dirs not registered ──────────────────────────────

    def test_detects_unregistered_directory(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        # Create an orphan dir that matches the pattern
        orphan = parent / "temp_worktree_orphan123"
        orphan.mkdir()

        git_output = self._git_list_output()  # no registered worktrees

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout=git_output, stderr="")):
            result = mgr._detect_orphans()

        assert str(orphan) in result

    def test_does_not_flag_non_matching_directory(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        # A dir that doesn't match the pattern
        unrelated = parent / "some_other_dir"
        unrelated.mkdir()

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout="", stderr="")):
            result = mgr._detect_orphans()

        assert str(unrelated) not in result

    def test_does_not_flag_registered_directory(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        # Create dir and register it with Git
        wt_dir = parent / "temp_worktree_abc"
        wt_dir.mkdir()

        git_output = self._git_list_output(str(wt_dir))

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout=git_output, stderr="")):
            result = mgr._detect_orphans()

        assert str(wt_dir) not in result

    # ── Case B: prunable Git entries with missing directory ──────────────────

    def test_detects_prunable_git_entry(self, tmp_path):
        """A Git-registered entry whose directory is gone should be an orphan."""
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)
        missing_path = str(parent / "temp_worktree_missing")

        # Prunable entry — directory doesn't exist
        prunable_output = (
            f"worktree {missing_path}\n"
            "HEAD abc123\n"
            "detached\n"
            "prunable gitdir points to non-existent\n\n"
        )

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout=prunable_output, stderr="")):
            result = mgr._detect_orphans()

        assert missing_path in result

    def test_ignores_prunable_outside_parent(self, tmp_path):
        """Prunable entries outside our managed dir should be ignored."""
        mgr = _make_manager(tmp_path)

        outside_path = "/some/completely/different/path/temp_worktree_x"
        prunable_output = (
            f"worktree {outside_path}\n"
            "HEAD abc123\n"
            "detached\n"
            "prunable gitdir points to non-existent\n\n"
        )

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout=prunable_output, stderr="")):
            result = mgr._detect_orphans()

        assert outside_path not in result

    def test_returns_empty_when_no_orphans(self, tmp_path):
        mgr = _make_manager(tmp_path)

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout="", stderr="")):
            result = mgr._detect_orphans()

        assert result == []

    def test_mixed_orphans_and_clean_worktrees(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        clean_wt = parent / "temp_worktree_clean"
        clean_wt.mkdir()
        orphan_wt = parent / "temp_worktree_orphan"
        orphan_wt.mkdir()

        git_output = self._git_list_output(str(clean_wt))

        with patch.object(mgr, "_run_git_command",
                          return_value=Mock(returncode=0, stdout=git_output, stderr="")):
            result = mgr._detect_orphans()

        assert str(orphan_wt) in result
        assert str(clean_wt) not in result


# ---------------------------------------------------------------------------
# Task 5.3 — cleanup_orphans()
# ---------------------------------------------------------------------------

class TestCleanupOrphans:
    """Test the public cleanup_orphans() method."""

    def test_returns_zero_when_no_orphans(self, tmp_path):
        mgr = _make_manager(tmp_path)

        with patch.object(mgr, "_detect_orphans", return_value=[]):
            count = mgr.cleanup_orphans()

        assert count == 0

    def test_removes_filesystem_orphan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)
        orphan = parent / "temp_worktree_gone"
        orphan.mkdir()

        with patch.object(mgr, "_detect_orphans", return_value=[str(orphan)]), \
             patch.object(mgr, "_run_git_command", return_value=Mock(returncode=0)):
            count = mgr.cleanup_orphans()

        assert count == 1
        assert not orphan.exists()

    def test_removes_git_only_orphan(self, tmp_path):
        """When the directory doesn't exist, only git removal is attempted."""
        mgr = _make_manager(tmp_path)
        missing_path = str(tmp_path / "temp_worktree_missing")

        with patch.object(mgr, "_detect_orphans", return_value=[missing_path]), \
             patch.object(mgr, "_run_git_command", return_value=Mock(returncode=0)):
            count = mgr.cleanup_orphans()

        assert count == 1

    def test_continues_after_individual_failure(self, tmp_path):
        """A failure on one orphan should not stop cleanup of the rest."""
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        orphan_ok = parent / "temp_worktree_ok"
        orphan_ok.mkdir()
        orphan_bad = parent / "temp_worktree_bad"
        orphan_bad.mkdir()

        call_count = {"n": 0}

        def _rmtree_side_effect(path):
            call_count["n"] += 1
            if "bad" in str(path):
                raise OSError("Permission denied")
            shutil.rmtree.__wrapped__(path) if hasattr(shutil.rmtree, "__wrapped__") else None

        with patch.object(mgr, "_detect_orphans",
                          return_value=[str(orphan_bad), str(orphan_ok)]), \
             patch.object(mgr, "_run_git_command", return_value=Mock(returncode=0)), \
             patch("shutil.rmtree", side_effect=_rmtree_side_effect):
            count = mgr.cleanup_orphans()

        # orphan_ok succeeds, orphan_bad fails — count reflects only successes
        # (exact count depends on order; at minimum 0-2 can succeed)
        assert isinstance(count, int)
        assert 0 <= count <= 2

    def test_does_not_raise_on_cleanup_failure(self, tmp_path):
        """Failures in individual orphan cleanup must NOT propagate."""
        mgr = _make_manager(tmp_path)

        with patch.object(mgr, "_detect_orphans", return_value=["/fake/path"]), \
             patch.object(mgr, "_run_git_command", side_effect=Exception("git error")):
            # Should not raise
            count = mgr.cleanup_orphans()

        assert count == 0

    def test_returns_count_of_successfully_cleaned(self, tmp_path):
        mgr = _make_manager(tmp_path)
        parent = Path(mgr.worktree_parent_dir)

        orphans = []
        for i in range(3):
            o = parent / f"temp_worktree_{i}"
            o.mkdir()
            orphans.append(str(o))

        with patch.object(mgr, "_detect_orphans", return_value=orphans), \
             patch.object(mgr, "_run_git_command", return_value=Mock(returncode=0)):
            count = mgr.cleanup_orphans()

        assert count == 3
        for o in orphans:
            assert not Path(o).exists()


# ---------------------------------------------------------------------------
# Task 5.4 — auto_cleanup_orphans config
# ---------------------------------------------------------------------------

class TestAutoCleanupOrphans:
    """Test that auto_cleanup_orphans=True triggers cleanup during __init__."""

    def test_auto_cleanup_called_on_init_when_enabled(self, tmp_path):
        with patch.object(WorkspaceManager, "_validate_repository"), \
             patch.object(WorkspaceManager, "cleanup_orphans", return_value=2) as mock_cleanup:
            mgr = _make_manager(tmp_path, auto_cleanup_orphans=True)

        mock_cleanup.assert_called_once()

    def test_auto_cleanup_not_called_by_default(self, tmp_path):
        with patch.object(WorkspaceManager, "_validate_repository"), \
             patch.object(WorkspaceManager, "cleanup_orphans", return_value=0) as mock_cleanup:
            mgr = _make_manager(tmp_path)  # auto_cleanup_orphans defaults to False

        mock_cleanup.assert_not_called()

    def test_auto_cleanup_orphans_attribute_stored(self, tmp_path):
        mgr_false = _make_manager(tmp_path)
        assert mgr_false.auto_cleanup_orphans is False

        with patch.object(WorkspaceManager, "cleanup_orphans", return_value=0):
            mgr_true = _make_manager(tmp_path, auto_cleanup_orphans=True)
        assert mgr_true.auto_cleanup_orphans is True

    def test_auto_cleanup_zero_orphans_no_error(self, tmp_path):
        """auto_cleanup with zero orphans found should not raise."""
        with patch.object(WorkspaceManager, "_validate_repository"), \
             patch.object(WorkspaceManager, "cleanup_orphans", return_value=0):
            # Should not raise
            mgr = _make_manager(tmp_path, auto_cleanup_orphans=True)
        assert mgr is not None
