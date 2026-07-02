# Candidate Database

`openevolve/database.py` — a SQLite audit trail (via SQLAlchemy) that records
every run, every candidate, and every event with full parent-child lineage.
Foreign keys are enforced (`PRAGMA foreign_keys=ON`).

## Schema

```mermaid
erDiagram
    RUNS ||--o{ CANDIDATES : has
    RUNS ||--o{ AUDIT_LOG : has
    CANDIDATES ||--o{ CANDIDATES : parent_of
    CANDIDATES ||--o{ AUDIT_LOG : referenced_by

    RUNS {
      string id PK
      string target_repo
      float start_time
      float end_time
      string status
      float success_threshold
      float final_improvement
      text config_json
    }
    CANDIDATES {
      string id PK
      string run_id FK
      int generation
      string parent_id FK
      text patch_content
      bool applied
      bool tested
      int exit_code
      text metrics_json
      float score
      bool failed
      string failure_phase
      text error_message
    }
    AUDIT_LOG {
      int id PK
      string run_id FK
      string candidate_id FK
      float timestamp
      string event_type
      text event_data
    }
```

## Event trail

Every meaningful step appends an `audit_log` row, giving a replayable history:

```mermaid
flowchart LR
    A[run_created] --> B[generation_start]
    B --> C[patch_generated]
    C --> D[patch_applied]
    D --> E[test_executed]
    E --> F[metrics_extracted]
    F --> G[candidate_recorded]
    G --> H[run_completed]
```

## Key operations

| Method | Role |
|--------|------|
| `create_run(...)` | Open a run row + `run_created` event |
| `insert_candidate(...)` | Insert/update a candidate (upsert) + event |
| `update_candidate_results(...)` | Attach test output, metrics, score |
| `get_recent_failures(window)` | Feed prior failures back into the prompt |
| `get_best_candidate(run_id)` | Highest-scoring candidate for a run |
| `complete_run(...)` | Close the run + record final improvement |
| `export_run(run_id)` | Full JSON export (feeds `docs/data.json`) |
| `export_audit_trail(run_id)` | Human-readable Markdown of the whole run |

## Storage notes

- A `.db`/`.sqlite` path is used directly as the audit database.
- A directory path stores audit data at `optimizer_audit.sqlite3` inside it.
- `:memory:` keeps everything in-memory (used by tests).
