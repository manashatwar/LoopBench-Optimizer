"""
Evaluator for the LoopBench JSON Parser demo.

Metrics:
  correctness    : 1.0 if all pytest tests pass, 0.0 otherwise (regression gate)
  speed_ms       : average parse time of the benchmark document (ms)
  speed_score    : exp(-speed_ms / 150.0) — matches the sandbox scoring convention
  combined_score : correctness * speed_score (primary fitness signal)
"""

import math
import os
import re
import subprocess
import sys
from pathlib import Path

from openevolve.evaluation_result import EvaluationResult

_EXAMPLE_DIR = Path(__file__).parent.resolve()
_TEST_FILE = _EXAMPLE_DIR / "test_json_parser.py"
_PENALTY_MS = 99999.0


def _parse_speed_ms(output: str) -> float:
    match = re.search(r"LOOPBENCH_SPEED_MS=([0-9]+(?:\.[0-9]+)?)", output)
    return float(match.group(1)) if match else _PENALTY_MS


def _parse_test_counts(output: str) -> tuple[int, int]:
    passed_match = re.search(r"(\d+) passed", output)
    failed_match = re.search(r"(\d+) failed", output)
    n_passed = int(passed_match.group(1)) if passed_match else 0
    n_failed = int(failed_match.group(1)) if failed_match else 0
    return n_passed, n_failed


def evaluate(program_path: str) -> EvaluationResult:
    resolved_path = str(Path(program_path).resolve())
    env = os.environ.copy()
    env["LOOPBENCH_PROGRAM_PATH"] = resolved_path

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(_TEST_FILE), "-v", "-s", "--tb=short", "-q"],
            capture_output=True, text=True, timeout=180, env=env, cwd=str(_EXAMPLE_DIR),
        )
        output = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired:
        return EvaluationResult(
            metrics={"correctness": 0.0, "speed_ms": _PENALTY_MS, "speed_score": 0.0, "combined_score": 0.0},
            artifacts={"error": "Evaluation subprocess timed out after 180s"},
        )
    except Exception as exc:
        return EvaluationResult(
            metrics={"correctness": 0.0, "speed_ms": _PENALTY_MS, "speed_score": 0.0, "combined_score": 0.0},
            artifacts={"error": str(exc)},
        )

    n_passed, n_failed = _parse_test_counts(output)
    speed_ms = _parse_speed_ms(output)

    correctness = 1.0 if (n_failed == 0 and n_passed > 0) else 0.0
    speed_score = math.exp(-speed_ms / 150.0) if correctness > 0.0 else 0.0
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
    initial = _EXAMPLE_DIR / "initial_program.py"
    print(f"Evaluating: {initial}")
    result = evaluate(str(initial))
    for k, v in result.metrics.items():
        print(f"  {k:20s}: {v}")
    print(f"  passed : {result.artifacts.get('n_passed')}  failed : {result.artifacts.get('n_failed')}")
