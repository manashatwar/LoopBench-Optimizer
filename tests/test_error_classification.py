"""
Tests for enhanced error classification and handling (Task 3.1).

This test suite verifies that error classification correctly identifies
different error types and provides actionable error messages.
"""

import subprocess
from unittest.mock import Mock, patch

import pytest

from openevolve.workspace_errors import (
    GitVersionError,
    RepositoryValidationError,
)
from openevolve.workspace_manager import WorkspaceManager


class TestErrorClassification:
    """Test _classify_git_error method"""

    def test_classify_path_exists_error(self, tmp_path):
        """Test classification of path already exists error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        # Test various "path exists" error messages
        assert manager._classify_git_error("fatal: path already exists") == "path_exists"
        assert manager._classify_git_error("error: path exists") == "path_exists"
        assert manager._classify_git_error("Path Already Exists") == "path_exists"

    def test_classify_lock_file_error(self, tmp_path):
        """Test classification of lock file error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: Unable to create '.git/index.lock'") == "lock_file"
        assert manager._classify_git_error("error: index.lock exists") == "lock_file"

    def test_classify_disk_space_error(self, tmp_path):
        """Test classification of disk space error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: No space left on device") == "disk_space"
        assert manager._classify_git_error("error: disk full") == "disk_space"
        assert manager._classify_git_error("error: not enough space") == "disk_space"

    def test_classify_not_git_repo_error(self, tmp_path):
        """Test classification of not a git repository error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: not a git repository") == "not_git_repo"
        assert manager._classify_git_error("error: not found in .git") == "not_git_repo"

    def test_classify_invalid_ref_error(self, tmp_path):
        """Test classification of invalid reference error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: invalid reference") == "invalid_ref"
        assert manager._classify_git_error("error: unknown revision") == "invalid_ref"
        assert manager._classify_git_error("fatal: bad revision") == "invalid_ref"

    def test_classify_corrupted_repo_error(self, tmp_path):
        """Test classification of repository corruption error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: corrupt object") == "corrupted_repo"
        assert manager._classify_git_error("error: broken repository") == "corrupted_repo"

    def test_classify_permission_denied_error(self, tmp_path):
        """Test classification of permission denied error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("fatal: Permission denied") == "permission_denied"
        assert manager._classify_git_error("error: access denied") == "permission_denied"

    def test_classify_unknown_error(self, tmp_path):
        """Test classification of unknown error"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test"],
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
        
        assert manager._classify_git_error("some random error") == "unknown"


class TestActionableErrorMessages:
    """Test that error messages are descriptive and actionable"""

    def test_git_not_installed_message(self):
        """Test that Git not installed error has actionable message"""
        error = GitVersionError(required="2.5", found="not installed")
        message = str(error)
        
        # Should mention action required
        assert "Action required" in message
        # Should provide download link
        assert "git-scm.com" in message
        # Should be clear about the problem
        assert "not installed" in message or "not in PATH" in message

    def test_git_version_too_old_message(self):
        """Test that old Git version error has actionable message"""
        error = GitVersionError(required="2.5", found="2.4")
        message = str(error)
        
        # Should mention action required
        assert "Action required" in message
        # Should mention the versions
        assert "2.5" in message
        assert "2.4" in message
        # Should provide update guidance
        assert "Update" in message or "upgrade" in message.lower()

    def test_repository_not_found_message(self, tmp_path):
        """Test that repository not found error has actionable message"""
        with pytest.raises(RepositoryValidationError) as exc_info:
            WorkspaceManager("/nonexistent/path")
        
        message = str(exc_info.value)
        assert "Action required" in message
        assert "does not exist" in message

    def test_not_git_repo_message(self, tmp_path):
        """Test that not a Git repository error has actionable message"""
        not_repo = tmp_path / "not_repo"
        not_repo.mkdir()
        
        with pytest.raises(RepositoryValidationError) as exc_info:
            WorkspaceManager(str(not_repo))
        
        message = str(exc_info.value)
        assert "Action required" in message
        assert "git init" in message
        # Should provide complete initialization steps
        assert "git commit" in message

    def test_no_commits_message(self, tmp_path):
        """Test that no commits error has actionable message"""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        
        with pytest.raises(RepositoryValidationError) as exc_info:
            WorkspaceManager(str(repo))
        
        message = str(exc_info.value)
        assert "Action required" in message
        assert "no commits" in message
        # Should explain how to create initial commit
        assert "git commit" in message


class TestFailFastBehavior:
    """Test that configuration errors fail fast"""

    def test_invalid_repo_fails_immediately(self, tmp_path):
        """Test that invalid repository fails during initialization"""
        not_repo = tmp_path / "not_repo"
        not_repo.mkdir()
        
        # Should raise during __init__, not later
        with pytest.raises(RepositoryValidationError):
            WorkspaceManager(str(not_repo))

    def test_git_not_found_fails_immediately(self, tmp_path):
        """Test that Git not found fails during initialization"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError()
            
            # Should raise during __init__, not later
            with pytest.raises(GitVersionError):
                WorkspaceManager(str(repo))

    def test_old_git_version_fails_immediately(self, tmp_path):
        """Test that old Git version fails during initialization"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="git version 2.4.0",
                stderr="",
            )
            
            # Should raise during __init__, not later
            with pytest.raises(GitVersionError) as exc_info:
                WorkspaceManager(str(repo))
            
            assert exc_info.value.required == "2.5"
            assert exc_info.value.found == "2.4"
