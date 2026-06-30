"""
sandbox/test_sandbox.py -- Task 3 Validation Script

Verifies that the Docker sandbox correctly:
  1. Builds the loopbench-sandbox image
  2. Runs the Fibonacci test suite inside a container
  3. Returns a valid score.json to the host

Usage (no LLM required):
  python sandbox/test_sandbox.py

Expected output:
  [OK] Sandbox test passed!
  score.json: {"passed": 13, "failed": 0, "combined_score": 0.xxxx, ...}

Exit code: 0 on success, 1 on failure.
"""

import json
import sys
from pathlib import Path

# Ensure project root is on the path
_REPO_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(_REPO_ROOT))

from sandbox.runner import build_sandbox_image, run_in_sandbox  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────
_INITIAL_PROGRAM = _REPO_ROOT / "examples" / "fibonacci_optimizer" / "initial_program.py"
_TEST_FILE       = _REPO_ROOT / "examples" / "fibonacci_optimizer" / "test_fibonacci.py"


def main() -> int:
    print("=" * 60)
    print("LoopBench — Task 3: Docker Sandbox Validation")
    print("=" * 60)
    print()

    # ── Step 1: Check paths exist ─────────────────────────────────────────────
    for label, path in [("Initial program", _INITIAL_PROGRAM), ("Test file", _TEST_FILE)]:
        if path.exists():
            print(f"[OK]   {label}: {path}")
        else:
            print(f"[FAIL] {label} not found: {path}")
            return 1
    print()

    # ── Step 2: Build the sandbox image ──────────────────────────────────────
    print("Step 1: Building loopbench-sandbox Docker image...")
    ok = build_sandbox_image(repo_root=str(_REPO_ROOT), rebuild=False)
    if not ok:
        print("[FAIL] Image build failed. Check Docker is running and try again.")
        return 1
    print()

    # ── Step 3: Run the test suite in the sandbox ─────────────────────────────
    print("Step 2: Running Fibonacci tests inside Docker container...")
    print("        (network=none, code mounted read-only)")
    print()

    score = run_in_sandbox(
        program_path=str(_INITIAL_PROGRAM),
        test_file=str(_TEST_FILE),
        sandbox_cfg={"network_off": True, "readonly_mount": True},
        repo_root=str(_REPO_ROOT),
    )

    # ── Step 4: Validate the result ───────────────────────────────────────────
    print()
    print("Step 3: Validating score.json...")
    print()

    score_display = json.dumps(score, indent=2)
    print("score.json contents:")
    print(score_display)
    print()

    # Assertions
    errors = []

    if "error" in score and score["error"]:
        errors.append(f"Container returned error: {score['error']}")

    if not score.get("all_passed", False):
        errors.append(
            f"Expected all tests to pass — "
            f"passed={score.get('passed')}, failed={score.get('failed')}"
        )

    n_passed = score.get("passed", 0)
    if n_passed < 10:
        errors.append(f"Expected >= 10 tests to pass, got {n_passed}")

    if score.get("combined_score", 0) <= 0:
        errors.append("combined_score must be > 0 for a passing run")

    if errors:
        print("[FAIL] Sandbox validation FAILED:")
        for e in errors:
            print(f"   - {e}")
        return 1

    print("=" * 60)
    print("[OK] Sandbox test passed!")
    print(f"   Tests passed   : {score['passed']}")
    print(f"   Tests failed   : {score['failed']}")
    print(f"   Speed (fib 30) : {score.get('speed_ms', 'N/A')} ms")
    print(f"   Combined score : {score['combined_score']:.4f}")
    print("=" * 60)
    print()
    print("The Docker sandbox is working correctly.")
    print("Set sandbox.use_docker: true in your loopbench.yaml to use it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
