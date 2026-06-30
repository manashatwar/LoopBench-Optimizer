"""
Task 11.2 — End-to-end tests for the Ghost Worktree System.

Full workflow tests:
- Create worktree → write code → evaluate → cleanup
- Failure scenarios: evaluation crash, orphan cleanup after simulated crash
- Main workspace isolation verification
- Concurrent worktree creation from multiple threads
"""

import subprocess
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from openevolve.workspace_errors import WorktreeRemovalError
from openevolve.workspace_manager import WorkspaceManager


# ---------------------------------------------------------------------------
# Helper — create a real Git repo
# ---------------------------------------------------------------------------

def _make_git_repo(tmp_path, name="repo") -> Path:
    """Create a minimal real Git repository with one commit."""
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "e2e@test.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "E2E"],
                   cwd=repo, check=True, capture_output=True)
    (repo / "main.py").write_text("def solution(): return 0\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"],
                   cwd=repo, check=True, capture_output=True)
    return repo


# ---------------------------------------------------------------------------
# E2E 1 — Complete lifecycle: create → write → read → cleanup
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end lifecycle tests using real Git repos."""

    def test_create_write_read_cleanup(self, tmp_path):
        """Full round-trip: worktree is created, code written, read back, then cleaned."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        with WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        ) as wt_path:
            wt = Path(wt_path)
            assert wt.exists(), "Worktree directory must exist"

            # Original file from the repo is accessible
            assert (wt / "main.py").exists()
            assert "solution" in (wt / "main.py").read_text()

            # Write candidate code
            candidate = "def solution(): return 42\n"
            (wt / "main.py").write_text(candidate)
            assert (wt / "main.py").read_text() == candidate

            # Main repo is NOT affected
            assert (repo / "main.py").read_text() == "def solution(): return 0\n"

        # After exiting, worktree should be cleaned up
        assert not Path(wt_path).exists(), "Worktree must be removed after __exit__"
        # Main repo still intact
        assert (repo / "main.py").read_text() == "def solution(): return 0\n"

    def test_worktree_has_full_repo_content(self, tmp_path):
        """Worktree should contain all committed files from the repo."""
        repo = _make_git_repo(tmp_path)
        (repo / "utils.py").write_text("HELPER = True\n")
        (repo / "data").mkdir()
        (repo / "data" / "config.json").write_text('{"key": "value"}\n')
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add files"],
                       cwd=repo, check=True, capture_output=True)

        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        with WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        ) as wt_path:
            wt = Path(wt_path)
            assert (wt / "main.py").exists()
            assert (wt / "utils.py").exists()
            assert (wt / "data" / "config.json").exists()

    def test_multiple_sequential_worktrees(self, tmp_path):
        """Creating multiple worktrees sequentially should work and produce unique paths."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        paths = []
        for i in range(3):
            with WorkspaceManager(
                repo_root=str(repo),
                worktree_parent_dir=str(wt_parent),
            ) as wt_path:
                paths.append(wt_path)
                (Path(wt_path) / "main.py").write_text(f"V{i}")

        assert len(set(paths)) == 3
        for p in paths:
            assert not Path(p).exists()


# ---------------------------------------------------------------------------
# E2E 2 — Failure scenarios
# ---------------------------------------------------------------------------

class TestFailureScenarios:
    """Test cleanup behaviour during evaluation crashes and exceptions."""

    def test_exception_in_body_still_cleans_up(self, tmp_path):
        """If the code inside 'with' raises, the worktree is still cleaned."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        captured_path = {}
        with pytest.raises(ValueError, match="simulated crash"):
            with WorkspaceManager(
                repo_root=str(repo),
                worktree_parent_dir=str(wt_parent),
            ) as wt_path:
                captured_path["p"] = wt_path
                assert Path(wt_path).exists()
                raise ValueError("simulated crash")

        assert not Path(captured_path["p"]).exists(), \
            "Worktree must be cleaned up even after exception"

    def test_keyboard_interrupt_still_cleans_up(self, tmp_path):
        """KeyboardInterrupt should not prevent cleanup."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        captured_path = {}
        with pytest.raises(KeyboardInterrupt):
            with WorkspaceManager(
                repo_root=str(repo),
                worktree_parent_dir=str(wt_parent),
            ) as wt_path:
                captured_path["p"] = wt_path
                raise KeyboardInterrupt()

        assert not Path(captured_path["p"]).exists()

    def test_cleanup_failure_does_not_mask_body_exception(self, tmp_path):
        """If cleanup fails AND the body raised, the body exception propagates."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        mgr = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        )
        wt_path = mgr.__enter__()

        # Sabotage removal so it fails
        with patch.object(mgr, "remove_worktree",
                          side_effect=WorktreeRemovalError("forced fail", attempts=3)):
            # __exit__ should NOT raise the WorktreeRemovalError
            result = mgr.__exit__(ValueError, ValueError("body error"), None)

        assert result is False  # Original exception should propagate


# ---------------------------------------------------------------------------
# E2E 3 — Orphan cleanup after simulated crash
# ---------------------------------------------------------------------------

class TestOrphanCleanupE2E:
    """Simulate a crash that leaves orphaned worktrees and verify cleanup."""

    def test_orphan_left_behind_is_cleaned(self, tmp_path):
        """Simulate a crash leaving an unregistered worktree dir, then cleanup."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        # Create a worktree normally, then deregister it from Git
        # to simulate a process crash that left the directory behind.
        mgr1 = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        )
        orphan_path = mgr1.create_worktree()
        assert Path(orphan_path).exists()

        # Deregister from Git (simulates a crash that didn't clean up)
        subprocess.run(
            ["git", "worktree", "remove", "--force", orphan_path],
            cwd=repo, capture_output=True, check=False,
        )
        # Re-create the directory (the crash left files behind)
        Path(orphan_path).mkdir(exist_ok=True)
        assert Path(orphan_path).exists()

        # Now create a new manager and run cleanup
        mgr2 = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        )
        count = mgr2.cleanup_orphans()

        assert count >= 1, "At least the orphan should be cleaned"
        assert not Path(orphan_path).exists(), "Orphan directory should be removed"

    def test_auto_cleanup_on_init(self, tmp_path):
        """auto_cleanup_orphans=True should clean up orphans during __init__."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        # Create an orphaned worktree directory manually
        orphan = wt_parent / "temp_worktree_fake-orphan-id"
        orphan.mkdir()

        mgr = WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
            auto_cleanup_orphans=True,
        )

        assert not orphan.exists(), "auto_cleanup_orphans should remove the orphan on init"


# ---------------------------------------------------------------------------
# E2E 4 — Main workspace isolation
# ---------------------------------------------------------------------------

class TestMainWorkspaceIsolation:
    """Verify the main repo is never modified by worktree operations."""

    def test_worktree_changes_do_not_affect_main_repo(self, tmp_path):
        """Writing, deleting, and modifying files in worktree must not touch main repo."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        original_content = (repo / "main.py").read_text()

        with WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        ) as wt_path:
            wt = Path(wt_path)
            # Modify existing file
            (wt / "main.py").write_text("MUTATED = True")
            # Create new file
            (wt / "new_candidate.py").write_text("NEW = True")

        # Main repo files unchanged
        assert (repo / "main.py").read_text() == original_content
        assert not (repo / "new_candidate.py").exists()

    def test_git_status_clean_after_worktree(self, tmp_path):
        """Main repo should have clean git status after worktree use."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        with WorkspaceManager(
            repo_root=str(repo),
            worktree_parent_dir=str(wt_parent),
        ) as wt_path:
            (Path(wt_path) / "main.py").write_text("MODIFIED")

        # Check git status is clean
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        assert result.stdout.strip() == "", f"Repo should be clean, got: {result.stdout}"


# ---------------------------------------------------------------------------
# E2E 5 — Concurrent worktree creation
# ---------------------------------------------------------------------------

class TestConcurrentWorktrees:
    """Test creating worktrees concurrently from multiple threads."""

    def test_concurrent_worktree_creation(self, tmp_path):
        """Multiple threads creating worktrees simultaneously should all succeed."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        results = {}
        errors = {}
        num_threads = 4

        def _worker(thread_id):
            try:
                with WorkspaceManager(
                    repo_root=str(repo),
                    worktree_parent_dir=str(wt_parent),
                ) as wt_path:
                    # Write unique content
                    (Path(wt_path) / "main.py").write_text(f"THREAD_{thread_id}")
                    # Read back
                    content = (Path(wt_path) / "main.py").read_text()
                    results[thread_id] = {"path": wt_path, "content": content}
            except Exception as e:
                errors[thread_id] = str(e)

        threads = [
            threading.Thread(target=_worker, args=(i,))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # All threads should succeed
        assert not errors, f"Errors in threads: {errors}"
        assert len(results) == num_threads

        # Each thread should have had unique content
        for tid, r in results.items():
            assert r["content"] == f"THREAD_{tid}"

        # All worktrees should be cleaned up
        for r in results.values():
            assert not Path(r["path"]).exists()

        # All paths should be unique
        paths = [r["path"] for r in results.values()]
        assert len(set(paths)) == num_threads

    def test_concurrent_worktrees_dont_interfere(self, tmp_path):
        """Concurrent worktrees should have independent filesystem state."""
        repo = _make_git_repo(tmp_path)
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        barrier = threading.Barrier(2)  # sync two threads
        contents = {}

        def _worker(thread_id):
            with WorkspaceManager(
                repo_root=str(repo),
                worktree_parent_dir=str(wt_parent),
            ) as wt_path:
                wt = Path(wt_path)
                # Write unique content
                (wt / "main.py").write_text(f"WORKER_{thread_id}")
                barrier.wait(timeout=10)  # both threads alive here
                # After both have written, each reads THEIR OWN file
                contents[thread_id] = (wt / "main.py").read_text()

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # Each thread should see its own content, not the other's
        assert contents[0] == "WORKER_0"
        assert contents[1] == "WORKER_1"
