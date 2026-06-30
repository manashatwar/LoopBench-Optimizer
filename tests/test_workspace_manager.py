"""
Tests for WorkspaceManager initialization and validation.

This test suite covers task 1.2: WorkspaceManager initialization and validation
including __init__, _validate_repository(), and _run_git_command().
"""

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from openevolve.workspace_errors import (
    GitVersionError,
    RepositoryValidationError,
    WorktreeCreationError,
    WorktreeRemovalError,
)
from openevolve.workspace_manager import WorkspaceManager


class TestWorkspaceManagerInitialization:
    """Test WorkspaceManager initialization"""

    def test_init_with_valid_repo(self, tmp_path):
        """Test initialization with a valid Git repository"""
        # Create a temporary Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        
        # Initialize Git repo
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        
        # Create initial commit
        (repo / "README.md").write_text("# Test Repo")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Initialize WorkspaceManager
        manager = WorkspaceManager(str(repo))
        
        assert manager.repo_root == str(repo.resolve())
        assert manager.git_timeout == 30  # default
        assert manager.worktree_pattern == "temp_worktree_{candidate_id}"  # default
        assert manager.worktree_parent_dir == str(repo.parent.resolve())  # default
        assert manager.current_worktree_path is None
        assert manager.current_candidate_id is None

    def test_init_with_custom_config(self, tmp_path):
        """Test initialization with custom configuration"""
        # Create a temporary Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        
        # Initialize Git repo with commit
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Create custom worktree parent dir
        worktree_parent = tmp_path / "worktrees"
        worktree_parent.mkdir()

        # Initialize with custom config
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(worktree_parent),
            git_timeout=60,
            worktree_pattern="custom_{candidate_id}",
        )

        assert manager.worktree_parent_dir == str(worktree_parent.resolve())
        assert manager.git_timeout == 60
        assert manager.worktree_pattern == "custom_{candidate_id}"

    def test_init_nonexistent_directory(self):
        """Test initialization with non-existent directory"""
        with pytest.raises(RepositoryValidationError, match="does not exist"):
            WorkspaceManager("/nonexistent/path")

    def test_init_not_a_directory(self, tmp_path):
        """Test initialization with a file instead of directory"""
        file_path = tmp_path / "file.txt"
        file_path.write_text("not a directory")

        with pytest.raises(RepositoryValidationError, match="not a directory"):
            WorkspaceManager(str(file_path))

    def test_init_not_git_repo(self, tmp_path):
        """Test initialization with directory that's not a Git repository"""
        not_repo = tmp_path / "not_repo"
        not_repo.mkdir()

        with pytest.raises(RepositoryValidationError, match="not a Git repository"):
            WorkspaceManager(str(not_repo))

    def test_init_repo_no_commits(self, tmp_path):
        """Test initialization with Git repo that has no commits"""
        repo = tmp_path / "repo"
        repo.mkdir()
        
        # Initialize Git repo but don't commit
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)

        with pytest.raises(RepositoryValidationError, match="has no commits"):
            WorkspaceManager(str(repo))

    def test_init_worktree_parent_inside_repo(self, tmp_path):
        """Test that worktree parent cannot be inside repository"""
        # Create a temporary Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        
        # Initialize Git repo with commit
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Try to set worktree parent inside repo
        with pytest.raises(ValueError, match="cannot be inside the repository"):
            WorkspaceManager(str(repo), worktree_parent_dir=str(repo / "subdir"))


class TestValidateRepository:
    """Test _validate_repository method"""

    def test_validate_checks_git_version(self, tmp_path):
        """Test that validation checks Git version"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        # Mock git --version to return old version
        with patch("subprocess.run") as mock_run:
            # First call: git --version (old version)
            mock_run.return_value = Mock(
                returncode=0,
                stdout="git version 2.4.0",
                stderr="",
            )
            
            with pytest.raises(GitVersionError) as exc_info:
                WorkspaceManager(str(repo))
            
            assert exc_info.value.required == "2.5"
            assert exc_info.value.found == "2.4"

    def test_validate_git_not_installed(self, tmp_path):
        """Test validation when Git is not installed"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        with patch("subprocess.run") as mock_run:
            # Simulate Git not found
            mock_run.side_effect = FileNotFoundError()
            
            with pytest.raises(GitVersionError, match="not installed"):
                WorkspaceManager(str(repo))


class TestRunGitCommand:
    """Test _run_git_command helper method"""

    def test_run_git_command_success(self, tmp_path):
        """Test successful Git command execution"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Run a simple git command
        result = manager._run_git_command(["status"])
        
        assert result.returncode == 0
        assert "On branch" in result.stdout or "HEAD detached" in result.stdout

    def test_run_git_command_failure(self, tmp_path):
        """Test Git command that fails"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Run an invalid git command
        with pytest.raises(RuntimeError, match="Git command failed"):
            manager._run_git_command(["invalid-command"])

    def test_run_git_command_timeout(self, tmp_path):
        """Test Git command timeout handling"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))

        # Mock subprocess.run to simulate timeout
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("git", 1)
            
            with pytest.raises(RuntimeError, match="timed out"):
                manager._run_git_command(["status"], timeout=1)

    def test_run_git_command_custom_timeout(self, tmp_path):
        """Test Git command with custom timeout"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo), git_timeout=10)
        
        # The command should use custom timeout
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="", stderr="")
            
            manager._run_git_command(["status"], timeout=5)
            
            # Check that timeout parameter was passed
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 5

    def test_run_git_command_check_false(self, tmp_path):
        """Test Git command with check=False doesn't raise on failure"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "test.txt").write_text("test")
        subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Test commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Run invalid command with check=False
        result = manager._run_git_command(["invalid-command"], check=False)
        
        # Should not raise, but return non-zero exit code
        assert result.returncode != 0


class TestContextManagerProtocol:
    """Test context manager protocol implementation (__enter__ and __exit__)"""

    def test_enter_calls_create_worktree(self, tmp_path):
        """Test that __enter__ calls create_worktree and returns path"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))

        # Mock create_worktree to avoid actual worktree creation
        with patch.object(manager, "create_worktree") as mock_create:
            mock_create.return_value = "/fake/worktree/path"
            
            # Call __enter__
            result = manager.__enter__()
            
            # Verify create_worktree was called
            mock_create.assert_called_once()
            
            # Verify return value is the worktree path
            assert result == "/fake/worktree/path"

    def test_exit_calls_remove_worktree(self, tmp_path):
        """Test that __exit__ calls remove_worktree"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        manager.current_worktree_path = "/fake/worktree/path"

        # Mock remove_worktree
        with patch.object(manager, "remove_worktree") as mock_remove:
            # Call __exit__ with no exception
            result = manager.__exit__(None, None, None)
            
            # Verify remove_worktree was called with the worktree path
            mock_remove.assert_called_once_with("/fake/worktree/path")
            
            # Verify __exit__ returns False to propagate exceptions
            assert result is False

    def test_exit_returns_false_to_propagate_exceptions(self, tmp_path):
        """Test that __exit__ returns False to allow exception propagation"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        manager.current_worktree_path = "/fake/worktree/path"

        # Mock remove_worktree
        with patch.object(manager, "remove_worktree"):
            # Call __exit__ with an exception
            result = manager.__exit__(ValueError, ValueError("test error"), None)
            
            # Verify __exit__ returns False (does not suppress exception)
            assert result is False

    def test_exit_cleans_up_even_when_exception_occurred(self, tmp_path):
        """Test that __exit__ cleans up worktree even when an exception occurred"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        manager.current_worktree_path = "/fake/worktree/path"

        # Mock remove_worktree
        with patch.object(manager, "remove_worktree") as mock_remove:
            # Call __exit__ with an exception context
            manager.__exit__(RuntimeError, RuntimeError("evaluation failed"), None)
            
            # Verify cleanup was still attempted
            mock_remove.assert_called_once_with("/fake/worktree/path")

    def test_exit_handles_cleanup_failure_gracefully(self, tmp_path):
        """Test that __exit__ handles cleanup failures without crashing"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        manager.current_worktree_path = "/fake/worktree/path"

        # Mock remove_worktree to raise an exception
        with patch.object(manager, "remove_worktree") as mock_remove:
            mock_remove.side_effect = Exception("Cleanup failed")
            
            # Call __exit__ - should not raise exception
            result = manager.__exit__(None, None, None)
            
            # Verify __exit__ still returns False
            assert result is False
            # No exception should be raised from cleanup failure

    def test_exit_does_not_cleanup_if_no_worktree(self, tmp_path):
        """Test that __exit__ does not attempt cleanup if no worktree was created"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        # current_worktree_path is None (no worktree created)

        # Mock remove_worktree
        with patch.object(manager, "remove_worktree") as mock_remove:
            # Call __exit__
            result = manager.__exit__(None, None, None)
            
            # Verify remove_worktree was NOT called
            mock_remove.assert_not_called()
            
            # Verify __exit__ still returns False
            assert result is False

    def test_with_statement_integration(self, tmp_path):
        """Test using WorkspaceManager with Python's with statement"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))

        # Mock create_worktree and remove_worktree
        with patch.object(manager, "create_worktree") as mock_create, \
             patch.object(manager, "remove_worktree") as mock_remove:
            
            mock_create.return_value = "/fake/worktree/path"
            manager.current_worktree_path = "/fake/worktree/path"
            
            # Use with statement
            with manager as worktree_path:
                # Verify worktree path is returned
                assert worktree_path == "/fake/worktree/path"
                # Verify create was called
                mock_create.assert_called_once()
            
            # After exiting, verify cleanup was called
            mock_remove.assert_called_once_with("/fake/worktree/path")

    def test_with_statement_cleanup_on_exception(self, tmp_path):
        """Test that with statement cleans up even when exception occurs"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))

        # Mock create_worktree and remove_worktree
        with patch.object(manager, "create_worktree") as mock_create, \
             patch.object(manager, "remove_worktree") as mock_remove:
            
            mock_create.return_value = "/fake/worktree/path"
            manager.current_worktree_path = "/fake/worktree/path"
            
            # Use with statement and raise exception
            with pytest.raises(ValueError, match="test error"):
                with manager as worktree_path:
                    assert worktree_path == "/fake/worktree/path"
                    raise ValueError("test error")
            
            # Verify cleanup was still called despite exception
            mock_remove.assert_called_once_with("/fake/worktree/path")


class TestWorkspaceManagerIntegration:
    """Integration tests for WorkspaceManager initialization"""

    def test_full_initialization_flow(self, tmp_path):
        """Test complete initialization flow with real Git repository"""
        # Create a real Git repository with multiple commits
        repo = tmp_path / "repo"
        repo.mkdir()
        
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        
        # Create initial commit
        (repo / "README.md").write_text("# Test Repository")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        
        # Create second commit
        (repo / "file1.txt").write_text("content")
        subprocess.run(["git", "add", "file1.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add file1"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Create custom worktree parent
        worktree_parent = tmp_path / "worktrees"
        worktree_parent.mkdir()

        # Initialize WorkspaceManager with full configuration
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(worktree_parent),
            git_timeout=45,
            worktree_pattern="test_wt_{candidate_id}",
        )

        # Verify all attributes are set correctly
        assert manager.repo_root == str(repo.resolve())
        assert manager.worktree_parent_dir == str(worktree_parent.resolve())
        assert manager.git_timeout == 45
        assert manager.worktree_pattern == "test_wt_{candidate_id}"
        assert manager.current_worktree_path is None
        assert manager.current_candidate_id is None

        # Verify we can run Git commands
        result = manager._run_git_command(["log", "--oneline"])
        assert result.returncode == 0
        assert "Initial commit" in result.stdout
        assert "Add file1" in result.stdout



class TestCandidateIDGeneration:
    """Test candidate ID generation"""

    def test_generate_candidate_id_format(self, tmp_path):
        """Test that candidate IDs follow UUID4 format"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Generate a candidate ID
        candidate_id = manager._generate_candidate_id()
        
        # Verify it's a valid UUID4 string
        import uuid
        try:
            parsed_uuid = uuid.UUID(candidate_id, version=4)
            assert str(parsed_uuid) == candidate_id
        except ValueError:
            pytest.fail(f"Generated ID '{candidate_id}' is not a valid UUID4")

    def test_generate_candidate_id_uniqueness(self, tmp_path):
        """Test that consecutive candidate IDs are unique"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Generate multiple candidate IDs
        ids = [manager._generate_candidate_id() for _ in range(100)]
        
        # Verify all IDs are unique
        assert len(ids) == len(set(ids)), "Generated IDs are not unique"



class TestWorktreePathConstruction:
    """Test worktree path construction"""

    def test_worktree_path_uses_pattern(self, tmp_path):
        """Test that worktree path follows the configured pattern"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Create with custom pattern
        worktree_parent = tmp_path / "worktrees"
        worktree_parent.mkdir()
        
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(worktree_parent),
            worktree_pattern="custom_{candidate_id}_wt"
        )
        
        # Mock _run_git_command to avoid actual worktree creation
        with patch.object(manager, "_run_git_command"):
            worktree_path = manager.create_worktree()
            
            # Verify path structure
            assert manager.current_candidate_id is not None
            expected_name = f"custom_{manager.current_candidate_id}_wt"
            expected_path = str(worktree_parent / expected_name)
            assert worktree_path == expected_path

    def test_worktree_path_in_parent_dir(self, tmp_path):
        """Test that worktree is created in the parent directory"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        worktree_parent = tmp_path / "worktrees"
        worktree_parent.mkdir()
        
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(worktree_parent)
        )
        
        # Mock _run_git_command
        with patch.object(manager, "_run_git_command"):
            worktree_path = manager.create_worktree()
            
            # Verify worktree path is inside worktree_parent
            assert Path(worktree_path).parent == worktree_parent



class TestCreateWorktree:
    """Test worktree creation"""

    def test_create_worktree_success(self, tmp_path):
        """Test successful worktree creation"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Create worktree
        worktree_path = manager.create_worktree()
        
        # Verify worktree was created
        assert Path(worktree_path).exists()
        assert Path(worktree_path).is_dir()
        
        # Verify worktree contains repository files
        assert (Path(worktree_path) / "README.md").exists()
        
        # Verify state tracking
        assert manager.current_worktree_path == worktree_path
        assert manager.current_candidate_id is not None
        
        # Cleanup
        manager.remove_worktree(worktree_path)

    def test_create_worktree_sets_candidate_id(self, tmp_path):
        """Test that create_worktree generates and stores candidate ID"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Verify candidate ID is None before creation
        assert manager.current_candidate_id is None
        
        # Create worktree
        worktree_path = manager.create_worktree()
        
        # Verify candidate ID was set
        assert manager.current_candidate_id is not None
        
        # Verify candidate ID is in the path
        assert manager.current_candidate_id in worktree_path
        
        # Cleanup
        manager.remove_worktree(worktree_path)

    def test_create_worktree_uses_head_as_base(self, tmp_path):
        """Test that worktree is created based on HEAD"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Get current HEAD commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True
        )
        head_commit = result.stdout.strip()
        
        # Create worktree
        worktree_path = manager.create_worktree()
        
        # Get commit of worktree
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree_path,
            check=True,
            capture_output=True,
            text=True
        )
        worktree_commit = result.stdout.strip()
        
        # Verify worktree is at same commit as HEAD
        assert worktree_commit == head_commit
        
        # Cleanup
        manager.remove_worktree(worktree_path)

    def test_create_worktree_failure_raises_error(self, tmp_path):
        """Test that worktree creation failure raises WorktreeCreationError"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Mock _run_git_command to simulate failure
        with patch.object(manager, "_run_git_command") as mock_run:
            mock_run.side_effect = RuntimeError("Git worktree add failed")
            
            # Attempt to create worktree
            with pytest.raises(WorktreeCreationError, match="Failed to create worktree"):
                manager.create_worktree()



class TestRemoveWorktree:
    """Test worktree removal"""

    def test_remove_worktree_success(self, tmp_path):
        """Test successful worktree removal"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Create worktree
        worktree_path = manager.create_worktree()
        assert Path(worktree_path).exists()
        
        # Remove worktree
        manager.remove_worktree(worktree_path)
        
        # Verify worktree directory is gone
        assert not Path(worktree_path).exists()

    def test_remove_worktree_failure_raises_error(self, tmp_path):
        """Test that worktree removal failure raises WorktreeRemovalError"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Mock _run_git_command to simulate failure
        with patch.object(manager, "_run_git_command") as mock_run:
            mock_run.side_effect = RuntimeError("Git worktree remove failed")
            
            # Attempt to remove worktree
            with pytest.raises(
                WorktreeRemovalError,
                match="Failed to remove worktree"
            ):
                manager.remove_worktree("/fake/worktree/path")



class TestGetBaseBranch:
    """Test _get_base_branch helper method"""

    def test_get_base_branch_on_branch(self, tmp_path):
        """Test _get_base_branch returns HEAD when on a branch"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Get base branch
        base = manager._get_base_branch()
        
        # Should return "HEAD" when on a branch
        assert base == "HEAD"

    def test_get_base_branch_detached_head(self, tmp_path):
        """Test _get_base_branch returns commit SHA when in detached HEAD"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Get current commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True
        )
        commit_sha = result.stdout.strip()
        
        # Enter detached HEAD state
        subprocess.run(
            ["git", "checkout", commit_sha],
            cwd=repo,
            check=True,
            capture_output=True
        )

        manager = WorkspaceManager(str(repo))
        
        # Get base branch
        base = manager._get_base_branch()
        
        # Should return commit SHA when detached
        assert base == commit_sha
        assert len(base) == 40  # SHA-1 hash length



class TestWorkspaceManagerIntegration:
    """Integration tests for WorkspaceManager core functionality"""

    def test_full_initialization_flow(self, tmp_path):
        """Test complete initialization flow with real Git repository"""
        # Create a real Git repository with multiple commits
        repo = tmp_path / "repo"
        repo.mkdir()
        
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        
        # Create initial commit
        (repo / "README.md").write_text("# Test Repository")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        
        # Create second commit
        (repo / "file1.txt").write_text("content")
        subprocess.run(["git", "add", "file1.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Add file1"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        # Create custom worktree parent
        worktree_parent = tmp_path / "worktrees"
        worktree_parent.mkdir()

        # Initialize WorkspaceManager with full configuration
        manager = WorkspaceManager(
            str(repo),
            worktree_parent_dir=str(worktree_parent),
            git_timeout=45,
            worktree_pattern="test_wt_{candidate_id}",
        )

        # Verify all attributes are set correctly
        assert manager.repo_root == str(repo.resolve())
        assert manager.worktree_parent_dir == str(worktree_parent.resolve())
        assert manager.git_timeout == 45
        assert manager.worktree_pattern == "test_wt_{candidate_id}"
        assert manager.current_worktree_path is None
        assert manager.current_candidate_id is None

        # Verify we can run Git commands
        result = manager._run_git_command(["log", "--oneline"])
        assert result.returncode == 0
        assert "Initial commit" in result.stdout
        assert "Add file1" in result.stdout

    def test_complete_context_manager_flow(self, tmp_path):
        """Test complete flow using context manager"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Use context manager
        with manager as worktree_path:
            # Verify worktree exists and is accessible
            assert Path(worktree_path).exists()
            assert Path(worktree_path).is_dir()
            
            # Verify worktree contains repository files
            assert (Path(worktree_path) / "README.md").exists()
            
            # Read an existing file (avoid creating untracked files that Git won't remove)
            readme_content = (Path(worktree_path) / "README.md").read_text()
            assert readme_content == "# Test"
            
            # Store path for verification after context
            stored_path = worktree_path
        
        # After exiting context, worktree should be cleaned up
        assert not Path(stored_path).exists()

    def test_context_manager_cleanup_on_exception(self, tmp_path):
        """Test that context manager cleans up even when exception occurs"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Use context manager with exception
        with pytest.raises(ValueError, match="test exception"):
            with manager as worktree_path:
                assert Path(worktree_path).exists()
                stored_path = worktree_path
                raise ValueError("test exception")
        
        # Verify cleanup still occurred
        assert not Path(stored_path).exists()

    def test_multiple_sequential_worktrees(self, tmp_path):
        """Test creating multiple worktrees sequentially"""
        # Create a real Git repository
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        manager = WorkspaceManager(str(repo))
        
        # Create multiple worktrees sequentially
        paths = []
        for i in range(3):
            with manager as worktree_path:
                paths.append(worktree_path)
                assert Path(worktree_path).exists()
                
                # Verify each path is unique
                for prev_path in paths[:-1]:
                    assert worktree_path != prev_path
        
        # Verify all worktrees were cleaned up
        for path in paths:
            assert not Path(path).exists()
