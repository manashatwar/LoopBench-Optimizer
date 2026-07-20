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

import hashlib
import json
import statistics
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, List, Optional

# Distribution field names written by the sandbox entrypoint (design §C1).
_DISTRIBUTION_FIELDS = (
    "speed_ms_median",
    "speed_ms_mean",
    "speed_ms_stddev",
    "speed_ms_samples",
    "runs",
)


def aggregate_speed_samples(samples: Any) -> dict[str, Any]:
    """Aggregate kept per-run speed markers into a distribution.

    This is the host-side reference implementation of the aggregation the
    sandbox entrypoint performs inside the container. It mirrors that logic so
    the math can be unit-tested without Docker.

    Args:
        samples: Iterable of per-run speed values (ms). These are the *kept*
                 runs — the warm-up run (when K >= 3) has already been dropped.

    Returns:
        A dict with ``speed_ms`` (the median, for back-compat), plus
        ``speed_ms_median``, ``speed_ms_mean``, ``speed_ms_stddev``,
        ``speed_ms_samples``, and ``runs``. When no samples are supplied the
        speed fields are ``None`` and ``runs`` is 0.
    """
    values: List[float] = []
    for sample in samples or []:
        if sample is None:
            continue
        try:
            values.append(float(sample))
        except (TypeError, ValueError):
            continue

    if not values:
        return {
            "speed_ms": None,
            "speed_ms_median": None,
            "speed_ms_mean": None,
            "speed_ms_stddev": None,
            "speed_ms_samples": [],
            "runs": 0,
        }

    median = statistics.median(values)
    mean = statistics.fmean(values)
    # Sample standard deviation (ddof=1); 0 for a single kept run so that
    # repeats=1 reproduces the current single-shot behavior.
    stddev = statistics.stdev(values) if len(values) > 1 else 0.0

    return {
        "speed_ms": round(median, 4),
        "speed_ms_median": round(median, 4),
        "speed_ms_mean": round(mean, 4),
        "speed_ms_stddev": round(stddev, 4),
        "speed_ms_samples": [round(v, 4) for v in values],
        "runs": len(values),
    }


def _normalize_distribution_fields(score: dict[str, Any]) -> None:
    """Ensure a loaded score carries the C1 speed-distribution fields.

    Newer containers write these directly; for older score.json payloads (or a
    generic scorer that only reports ``speed_ms_samples``) the fields are
    derived here so callers always see a consistent distribution.
    """
    if not isinstance(score, dict):
        return

    if all(field in score for field in _DISTRIBUTION_FIELDS):
        if score.get("speed_ms_samples") is None:
            score["speed_ms_samples"] = []
        return

    samples = score.get("speed_ms_samples")
    if samples is None:
        single = score.get("speed_ms")
        samples = [single] if single is not None else []

    aggregate = aggregate_speed_samples(samples)
    for field, value in aggregate.items():
        # Preserve any value the container already provided (e.g. speed_ms).
        score.setdefault(field, value)


def _repeats_from_cfg(sandbox_cfg: Optional[dict]) -> int:
    """Resolve the configured number of speed-measurement repeats (default 1)."""
    raw = (sandbox_cfg or {}).get("repeats", 1)
    try:
        repeats = int(raw)
    except (TypeError, ValueError):
        return 1
    return repeats if repeats >= 1 else 1

# Docker image name — built from sandbox/Dockerfile.sandbox
SANDBOX_IMAGE = "loopbench-sandbox"

# Timeout for the entire docker run (seconds)
SANDBOX_TIMEOUT_S = 120


def verify_output_streams(stdout: Optional[str], stderr: Optional[str]) -> bool:
    """Return whether both process output streams were captured."""
    return stdout is not None and stderr is not None


def _stream_text(value: Any) -> Optional[str]:
    """Normalize captured timeout output while preserving missing streams."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def _cleanup_container(container_name: str) -> None:
    """Force-remove a named container; absence is an expected no-op."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _stop_container(container_name: str) -> None:
    """Allow a timed-out container five seconds to stop gracefully."""
    try:
        subprocess.run(
            ["docker", "stop", "--time", "5", container_name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def _execute_container(
    docker_cmd: list[str],
    score_file: Path,
    timeout: float,
) -> dict[str, Any]:
    """Run Docker, verify stream capture, and then load computed metrics."""
    container_name = f"loopbench-sandbox-{uuid.uuid4().hex[:12]}"
    docker_cmd = [*docker_cmd[:2], "--name", container_name, *docker_cmd[2:]]
    started_at = time.monotonic()

    try:
        proc = subprocess.run(
            docker_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        execution_time = time.monotonic() - started_at
        _stop_container(container_name)
        return _error_result(
            f"Container timed out after {timeout:g}s",
            status="timeout",
            stdout=_stream_text(exc.stdout),
            stderr=_stream_text(exc.stderr),
            execution_time=execution_time,
            timeout=True,
        )
    except FileNotFoundError:
        return _error_result(
            "Docker not found - is Docker Desktop running?",
            status="docker_unavailable",
            execution_time=time.monotonic() - started_at,
        )
    finally:
        _cleanup_container(container_name)

    execution_time = time.monotonic() - started_at
    if not verify_output_streams(proc.stdout, proc.stderr):
        return _error_result(
            "Docker output stream capture failed: both stdout and stderr are required",
            status="output_capture_failed",
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time=execution_time,
        )

    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr)

    if not score_file.exists():
        return _error_result(
            f"score.json not written by container (exit={proc.returncode}). "
            f"stdout: {proc.stdout[-500:]}",
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time=execution_time,
        )

    try:
        with open(score_file, encoding="utf-8") as file_handle:
            score = json.load(file_handle)
    except (OSError, json.JSONDecodeError) as exc:
        return _error_result(
            f"Unable to read score.json: {exc}",
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time=execution_time,
        )

    if not isinstance(score, dict):
        return _error_result(
            "score.json is not a dictionary",
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            execution_time=execution_time,
        )

    # Parse / backfill the C1 speed-distribution fields (design §C1).
    _normalize_distribution_fields(score)

    score.update(
        {
            "status": "passed" if score.get("all_passed") else "failed",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "execution_time": execution_time,
        }
    )
    return score


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


def _resolve_test_cmd(sandbox_cfg: Optional[dict], container_test: str) -> str:
    """Build the command the container runs.

    Honors a user-supplied ``test_command`` from the sandbox config (so
    benchmarks, type checks, or non-pytest runners work). Falls back to the
    default pytest invocation when none is given (or the bare word "pytest").
    """
    user_cmd = (sandbox_cfg or {}).get("test_command")
    if user_cmd and str(user_cmd).strip() and str(user_cmd).strip().lower() != "pytest":
        return str(user_cmd).strip()
    return f"pytest {container_test} -v -s -q --tb=short"


def _normalize_packages(pip_install: Any) -> List[str]:
    """Coerce the pip_install config into a clean, sorted list of packages."""
    if not pip_install:
        return []
    items = pip_install.split() if isinstance(pip_install, str) else list(pip_install)
    seen = set()
    out = []
    for it in items:
        it = str(it).strip()
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return sorted(out)


def ensure_deps_image(packages: List[str], repo_root: Optional[str] = None) -> str:
    """Return an image that has ``packages`` installed on top of the base image.

    Builds (with network enabled — the ONLY networked step) a small derived
    image ``loopbench-sandbox:deps-<hash>`` and caches it by the hash of the
    package set, so repeated runs reuse it. The scored run still executes with
    --network=none. Falls back to the base image if the build fails.
    """
    if not packages:
        return SANDBOX_IMAGE
    if not build_sandbox_image(repo_root=repo_root):
        return SANDBOX_IMAGE

    digest = hashlib.sha1(("\n".join(packages)).encode()).hexdigest()[:12]
    tag = f"{SANDBOX_IMAGE}:deps-{digest}"

    exists = subprocess.run(["docker", "image", "inspect", tag], capture_output=True)
    if exists.returncode == 0:
        return tag

    dockerfile = (
        f"FROM {SANDBOX_IMAGE}\n"
        f"RUN pip install --no-cache-dir {' '.join(packages)}\n"
    )
    print(f"[sandbox] Installing dependencies into image: {', '.join(packages)}")
    try:
        build = subprocess.run(
            ["docker", "build", "-t", tag, "-"],
            input=dockerfile,
            text=True,
            capture_output=True,
            timeout=600,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"[sandbox] WARNING: dependency image build failed ({exc}); using base image")
        return SANDBOX_IMAGE
    if build.returncode != 0:
        print(f"[sandbox] WARNING: dependency install failed; using base image.\n{build.stderr[-800:]}")
        return SANDBOX_IMAGE
    print(f"[sandbox] Dependency image ready: {tag}")
    return tag


def _resolve_image(sandbox_cfg: Optional[dict], repo_root: Optional[str]) -> str:
    """Pick the container image, layering in pip dependencies when requested."""
    packages = _normalize_packages((sandbox_cfg or {}).get("pip_install"))
    return ensure_deps_image(packages, repo_root=repo_root)


def run_in_sandbox(
    program_path: str,
    test_file: str,
    sandbox_cfg: Optional[dict] = None,
    repo_root: Optional[str] = None,
    worktree_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Run a test suite against an evolved program inside a Docker container.

    Args:
        program_path:  Absolute path to the evolved program on the host
        test_file:     Absolute path to the pytest test file on the host
        sandbox_cfg:   Dict from loopbench.yaml sandbox section
        repo_root:     Repo root (for building the image if needed)
        worktree_path: Optional path to a Git worktree (created by WorkspaceManager).
                       When provided the worktree directory is mounted as /workspace
                       inside the container instead of the default temp-copy approach,
                       giving the container access to all supporting files in the
                       worktree (helpers, fixtures, configs, etc.).

    Returns:
        Score dictionary including stdout, stderr, exit_code, execution_time,
        status, and the existing metric fields.
    """
    sandbox_cfg = sandbox_cfg or {}
    timeout = float(sandbox_cfg.get("timeout", SANDBOX_TIMEOUT_S))
    if timeout <= 0:
        raise ValueError("sandbox timeout must be greater than zero")
    repeats = _repeats_from_cfg(sandbox_cfg)
    prog_path = Path(program_path).resolve()
    test_path = Path(test_file).resolve()

    # Ensure image is built (and layer in any requested pip dependencies).
    if not build_sandbox_image(repo_root=repo_root):
        return _error_result("Docker image build failed")
    image = _resolve_image(sandbox_cfg, repo_root)

    # ── Set up host directories ───────────────────────────────────────────────
    # When a worktree_path is provided we mount it directly — the worktree
    # already contains the evolved program and all supporting files.  Otherwise
    # we fall back to copying files into a temp workspace (original behaviour).
    if worktree_path is not None:
        wt_path = Path(worktree_path).resolve()
        prog_path = Path(program_path).resolve()
        test_path = Path(test_file).resolve()

        # If the program/test file are already inside the worktree we mount the
        # whole tree.  If not, copy them in first.
        try:
            prog_path.relative_to(wt_path)
            prog_in_wt = True
        except ValueError:
            prog_in_wt = False

        with tempfile.TemporaryDirectory(prefix="loopbench_results_") as results_tmp:
            results_path = Path(results_tmp) / "results"
            results_path.mkdir()

            if prog_in_wt:
                # Mounted in place — reference it at its real path in the tree.
                prog_rel = prog_path.relative_to(wt_path).as_posix()
            else:
                import shutil as _shutil
                _shutil.copy2(prog_path, wt_path / prog_path.name)
                prog_rel = prog_path.name

            # Copy test file into worktree if not already there
            try:
                test_path.relative_to(wt_path)
                test_in_wt = True
            except ValueError:
                test_in_wt = False

            if test_in_wt:
                test_rel = test_path.relative_to(wt_path).as_posix()
            else:
                import shutil as _shutil
                _shutil.copy2(test_path, wt_path / test_path.name)
                test_rel = test_path.name

            container_program = f"/workspace/{prog_rel}"
            container_test = f"/workspace/{test_rel}"
            test_cmd = _resolve_test_cmd(sandbox_cfg, container_test)

            docker_cmd = [
                "docker", "run",
                "--rm",
                "--network=none",
                "-v", f"{wt_path}:/workspace:ro",   # mount worktree directly
                "-v", f"{results_path}:/results",
                "-e", f"LOOPBENCH_PROGRAM_PATH={container_program}",
                "-e", f"LOOPBENCH_TEST_CMD={test_cmd}",
                "-e", f"LOOPBENCH_REPEATS={repeats}",
                image,
            ]

            print(f"[sandbox] Running container (worktree) for: {prog_path.name}")
            score_file = results_path / "score.json"
            return _execute_container(docker_cmd, score_file, timeout)

    # ── Original temp-copy approach (no worktree) ────────────────────────────
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
        test_cmd = _resolve_test_cmd(sandbox_cfg, container_test)

        # ── Build docker run command ──────────────────────────────────────────
        docker_cmd = [
            "docker", "run",
            "--rm",
            "--network=none",                       # no outbound network
            "-v", f"{workspace}:/workspace:ro",     # evolved code: read-only
            "-v", f"{results_path}:/results",       # results: read-write
            "-e", f"LOOPBENCH_PROGRAM_PATH={container_program}",
            "-e", f"LOOPBENCH_TEST_CMD={test_cmd}",
            "-e", f"LOOPBENCH_REPEATS={repeats}",
            image,
        ]

        print(f"[sandbox] Running container for: {prog_path.name}")
        score_file = results_path / "score.json"
        return _execute_container(docker_cmd, score_file, timeout)


def _error_result(
    message: str,
    *,
    status: str = "failed",
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    exit_code: Optional[int] = None,
    execution_time: Optional[float] = None,
    timeout: bool = False,
) -> dict[str, Any]:
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
        "status": status,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "execution_time": execution_time,
        "timeout": timeout,
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
