"""
LoopBench CLI — command: loopbench run --config <loopbench.yaml>

Architecture:
  loopbench.yaml is a SUPERSET of openevolve's config.yaml.
  It adds four top-level LoopBench-specific sections:
    target      : program + evaluator paths
    sandbox     : docker flags, test command
    metric      : primary metric name + threshold (maps to --target-score)
    constraints : max_iterations, max_token_cost_usd

  All other keys pass through to openevolve.Config unchanged.
  This CLI translates LoopBench fields → OpenEvolve API call.

Commands:
  loopbench run   --config <path>    Run the optimization loop
  loopbench init  --name <name>      Scaffold a new loopbench.yaml project
  loopbench check --config <path>    Validate config + dry-run evaluator
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ── loopbench.yaml schema keys ────────────────────────────────────────────────
_LOOPBENCH_KEYS = {"target", "sandbox", "metric", "constraints"}


def _load_loopbench_yaml(config_path: str) -> tuple[dict, dict]:
    """
    Load a loopbench.yaml and split it into:
      (loopbench_fields, openevolve_fields)

    Returns two dicts: LoopBench-specific config, and the remainder
    which is passed directly to openevolve.config.load_config().
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(path, "r") as f:
        raw = yaml.safe_load(f) or {}

    lb = {k: raw.pop(k) for k in list(raw.keys()) if k in _LOOPBENCH_KEYS}
    return lb, raw


def _resolve_path(base_dir: Path, p: str) -> str:
    """Resolve a path relative to the config file's directory."""
    resolved = Path(p)
    if not resolved.is_absolute():
        resolved = base_dir / resolved
    return str(resolved.resolve())


# ── run subcommand ─────────────────────────────────────────────────────────────
async def _run_async(args: argparse.Namespace) -> int:
    """Core async implementation of `loopbench run`."""
    from openevolve.controller import OpenEvolve
    from openevolve.config import load_config

    config_path = Path(args.config).resolve()
    base_dir = config_path.parent

    print(f"[LoopBench] Loading config: {config_path}")
    lb, oe_raw = _load_loopbench_yaml(str(config_path))

    # ── Resolve target paths ─────────────────────────────────────────────────
    target = lb.get("target", {})
    program_path = target.get("program")
    evaluator_path = target.get("evaluator")

    if not program_path:
        print("[LoopBench] ERROR: 'target.program' is required in loopbench.yaml")
        return 1
    if not evaluator_path:
        print("[LoopBench] ERROR: 'target.evaluator' is required in loopbench.yaml")
        return 1

    program_path = _resolve_path(base_dir, program_path)
    evaluator_path = _resolve_path(base_dir, evaluator_path)

    if not Path(program_path).exists():
        print(f"[LoopBench] ERROR: program not found: {program_path}")
        return 1
    if not Path(evaluator_path).exists():
        print(f"[LoopBench] ERROR: evaluator not found: {evaluator_path}")
        return 1

    # ── LoopBench constraint overrides ───────────────────────────────────────
    constraints = lb.get("constraints", {})
    max_iterations = constraints.get("max_iterations") or args.iterations
    if args.iterations:  # CLI flag always wins
        max_iterations = args.iterations

    metric_cfg = lb.get("metric", {})
    target_score = metric_cfg.get("threshold") or args.target_score
    if args.target_score:
        target_score = args.target_score

    # ── Sandbox injection (Task 3) ────────────────────────────────────────────
    sandbox_cfg = lb.get("sandbox", {})
    use_docker = sandbox_cfg.get("use_docker", False)
    if use_docker:
        # When sandbox is enabled, wrap the evaluator with sandbox/runner.py
        # The sandbox runner transparently replaces the evaluator contract.
        try:
            from sandbox.runner import make_sandboxed_evaluator
            evaluator_path = make_sandboxed_evaluator(
                evaluator_path=evaluator_path,
                sandbox_cfg=sandbox_cfg,
                base_dir=str(base_dir),
            )
            print("[LoopBench] Docker sandbox enabled ✅")
        except ImportError:
            print("[LoopBench] WARNING: sandbox/runner.py not found — running without Docker")

    # ── Build OpenEvolve config ───────────────────────────────────────────────
    import tempfile

    # Write the remaining (openevolve-compatible) keys to a temp yaml
    # so we can use load_config() which handles all validation
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as tmp:
        yaml.dump(oe_raw, tmp)
        tmp_path = tmp.name

    try:
        config = load_config(tmp_path)
    finally:
        os.unlink(tmp_path)

    # Apply CLI-level overrides
    if max_iterations:
        config.max_iterations = max_iterations
    if args.output:
        output_dir = args.output
    else:
        output_dir = str(base_dir / "loopbench_output")

    print(f"[LoopBench] Program  : {program_path}")
    print(f"[LoopBench] Evaluator: {evaluator_path}")
    print(f"[LoopBench] Metric   : {metric_cfg.get('name', 'combined_score')}")
    print(f"[LoopBench] Iterations: {max_iterations or config.max_iterations}")
    if target_score:
        print(f"[LoopBench] Target score: {target_score}")
    print(f"[LoopBench] Output   : {output_dir}")
    print()

    # ── Execute ───────────────────────────────────────────────────────────────
    try:
        runner = OpenEvolve(
            initial_program_path=program_path,
            evaluation_file=evaluator_path,
            config=config,
            output_dir=output_dir,
        )

        best = await runner.run(
            iterations=max_iterations,
            target_score=target_score,
        )

        print("\n[LoopBench] ✅ Evolution complete!")
        if best is None:
            print("[LoopBench] ⚠️ No valid programs were found during evolution.")
            print(f"[LoopBench] Results saved to: {output_dir}")
            return 1

        print("[LoopBench] Best metrics:")
        for name, value in best.metrics.items():
            if isinstance(value, (int, float)):
                print(f"  {name:20s}: {value:.4f}")
            else:
                print(f"  {name:20s}: {value}")
        print(f"\n[LoopBench] Results saved to: {output_dir}")
        return 0

    except Exception as exc:
        import traceback
        print(f"[LoopBench] ERROR: {exc}")
        traceback.print_exc()
        return 1


def _run_target_pipeline(args: argparse.Namespace) -> int:
    """Delegate to the hero-command pipeline (loopbench/hero.py)."""
    from loopbench.hero import run_target_pipeline
    return run_target_pipeline(args)


def _config_to_hero_args(args: argparse.Namespace, lb: dict, config_path: str) -> argparse.Namespace:
    """Translate an external-repo loopbench.yaml into hero-pipeline args.

    Used when the config's ``target`` names a repo/file to optimize (as opposed
    to a local ``program``). The evaluator/test file, sandbox command, pip deps,
    metric, and constraints all come from the config — the same file structure
    as the examples, pointed at an external repo.
    """
    base = Path(config_path).resolve().parent
    target = lb.get("target", {}) or {}
    sandbox = lb.get("sandbox", {}) or {}
    metric = lb.get("metric", {}) or {}
    constraints = lb.get("constraints", {}) or {}

    evaluator = target.get("evaluator") or sandbox.get("test_file")
    test_file = _resolve_path(base, evaluator) if evaluator else None

    pip = sandbox.get("pip")
    if isinstance(pip, (list, tuple)):
        pip = " ".join(str(p) for p in pip)

    return argparse.Namespace(
        target=target.get("repo") or str(base),
        target_file=target.get("file"),
        test_file=test_file,
        test_command=sandbox.get("command"),
        metric=metric.get("name") or "combined_score",
        target_score=metric.get("threshold"),
        pip=pip,
        io_tests=None,
        iterations=constraints.get("max_iterations"),
        max_tokens=constraints.get("max_tokens_total"),
        max_cost=constraints.get("max_token_cost_usd"),
        max_runtime=constraints.get("max_runtime_seconds"),
        output=getattr(args, "output", None),
        config=config_path,
        log_level=getattr(args, "log_level", "INFO"),
    )


def _cmd_run(args: argparse.Namespace) -> int:
    # Hero mode: `loopbench run --target <url|path> --metric <name>`
    if getattr(args, "target", None):
        return _run_target_pipeline(args)
    # Config mode: `loopbench run --config <yaml>`
    if not getattr(args, "config", None):
        print(
            "[LoopBench] ERROR: provide either --target <url|path> (hero mode) "
            "or --config <loopbench.yaml> (config mode).",
            file=sys.stderr,
        )
        return 1

    # External-repo config → clone + optimize via the hero pipeline (using the
    # config's evaluator/test/sandbox/metric/constraints). Local `target.program`
    # configs keep the existing evaluator-first controller path.
    try:
        lb, _ = _load_loopbench_yaml(args.config)
    except Exception as exc:
        print(f"[LoopBench] ERROR: {exc}", file=sys.stderr)
        return 1
    target = lb.get("target", {}) or {}
    if target.get("repo") or target.get("file"):
        if not target.get("file"):
            print("[LoopBench] ERROR: target.file is required when target.repo is set.", file=sys.stderr)
            return 1
        if not (target.get("evaluator") or (lb.get("sandbox", {}) or {}).get("test_file")):
            print("[LoopBench] ERROR: target.evaluator (the test/evaluator file) is required "
                  "to score an external repo.", file=sys.stderr)
            return 1
        return _run_target_pipeline(_config_to_hero_args(args, lb, args.config))

    return asyncio.run(_run_async(args))


# ── init subcommand ────────────────────────────────────────────────────────────
def _cmd_init(args: argparse.Namespace) -> int:
    """Scaffold a new loopbench.yaml project (or an external-repo job folder)."""
    if getattr(args, "job", None):
        from loopbench.scaffold import write_job
        paths = write_job(args.job)
        print(f"[LoopBench] ✅ Job folder scaffolded: {paths['dir']}")
        print(f"[LoopBench]   config    : {paths['config']}")
        print(f"[LoopBench]   evaluator : {paths['evaluator']}")
        print("[LoopBench] Next: edit target.repo/file + pip in loopbench.yaml, fill in")
        print("            the correctness + speed TODOs in test_target.py, then run:")
        print(f"  loopbench run --config {paths['config']}")
        return 0

    name = args.name or "my_project"
    output = Path(args.output or ".") / f"{name}.yaml"

    template_path = Path(__file__).parent.parent / "configs" / "loopbench_default.yaml"
    if not template_path.exists():
        print(f"[LoopBench] ERROR: default template not found at {template_path}")
        return 1

    import shutil
    shutil.copy(template_path, output)
    print(f"[LoopBench] ✅ Created: {output}")
    print("[LoopBench] Edit target.program and target.evaluator, then run:")
    print(f"  loopbench run --config {output}")
    return 0


# ── check subcommand ───────────────────────────────────────────────────────────
def _cmd_check(args: argparse.Namespace) -> int:
    """Validate config and dry-run the evaluator on the initial program."""
    config_path = Path(args.config).resolve()
    base_dir = config_path.parent

    print(f"[LoopBench] Checking config: {config_path}")
    try:
        lb, oe_raw = _load_loopbench_yaml(str(config_path))
    except Exception as exc:
        print(f"[LoopBench] ❌ YAML parse error: {exc}")
        return 1

    target = lb.get("target", {})

    # External-repo job: validate structure + the local test/evaluator file.
    # (A full dry-run needs to clone the repo + Docker, so `check` validates that
    # the job is well-formed rather than executing it.)
    if target.get("repo") or (target.get("file") and not target.get("program")):
        sandbox = lb.get("sandbox", {}) or {}
        ok = True
        if target.get("repo"):
            print(f"[LoopBench] ✅ repo   : {target['repo']}")
        if not target.get("file"):
            print("[LoopBench] ❌ target.file is required")
            ok = False
        else:
            print(f"[LoopBench] ✅ file   : {target['file']}")
        ev = target.get("evaluator") or sandbox.get("test_file")
        if not ev:
            print("[LoopBench] ❌ target.evaluator (your test file) is required")
            ok = False
        else:
            ev_path = Path(_resolve_path(base_dir, ev))
            if ev_path.exists():
                print(f"[LoopBench] ✅ test   : {ev_path}")
                text = ev_path.read_text(encoding="utf-8")
                if "LOOPBENCH_SPEED_MS" not in text:
                    print("[LoopBench] ⚠️  test file does not print LOOPBENCH_SPEED_MS "
                          "— the speed metric will be 0 (correctness-only scoring).")
            else:
                print(f"[LoopBench] ❌ test file not found: {ev_path}")
                ok = False
        if sandbox.get("command"):
            print(f"[LoopBench] ✅ command: {sandbox['command']}")
        else:
            print("[LoopBench] ⚠️  no sandbox.command — defaulting to pytest")
        print("[LoopBench] " + ("✅ Job looks valid. Run it with: "
                                 f"loopbench run --config {config_path.name}"
                                 if ok else "❌ Fix the errors above."))
        return 0 if ok else 1

    program = _resolve_path(base_dir, target.get("program", ""))
    evaluator = _resolve_path(base_dir, target.get("evaluator", ""))

    ok = True
    for label, path in [("program", program), ("evaluator", evaluator)]:
        if Path(path).exists():
            print(f"[LoopBench] ✅ {label}: {path}")
        else:
            print(f"[LoopBench] ❌ {label} not found: {path}")
            ok = False

    if not ok:
        return 1

    print("\n[LoopBench] Running evaluator on initial program (dry run)...")
    import importlib.util
    spec = importlib.util.spec_from_file_location("evaluator", evaluator)
    if spec is None or spec.loader is None:
        print(f"[LoopBench] ❌ Failed to load Python module spec from evaluator: {evaluator}")
        print("[LoopBench] Please ensure the evaluator path is correct and points to a valid Python file (usually ending in .py).")
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "evaluate"):
        print(f"[LoopBench] ❌ Evaluator module from {evaluator} does not contain an 'evaluate' function.")
        return 1
    result = mod.evaluate(program)
    print("[LoopBench] ✅ Evaluator returned:")
    if result is None:
        print("[LoopBench] ❌ Evaluator returned None")
        return 1

    if isinstance(result, dict):
        metrics = result
    elif hasattr(result, "metrics") and result.metrics is not None:
        metrics = result.metrics
    else:
        print(f"[LoopBench] ❌ Unexpected result format: {result}")
        return 1

    for k, v in metrics.items():
        print(f"  {k:20s}: {v}")
    return 0


# ── Argument parser ────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loopbench",
        description=(
            "LoopBench — evaluator-first agentic optimization loop.\n"
            "Turns any measurable software problem into a self-improving agent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="loopbench 0.1.0")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── run ──
    run_p = sub.add_parser("run", help="Run the optimization loop")
    run_p.add_argument("--config", "-c", help="Path to loopbench.yaml (evaluator-first mode)")
    run_p.add_argument("--target", help="GitHub URL or local path to optimize (hero mode)")
    run_p.add_argument("--metric", "-m", default="combined_score",
                       help="Metric name to optimize (default: combined_score)")
    run_p.add_argument("--target-file", dest="target_file",
                       help="File to optimize, relative to repo root (hero mode)")
    run_p.add_argument("--test-command", dest="test_command",
                       help="Override the detected test command (hero mode)")
    run_p.add_argument("--io-tests", dest="io_tests",
                       help="Path to a JSON file of stdin/stdout test cases (run mode). "
                            "Enables optimizing scripts that read stdin and print stdout "
                            "(e.g. competitive-programming solutions).")
    run_p.add_argument("--output", "-o", help="Output directory (default: loopbench_output/)")
    run_p.add_argument("--iterations", "-i", type=int, help="Override max iterations")
    run_p.add_argument("--target-score", "-t", type=float, help="Override target score threshold")
    run_p.add_argument("--max-tokens", dest="max_tokens", type=int,
                       help="Stop the loop after this many total LLM tokens (cost bound)")
    run_p.add_argument("--max-cost", dest="max_cost", type=float,
                       help="Stop the loop after this estimated USD spend "
                            "(requires pricing in loopbench.yaml constraints)")
    run_p.add_argument("--max-runtime", dest="max_runtime", type=float,
                       help="Stop the loop after this many seconds of wall-clock time")
    run_p.add_argument("--pip", dest="pip",
                       help="Space-separated pip packages to install in the sandbox "
                            "(overrides auto-detection), e.g. --pip \"numpy scipy\"")
    run_p.add_argument(
        "--log-level", "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    # ── init ──
    init_p = sub.add_parser("init", help="Scaffold a loopbench.yaml or an external-repo job folder")
    init_p.add_argument("--name", "-n", help="Project name (default: my_project)")
    init_p.add_argument("--output", "-o", help="Directory to create the YAML in (default: .)")
    init_p.add_argument("--job", dest="job",
                        help="Scaffold a full external-repo job folder at this path "
                             "(loopbench.yaml + test_target.py), e.g. --job my_job")

    # ── check ──
    check_p = sub.add_parser("check", help="Validate config and dry-run the evaluator")
    check_p.add_argument("--config", "-c", required=True, help="Path to loopbench.yaml")

    return parser


def main() -> int:
    """Entry point for the `loopbench` CLI command."""
    # Force UTF-8 encoding on standard streams to prevent UnicodeEncodeError under Windows command line
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, getattr(args, "log_level", "INFO")),
        format="%(levelname)s | %(name)s | %(message)s",
    )

    if args.command == "run":
        return _cmd_run(args)
    elif args.command == "init":
        return _cmd_init(args)
    elif args.command == "check":
        return _cmd_check(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
