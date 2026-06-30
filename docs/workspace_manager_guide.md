# WorkspaceManager Usage Guide

## Overview

The **WorkspaceManager** provides isolated workspace management for the LoopBench Optimizer using Git worktrees. Each candidate program is evaluated inside its own temporary Git worktree — a lightweight clone of your repository that shares the same `.git` history but has an independent working directory. This ensures:

- **Isolation**: Code mutations never affect the main workspace.
- **Safety**: Worktrees are automatically cleaned up even when exceptions occur.
- **Concurrency**: Multiple candidates can be evaluated in parallel without conflicts.

Used internally by `OptimizerLoop` in Phase 3 of every generation cycle. You can also use it directly.

---

## Quick Start

```python
from openevolve.workspace_manager import WorkspaceManager

# Create a context-managed worktree
with WorkspaceManager(repo_root="/path/to/your/repo") as worktree_path:
    # worktree_path is a real directory with all committed files
    from pathlib import Path

    # Write candidate code
    Path(worktree_path, "program.py").write_text("def solution(): return 42")

    # Evaluate it (the main repo is unaffected)
    score = my_evaluator(worktree_path)

# Worktree is automatically removed here, even if an exception was raised
```

---

## Configuration Options

| Parameter               | Type   | Default                             | Description                                                                 |
|-------------------------|--------|--------------------------------------|-----------------------------------------------------------------------------|
| `repo_root`             | `str`  | *(required)*                         | Absolute path to the Git repository root.                                   |
| `worktree_parent_dir`   | `str`  | Parent of `repo_root`                | Directory where worktree directories will be created.                       |
| `git_timeout`           | `int`  | `30`                                 | Timeout (seconds) for each Git subprocess command.                          |
| `worktree_pattern`      | `str`  | `temp_worktree_{candidate_id}`       | Naming template. Must contain `{candidate_id}`.                             |
| `min_disk_space_mb`     | `int`  | `100`                                | Minimum free disk space (MB) required before creating a worktree.           |
| `auto_cleanup_orphans`  | `bool` | `False`                              | If `True`, scans and removes orphaned worktrees during `__init__`.          |

### Example with custom configuration

```python
with WorkspaceManager(
    repo_root="/home/user/project",
    worktree_parent_dir="/tmp/worktrees",
    git_timeout=60,
    min_disk_space_mb=500,
    auto_cleanup_orphans=True,
) as wt:
    ...
```

---

## Error Handling and Recovery

### Exception Hierarchy

```
WorkspaceError (base)
├── WorktreeCreationError    — Worktree could not be created
├── WorktreeRemovalError     — All 3 cleanup stages failed
├── RepositoryValidationError — Repo is invalid, corrupt, or inaccessible
└── GitVersionError          — Git not found or version < 2.5
```

### Automatic Retry Logic

`create_worktree()` automatically retries up to **3 times** for transient errors:

| Error Type         | Recovery Action                              |
|--------------------|----------------------------------------------|
| `path_exists`      | Regenerate candidate ID and retry            |
| `lock_file`        | Wait with exponential backoff and retry      |
| `disk_space`       | Fail immediately with actionable message     |
| `not_git_repo`     | Fail immediately — configuration error       |
| `permission_denied`| Fail immediately — environment error         |

### Cascading Cleanup

`remove_worktree()` uses a 3-stage cascade:

1. **Normal**: `git worktree remove <path>`
2. **Forced**: `git worktree remove --force <path>`
3. **Manual**: `git worktree prune` + `shutil.rmtree(path)`

If all 3 stages fail, a `WorktreeRemovalError` is raised. When called from `__exit__`, this error is **logged but not re-raised**, so it never masks an exception from inside the `with` block.

---

## Integration with OpenEvolve

### Via `run_iteration_with_shared_db()`

Pass a `workspace_manager_config` dict to enable isolated evaluation:

```python
from openevolve.iteration import run_iteration_with_shared_db

await run_iteration_with_shared_db(
    iteration=0,
    config=config,
    database=db,
    evaluator=evaluator,
    llm_ensemble=llm,
    prompt_sampler=sampler,
    workspace_manager_config={
        "repo_root": "/path/to/repo",
        "worktree_parent_dir": "/tmp/worktrees",
        "auto_cleanup_orphans": True,
    },
)
```

When `workspace_manager_config` is `None` (default), the original behaviour is preserved — no worktrees are used.

### Via `evaluate_program(workspace_path=...)`

```python
scores = await evaluator.evaluate_program(
    program_code=candidate_code,
    program_id="candidate-001",
    workspace_path="/tmp/worktrees/temp_worktree_abc123",
)
```

When `workspace_path` is provided, the program file is written to that directory instead of a system temp file.

### Via Docker sandbox

```python
from sandbox.runner import run_in_sandbox

result = run_in_sandbox(
    program_path="/tmp/worktree/program.py",
    test_file="/tests/test_solution.py",
    worktree_path="/tmp/worktree",
)
```

When `worktree_path` is provided, the worktree directory is mounted directly as `/workspace:ro` inside the Docker container.

---

## Orphan Detection and Cleanup

Orphans are worktree directories left behind by crashed processes. They are detected by comparing:

1. **Filesystem**: Directories matching the `worktree_pattern` in `worktree_parent_dir`.
2. **Git registry**: Entries from `git worktree list --porcelain`.

A directory is an orphan if it matches the pattern but is **not registered** with Git (or is registered but marked as **prunable**).

### Manual cleanup

```python
mgr = WorkspaceManager(repo_root="/path/to/repo")
cleaned_count = mgr.cleanup_orphans()
print(f"Cleaned {cleaned_count} orphaned worktrees")
```

### Automatic cleanup on init

```python
# Orphans are cleaned up automatically during initialization
mgr = WorkspaceManager(
    repo_root="/path/to/repo",
    auto_cleanup_orphans=True,
)
```

---

## Logging and Observability

### Structured logs

All logs use a `key=value` format with an `event=` prefix:

```
event=worktree_created candidate_id=abc-123 path=/tmp/wt duration_ms=87
event=worktree_removed candidate_id=abc-123 success=True forced=False duration_ms=12
event=worktree_error event_sub=creation_failed error_type=path_exists attempt=2
event=orphans_detected count=3 paths=['/tmp/wt1', '/tmp/wt2', '/tmp/wt3']
event=worktree_slow_creation duration_ms=6500 threshold_ms=5000
```

### Log level control

Set the `WORKSPACE_LOG_LEVEL` environment variable:

```bash
# Full trace output
export WORKSPACE_LOG_LEVEL=DEBUG

# Only warnings and errors
export WORKSPACE_LOG_LEVEL=WARNING
```

This only affects the workspace manager module — it does not change the log level of the rest of the application.

### Performance warnings

| Event                      | Threshold | Meaning                             |
|----------------------------|-----------|--------------------------------------|
| `worktree_slow_creation`   | > 5 000 ms | Worktree creation took unusually long |
| `worktree_slow_removal`    | > 3 000 ms | Worktree removal took unusually long  |

---

## Troubleshooting

### "RepositoryValidationError: not a valid Git repository"

- Ensure `repo_root` points to a directory containing a `.git` directory.
- The repository must have at least one commit.

### "GitVersionError: Git version too old"

- Git ≥ 2.5 is required for worktree support.
- Check your version: `git --version`
- Update Git if needed.

### "WorktreeCreationError: insufficient disk space"

- The default minimum is 100 MB. Worktrees require roughly the same space as the working tree.
- Free up space or increase the `min_disk_space_mb` threshold.

### Orphaned worktrees accumulating

- Run `mgr.cleanup_orphans()` manually, or set `auto_cleanup_orphans=True`.
- You can also clean up manually:
  ```bash
  git worktree prune
  rm -rf /path/to/worktree_parent_dir/temp_worktree_*
  ```

### "WorktreeRemovalError: Failed after 3 attempts"

- A worktree could not be removed via Git or the filesystem.
- Check for locked files or permission issues.
- Manual cleanup: `git worktree remove --force <path>` or `rm -rf <path>`.

### Concurrent access issues

- Each `WorkspaceManager` instance manages exactly one worktree.
- Create separate instances for separate threads — do **not** share instances across threads.

---

## Security Considerations

The Ghost Worktree System is designed with defence-in-depth. The following controls are in place:

### Subprocess Injection Prevention

All Git commands are executed with **list arguments** — never as shell strings:

```python
# ✅ Safe — list args, shell=False (default)
subprocess.run(["git", "worktree", "add", path, base], ...)

# ❌ Never done — vulnerable to injection
subprocess.run(f"git worktree add {path} {base}", shell=True)
```

### Timeout Enforcement

Every `subprocess.run` call enforces `timeout=git_timeout` (default 30 s). If Git hangs, a `RepositoryValidationError` or `RuntimeError` is raised and cleanup proceeds.

### Path Traversal Prevention

Two layers protect against path traversal:

**1. `worktree_pattern` validation** (on `__init__`):

```python
# These patterns are rejected with ValueError:
WorkspaceManager(..., worktree_pattern="../../escape_{candidate_id}")  # '..' detected
WorkspaceManager(..., worktree_pattern="sub/dir_{candidate_id}")       # '/' detected
WorkspaceManager(..., worktree_pattern="no_placeholder")               # missing {candidate_id}
```

**2. Path-confinement check** (on each `create_worktree()`):

The resolved worktree path is validated to be strictly inside `worktree_parent_dir` using `Path.relative_to()`. If it escapes, a `WorktreeCreationError` is raised and no Git command is executed.

### Repository Root Isolation

`worktree_parent_dir` is validated to be **outside** `repo_root`. Creating worktrees inside the repository would contaminate the repository's working tree.

### Disk Space Guard

`_check_disk_space()` is called before every worktree creation. If free space is below `min_disk_space_mb` (default 100 MB), creation is refused with a `WorktreeCreationError`.

### Docker Sandbox

When a worktree path is passed to the Docker sandbox runner, the directory is mounted as **read-only** (`ro`):

```
-v /tmp/worktree:/workspace:ro
```

This prevents the evaluated program from modifying the source files.

### Audit Checklist

| Control | Status |
|---|---|
| Subprocess uses list args (no shell=True) | ✅ Verified |
| Timeout on every Git call | ✅ Verified |
| worktree_pattern rejects `..`, `/`, `\` | ✅ Added (task 11.5) |
| Path-confinement check on create | ✅ Added (task 11.5) |
| worktree_parent_dir outside repo_root | ✅ Verified |
| Disk space check before creation | ✅ Verified |
| Docker mount is read-only | ✅ Verified |
| UUID candidate IDs (unpredictable paths) | ✅ Verified |
