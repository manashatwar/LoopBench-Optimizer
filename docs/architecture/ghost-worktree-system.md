# Ghost Worktree System

`openevolve/workspace_manager.py` — `WorkspaceManager` gives each candidate its
own throwaway git worktree so mutations never touch the developer's checkout.
It is a context manager: the worktree is created on `__enter__` and always
cleaned up on `__exit__`, even when an exception is raised.

## Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant L as Iteration Loop
    participant W as WorkspaceManager
    participant G as Git CLI
    participant FS as Filesystem

    L->>W: with WorkspaceManager(repo_root) as wt
    W->>W: _generate_candidate_id()
    W->>W: _validate_repository() + _check_disk_space()
    W->>G: git worktree add <path> HEAD
    G->>FS: create directory + checkout files
    G-->>W: success
    W-->>L: worktree path
    Note over L: apply patch, run tests in the worktree
    L->>W: __exit__ (cleanup)
    W->>G: git worktree remove <path>
    alt normal remove fails
        W->>G: git worktree remove --force <path>
        alt still fails
            W->>G: git worktree prune
            W->>FS: shutil.rmtree(path)
        end
    end
    W-->>L: worktree removed
```

## 3-stage cascading cleanup

Removal never leaves ghosts behind — it escalates through three strategies:

```mermaid
flowchart LR
    R[remove_worktree] --> S1[1. git worktree remove]
    S1 -->|ok| DONE([removed])
    S1 -->|fail| S2[2. git worktree remove --force]
    S2 -->|ok| DONE
    S2 -->|fail| S3[3. git worktree prune + shutil.rmtree]
    S3 -->|ok| DONE
    S3 -->|fail| ERR([WorktreeRemovalError])
```

## Patch application (lenient)

`apply_patch` tries progressively more tolerant `git apply` option sets so a
semantically valid patch still lands despite minor formatting drift:

```
1. strict            git apply
2. --ignore-whitespace
3. --recount --ignore-whitespace
4. -C1 --recount --ignore-whitespace
5. --3way
```

Each attempt is checked (`git apply --check`) before it mutates files; a failed
verification is rolled back before the next option set is tried.

## Class

```mermaid
classDiagram
    class WorkspaceManager {
      +str repo_root
      +str worktree_parent_dir
      +int git_timeout
      +str worktree_pattern
      +int min_disk_space_mb
      +bool auto_cleanup_orphans
      +__enter__() str
      +__exit__(exc_type, exc, tb) bool
      +create_worktree() str
      +apply_patch(worktree, patch) ApplyResult
      +remove_worktree(path)
      +cleanup_orphans() int
      -_validate_repository()
      -_detect_orphans() List~str~
      -_check_disk_space() bool
      -_generate_candidate_id() str
    }
    class ApplyResult {
      +bool success
      +str status
      +str stdout
      +str stderr
      +error_output() str
    }
    WorkspaceManager --> ApplyResult : returns
```

## Safety properties

- **Path confinement** — resolved worktree path must stay inside
  `worktree_parent_dir`; traversal attempts are rejected.
- **Disk guard** — refuses to create a worktree below `min_disk_space_mb`.
- **Orphan sweep** — `cleanup_orphans()` (optionally on init) removes
  `temp_worktree_*` directories left by crashed runs.
