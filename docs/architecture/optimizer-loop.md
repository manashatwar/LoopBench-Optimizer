# Optimizer Loop

`openevolve/optimizer_loop.py` — the orchestrator. It establishes a baseline,
then runs generations until the metric stops improving (early stopping via
`patience`) or `max_iterations` is reached.

## Generation cycle

```mermaid
flowchart TB
    START([run]) --> BASE[establish_baseline<br/>score unmodified code]
    BASE --> G0{generations left<br/>and patience ok?}
    G0 -->|no| REPORT[generate_final_report]
    G0 -->|yes| MAP[Map repo context]

    MAP --> GEN[Generate edit with LLM]
    GEN --> APP[Apply edit + compute diff via difflib]
    APP --> TEST[Run tests in Docker sandbox]
    TEST --> REC[Record candidate to SQLite]
    REC --> SEL[Search strategy picks next baseline]
    SEL --> G0

    REPORT --> OUT([results.json + report artifacts])
```

## Responsibilities

| Method | Role |
|--------|------|
| `establish_baseline()` | Runs the original code once; records generation 0 |
| `execute_generation(gen, baseline)` | One full cycle; routes to the editing mode |
| `run()` | Baseline → loop → early stopping → final report |
| `generate_final_report(...)` | Improvement %, status, best/baseline candidates |

## Early stopping

- `patience` — stop after N consecutive generations with no improvement.
- `success_threshold` — improvement above this marks the run `successful`.
- Any generation error is recorded as a failed candidate; the loop continues
  (a `KeyboardInterrupt`/`SystemExit` is re-raised after saving partial state).

## Editing-mode routing

```mermaid
flowchart LR
    EG[execute_generation] --> M{rewrite_mode}
    M -->|diff| D[unified-diff via git apply]
    M -->|full| F[full-file rewrite]
    M -->|search_replace| S[SEARCH/REPLACE blocks]
    M -->|auto| A{file lines <= threshold}
    A -->|yes| F
    A -->|no| S
```

See [LLM Editing Engine](llm-editing.md) for details.
