#!/bin/sh
# LoopBench Sandbox Entrypoint
# Runs inside the Docker container. Executes the user-supplied sandbox command
# and writes structured JSON output to /results/score.json.
#
# Environment variables (set by sandbox/runner.py):
#   LOOPBENCH_PROGRAM_PATH  - Path to the evolved program inside /workspace
#   LOOPBENCH_TEST_CMD      - Command to run (default: pytest /workspace ...)
#   LOOPBENCH_REPEATS       - How many times to run the speed workload (default 1)
#
# Correctness signal:
#   * pytest commands  -> parsed from the JSON report (pass/fail counts)
#   * any other command -> the command's exit code (0 = pass)
# Speed signal (optional): a line "LOOPBENCH_SPEED_MS=<number>" on stdout.
#
# Repeated measurement (statistical speed gate, design §C1):
#   The workload runs K = LOOPBENCH_REPEATS times. When K >= 3 the first run is
#   discarded as a warm-up. The kept per-run speed markers are aggregated into a
#   distribution (median / mean / stddev / samples / runs); speed_ms is the
#   median so existing single-shot behavior is preserved when K == 1.

RESULTS_DIR="/results"
SCORE_FILE="$RESULTS_DIR/score.json"
PYTEST_JSON="$RESULTS_DIR/pytest_report.json"
OUTPUT_LOG="$RESULTS_DIR/output.log"
EXIT_FILE="$RESULTS_DIR/exit_code"
SAMPLES_FILE="$RESULTS_DIR/speed_samples.txt"

mkdir -p "$RESULTS_DIR"

# Run relative commands from the workspace root.
cd /workspace 2>/dev/null || true

TEST_CMD="${LOOPBENCH_TEST_CMD:-pytest /workspace -v -s -q --tb=short}"

# ── Resolve K (repeats) ───────────────────────────────────────────────────────
# Default to 1 (= today's single-shot behavior). Sanitize to a positive integer.
REPEATS="${LOOPBENCH_REPEATS:-1}"
case "$REPEATS" in
    ''|*[!0-9]*) REPEATS=1 ;;
esac
[ "$REPEATS" -lt 1 ] && REPEATS=1

# Discard the first run as a warm-up only when K >= 3.
WARMUP=0
[ "$REPEATS" -ge 3 ] && WARMUP=1

{
    echo "[sandbox] Starting test run at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[sandbox] Program: $LOOPBENCH_PROGRAM_PATH"
    echo "[sandbox] Command: $TEST_CMD"
    echo "[sandbox] Repeats: $REPEATS (warm-up discarded: $WARMUP)"
    echo "──────────────────────────────────────────"
} > "$OUTPUT_LOG"

# Decide whether this is a pytest invocation (structured scoring) or an
# arbitrary command (exit-code scoring).
IS_PYTEST=0
case "$TEST_CMD" in
    pytest*|*" -m pytest"*|*"/pytest"*) IS_PYTEST=1 ;;
esac

# Start with an empty samples file; each kept run appends one marker value.
: > "$SAMPLES_FILE"

# ── Run the workload K times ──────────────────────────────────────────────────
CMD_EXIT=0
run_idx=0
while [ "$run_idx" -lt "$REPEATS" ]; do
    run_idx=$((run_idx + 1))
    RUN_LOG="$RESULTS_DIR/run_${run_idx}.log"

    echo "── run ${run_idx}/${REPEATS} ──" >> "$OUTPUT_LOG"
    if [ "$IS_PYTEST" -eq 1 ]; then
        # Append JSON report flags so we can parse pass/fail counts.
        $TEST_CMD --json-report --json-report-file="$PYTEST_JSON" > "$RUN_LOG" 2>&1
        CMD_EXIT=$?
    else
        $TEST_CMD > "$RUN_LOG" 2>&1
        CMD_EXIT=$?
    fi
    cat "$RUN_LOG" >> "$OUTPUT_LOG"

    # Extract this run's speed marker (last marker emitted in the run, if any).
    marker=$(grep -oE 'LOOPBENCH_SPEED_MS=[0-9]+(\.[0-9]+)?' "$RUN_LOG" \
        | tail -n 1 | sed 's/.*=//')

    # Discard the warm-up run's sample when K >= 3.
    if [ "$WARMUP" -eq 1 ] && [ "$run_idx" -eq 1 ]; then
        echo "[sandbox] run ${run_idx} discarded as warm-up" >> "$OUTPUT_LOG"
    elif [ -n "$marker" ]; then
        echo "$marker" >> "$SAMPLES_FILE"
    fi
done

echo "$CMD_EXIT" > "$EXIT_FILE"

cat "$OUTPUT_LOG"
echo "──────────────────────────────────────────"
echo "[sandbox] command exit code: $CMD_EXIT"

# ── Parse results and write score.json ────────────────────────────────────────
python3 - <<'PYEOF'
import json
import math
import os
import statistics

results_dir = "/results"
pytest_json = os.path.join(results_dir, "pytest_report.json")
score_file = os.path.join(results_dir, "score.json")
exit_file = os.path.join(results_dir, "exit_code")
samples_file = os.path.join(results_dir, "speed_samples.txt")

# Command exit code (0 = success).
try:
    with open(exit_file) as f:
        cmd_exit = int(f.read().strip())
except Exception:
    cmd_exit = 1

# Correctness: prefer pytest's structured report, else fall back to exit code.
n_passed = n_failed = n_error = n_total = 0
duration = 0.0
if os.path.exists(pytest_json):
    try:
        with open(pytest_json) as f:
            report = json.load(f)
        summary = report.get("summary", {})
        n_passed = summary.get("passed", 0)
        n_failed = summary.get("failed", 0)
        n_error = summary.get("error", 0)
        n_total = summary.get("total", 0)
        duration = report.get("duration", 0.0)
        all_passed = (n_failed == 0 and n_error == 0 and n_passed > 0)
    except Exception:
        all_passed = (cmd_exit == 0)
else:
    # Non-pytest command: exit code is the correctness signal.
    all_passed = (cmd_exit == 0)
    n_passed, n_failed = (1, 0) if all_passed else (0, 1)
    n_total = 1

# Speed distribution: aggregate the kept per-run markers.
samples = []
if os.path.exists(samples_file):
    with open(samples_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                samples.append(float(line))
            except ValueError:
                pass

if samples:
    speed_ms_median = statistics.median(samples)
    speed_ms_mean = statistics.fmean(samples)
    # Sample standard deviation (ddof=1); 0 for a single kept run so that
    # repeats=1 reproduces the current single-shot behavior.
    speed_ms_stddev = statistics.stdev(samples) if len(samples) > 1 else 0.0
    speed_ms = speed_ms_median
else:
    speed_ms_median = speed_ms_mean = speed_ms_stddev = None
    speed_ms = None

runs = len(samples)

correctness = 1.0 if all_passed else 0.0
speed_score = math.exp(-speed_ms / 150.0) if (speed_ms is not None and correctness > 0) else 0.0
# When there is no speed marker, a passing run scores on correctness alone so
# that non-performance evaluators (unit tests, type checks) still produce a
# meaningful score.
if speed_ms is None:
    combined_score = correctness
else:
    combined_score = correctness * speed_score

result = {
    "passed": n_passed,
    "failed": n_failed,
    "errors": n_error,
    "total": n_total,
    "duration_s": round(duration, 4),
    "speed_ms": round(speed_ms, 4) if speed_ms is not None else None,
    "speed_ms_median": round(speed_ms_median, 4) if speed_ms_median is not None else None,
    "speed_ms_mean": round(speed_ms_mean, 4) if speed_ms_mean is not None else None,
    "speed_ms_stddev": round(speed_ms_stddev, 4) if speed_ms_stddev is not None else None,
    "speed_ms_samples": [round(v, 4) for v in samples],
    "runs": runs,
    "correctness": correctness,
    "speed_score": round(speed_score, 6),
    "combined_score": round(combined_score, 6),
    "all_passed": all_passed,
    "cmd_exit": cmd_exit,
}

with open(score_file, "w") as f:
    json.dump(result, f, indent=2)

print(f"[sandbox] score.json written: passed={n_passed}, failed={n_failed}, "
      f"exit={cmd_exit}, runs={runs}, speed_ms={result['speed_ms']}, "
      f"score={combined_score:.4f}")
PYEOF

echo "[sandbox] Done."
