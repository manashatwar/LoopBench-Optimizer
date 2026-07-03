#!/bin/sh
# LoopBench Sandbox Entrypoint
# Runs inside the Docker container. Executes the user-supplied sandbox command
# and writes structured JSON output to /results/score.json.
#
# Environment variables (set by sandbox/runner.py):
#   LOOPBENCH_PROGRAM_PATH  - Path to the evolved program inside /workspace
#   LOOPBENCH_TEST_CMD      - Command to run (default: pytest /workspace ...)
#
# Correctness signal:
#   * pytest commands  -> parsed from the JSON report (pass/fail counts)
#   * any other command -> the command's exit code (0 = pass)
# Speed signal (optional): a line "LOOPBENCH_SPEED_MS=<number>" on stdout.

RESULTS_DIR="/results"
SCORE_FILE="$RESULTS_DIR/score.json"
PYTEST_JSON="$RESULTS_DIR/pytest_report.json"
OUTPUT_LOG="$RESULTS_DIR/output.log"
EXIT_FILE="$RESULTS_DIR/exit_code"

mkdir -p "$RESULTS_DIR"

# Run relative commands from the workspace root.
cd /workspace 2>/dev/null || true

TEST_CMD="${LOOPBENCH_TEST_CMD:-pytest /workspace -v -s -q --tb=short}"

{
    echo "[sandbox] Starting test run at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[sandbox] Program: $LOOPBENCH_PROGRAM_PATH"
    echo "[sandbox] Command: $TEST_CMD"
    echo "──────────────────────────────────────────"
} > "$OUTPUT_LOG"

# Decide whether this is a pytest invocation (structured scoring) or an
# arbitrary command (exit-code scoring).
IS_PYTEST=0
case "$TEST_CMD" in
    pytest*|*" -m pytest"*|*"/pytest"*) IS_PYTEST=1 ;;
esac

if [ "$IS_PYTEST" -eq 1 ]; then
    # Append JSON report flags so we can parse pass/fail counts.
    $TEST_CMD --json-report --json-report-file="$PYTEST_JSON" >> "$OUTPUT_LOG" 2>&1
    CMD_EXIT=$?
else
    $TEST_CMD >> "$OUTPUT_LOG" 2>&1
    CMD_EXIT=$?
fi

echo "$CMD_EXIT" > "$EXIT_FILE"

cat "$OUTPUT_LOG"
echo "──────────────────────────────────────────"
echo "[sandbox] command exit code: $CMD_EXIT"

# ── Parse results and write score.json ────────────────────────────────────────
python3 - <<'PYEOF'
import json
import math
import os
import re

results_dir = "/results"
pytest_json = os.path.join(results_dir, "pytest_report.json")
score_file = os.path.join(results_dir, "score.json")
output_log = os.path.join(results_dir, "output.log")
exit_file = os.path.join(results_dir, "exit_code")

# Command exit code (0 = success).
try:
    with open(exit_file) as f:
        cmd_exit = int(f.read().strip())
except Exception:
    cmd_exit = 1

# Output text (for the speed marker).
content = ""
if os.path.exists(output_log):
    with open(output_log) as f:
        content = f.read()

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

# Speed marker (optional).
speed_ms = None
match = re.search(r"LOOPBENCH_SPEED_MS=([0-9]+(?:\.[0-9]+)?)", content)
if match:
    speed_ms = float(match.group(1))

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
    "correctness": correctness,
    "speed_score": round(speed_score, 6),
    "combined_score": round(combined_score, 6),
    "all_passed": all_passed,
    "cmd_exit": cmd_exit,
}

with open(score_file, "w") as f:
    json.dump(result, f, indent=2)

print(f"[sandbox] score.json written: passed={n_passed}, failed={n_failed}, "
      f"exit={cmd_exit}, score={combined_score:.4f}")
PYEOF

echo "[sandbox] Done."
