"""
Data models for workspace management.

This module defines dataclasses and types used by the WorkspaceManager
to represent worktree state and Git worktree information.
"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class ApplyResult:
    """Result of validating and applying a patch to a worktree."""

    success: bool
    status: str
    stdout: str = ""
    stderr: str = ""

    @property
    def error_output(self) -> str:
        """Return the most useful Git output for failure feedback."""
        return self.stderr or self.stdout


@dataclass
class WorkspaceState:
    """Internal state of a WorkspaceManager instance.
    
    This tracks the current worktree being managed and its metadata.
    """

    candidate_id: str
    """Unique identifier for the current candidate"""

    worktree_path: str
    """Absolute path to the worktree directory"""

    created_at: float
    """Timestamp when worktree was created"""

    base_commit: str
    """Git commit SHA the worktree is based on"""

    cleanup_attempted: bool = False
    """Whether cleanup has been attempted"""


@dataclass
class WorktreeInfo:
    """Information about a Git worktree.
    
    This represents metadata parsed from Git's worktree list command.
    """

    path: str
    """Absolute path to worktree directory"""

    commit: str
    """Git commit SHA"""

    branch: Optional[str]
    """Branch name (None if detached)"""

    locked: bool = False
    """Whether worktree is locked"""

    prunable: bool = False
    """Whether worktree is prunable (missing directory)"""

    @classmethod
    def parse_from_git_list(cls, git_output: str) -> List["WorktreeInfo"]:
        """Parse output of 'git worktree list --porcelain'.
        
        Args:
            git_output: Raw output from 'git worktree list --porcelain'
            
        Returns:
            List of WorktreeInfo objects representing registered worktrees
            
        Example output format:
            worktree /path/to/worktree
            HEAD abc123def456
            branch refs/heads/main
            
            worktree /path/to/another
            HEAD def789ghi012
            detached
        """
        worktrees = []
        current_worktree = {}

        for line in git_output.strip().split("\n"):
            line = line.strip()
            if not line:
                # Empty line indicates end of worktree entry
                if current_worktree:
                    worktrees.append(
                        cls(
                            path=current_worktree.get("path", ""),
                            commit=current_worktree.get("commit", ""),
                            branch=current_worktree.get("branch"),
                            locked=current_worktree.get("locked", False),
                            prunable=current_worktree.get("prunable", False),
                        )
                    )
                    current_worktree = {}
            elif line.startswith("worktree "):
                current_worktree["path"] = line[9:]  # Remove "worktree " prefix
            elif line.startswith("HEAD "):
                current_worktree["commit"] = line[5:]  # Remove "HEAD " prefix
            elif line.startswith("branch "):
                # Extract branch name from refs/heads/branch_name
                branch_ref = line[7:]  # Remove "branch " prefix
                if branch_ref.startswith("refs/heads/"):
                    current_worktree["branch"] = branch_ref[11:]
                else:
                    current_worktree["branch"] = branch_ref
            elif line == "detached":
                current_worktree["branch"] = None
            elif line == "locked" or line.startswith("locked "):
                current_worktree["locked"] = True
            elif line == "prunable" or line.startswith("prunable "):
                current_worktree["prunable"] = True

        # Handle last entry if file doesn't end with empty line
        if current_worktree:
            worktrees.append(
                cls(
                    path=current_worktree.get("path", ""),
                    commit=current_worktree.get("commit", ""),
                    branch=current_worktree.get("branch"),
                    locked=current_worktree.get("locked", False),
                    prunable=current_worktree.get("prunable", False),
                )
            )

        return worktrees
