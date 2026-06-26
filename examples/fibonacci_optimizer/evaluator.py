"""
Evaluator for the LoopBench Fibonacci Hello World demo.

Follows the OpenEvolve evaluator contract:
  evaluate(program_path: str) -> EvaluationResult

Metrics returned:
  correctness    : 1.0 if all pytest tests pass, 0.0 otherwise (regression gate)
  speed_ms       : raw fib(30) execution time in milliseconds
  speed_score    : exp(-speed_ms / 150) — exponential decay, higher = faster
  combined_score : correctness * speed_score (primary fitness signal for OpenEvolve)

Speed score intuition:
  ~0ms  → 1.00  (perfect cache/iterative)
  ~5ms  → 0.97
  ~50ms → 0.72
  ~150ms → 0.37
  ~300ms → 0.14  (baseline naive recursion)
  ~500ms → 0.04
  2000ms → 0.00  (hard limit)
"""

import math
import os
import re
import subprocess
import sys
from pathlib import Path

from openevolve.evaluation_result import EvaluationResult

# Path to this example directory (evaluator.py lives alongside test_fibonacci.py)
_EXAMPLE_DIR = Path(__file__).parent.resolve()
_TEST_FILE = _EXAMPLE_DIR / "test_fibonacci.py"

# Penalty value for speed_ms when the marker is missing or times out
_PENALTY_MS = 9999.0


def _parse_speed_ms(output: str) -> float:
    """
    Extract LOOPBENCH_SPEED_MS=<value> from pytest stdout.
    Returns _PENALTY_MS if the marker is absent (e.g., the test crashed).
    """
    match = re.search(r"LOOPBENCH_SPEED_MS=([0-9]+(?:\.[0-9]+)?)", output)
    return float(match.group(1)) if match else _PENALTY_MS


def _parse_test_counts(output: str) -> tuple[int, int]:
    """
    Extract (n_passed, n_failed) from pytest -q summary line.
    Examples: '12 passed in 0.38s'  |  '11 passed, 1 failed in 0.42s'
    """
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    n_passed = int(passed_match.group(1)) if passed_match else 0
    n_failed = int(failed_match.group(1)) if failed_match else 0
    return n_passed, n_failed


def evaluate(program_path: str) -> EvaluationResult:
    """
    Evaluate an evolved Fibonacci program using pytest.

    Args:
        program_path: Absolute path to the evolved program file.

    Returns:
        EvaluationResult with correctness, speed, and combined metrics.
    """
    resolved_path = str(Path(program_path).resolve())

    # Inject the evolved program path so test_fibonacci.py can load it
    env = os.environ.copy()
    env["LOOPBENCH_PROGRAM_PATH"] = resolved_path

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(_TEST_FILE),
                "-v",          # verbose for artifact capture
                "-s",          # allow print() output (needed for LOOPBENCH_SPEED_MS)
                "--tb=short",  # concise tracebacks in artifacts
                "-q",          # summary line for pass/fail parsing
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=str(_EXAMPLE_DIR),
        )
        output = proc.stdout + proc.stderr

    except subprocess.TimeoutExpired:
        return EvaluationResult(
            metrics={
                "correctness": 0.0,
                "speed_ms": _PENALTY_MS,
                "speed_score": 0.0,
                "combined_score": 0.0,
            },
            artifacts={"error": "Evaluation subprocess timed out after 60s"},
        )
    except Exception as exc:
        return EvaluationResult(
            metrics={
                "correctness": 0.0,
                "speed_ms": _PENALTY_MS,
                "speed_score": 0.0,
                "combined_score": 0.0,
            },
            artifacts={"error": str(exc)},
        )

    n_passed, n_failed = _parse_test_counts(output)
    speed_ms = _parse_speed_ms(output)

    # ── Regression gate ─────────────────────────────────────────────────────
    # Any failing test → correctness = 0.0 → combined_score = 0.0
    # The patch is rejected by OpenEvolve's evolution logic.
    correctness = 1.0 if (n_failed == 0 and n_passed > 0) else 0.0

    # ── Speed score ─────────────────────────────────────────────────────────
    # Exponential decay: faster = higher score, max 1.0
    speed_score = math.exp(-speed_ms / 150.0) if correctness > 0.0 else 0.0

    # ── Primary fitness signal ───────────────────────────────────────────────
    combined_score = correctness * speed_score

    return EvaluationResult(
        metrics={
            "correctness": correctness,
            "speed_ms": speed_ms,
            "speed_score": round(speed_score, 6),
            "combined_score": round(combined_score, 6),
        },
        artifacts={
            "pytest_output": output,
            "n_passed": str(n_passed),
            "n_failed": str(n_failed),
            "program_path": resolved_path,
        },
    )


if __name__ == "__main__":
    """Quick sanity check — evaluate the initial (naive) program."""
    initial = _EXAMPLE_DIR / "initial_program.py"
    print(f"Evaluating: {initial}")
    result = evaluate(str(initial))
    print("\n── Metrics ──────────────────────────────")
    for k, v in result.metrics.items():
        print(f"  {k:20s}: {v}")
    print("\n── Test counts ──────────────────────────")
    print(f"  passed : {result.artifacts.get('n_passed')}")
    print(f"  failed : {result.artifacts.get('n_failed')}")
