"""
Custom exceptions for workspace management operations.

This module defines the exception hierarchy for the Ghost Worktree System,
providing specific error types for different failure scenarios.
"""


class WorkspaceError(Exception):
    """Base exception for workspace-related errors."""

    pass


class WorktreeCreationError(WorkspaceError):
    """Raised when worktree creation fails.
    
    Attributes:
        message: Human-readable error description
        git_output: Raw output from Git command for debugging
    """

    def __init__(self, message: str, git_output: str):
        super().__init__(message)
        self.git_output = git_output


class WorktreeRemovalError(WorkspaceError):
    """Raised when worktree removal fails after all retry attempts.
    
    Attributes:
        message: Human-readable error description
        attempts: Number of removal attempts made
    """

    def __init__(self, message: str, attempts: int):
        super().__init__(message)
        self.attempts = attempts


class RepositoryValidationError(WorkspaceError):
    """Raised when repository validation fails.
    
    This includes cases where:
    - Directory is not a Git repository
    - Repository has no commits
    - Repository is in a corrupted state
    """

    pass


class GitVersionError(WorkspaceError):
    """Raised when Git version is insufficient for worktree operations.
    
    Attributes:
        required: Minimum required Git version
        found: Actual Git version found
    """

    def __init__(self, required: str, found: str):
        if found == "not installed":
            message = (
                f"Git is not installed or not in PATH.\n\n"
                f"Action required: Install Git {required} or later.\n"
                f"  - Download from: https://git-scm.com/downloads\n"
                f"  - Ensure 'git' command is accessible from the terminal/command line"
            )
        else:
            message = (
                f"Git version {required} or later is required for worktree operations.\n"
                f"Found: Git version {found}\n\n"
                f"Action required: Update Git to version {required} or later.\n"
                f"  - Download from: https://git-scm.com/downloads\n"
                f"  - Current version: {found}"
            )
        super().__init__(message)
        self.required = required
        self.found = found
