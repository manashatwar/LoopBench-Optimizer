#!/bin/sh
# LoopBench generic (non-Python) scorer.
#
# Computes /results/score.json from a command exit code and the kept speed
# markers using ONLY shell + awk — no python3 dependency (design §C3, R7).
# This mirrors the pytest path's scorer exactly for identical inputs (CP5):
#   correctness    = 1.0 if exit == 0 else 0.0  (passed=1/failed=0)
#   speed_ms       = median of the kept samples (back-compat name)
#   speed_score    = exp(-speed_ms / 150.0)      when speed present & correct
#   combined_score = correctness * speed_score   (or correctness when no marker)
# Rounding matches the python scorer: speeds 4dp, speed_score/combined 6dp.
#
# Usage: score_generic.sh <exit_file> <samples_file> <score_file>
#   exit_file    - file containing the command's integer exit code
#   samples_file - file with one kept speed marker (ms) per line (may be empty)
#   score_file   - path to write score.json

EXIT_FILE="$1"
SAMPLES_FILE="$2"
SCORE_FILE="$3"

# ── Resolve the command exit code (default to failure if unreadable) ──────────
CMD_EXIT=1
if [ -f "$EXIT_FILE" ]; then
    CMD_EXIT=$(cat "$EXIT_FILE" 2>/dev/null)
fi
case "$CMD_EXIT" in
    ''|*[!0-9]*) CMD_EXIT=1 ;;
esac

# An absent samples file is treated as "no markers".
[ -f "$SAMPLES_FILE" ] || SAMPLES_FILE=/dev/null

awk -v cmd_exit="$CMD_EXIT" '
    function round4(x) { return sprintf("%.4f", x) + 0 }

    # Collect numeric samples (ignore blanks / non-numeric lines).
    {
        v = $0
        gsub(/^[ \t\r]+|[ \t\r]+$/, "", v)
        if (v == "") next
        if (v ~ /^[0-9]+(\.[0-9]+)?$/) vals[n++] = v + 0
    }

    END {
        # Correctness from exit code (matches the non-pytest python semantics).
        all_passed = (cmd_exit == 0)
        correctness = all_passed ? 1.0 : 0.0
        n_passed = all_passed ? 1 : 0
        n_failed = all_passed ? 0 : 1

        if (n > 0) {
            # Insertion sort (n is tiny — a handful of repeats).
            for (i = 1; i < n; i++) {
                key = vals[i]; j = i - 1
                while (j >= 0 && vals[j] > key) { vals[j+1] = vals[j]; j-- }
                vals[j+1] = key
            }

            # Median.
            if (n % 2 == 1) median = vals[(n-1)/2]
            else            median = (vals[n/2 - 1] + vals[n/2]) / 2.0

            # Mean.
            sum = 0
            for (i = 0; i < n; i++) sum += vals[i]
            mean = sum / n

            # Sample standard deviation (ddof=1); 0 for a single kept run.
            if (n > 1) {
                ss = 0
                for (i = 0; i < n; i++) ss += (vals[i] - mean) * (vals[i] - mean)
                stddev = sqrt(ss / (n - 1))
            } else {
                stddev = 0.0
            }

            speed_ms = round4(median)   # 4dp-rounded median drives speed_score
            speed_score = (correctness > 0) ? exp(-speed_ms / 150.0) : 0.0
            combined_score = correctness * speed_score
            has_speed = 1
        } else {
            # No markers: score on correctness alone (mirror the python path).
            speed_score = 0.0
            combined_score = correctness
            has_speed = 0
        }

        # ── Emit JSON by hand (valid JSON, null where appropriate) ────────────
        printf "{\n"
        printf "  \"passed\": %d,\n", n_passed
        printf "  \"failed\": %d,\n", n_failed
        printf "  \"errors\": 0,\n"
        printf "  \"total\": 1,\n"
        printf "  \"duration_s\": 0.0,\n"
        if (has_speed) {
            printf "  \"speed_ms\": %.4f,\n", speed_ms
            printf "  \"speed_ms_median\": %.4f,\n", median
            printf "  \"speed_ms_mean\": %.4f,\n", mean
            printf "  \"speed_ms_stddev\": %.4f,\n", stddev
            printf "  \"speed_ms_samples\": ["
            for (i = 0; i < n; i++) {
                if (i > 0) printf ", "
                printf "%.4f", vals[i]
            }
            printf "],\n"
            printf "  \"runs\": %d,\n", n
        } else {
            printf "  \"speed_ms\": null,\n"
            printf "  \"speed_ms_median\": null,\n"
            printf "  \"speed_ms_mean\": null,\n"
            printf "  \"speed_ms_stddev\": null,\n"
            printf "  \"speed_ms_samples\": [],\n"
            printf "  \"runs\": 0,\n"
        }
        printf "  \"correctness\": %.1f,\n", correctness
        printf "  \"speed_score\": %.6f,\n", speed_score
        printf "  \"combined_score\": %.6f,\n", combined_score
        printf "  \"all_passed\": %s,\n", (all_passed ? "true" : "false")
        printf "  \"cmd_exit\": %d\n", cmd_exit
        printf "}\n"
    }
' "$SAMPLES_FILE" > "$SCORE_FILE"
