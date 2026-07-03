"""Evaluator for the Gradient Descent demo (correctness × speed)."""
import math
import os
import re
import subprocess
import sys
from pathlib import Path

from openevolve.evaluation_result import EvaluationResult

_EXAMPLE_DIR = Path(__file__).parent.resolve()
_TEST_FILE = _EXAMPLE_DIR / "test_gradient_descent.py"
_PENALTY_MS = 99999.0


def _parse_speed_ms(output: str) -> float:
    m = re.search(r"LOOPBENCH_SPEED_MS=([0-9]+(?:\.[0-9]+)?)", output)
    return float(m.group(1)) if m else _PENALTY_MS


def _counts(output: str):
    p = re.search(r"(\d+) passed", output)
    f = re.search(r"(\d+) failed", output)
    return (int(p.group(1)) if p else 0, int(f.group(1)) if f else 0)


def evaluate(program_path: str) -> EvaluationResult:
    env = os.environ.copy()
    env["LOOPBENCH_PROGRAM_PATH"] = str(Path(program_path).resolve())
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(_TEST_FILE), "-v", "-s", "--tb=short", "-q"],
            capture_output=True, text=True, timeout=240, env=env, cwd=str(_EXAMPLE_DIR),
        )
        out = proc.stdout + proc.stderr
    except Exception as exc:
        return EvaluationResult(
            metrics={"correctness": 0.0, "speed_ms": _PENALTY_MS, "speed_score": 0.0, "combined_score": 0.0},
            artifacts={"error": str(exc)},
        )
    n_pass, n_fail = _counts(out)
    speed_ms = _parse_speed_ms(out)
    correctness = 1.0 if (n_fail == 0 and n_pass > 0) else 0.0
    speed_score = math.exp(-speed_ms / 150.0) if correctness > 0.0 else 0.0
    return EvaluationResult(
        metrics={
            "correctness": correctness,
            "speed_ms": speed_ms,
            "speed_score": round(speed_score, 6),
            "combined_score": round(correctness * speed_score, 6),
        },
        artifacts={"pytest_output": out, "n_passed": str(n_pass), "n_failed": str(n_fail)},
    )
