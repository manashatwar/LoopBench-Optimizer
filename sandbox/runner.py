"""
LoopBench Sandbox Runner
Host-side module that executes evolved code inside a Docker container and
returns a JSON score dict to the caller.

Two public interfaces:
  1. run_in_sandbox(program_path, test_file, sandbox_cfg) -> dict
     Direct call — used by sandbox/test_sandbox.py for validation.

  2. make_sandboxed_evaluator(evaluator_path, sandbox_cfg, base_dir) -> str
     Returns a path to a temporary wrapper evaluator.py that routes
     evaluate() calls through Docker. Used by loopbench/cli.py when
     sandbox.use_docker = true in loopbench.yaml.

Docker contract:
  Host mounts:
    <workspace_dir>  → /workspace  (read-only: evolved code + tests)
    <results_dir>    → /results    (read-write: score.json output)
  Env vars passed to container:
    LOOPBENCH_PROGRAM_PATH = /workspace/<program_filename>
    LOOPBENCH_TEST_CMD     = pytest /workspace/<test_file> -v -s -q --tb=short

Security:
  --network=none    no outbound network from the container
  :ro mount         evolved code cannot write to itself
  --rm              container is removed after each run
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

# Docker image name — built from sandbox/Dockerfile.sandbox
SANDBOX_IMAGE = "loopbench-sandbox"

# Timeout for the entire docker run (seconds)
SANDBOX_TIMEOUT_S = 120


def build_sandbox_image(
    repo_root: Optional[str] = None,
    rebuild: bool = False,
) -> bool:
    """
    Build the loopbench-sandbox Docker image if not already present.

    Args:
        repo_root: Path to repo root (defaults to parent of this file's parent)
        rebuild:   Force rebuild even if image exists

    Returns:
        True if image is ready, False on build failure
    """
    if repo_root is None:
        repo_root = str(Path(__file__).parent.parent.resolve())

    # Check if image already exists
    if not rebuild:
        check = subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE],
            capture_output=True,
        )
        if check.returncode == 0:
            return True  # Already built

    print(f"[sandbox] Building {SANDBOX_IMAGE} image...")
    result = subprocess.run(
        [
            "docker", "build",
            "-f", "sandbox/Dockerfile.sandbox",
            "-t", SANDBOX_IMAGE,
            ".",
        ],
        cwd=repo_root,
        capture_output=False,  # Stream output to console
        timeout=300,
    )
    if result.returncode != 0:
        print(f"[sandbox] FAILED: Docker build failed (exit {result.returncode})")
        return False

    print(f"[sandbox] Image '{SANDBOX_IMAGE}' built successfully")
    return True


def run_in_sandbox(
    program_path: str,
    test_file: str,
    sandbox_cfg: Optional[dict] = None,
    repo_root: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run a test suite against an evolved program inside a Docker container.

    Args:
        program_path:  Absolute path to the evolved program on the host
        test_file:     Absolute path to the pytest test file on the host
        sandbox_cfg:   Dict from loopbench.yaml sandbox section
        repo_root:     Repo root (for building the image if needed)

    Returns:
        dict with keys: passed, failed, errors, total, speed_ms,
                        correctness, speed_score, combined_score, all_passed
    """
    sandbox_cfg = sandbox_cfg or {}
    prog_path = Path(program_path).resolve()
    test_path = Path(test_file).resolve()

    # Ensure image is built
    if not build_sandbox_image(repo_root=repo_root):
        return _error_result("Docker image build failed")

    # ── Set up host directories ───────────────────────────────────────────────
    # The workspace dir contains both the evolved program and the test file.
    # We copy both into a temp dir so we can mount it cleanly read-only.
    with tempfile.TemporaryDirectory(prefix="loopbench_workspace_") as workspace:
        workspace_path = Path(workspace)
        results_path = workspace_path / "results"
        results_path.mkdir()

        # Copy evolved program into workspace
        import shutil
        dest_program = workspace_path / prog_path.name
        shutil.copy2(prog_path, dest_program)

        # Copy test file into workspace
        dest_test = workspace_path / test_path.name
        shutil.copy2(test_path, dest_test)

        # Container-side paths
        container_program = f"/workspace/{prog_path.name}"
        container_test = f"/workspace/{test_path.name}"
        test_cmd = (
            f"pytest {container_test} -v -s -q --tb=short"
        )

        # ── Build docker run command ──────────────────────────────────────────
        docker_cmd = [
            "docker", "run",
            "--rm",
            "--network=none",                       # no outbound network
            "-v", f"{workspace}:/workspace:ro",     # evolved code: read-only
            "-v", f"{results_path}:/results",       # results: read-write
            "-e", f"LOOPBENCH_PROGRAM_PATH={container_program}",
            "-e", f"LOOPBENCH_TEST_CMD={test_cmd}",
            SANDBOX_IMAGE,
        ]

        print(f"[sandbox] Running container for: {prog_path.name}")
        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=SANDBOX_TIMEOUT_S,
            )
            if proc.stdout:
                print(proc.stdout)
            if proc.stderr:
                print(proc.stderr)
        except subprocess.TimeoutExpired:
            return _error_result(f"Container timed out after {SANDBOX_TIMEOUT_S}s")
        except FileNotFoundError:
            return _error_result("Docker not found — is Docker Desktop running?")

        # ── Read score.json from results dir ─────────────────────────────────
        score_file = results_path / "score.json"
        if not score_file.exists():
            return _error_result(
                f"score.json not written by container (exit={proc.returncode}). "
                f"stdout: {proc.stdout[-500:] if proc.stdout else ''}"
            )

        with open(score_file) as f:
            score = json.load(f)

        if isinstance(score, dict):
            return score
        return _error_result("score.json is not a dictionary")


def _error_result(message: str) -> dict[str, Any]:
    """Return a zero-score result with an error message."""
    print(f"[sandbox] ERROR: {message}")
    return {
        "passed": 0,
        "failed": 0,
        "errors": 1,
        "total": 0,
        "speed_ms": None,
        "correctness": 0.0,
        "speed_score": 0.0,
        "combined_score": 0.0,
        "all_passed": False,
        "error": message,
    }


def make_sandboxed_evaluator(
    evaluator_path: str,
    sandbox_cfg: dict,
    base_dir: str,
) -> str:
    """
    Generate a temporary wrapper evaluator.py that routes evaluate() calls
    through Docker instead of running tests locally.

    Called by loopbench/cli.py when sandbox.use_docker = true.

    Args:
        evaluator_path: Original evaluator path (used to find the test file)
        sandbox_cfg:    sandbox section from loopbench.yaml
        base_dir:       Config file directory (for resolving relative paths)

    Returns:
        Path to the generated wrapper evaluator file
    """
    evaluator_dir = Path(evaluator_path).parent.resolve()

    # Locate the test file (convention: test_*.py alongside evaluator.py)
    test_files = list(evaluator_dir.glob("test_*.py"))
    if not test_files:
        raise FileNotFoundError(
            f"No test_*.py found alongside {evaluator_path}. "
            "Docker sandbox requires a pytest file in the same directory."
        )
    test_file = str(test_files[0])
    repo_root = str(Path(__file__).parent.parent.resolve())

    # Write a wrapper evaluator that calls run_in_sandbox()
    wrapper_code = f'''\
"""
Auto-generated sandboxed evaluator wrapper.
Created by loopbench/cli.py — do not edit manually.
"""
import sys
sys.path.insert(0, {repr(repo_root)})
from sandbox.runner import run_in_sandbox
from openevolve.evaluation_result import EvaluationResult

_TEST_FILE = {repr(test_file)}
_REPO_ROOT = {repr(repo_root)}
_SANDBOX_CFG = {repr(sandbox_cfg)}


def evaluate(program_path: str) -> EvaluationResult:
    score = run_in_sandbox(
        program_path=program_path,
        test_file=_TEST_FILE,
        sandbox_cfg=_SANDBOX_CFG,
        repo_root=_REPO_ROOT,
    )
    return EvaluationResult(
        metrics={{
            "correctness":    score.get("correctness", 0.0),
            "speed_ms":       score.get("speed_ms") or 9999.0,
            "speed_score":    score.get("speed_score", 0.0),
            "combined_score": score.get("combined_score", 0.0),
        }},
        artifacts={{
            "passed":  str(score.get("passed", 0)),
            "failed":  str(score.get("failed", 0)),
            "error":   score.get("error", ""),
        }},
    )
'''

    # Write to a temp file that persists for the duration of the run
    import tempfile
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix="_sandboxed_evaluator.py",
        prefix="loopbench_",
        delete=False,
        encoding="utf-8",
    )
    tmp.write(wrapper_code)
    tmp.close()
    print(f"[sandbox] Sandboxed evaluator written to: {tmp.name}")
    return tmp.name
