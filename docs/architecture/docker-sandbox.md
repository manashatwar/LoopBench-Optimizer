# Docker Sandbox

`sandbox/` — runs a candidate's tests inside a locked-down container and returns
a structured score. Untrusted, LLM-generated code never runs on the host.

## Execution flow

```mermaid
sequenceDiagram
    autonumber
    participant H as Host (runner.py)
    participant D as Docker
    participant C as Container (entrypoint.sh)
    participant R as /results

    H->>H: build_sandbox_image() if missing
    H->>D: docker run --rm --network=none<br/>-v code:/workspace:ro -v results:/results
    D->>C: start entrypoint.sh
    C->>C: run the configured command<br/>(pytest → JSON report; else exit code)
    C->>R: write score.json (passed, failed, speed_ms, scores, cmd_exit)
    C-->>D: exit code
    D-->>H: stdout + stderr + exit code
    H->>R: read score.json
    H->>H: verify_output_streams(stdout, stderr)
    H-->>H: return score dict
```

## Isolation guarantees

| Control | Effect |
|---------|--------|
| `--network=none` | No outbound network from the container |
| `-v code:/workspace:ro` | Evolved code is mounted read-only |
| `--rm` | Container is destroyed after each run |
| timeout | A hung test cannot block the loop (stop + force-kill) |

## Any command, not just pytest

The command is resolved from the sandbox config (`test_command` /
`--test-command`), defaulting to `pytest`. Correctness is derived two ways:

| Command | Correctness signal |
|---------|--------------------|
| `pytest ...` | pass/fail counts from the pytest JSON report |
| anything else (`python bench.py`, a type checker, …) | the command's **exit code** (`0` = pass) |

Either way, a `LOOPBENCH_SPEED_MS=<n>` line on stdout feeds the speed score. If
no speed marker is emitted (e.g. a pure-correctness check), a passing run scores
on correctness alone.

## score.json contract

The container writes `/results/score.json`; the host normalizes it into:

```json
{
  "passed": 13, "failed": 0, "errors": 0, "total": 13,
  "speed_ms": 1.47, "correctness": 1.0,
  "speed_score": 0.99, "combined_score": 0.99,
  "all_passed": true, "cmd_exit": 0
}
```

`combined_score = correctness * speed_score`, where `speed_score` decays
exponentially with `speed_ms` (and equals `correctness` when no speed marker is
present). A missing or unparseable `score.json`, or a failure to capture both
output streams, is treated as a failed candidate.

## Public interface (`sandbox/runner.py`)

| Function | Role |
|----------|------|
| `build_sandbox_image(...)` | Build `loopbench-sandbox` if not present |
| `run_in_sandbox(program, test, ...)` | Run the command, return the score dict |
| `_resolve_test_cmd(cfg, container_test)` | Honor a user command; default to pytest |
| `verify_output_streams(stdout, stderr)` | Assert both streams were captured |
| `make_sandboxed_evaluator(...)` | Wrap an evaluator to route through Docker |
