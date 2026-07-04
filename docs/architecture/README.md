# LoopBench Architecture

Per-subsystem architecture references. Each document is self-contained with
Mermaid diagrams that render directly on GitHub.

| Subsystem | What it does |
|-----------|--------------|
| [Optimizer Loop](optimizer-loop.md) | The generation orchestrator that ties every subsystem together |
| [Ghost Worktree System](ghost-worktree-system.md) | Isolated, disposable git worktrees per candidate |
| [Repo Context Mapper](repo-context-mapper.md) | Builds an LLM-ready, token-budgeted map of the repository |
| [LLM Editing Engine](llm-editing.md) | Full-rewrite / search-replace / auto edit strategies |
| [Docker Sandbox](docker-sandbox.md) | Network-isolated, read-only execution of any test/benchmark command |
| [Candidate Database](candidate-database.md) | SQLite audit trail of runs, candidates, and events |
| [Search Strategy](search-strategy.md) | How the next baseline is chosen each generation |

## System overview

```mermaid
flowchart TB
    CLI[loopbench CLI] --> OL[Optimizer Loop]

    OL --> RCM[Repo Context Mapper]
    OL --> LLM[LLM Editing Engine]
    OL --> GW[Ghost Worktree System]
    OL --> SB[Docker Sandbox]
    OL --> DB[Candidate Database]
    OL --> SS[Search Strategy]

    RCM -->|context map| LLM
    LLM -->|edit + diff| GW
    GW -->|isolated code| SB
    SB -->|score.json| DB
    DB -->|history| SS
    SS -->|next baseline| OL
```

## End-to-end run

From the command line to the output artifacts:

```mermaid
flowchart TB
    U([Developer]) -->|"loopbench run --target REPO --metric latency"| CLI[loopbench CLI]
    CLI --> RM[Resolve repo<br/>clone URL or local path]
    RM --> DET[Detect language and test command]
    DET --> BASE[Establish baseline score]
    BASE --> MAP

    subgraph LOOP [OptimizerLoop - repeats each generation]
      direction TB
      MAP[1 Map repo context] --> GEN[2 Generate edit with LLM]
      GEN --> APP[3 Apply edit and compute valid diff]
      APP --> TEST[4 Test in Docker sandbox]
      TEST --> REC[5 Score and record to SQLite]
      REC --> SEL[6 Select next baseline]
      SEL -.->|next generation| MAP
    end

    SEL --> OUT
    subgraph OUT [Output artifacts]
      direction TB
      A1[best.patch]
      A2[report/validation_report.md]
      A3[docs/data.json]
      A4[test_log.txt]
    end
```

## One generation, step by step

```mermaid
sequenceDiagram
    autonumber
    participant L as OptimizerLoop
    participant M as RepoContextMapper
    participant E as LLMEnsemble
    participant W as WorkspaceManager
    participant S as Docker Sandbox
    participant DB as CandidateDatabase

    L->>M: get_context_map(repo, target)
    M-->>L: ContextMap (relevant files)
    L->>E: generate(prompt)
    E-->>L: improved file / SEARCH-REPLACE blocks
    L->>L: apply edit + compute diff via difflib
    L->>W: create_worktree()
    W-->>L: isolated worktree path
    L->>S: run tests (--network=none, read-only mount)
    S-->>L: score.json (passed, speed_ms, combined_score)
    L->>DB: insert_candidate(patch, metrics, score)
    L->>W: remove_worktree()
    Note over L: SearchStrategy selects the next baseline<br/>(best candidate is kept separately, never regresses)
```

## Core components

```mermaid
classDiagram
    class OptimizerLoop {
      +str repo_path
      +str target_file
      +str test_file
      +int max_iterations
      +str rewrite_mode
      +run() dict
      +establish_baseline() dict
      +execute_generation(gen, baseline) dict
    }
    class WorkspaceManager {
      +str repo_root
      +str worktree_pattern
      +create_worktree() str
      +apply_patch(worktree, patch) ApplyResult
      +remove_worktree(path)
      +cleanup_orphans() int
    }
    class LLMEnsemble {
      +generate(prompt) str
      +generate_patch(prompt) str
    }
    class CandidateDatabase {
      +create_run() str
      +insert_candidate() str
      +export_run(run_id) dict
      +get_best_candidate() dict
    }
    class Sandbox {
      +run_in_sandbox(program, test) dict
      +verify_output_streams() bool
    }
    class SearchStrategy {
      <<abstract>>
      +select_baseline(history, generation)
      +should_parallelize() bool
    }

    OptimizerLoop --> LLMEnsemble : generates edits
    OptimizerLoop --> WorkspaceManager : isolates edits
    OptimizerLoop --> Sandbox : runs tests
    OptimizerLoop --> CandidateDatabase : records lineage
    OptimizerLoop --> SearchStrategy : picks next baseline
```
