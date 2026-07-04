"""
Manages isolated Git worktrees for safe candidate evaluation.

The :class:`WorkspaceManager` class provides context-manager-based lifecycle
management for temporary Git worktrees, ensuring automatic cleanup even when
exceptions occur.  This enables safe evaluation of code mutations in the
evolutionary loop without affecting the main workspace.

Quick start
-----------

.. code-block:: python

    from openevolve.workspace_manager import WorkspaceManager

    # Basic usage — worktree is created on __enter__ and removed on __exit__
    with WorkspaceManager(repo_root="/path/to/repo") as worktree_path:
        pathlib.Path(worktree_path, "program.py").write_text(candidate_code)
        score = evaluate(worktree_path)
    # Worktree automatically cleaned up here, even if evaluate() raised.

Configuration
-------------

===============================  ==========================================
Parameter                        Description
===============================  ==========================================
``repo_root``                    Absolute path to the Git repository.
``worktree_parent_dir``          Directory where worktrees are created
                                 (default: parent of ``repo_root``).
``git_timeout``                  Seconds before a Git command times out
                                 (default: 30).
``worktree_pattern``             Naming template — must contain
                                 ``{candidate_id}`` (default:
                                 ``temp_worktree_{candidate_id}``).
``min_disk_space_mb``            Minimum free disk space in MB required
                                 before worktree creation (default: 100).
``auto_cleanup_orphans``         Scan and remove orphaned worktrees from
                                 previous runs during ``__init__``
                                 (default: ``False``).
===============================  ==========================================

Exception hierarchy
-------------------

All workspace-specific exceptions are defined in
:mod:`openevolve.workspace_errors`:

- :class:`~openevolve.workspace_errors.WorkspaceError` — base class
    - :class:`~openevolve.workspace_errors.WorktreeCreationError`
    - :class:`~openevolve.workspace_errors.WorktreeRemovalError`
    - :class:`~openevolve.workspace_errors.RepositoryValidationError`
    - :class:`~openevolve.workspace_errors.GitVersionError`

Environment variables
---------------------

``WORKSPACE_LOG_LEVEL``
    Set to ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` to control the
    verbosity of this module's logger without affecting the rest of the
    application.  Default behaviour defers to the root logger's level.

Integration with OpenEvolve
---------------------------

.. code-block:: python

    from openevolve.iteration import run_iteration_with_shared_db

    # Pass workspace config to get isolated worktrees per candidate
    await run_iteration_with_shared_db(
        iteration=0,
        config=config,
        database=db,
        evaluator=evaluator,
        llm_ensemble=llm,
        prompt_sampler=sampler,
        workspace_manager_config={
            "repo_root": "/path/to/repo",
            "auto_cleanup_orphans": True,
        },
    )

Thread safety
-------------

Each :class:`WorkspaceManager` instance manages exactly **one** worktree.
Different threads should create **separate instances** — there is no shared
mutable state between instances.  The underlying Git worktree mechanism is
thread-safe for concurrent adds from different processes / threads.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from types import TracebackType
from typing import List, Optional, Type

from .workspace_errors import (
    GitVersionError,
    RepositoryValidationError,
    WorktreeCreationError,
    WorktreeRemovalError,
)
from .workspace_types import ApplyResult, WorktreeInfo

import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Task 9.4 — Logging configuration
# ---------------------------------------------------------------------------
# Honour the WORKSPACE_LOG_LEVEL environment variable so users can get
# verbose debug output without modifying code.
#   WORKSPACE_LOG_LEVEL=DEBUG  → full Git command traces
#   WORKSPACE_LOG_LEVEL=WARNING → only problems
#   (default = INFO)
# The env-var only configures THIS module’s logger so it doesn’t stomp on
# the rest of the application’s log level.

def _configure_module_logging() -> None:
    """Apply WORKSPACE_LOG_LEVEL env-var to the module logger on import."""
    env_level = os.environ.get("WORKSPACE_LOG_LEVEL", "").upper()
    numeric = getattr(logging, env_level, None)
    if isinstance(numeric, int):
        logger.setLevel(numeric)


_configure_module_logging()


def _slog(level: int, event: str, **fields) -> None:
    """
    Emit a single-line structured log in ``key=value`` format.

    Always includes ``event`` as the first field, then any extra ``fields``
    in the order given.  Values are repr’d only when they contain spaces.

    Example output::

        event=worktree_created candidate_id=abc-123 duration_ms=87 path=/tmp/wt
    """
    parts = [f"event={event}"]
    for k, v in fields.items():
        s = str(v)
        parts.append(f"{k}={s!r}" if " " in s else f"{k}={s}")
    logger.log(level, " ".join(parts))


class WorkspaceManager:
    """
    Manages isolated Git worktrees for safe candidate evaluation.

    This class implements Python's context manager protocol to ensure
    automatic cleanup of temporary worktrees even when exceptions occur.

    Usage::

        with WorkspaceManager(repo_root="/path/to/repo") as worktree_path:
            # Evaluate candidate in worktree_path
            write_candidate_code(worktree_path / "program.py")
            metrics = evaluate(worktree_path)
        # Worktree automatically cleaned up

    Attributes:
        repo_root (str): Absolute path to the Git repository root.
        worktree_parent_dir (str): Directory where worktrees are created.
        git_timeout (int): Timeout in seconds for Git commands.
        worktree_pattern (str): Pattern for worktree directory names.
        min_disk_space_mb (int): Minimum free disk space in MB before creation.
        auto_cleanup_orphans (bool): Whether orphans are cleaned on init.
        current_worktree_path (Optional[str]): Path to the currently managed worktree.
        current_candidate_id (Optional[str]): UUID of the current candidate.

    Raises:
        RepositoryValidationError: On ``__init__`` if ``repo_root`` is invalid.
        GitVersionError: On ``__init__`` if Git < 2.5 or not installed.
        WorktreeCreationError: On ``__enter__`` if worktree creation fails.
        WorktreeRemovalError: On ``remove_worktree()`` if all 3 cleanup
            stages fail (note: ``__exit__`` swallows this to avoid masking
            the original exception).
    """

    def __init__(
        self,
        repo_root: str,
        worktree_parent_dir: Optional[str] = None,
        git_timeout: int = 30,
        worktree_pattern: str = "temp_worktree_{candidate_id}",
        min_disk_space_mb: int = 100,
        auto_cleanup_orphans: bool = False,
    ):
        """
        Initialize WorkspaceManager.

        Args:
            repo_root: Path to the Git repository root
            worktree_parent_dir: Directory where worktrees are created
                                (default: parent of repo_root)
            git_timeout: Timeout in seconds for Git commands (default: 30)
            worktree_pattern: Pattern for worktree directory names
                            (default: "temp_worktree_{candidate_id}")
            min_disk_space_mb: Minimum free disk space required before creating a
                               worktree, in megabytes (default: 100)
            auto_cleanup_orphans: If True, automatically scan and remove orphaned
                                  worktrees from previous runs during initialization
                                  (default: False)

        Raises:
            RepositoryValidationError: If repo_root is not a valid Git repository
            ValueError: If worktree_parent_dir is inside the repository
            GitVersionError: If Git is not installed or version < 2.5
        """
        self.repo_root = str(Path(repo_root).resolve())
        self.git_timeout = git_timeout
        self.worktree_pattern = worktree_pattern
        self.min_disk_space_mb = min_disk_space_mb
        self.auto_cleanup_orphans = auto_cleanup_orphans

        # Set worktree parent directory (default: parent of repo_root)
        if worktree_parent_dir is None:
            self.worktree_parent_dir = str(Path(self.repo_root).parent)
        else:
            self.worktree_parent_dir = str(Path(worktree_parent_dir).resolve())

        # State tracking
        self.current_worktree_path: Optional[str] = None
        self.current_candidate_id: Optional[str] = None

        # ── Security: validate worktree_pattern ────────────────────────────
        # The pattern is used to construct filesystem paths.  Reject patterns
        # containing path separators or traversal sequences so a crafted
        # candidate_id cannot escape the worktree_parent_dir sandbox.
        _forbidden = {"/", "\\", ".."}
        pattern_check = worktree_pattern.replace("{candidate_id}", "")
        if any(tok in pattern_check for tok in _forbidden):
            raise ValueError(
                f"worktree_pattern '{worktree_pattern}' contains illegal characters "
                f"(path separators or '..' are not allowed).\n"
                f"Use a simple name like 'temp_worktree_{{candidate_id}}'."
            )
        if "{candidate_id}" not in worktree_pattern:
            raise ValueError(
                f"worktree_pattern must contain '{{candidate_id}}' placeholder, "
                f"got: '{worktree_pattern}'"
            )

        # Validate repository and configuration
        self._validate_repository()

        # Validate worktree_parent_dir is not inside the repository
        repo_path = Path(self.repo_root)
        parent_path = Path(self.worktree_parent_dir)
        try:
            parent_path.relative_to(repo_path)
            raise ValueError(
                f"Worktree parent directory '{self.worktree_parent_dir}' "
                f"cannot be inside the repository '{self.repo_root}'"
            )
        except ValueError as e:
            if "cannot be inside the repository" in str(e):
                raise
            pass

        logger.info(
            f"WorkspaceManager initialized: repo={self.repo_root}, "
            f"worktree_parent={self.worktree_parent_dir}"
        )

        # Task 5.4 — auto-cleanup orphans from crashed/interrupted previous runs
        if self.auto_cleanup_orphans:
            logger.info("auto_cleanup_orphans=True: scanning for orphaned worktrees…")
            cleaned = self.cleanup_orphans()
            if cleaned:
                logger.info(f"Auto-cleanup removed {cleaned} orphaned worktree(s)")

    def __enter__(self) -> str:
        """
        Enter context manager and create a worktree.

        Emits structured log: ``event=worktree_enter candidate_id=... repo=...``

        Returns:
            str: Absolute path to the created worktree directory

        Raises:
            WorktreeCreationError: If worktree creation fails
        """
        _slog(logging.DEBUG, "worktree_enter",
              repo=self.repo_root,
              worktree_parent=self.worktree_parent_dir)
        return self.create_worktree()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> bool:
        """
        Exit context manager and cleanup worktree.

        Cleanup is performed regardless of whether an exception occurred.
        Emits structured log: ``event=worktree_removed candidate_id=... success=... duration_ms=...``

        Args:
            exc_type: Exception type if an exception occurred
            exc_val: Exception instance if an exception occurred
            exc_tb: Traceback if an exception occurred

        Returns:
            False (allows exceptions to propagate)
        """
        import time
        exit_start = time.time()
        candidate_id = self.current_candidate_id or "unknown"
        wt_path = self.current_worktree_path

        if wt_path:
            try:
                self.remove_worktree(wt_path)
                duration_ms = int((time.time() - exit_start) * 1000)
                _slog(logging.INFO, "worktree_removed",
                      candidate_id=candidate_id,
                      path=wt_path,
                      success=True,
                      forced=False,
                      duration_ms=duration_ms)
            except Exception as e:
                duration_ms = int((time.time() - exit_start) * 1000)
                _slog(logging.ERROR, "worktree_error",
                      event_sub="removal_failed",
                      candidate_id=candidate_id,
                      path=wt_path,
                      error_type=type(e).__name__,
                      message=str(e),
                      duration_ms=duration_ms)
                logger.error(
                    f"Failed to cleanup worktree {wt_path}: {e}",
                    exc_info=True,
                )
                # Do not re-raise - cleanup failure should not mask original exception

        return False  # Propagate any exception that occurred in the context

    def create_worktree(self) -> str:
        """
        Create a new worktree and return its path.

        This is called automatically by __enter__() but can be used
        directly if manual management is needed.

        Implements retry logic with exponential backoff for transient errors:
        - Path already exists: regenerate candidate_id
        - Git lock file errors: retry with delay

        Returns:
            str: Absolute path to the created worktree

        Raises:
            WorktreeCreationError: If worktree creation fails after all retry attempts
        """
        import time
        
        max_attempts = 3
        
        for attempt in range(max_attempts):
            try:
                # Generate unique candidate ID
                self.current_candidate_id = self._generate_candidate_id()

                # Construct worktree path
                worktree_name = self.worktree_pattern.format(
                    candidate_id=self.current_candidate_id
                )
                self.current_worktree_path = str(
                    Path(self.worktree_parent_dir) / worktree_name
                )

                # ── Security: path-confinement check ───────────────────────
                # Ensure the resolved worktree path is strictly inside
                # worktree_parent_dir, even if the pattern or candidate_id
                # contains unusual characters that might escape the sandbox.
                resolved_wt = Path(self.current_worktree_path).resolve()
                resolved_parent = Path(self.worktree_parent_dir).resolve()
                try:
                    resolved_wt.relative_to(resolved_parent)
                except ValueError:
                    raise WorktreeCreationError(
                        f"Resolved worktree path '{resolved_wt}' is outside "
                        f"the allowed worktree_parent_dir '{resolved_parent}'.\n"
                        f"This may indicate a path-traversal attempt via "
                        f"worktree_pattern or candidate_id.",
                        git_output="path_traversal_blocked",
                    )

                # Check disk space before creating worktree
                if not self._check_disk_space():
                    raise WorktreeCreationError(
                        f"Insufficient disk space in {self.worktree_parent_dir}. "
                        f"At least 100MB free space required.",
                        git_output="disk_space_check_failed"
                    )

                # Get base branch/commit for worktree creation
                base_branch = self._get_base_branch()
                
                # Log worktree creation attempt
                if attempt > 0:
                    logger.info(
                        f"Retrying worktree creation (attempt {attempt + 1}/{max_attempts}): "
                        f"candidate_id={self.current_candidate_id}, "
                        f"path={self.current_worktree_path}, base={base_branch}"
                    )
                else:
                    logger.info(
                        f"Creating worktree: candidate_id={self.current_candidate_id}, "
                        f"path={self.current_worktree_path}, base={base_branch}"
                    )
                
                start_time = time.time()

                # Create the worktree using git worktree add
                self._run_git_command(
                    ["worktree", "add", self.current_worktree_path, base_branch]
                )

                duration_ms = int((time.time() - start_time) * 1000)

                # Task 9.1/9.3: structured log + slow-operation warning
                _slog(logging.INFO, "worktree_created",
                      candidate_id=self.current_candidate_id,
                      path=self.current_worktree_path,
                      base=base_branch,
                      attempt=attempt + 1,
                      duration_ms=duration_ms)

                if duration_ms > 5000:  # >5 s is surprisingly slow
                    _slog(logging.WARNING, "worktree_slow_creation",
                          candidate_id=self.current_candidate_id,
                          duration_ms=duration_ms,
                          threshold_ms=5000)

                return self.current_worktree_path
                
            except RuntimeError as e:
                error_str = str(e)
                error_type = self._classify_git_error(error_str)

                # Task 9.2: structured error log
                _slog(logging.WARNING, "worktree_error",
                      event_sub="creation_failed",
                      candidate_id=self.current_candidate_id,
                      error_type=error_type,
                      attempt=attempt + 1,
                      message=error_str[:200])

                # Handle transient errors with retry
                if attempt < max_attempts - 1:
                    if error_type == "path_exists":
                        logger.warning(
                            f"Path already exists, regenerating candidate_id "
                            f"(attempt {attempt + 1}/{max_attempts})"
                        )
                        # Exponential backoff
                        time.sleep(2 ** attempt)
                        continue
                    elif error_type == "lock_file":
                        backoff_time = 2 ** attempt
                        logger.warning(
                            f"Git lock file detected, retrying in {backoff_time}s "
                            f"(attempt {attempt + 1}/{max_attempts})"
                        )
                        time.sleep(backoff_time)
                        continue
                
                # All retries exhausted or non-retriable error
                error_msg = f"Failed to create worktree at {self.current_worktree_path} after {attempt + 1} attempt(s): {e}"
                logger.error(error_msg)
                raise WorktreeCreationError(
                    error_msg,
                    git_output=error_str
                )

    def apply_patch(self, worktree: str | Path, patch: str) -> ApplyResult:
        """Validate and apply a unified diff to ``worktree``.

        Tries progressively more lenient ``git apply`` option sets so that
        LLM-generated diffs — which frequently have imperfect line counts,
        whitespace, or context — still apply when semantically valid. The
        strictest option set is attempted first to preserve exact behaviour
        for well-formed patches.
        """
        worktree_path = Path(worktree).resolve()
        if not patch.strip():
            result = ApplyResult(
                success=False,
                status="failed",
                stderr="Patch content is empty.",
            )
            self.cleanup_worktree(worktree_path)
            return result

        # Option sets ordered from strictest to most lenient.
        option_sets: List[List[str]] = [
            [],                                              # strict (original behaviour)
            ["--ignore-whitespace"],                         # tolerate whitespace diffs
            ["--recount", "--ignore-whitespace"],            # fix bad @@ line counts
            ["-C1", "--recount", "--ignore-whitespace"],     # reduce required context
            ["--3way"],                                      # fall back to 3-way merge
        ]

        last_stdout = ""
        last_stderr = "Patch did not apply with any option set."

        for opts in option_sets:
            check = self._run_patch_command(
                worktree_path, ["apply", "--check", *opts, "-"], patch
            )
            if check.returncode != 0:
                last_stdout, last_stderr = check.stdout, check.stderr
                continue

            applied = self._run_patch_command(
                worktree_path, ["apply", *opts, "-"], patch
            )
            if applied.returncode != 0:
                last_stdout, last_stderr = applied.stdout, applied.stderr
                continue

            if not self.verify_clean_application(worktree_path):
                # Roll back the partial application before trying the next set.
                self._run_patch_command(
                    worktree_path, ["apply", "--reverse", *opts, "-"], patch
                )
                last_stdout = applied.stdout
                last_stderr = (
                    "Patch applied but the worktree contains conflicts "
                    "or invalid whitespace."
                )
                continue

            return ApplyResult(
                success=True,
                status="passed",
                stdout=applied.stdout,
                stderr=applied.stderr,
            )

        # Every option set failed — clean up and report the last error.
        result = ApplyResult(
            success=False,
            status="failed",
            stdout=last_stdout,
            stderr=last_stderr,
        )
        self.cleanup_worktree(worktree_path)
        return result

    def verify_clean_application(self, worktree: str | Path) -> bool:
        """Return whether an applied patch left no conflicts or diff errors."""
        worktree_path = Path(worktree).resolve()
        try:
            diff_check = subprocess.run(
                ["git", "diff", "--check"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
                check=False,
            )
            conflicts = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=U"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
                check=False,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False
        return (
            diff_check.returncode == 0
            and conflicts.returncode == 0
            and not conflicts.stdout.strip()
        )

    def cleanup_worktree(self, worktree: str | Path) -> None:
        """Remove a worktree and clear this manager's matching state."""
        worktree_path = str(Path(worktree).resolve())
        self.remove_worktree(worktree_path)
        if self.current_worktree_path == worktree_path:
            self.current_worktree_path = None
            self.current_candidate_id = None

    def _run_patch_command(
        self,
        worktree: Path,
        args: List[str],
        patch: str,
    ) -> subprocess.CompletedProcess:
        """Run a Git patch command with the patch supplied on stdin."""
        try:
            return subprocess.run(
                ["git", *args],
                cwd=worktree,
                input=patch,
                capture_output=True,
                text=True,
                timeout=self.git_timeout,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(
                args=["git", *args],
                returncode=1,
                stdout="",
                stderr=str(exc),
            )

    def remove_worktree(self, worktree_path: str) -> None:
        """
        Remove a worktree with cascading cleanup strategy.

        This method implements a 3-stage cleanup process:
        1. Normal removal: git worktree remove <path>
        2. Forced removal: git worktree remove --force <path>
        3. Manual cleanup: git worktree prune + shutil.rmtree(path)

        Each attempt is logged. If all 3 attempts fail, raises WorktreeRemovalError.

        This is called automatically by __exit__() but can be used
        directly if manual management is needed.

        Args:
            worktree_path: Path to the worktree to remove

        Raises:
            WorktreeRemovalError: If removal fails after all 3 cleanup attempts
        """
        import time

        _slog(logging.DEBUG, "worktree_remove_start", path=worktree_path)
        start_time = time.time()

        # Attempt 1: Normal removal. A disposable candidate worktree is
        # intentionally dirtied (we write the evolved file into it), so a
        # non-forced remove is EXPECTED to fail with "modified or untracked
        # files" — that's not an error, we just fall through to --force. Keep
        # this attempt quiet; genuine problems still surface in attempts 2/3.
        logger.debug(f"Removing worktree (normal): {worktree_path}")
        try:
            self._run_git_command(["worktree", "remove", worktree_path], quiet=True)
            duration_ms = int((time.time() - start_time) * 1000)
            _slog(logging.INFO, "worktree_removed",
                  path=worktree_path,
                  method="normal",
                  forced=False,
                  duration_ms=duration_ms)
            if duration_ms > 3000:  # >3 s removal is slow
                _slog(logging.WARNING, "worktree_slow_removal",
                      path=worktree_path,
                      duration_ms=duration_ms,
                      threshold_ms=3000)
            return
        except RuntimeError as e:
            _slog(logging.DEBUG, "worktree_normal_removal_failed_forcing",
                  path=worktree_path,
                  error_type=self._classify_git_error(str(e)))

        # Attempt 2: Forced removal (the expected path for a dirtied candidate
        # worktree). This is where real failures start logging loudly.
        logger.debug(f"Removing worktree (forced): {worktree_path}")
        try:
            self._run_git_command(["worktree", "remove", "--force", worktree_path])
            duration_ms = int((time.time() - start_time) * 1000)
            _slog(logging.INFO, "worktree_removed",
                  path=worktree_path,
                  method="forced",
                  forced=True,
                  duration_ms=duration_ms)
            if duration_ms > 3000:
                _slog(logging.WARNING, "worktree_slow_removal",
                      path=worktree_path,
                      duration_ms=duration_ms,
                      threshold_ms=3000)
            return
        except RuntimeError as e:
            _slog(logging.WARNING, "worktree_error",
                  event_sub="forced_removal_failed",
                  path=worktree_path,
                  error_type=self._classify_git_error(str(e)),
                  message=str(e)[:200])

        # Attempt 3: Manual cleanup (prune + filesystem removal)
        logger.info(f"Attempt 3: Manual cleanup - git worktree prune + shutil.rmtree({worktree_path})")
        try:
            # First, prune Git's worktree metadata
            self._run_git_command(["worktree", "prune"], check=False)
            logger.info("Git worktree prune executed")

            # Then, manually remove the directory if it still exists
            worktree_path_obj = Path(worktree_path)
            if worktree_path_obj.exists():
                logger.info(f"Removing directory tree: {worktree_path}")
                shutil.rmtree(worktree_path)
                logger.info(f"Directory tree removed: {worktree_path}")
            else:
                logger.info(f"Directory does not exist (already cleaned by prune): {worktree_path}")

            duration_ms = int((time.time() - start_time) * 1000)
            _slog(logging.INFO, "worktree_removed",
                  path=worktree_path,
                  method="manual_prune",
                  forced=True,
                  duration_ms=duration_ms)
            if duration_ms > 3000:
                _slog(logging.WARNING, "worktree_slow_removal",
                      path=worktree_path,
                      duration_ms=duration_ms,
                      threshold_ms=3000)
            return
        except Exception as e:
            _slog(logging.ERROR, "worktree_error",
                  event_sub="manual_cleanup_failed",
                  path=worktree_path,
                  error_type=type(e).__name__,
                  message=str(e)[:200])
            logger.error(f"Attempt 3 failed (manual cleanup): {e}", exc_info=True)

        # All 3 attempts exhausted
        duration_ms = int((time.time() - start_time) * 1000)
        error_msg = (
            f"Failed to remove worktree at {worktree_path} after 3 attempts. "
            f"Duration: {duration_ms}ms. "
            f"Manual intervention may be required."
        )
        _slog(logging.ERROR, "worktree_error",
              event_sub="all_removal_attempts_failed",
              path=worktree_path,
              attempts=3,
              duration_ms=duration_ms)
        logger.error(error_msg)
        raise WorktreeRemovalError(error_msg, attempts=3)

    def cleanup_orphans(self) -> int:
        """
        Detect and remove all orphaned worktrees from previous runs.

        An orphan is a directory that matches the worktree naming pattern but is
        either:
        - Not registered with Git (directory exists but Git doesn't know about it), or
        - Registered with Git but the directory is missing (prunable registration).

        Cleanup strategy for each orphan:
        1. ``git worktree remove --force <path>`` to deregister from Git.
        2. ``shutil.rmtree(path)`` if the directory still exists after step 1.

        Individual cleanup failures are logged but do NOT raise exceptions, so all
        orphans are attempted even when some fail.

        Returns:
            int: Number of orphaned worktrees successfully removed.
        """
        orphans = self._detect_orphans()

        if orphans:
            logger.info(
                f"event=orphans_detected count={len(orphans)} "
                f"paths={orphans}"
            )
        else:
            logger.debug("Orphan detection: no orphaned worktrees found")
            return 0

        cleaned = 0
        for path in orphans:
            try:
                # Step 1 — deregister from Git (--force ignores modifications)
                self._run_git_command(
                    ["worktree", "remove", "--force", path], check=False
                )

                # Step 2 — remove directory if it still exists
                path_obj = Path(path)
                if path_obj.exists():
                    shutil.rmtree(path)
                    logger.info(f"Cleaned orphaned worktree (filesystem): {path}")
                else:
                    logger.info(f"Cleaned orphaned worktree (git-only): {path}")

                cleaned += 1
            except Exception as e:
                logger.error(
                    f"Failed to cleanup orphan {path}: {e}",
                    exc_info=True,
                )

        logger.info(f"Orphan cleanup complete: {cleaned}/{len(orphans)} removed")
        return cleaned

    def _run_git_command(
        self,
        args: List[str],
        check: bool = True,
        timeout: Optional[int] = None,
        quiet: bool = False,
    ) -> subprocess.CompletedProcess:
        """
        Execute a Git command with error handling.

        Args:
            args: Git command arguments (e.g., ["worktree", "add", path])
            check: Whether to raise exception on non-zero exit
            timeout: Command timeout in seconds (default: self.git_timeout)

        Returns:
            subprocess.CompletedProcess: Command result

        Raises:
            subprocess.TimeoutExpired: If command times out
            RuntimeError: If command fails and check=True
        """
        timeout = timeout or self.git_timeout
        cmd = ["git"] + args
        
        try:
            result = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,  # We'll handle check manually for better error messages
            )
            
            if check and result.returncode != 0:
                error_output = result.stderr or result.stdout
                
                # Classify error type for better error messages
                error_type = self._classify_git_error(error_output)

                # ``quiet`` downgrades expected/handled failures (e.g. the first
                # non-forced worktree-remove attempt on a candidate worktree we
                # intentionally dirtied) to DEBUG so they don't spam ERROR logs.
                # The RuntimeError is still raised, so control flow is unchanged.
                (logger.debug if quiet else logger.error)(
                    f"Git command failed: {' '.join(cmd)}\n"
                    f"Exit code: {result.returncode}\n"
                    f"Error type: {error_type}\n"
                    f"Stdout: {result.stdout}\n"
                    f"Stderr: {result.stderr}"
                )
                
                # Provide actionable error messages based on error type
                # Transient errors (retriable)
                if error_type == "path_exists":
                    raise RuntimeError(
                        f"Worktree path already exists.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"This may be a leftover from a previous run. The system will retry with a new candidate ID.\n"
                        f"If retries fail, consider using cleanup_orphans() or removing the directory manually."
                    )
                elif error_type == "lock_file":
                    raise RuntimeError(
                        f"Git lock file exists - another Git operation may be in progress.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"The system will retry after a brief delay.\n"
                        f"If this persists, check for stale lock files in .git/ directory (e.g., .git/index.lock)."
                    )
                
                # Fail-fast errors (configuration/setup issues)
                elif error_type == "disk_space":
                    raise RuntimeError(
                        f"Insufficient disk space for worktree creation.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"Action required: Free up disk space in '{self.worktree_parent_dir}' and try again.\n"
                        f"Worktrees typically require space proportional to your repository size."
                    )
                elif error_type == "not_git_repo":
                    raise RepositoryValidationError(
                        f"Directory is not a valid Git repository.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"Action required: Ensure '{self.repo_root}' is a Git repository with at least one commit.\n"
                        f"Initialize with: git init && git add . && git commit -m 'Initial commit'"
                    )
                elif error_type == "invalid_ref":
                    raise RepositoryValidationError(
                        f"Invalid Git reference or branch.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"Action required: Ensure the repository has valid commits and the base branch exists.\n"
                        f"Check current branch with: git branch"
                    )
                elif error_type == "corrupted_repo":
                    raise RepositoryValidationError(
                        f"Git repository corruption detected.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"Action required: Repair the repository or use a clean clone.\n"
                        f"Try: git fsck --full"
                    )
                elif error_type == "permission_denied":
                    raise RepositoryValidationError(
                        f"Permission denied - insufficient access rights.\n"
                        f"Git error: {error_output.strip()}\n\n"
                        f"Action required: Check file/directory permissions for:\n"
                        f"  - Repository: {self.repo_root}\n"
                        f"  - Worktree parent: {self.worktree_parent_dir}"
                    )
                else:
                    raise RuntimeError(
                        f"Git command failed with exit code {result.returncode}.\n"
                        f"Command: {' '.join(cmd)}\n"
                        f"Error output: {error_output.strip()}\n\n"
                        f"Review the error output above for details."
                    )
            
            return result
            
        except subprocess.TimeoutExpired:
            logger.error(f"Git command timed out after {timeout}s: {' '.join(cmd)}")
            raise RuntimeError(
                f"Git command timed out after {timeout}s.\n"
                f"Command: {' '.join(cmd)}\n\n"
                f"This may indicate:\n"
                f"  - Slow filesystem operations (network drives, encrypted storage)\n"
                f"  - Large repository with many files\n"
                f"  - System resource constraints\n\n"
                f"Consider increasing git_timeout if operations are legitimately slow."
            )
        except FileNotFoundError:
            logger.error("Git executable not found in PATH")
            raise GitVersionError(
                required="2.5",
                found="not installed"
            )
    
    def _classify_git_error(self, error_output: str) -> str:
        """
        Classify Git error type from command output.
        
        Args:
            error_output: Error message from Git command
            
        Returns:
            Error type classification for retry/fail-fast decisions
            
        Error types:
        - 'path_exists': Worktree path already exists (transient, retriable)
        - 'lock_file': Git lock file preventing operation (transient, retriable)
        - 'disk_space': Insufficient disk space (fail-fast)
        - 'not_git_repo': Directory is not a Git repository (fail-fast)
        - 'invalid_ref': Invalid branch/commit reference (fail-fast)
        - 'corrupted_repo': Repository corruption detected (fail-fast)
        - 'permission_denied': Permission issues (fail-fast)
        - 'unknown': Unclassified error
        """
        error_lower = error_output.lower()
        
        # Transient errors (retriable)
        if "already exists" in error_lower or "path exists" in error_lower:
            return "path_exists"
        elif "index.lock" in error_lower or ("unable to create" in error_lower and "lock" in error_lower):
            return "lock_file"
        
        # Fail-fast errors (configuration/setup issues)
        elif "no space left" in error_lower or "disk full" in error_lower or "not enough space" in error_lower:
            return "disk_space"
        elif "not a git repository" in error_lower or "not found in" in error_lower:
            return "not_git_repo"
        elif "invalid reference" in error_lower or "unknown revision" in error_lower or "bad revision" in error_lower:
            return "invalid_ref"
        elif "corrupt" in error_lower or "broken" in error_lower:
            return "corrupted_repo"
        elif "permission denied" in error_lower or "access denied" in error_lower:
            return "permission_denied"
        else:
            return "unknown"

    def _validate_repository(self) -> None:
        """
        Validate that repo_root is a valid Git repository.

        Checks:
        - Directory exists
        - .git directory exists
        - Repository has at least one commit
        - Git version >= 2.5

        Raises:
            RepositoryValidationError: If validation fails
            GitVersionError: If Git version is insufficient
        """
        # Check if directory exists
        repo_path = Path(self.repo_root)
        if not repo_path.exists():
            raise RepositoryValidationError(
                f"Repository directory does not exist: '{self.repo_root}'\n\n"
                f"Action required: Create the directory or verify the path is correct."
            )
        
        if not repo_path.is_dir():
            raise RepositoryValidationError(
                f"Repository path is not a directory: '{self.repo_root}'\n\n"
                f"Action required: Provide a valid directory path, not a file."
            )
        
        # Check if .git directory exists
        git_dir = repo_path / ".git"
        if not git_dir.exists():
            raise RepositoryValidationError(
                f"Directory '{self.repo_root}' is not a Git repository.\n\n"
                f"Action required: Initialize Git repository with:\n"
                f"  cd {self.repo_root}\n"
                f"  git init\n"
                f"  git add .\n"
                f"  git commit -m 'Initial commit'"
            )
        
        # Check Git version >= 2.5
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            version_output = result.stdout.strip()
            # Extract version number from "git version X.Y.Z"
            version_parts = version_output.split()
            if len(version_parts) >= 3:
                version_str = version_parts[2]
                # Parse major.minor version
                version_numbers = version_str.split(".")
                if len(version_numbers) >= 2:
                    try:
                        major = int(version_numbers[0])
                        minor = int(version_numbers[1])
                        
                        # Check if version >= 2.5
                        if major < 2 or (major == 2 and minor < 5):
                            raise GitVersionError(
                                required="2.5",
                                found=f"{major}.{minor}"
                            )
                    except ValueError:
                        # Could not parse version numbers, log warning but continue
                        logger.warning(f"Could not parse Git version: {version_str}")
        except FileNotFoundError:
            raise GitVersionError(
                required="2.5",
                found="not installed"
            )
        except subprocess.TimeoutExpired:
            raise RepositoryValidationError(
                "Git version check timed out.\n\n"
                "Action required: Check your Git installation and system responsiveness."
            )
        except subprocess.CalledProcessError as e:
            raise RepositoryValidationError(
                f"Failed to check Git version.\n"
                f"Error: {e.stderr if e.stderr else str(e)}\n\n"
                f"Action required: Verify Git is properly installed and accessible."
            )
        
        # Check if repository has at least one commit
        result = self._run_git_command(["rev-parse", "HEAD"], check=False)
        if result.returncode != 0:
            raise RepositoryValidationError(
                f"Repository '{self.repo_root}' has no commits.\n\n"
                f"Action required: Create an initial commit:\n"
                f"  cd {self.repo_root}\n"
                f"  git add .\n"
                f"  git commit -m 'Initial commit'\n\n"
                f"Worktrees require at least one commit to function properly."
            )
        
        logger.info(f"Repository validation successful: {self.repo_root}")

    def _detect_orphans(self) -> List[str]:
        """
        Detect orphaned worktree directories.

        An orphan is a directory matching the worktree naming pattern that is:
        - Present on disk but **not** registered with Git (``git worktree list``), or
        - Registered with Git but its directory is **missing** (prunable entry).

        Detection algorithm:
        1. Glob ``worktree_parent_dir`` for directories matching ``worktree_pattern``.
        2. Run ``git worktree list --porcelain`` and collect all registered paths.
        3. Any glob match not in the registered set → filesystem orphan.
        4. Any registered entry marked ``prunable`` whose directory is gone → Git orphan.

        Returns:
            List[str]: Absolute string paths of orphaned worktrees.
        """
        parent = Path(self.worktree_parent_dir)

        # Build glob pattern from the worktree name template
        glob_pattern = self.worktree_pattern.replace("{candidate_id}", "*")
        potential_dirs = [p for p in parent.glob(glob_pattern) if p.is_dir()]

        # Fetch all worktrees known to Git
        result = self._run_git_command(
            ["worktree", "list", "--porcelain"], check=False
        )
        registered = WorktreeInfo.parse_from_git_list(result.stdout)
        # Use resolved absolute paths for reliable comparison
        registered_paths = {str(Path(wt.path).resolve()) for wt in registered}

        orphans: List[str] = []

        # Case A: directory exists on disk but Git doesn't know about it
        for dir_path in potential_dirs:
            resolved = str(dir_path.resolve())
            if resolved not in registered_paths:
                orphans.append(str(dir_path))
                logger.debug(f"Orphan (unregistered dir): {dir_path}")

        # Case B: Git knows about it but the directory is gone (prunable)
        for wt in registered:
            wt_path = Path(wt.path)
            if wt.prunable and not wt_path.exists():
                # Only report orphans that are inside our managed parent dir
                try:
                    wt_path.relative_to(parent)
                    orphans.append(str(wt_path))
                    logger.debug(f"Orphan (prunable git entry): {wt_path}")
                except ValueError:
                    pass  # Not our worktree — skip

        return orphans

    def _check_disk_space(self) -> bool:
        """
        Check whether sufficient free disk space is available in worktree_parent_dir.

        Uses shutil.disk_usage() for cross-platform compatibility (Windows + Linux/macOS).
        Requires at least ``self.min_disk_space_mb`` MB of free space.

        Returns:
            True  — enough space is available, creation may proceed.
            False — insufficient space; caller should abort and raise an error.
        """
        import shutil

        try:
            usage = shutil.disk_usage(self.worktree_parent_dir)
            available_mb = usage.free / (1024 * 1024)
            if available_mb < self.min_disk_space_mb:
                logger.warning(
                    f"Low disk space: {available_mb:.1f}MB available in "
                    f"'{self.worktree_parent_dir}'. "
                    f"Minimum required: {self.min_disk_space_mb}MB. "
                    f"Worktree creation may fail."
                )
                return False
            logger.debug(
                f"Disk space OK: {available_mb:.1f}MB available "
                f"(minimum: {self.min_disk_space_mb}MB)"
            )
            return True
        except OSError as e:
            # Cannot determine free space (e.g., network filesystem, unusual mount).
            # Log a warning but allow the operation to continue — Git will surface
            # the actual error if disk really is full.
            logger.warning(
                f"Could not check disk space in '{self.worktree_parent_dir}': {e}. "
                f"Proceeding with worktree creation."
            )
            return True

    def _generate_candidate_id(self) -> str:
        """
        Generate a unique candidate ID.

        Uses UUID4 for uniqueness across processes.

        Returns:
            str: Unique candidate identifier
        """
        import uuid
        return str(uuid.uuid4())

    def _get_base_branch(self) -> str:
        """
        Determine the base branch/commit for worktree creation.

        Handles both normal and detached HEAD states (task 3.5):
        - Normal branch: returns ``"HEAD"`` so Git uses the current branch tip.
        - Detached HEAD: returns the full commit SHA so the worktree is based
          on the correct commit even without a branch name.

        Uncommitted changes in the main workspace do NOT affect worktree creation
        because Git worktrees operate on committed state only.

        Returns:
            str: ``"HEAD"`` when on a branch, or a 40-char commit SHA when detached.
        """
        try:
            # Returns exit-code 0 only when HEAD points at a branch reference.
            # Exit-code 1 means detached HEAD.
            result = self._run_git_command(
                ["symbolic-ref", "-q", "HEAD"], check=False
            )

            if result.returncode == 0:
                # On a branch — use the symbolic HEAD so the worktree tracks the
                # same branch tip as the main workspace.
                return "HEAD"
            else:
                # Detached HEAD — use the concrete commit SHA to avoid
                # "fatal: not a branch" errors from git worktree add.
                result = self._run_git_command(["rev-parse", "HEAD"])
                commit_sha = result.stdout.strip()
                logger.info(
                    f"Repository is in detached HEAD state. "
                    f"Using commit SHA as worktree base: {commit_sha[:8]}…"
                )
                return commit_sha
        except Exception as e:
            logger.warning(f"Failed to determine base branch, defaulting to HEAD: {e}")
            return "HEAD"
