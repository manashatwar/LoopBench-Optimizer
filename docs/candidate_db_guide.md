# Candidate Database Guide

The **Candidate Database** extends OpenEvolve's `ProgramDatabase` with a SQLite audit trail that tracks every optimization candidate, run, and event. All data is stored persistently so no progress is ever lost.

## Quick Start

```python
from openevolve.database import CandidateDatabase

# In-memory (tests / one-off runs)
db = CandidateDatabase(":memory:")

# File-backed (production)
db = CandidateDatabase("optimizer.db")

# Create a run
run_id = db.create_run(
    run_id="my-run-001",
    target_repo="https://github.com/org/repo",
    success_threshold=0.10,
)

# Insert a candidate
cid = db.insert_candidate(
    run_id=run_id,
    generation=1,
    parent_id=None,
    patch_content="--- a/main.py\n+++ b/main.py\n...",
    score=0.75,
    metrics={"combined_score": 0.75, "latency": 23.4},
    failed=False,
    applied=True,
    tested=True,
    exit_code=0,
    stdout="Tests passed",
    stderr="",
    execution_time=2.1,
)

# Query
best = db.get_best_candidate(run_id=run_id)
failures = db.get_recent_failures(window=5, run_id=run_id)

# Export complete run
export = db.export_run(run_id)

# Close when done (file-backed only)
db.close()
```

## Database Schema

### Tables

```sql
-- Top-level runs
CREATE TABLE runs (
    id                TEXT PRIMARY KEY,
    target_repo       TEXT NOT NULL DEFAULT '',
    start_time        REAL NOT NULL,
    end_time          REAL,
    config_json       TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL,          -- 'running' | 'completed' | 'successful' | 'failed'
    success_threshold REAL NOT NULL DEFAULT 0.0,
    final_improvement REAL,
    metadata_json     TEXT
);

-- Optimization attempts
CREATE TABLE candidates (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES runs(id),
    generation      INTEGER NOT NULL,
    parent_id       TEXT REFERENCES candidates(id),
    timestamp       REAL NOT NULL,
    patch_content   TEXT NOT NULL DEFAULT '',
    applied         INTEGER NOT NULL DEFAULT 0,
    tested          INTEGER NOT NULL DEFAULT 0,
    exit_code       INTEGER,
    stdout          TEXT,
    stderr          TEXT,
    execution_time  REAL,
    metrics_json    TEXT,
    score           REAL,
    failed          INTEGER NOT NULL DEFAULT 0,
    failure_phase   TEXT,
    error_message   TEXT
);

-- Audit trail events
CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT NOT NULL REFERENCES runs(id),
    candidate_id TEXT REFERENCES candidates(id),
    timestamp    REAL NOT NULL,
    event_type   TEXT NOT NULL,
    event_data   TEXT NOT NULL
);
```

**Indexes**: generation+score, run_id+timestamp, parent_id, run_id+event_timestamp.

## API Reference

### Run Management

```python
# Start a run
run_id = db.create_run(
    run_id="abc123",          # optional — auto-generated if omitted
    target_repo="https://...",
    config={"max_iterations": 50},
    success_threshold=0.10,
)

# Complete a run
db.complete_run(run_id, status="successful", final_improvement=0.25)

# Query
run = db.get_run(run_id)    # -> dict | None
runs = db.get_all_runs()    # -> list[dict], newest first
```

### Candidate Management

```python
# Insert (upserts on id conflict)
cid = db.insert_candidate(
    run_id=run_id,
    generation=1,
    parent_id=None,          # None for baseline candidates
    patch_content="...",
    score=0.8,
    metrics={"combined_score": 0.8},
    failed=False,
    applied=True,
    tested=True,
    exit_code=0,
    stdout="ok",
    stderr="",
    execution_time=1.5,
)

# Update results after testing
db.update_candidate_results(
    cid,
    metrics={"combined_score": 0.85},
    score=0.85,
    exit_code=0,
    stdout="Tests passed",
    tested=True,
)

# Record a failure
db.record_failure(
    candidate_id=cid,
    run_id=run_id,
    generation=2,
    parent_id=baseline_id,
    failure_phase="apply",
    error_message="patch conflict on line 42",
    patch_content="...",
)

# Query
candidate = db.get_candidate(cid)
best = db.get_best_candidate(run_id=run_id, before_generation=10)
failures = db.get_recent_failures(window=5, run_id=run_id)
```

### Audit Trail

```python
# Log an event (OptimizerLoop calls this automatically)
db.log_event(
    "generation_start",
    {"generation": 1, "baseline_id": "..."},
    candidate_id=cid,        # optional
)

# Supported event types
# generation_start, patch_generated, patch_applied,
# test_executed, metrics_extracted, candidate_scored

# Query
events = db.get_audit_log(
    run_id=run_id,
    event_type="patch_generated",   # optional filter
    candidate_id=cid,               # optional filter
)

# Export as Markdown
markdown = db.export_audit_trail(run_id=run_id)
db.export_audit_trail(run_id=run_id, output_path="report/audit.md")
```

### Export

```python
# Full run export (JSON-serialisable dict)
export = db.export_run(run_id)
# Keys: run, candidates, programs, best_candidate, failures, audit_log

# Write to file
db.export_run(run_id, path="results/run_export.json")
```

## With OptimizerLoop

`OptimizerLoop` manages the database automatically. You rarely need to interact with it directly during a run. After a run completes:

```python
from openevolve.optimizer_loop import OptimizerLoop

loop = OptimizerLoop(config)
result = loop.run()

run_id = result["run_id"]
export = result["export"]              # Full export dict
best = result["best_candidate"]        # Best candidate dict
audit = loop.db.export_audit_trail()   # Markdown audit trail
```

## Using the CLI

```bash
# Export a completed run
optimizer export --run-id <id> --format json
optimizer export --run-id <id> --format markdown

# Launch dashboard (generates docs/data.json)
optimizer dashboard --run-id <id> --no-server
```

## Best Practices

- Always use `parent_id=None` for baseline (generation-0) candidates.
- Use `record_failure()` for failed candidates to keep the audit trail clean.
- Call `db.close()` for file-backed databases when done.
- Use `":memory:"` for tests to avoid disk I/O.
