#!/bin/sh
# LoopBench Sandbox Entrypoint
# Runs inside the Docker container. Executes the user-supplied test command
# and writes structured JSON output to /results/score.json.
#
# Environment variables (set by sandbox/runner.py):
#   LOOPBENCH_PROGRAM_PATH  - Path to the evolved program inside /workspace
#   LOOPBENCH_TEST_CMD      - pytest command to run (default: pytest /workspace -v -s -q)
#   LOOPBENCH_TEST_FILE     - Path to the test file inside /workspace

set -e

RESULTS_DIR="/results"
SCORE_FILE="$RESULTS_DIR/score.json"
PYTEST_JSON="$RESULTS_DIR/pytest_report.json"
OUTPUT_LOG="$RESULTS_DIR/output.log"

mkdir -p "$RESULTS_DIR"

# ── Determine test command ────────────────────────────────────────────────────
TEST_CMD="${LOOPBENCH_TEST_CMD:-pytest /workspace -v -s -q --tb=short}"

echo "[sandbox] Starting test run at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee "$OUTPUT_LOG"
echo "[sandbox] Program: $LOOPBENCH_PROGRAM_PATH" | tee -a "$OUTPUT_LOG"
echo "[sandbox] Command: $TEST_CMD" | tee -a "$OUTPUT_LOG"
echo "──────────────────────────────────────────" | tee -a "$OUTPUT_LOG"

# ── Run pytest with JSON report ───────────────────────────────────────────────
set +e   # Don't exit on test failure — we need to capture it
$TEST_CMD \
    --json-report \
    --json-report-file="$PYTEST_JSON" \
    2>&1 | tee -a "$OUTPUT_LOG"
PYTEST_EXIT=$?
set -e

echo "──────────────────────────────────────────" | tee -a "$OUTPUT_LOG"
echo "[sandbox] pytest exit code: $PYTEST_EXIT" | tee -a "$OUTPUT_LOG"

# ── Parse pytest JSON report and write score.json ────────────────────────────
python3 - <<'PYEOF'
import json
import os
import re
import sys

results_dir = "/results"
pytest_json = os.path.join(results_dir, "pytest_report.json")
score_file = os.path.join(results_dir, "score.json")
output_log = os.path.join(results_dir, "output.log")

# Read pytest JSON report
report = {}
if os.path.exists(pytest_json):
    with open(pytest_json) as f:
        report = json.load(f)

summary = report.get("summary", {})
n_passed = summary.get("passed", 0)
n_failed = summary.get("failed", 0)
n_error  = summary.get("error", 0)
n_total  = summary.get("total", 0)
duration = report.get("duration", 0.0)

# Parse LOOPBENCH_SPEED_MS from output log
speed_ms = None
if os.path.exists(output_log):
    with open(output_log) as f:
        content = f.read()
    match = re.search(r"LOOPBENCH_SPEED_MS=([0-9]+(?:\.[0-9]+)?)", content)
    if match:
        speed_ms = float(match.group(1))

# Compute score
import math
all_passed = (n_failed == 0 and n_error == 0 and n_passed > 0)
correctness = 1.0 if all_passed else 0.0
speed_score = math.exp(-speed_ms / 150.0) if (speed_ms is not None and correctness > 0) else 0.0
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
}

with open(score_file, "w") as f:
    json.dump(result, f, indent=2)

print(f"[sandbox] score.json written: passed={n_passed}, failed={n_failed}, score={combined_score:.4f}")
PYEOF

echo "[sandbox] Done."
