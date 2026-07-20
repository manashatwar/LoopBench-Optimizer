"""
Command-line interface for OpenEvolve
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml

from openevolve import OpenEvolve
from openevolve.config import load_config

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments"""
    parser = argparse.ArgumentParser(description="OpenEvolve - Evolutionary coding agent")

    parser.add_argument("initial_program", help="Path to the initial program file")

    parser.add_argument(
        "evaluation_file", help="Path to the evaluation file containing an 'evaluate' function"
    )

    parser.add_argument("--config", "-c", help="Path to configuration file (YAML)", default=None)

    parser.add_argument("--output", "-o", help="Output directory for results", default=None)

    parser.add_argument(
        "--iterations", "-i", help="Maximum number of iterations", type=int, default=None
    )

    parser.add_argument(
        "--target-score", "-t", help="Target score to reach", type=float, default=None
    )

    parser.add_argument(
        "--log-level",
        "-l",
        help="Logging level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
    )

    parser.add_argument(
        "--checkpoint",
        help="Path to checkpoint directory to resume from (e.g., openevolve_output/checkpoints/checkpoint_50)",
        default=None,
    )

    parser.add_argument("--api-base", help="Base URL for the LLM API", default=None)

    parser.add_argument("--primary-model", help="Primary LLM model name", default=None)

    parser.add_argument("--secondary-model", help="Secondary LLM model name", default=None)

    return parser.parse_args()


async def main_async() -> int:
    """
    Main asynchronous entry point

    Returns:
        Exit code
    """
    args = parse_args()

    # Check if files exist
    if not os.path.exists(args.initial_program):
        print(f"Error: Initial program file '{args.initial_program}' not found")
        return 1

    if not os.path.exists(args.evaluation_file):
        print(f"Error: Evaluation file '{args.evaluation_file}' not found")
        return 1

    # Load base config from file or defaults
    config = load_config(args.config)

    # Create config object with command-line overrides
    if args.api_base or args.primary_model or args.secondary_model:
        # Apply command-line overrides
        if args.api_base:
            config.llm.api_base = args.api_base
            print(f"Using API base: {config.llm.api_base}")

        if args.primary_model:
            config.llm.primary_model = args.primary_model
            print(f"Using primary model: {config.llm.primary_model}")

        if args.secondary_model:
            config.llm.secondary_model = args.secondary_model
            print(f"Using secondary model: {config.llm.secondary_model}")

        # Rebuild models list to apply CLI overrides
        if args.primary_model or args.secondary_model:
            config.llm.rebuild_models()
            print("Applied CLI model overrides - active models:")
            for i, model in enumerate(config.llm.models):
                print(f"  Model {i+1}: {model.name} (weight: {model.weight})")

    # Initialize OpenEvolve
    try:
        openevolve = OpenEvolve(
            initial_program_path=args.initial_program,
            evaluation_file=args.evaluation_file,
            config=config,
            output_dir=args.output,
        )

        # Load from checkpoint if specified
        if args.checkpoint:
            if not os.path.exists(args.checkpoint):
                print(f"Error: Checkpoint directory '{args.checkpoint}' not found")
                return 1
            print(f"Loading checkpoint from {args.checkpoint}")
            openevolve.database.load(args.checkpoint)
            print(
                f"Checkpoint loaded successfully (iteration {openevolve.database.last_iteration})"
            )

        # Override log level if specified
        if args.log_level:
            logging.getLogger().setLevel(getattr(logging, args.log_level))

        # Run evolution
        best_program = await openevolve.run(
            iterations=args.iterations,
            target_score=args.target_score,
            checkpoint_path=args.checkpoint,
        )

        # Get the checkpoint path
        checkpoint_dir = os.path.join(openevolve.output_dir, "checkpoints")
        latest_checkpoint = None
        if os.path.exists(checkpoint_dir):
            checkpoints = [
                os.path.join(checkpoint_dir, d)
                for d in os.listdir(checkpoint_dir)
                if os.path.isdir(os.path.join(checkpoint_dir, d))
            ]
            if checkpoints:
                latest_checkpoint = sorted(
                    checkpoints, key=lambda x: int(x.split("_")[-1]) if "_" in x else 0
                )[-1]

        print("\nEvolution complete!")
        print("Best program metrics:")
        for name, value in best_program.metrics.items():
            # Handle mixed types: format numbers as floats, others as strings
            if isinstance(value, (int, float)):
                print(f"  {name}: {value:.4f}")
            else:
                print(f"  {name}: {value}")

        if latest_checkpoint:
            print(f"\nLatest checkpoint saved at: {latest_checkpoint}")
            print(f"To resume, use: --checkpoint {latest_checkpoint}")

        return 0

    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback

        traceback.print_exc()
        return 1


def main() -> int:
    """
    Main entry point

    Returns:
        Exit code
    """
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
    return asyncio.run(main_async())


# =============================================================================
# OptimizerLoop CLI — Tasks 13.1 – 13.7
# Commands: optimizer init | run | resume | export
# Requirements: 9.1 – 9.8, 15.6, 15.7
# =============================================================================


# ---------------------------------------------------------------------------
# Task 13.3 — progress display helpers
# ---------------------------------------------------------------------------

def _fmt_elapsed(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def _fmt_eta(elapsed: float, done: int, total: int) -> str:
    if done == 0:
        return "estimating…"
    return _fmt_elapsed((elapsed / done) * (total - done))


def optimizer_print_progress(
    *,
    run_id: str,
    generation: int,
    max_iterations: int,
    best_score: float,
    baseline_score: float,
    current_score,
    elapsed: float,
    recent_candidates=None,
):
    """Print a live progress block to stdout (Req 9.6)."""
    improvement = 0.0
    if abs(baseline_score) > 1e-9:
        improvement = (best_score - baseline_score) / abs(baseline_score) * 100

    print(f"\n{'─'*60}")
    print(f"Optimization Run: {run_id}")
    print(f"Generation      : {generation}/{max_iterations}")
    print(f"Best Score      : {best_score:.4f} ({improvement:+.1f}% from baseline)")
    if current_score is not None:
        print(f"Current Score   : {current_score:.4f}")
    print(f"Time Elapsed    : {_fmt_elapsed(elapsed)}")
    print(f"ETA             : {_fmt_eta(elapsed, generation, max_iterations)}")
    if recent_candidates:
        print("\nRecent Candidates:")
        for c in recent_candidates[-5:]:
            mark = "✓" if not c.get("failed") else "✗"
            score = c.get("score")
            score_str = f"{score:.4f}" if isinstance(score, float) else "N/A"
            err = c.get("error_message") or c.get("failure_phase") or ""
            suffix = f" ({err})" if err else ""
            print(f"  {mark} gen{c.get('generation', '?')}: {score_str}{suffix}")
    print(f"{'─'*60}")


# ---------------------------------------------------------------------------
# Task 13.4 — atomic output helpers
# ---------------------------------------------------------------------------

def _serialisable(obj):
    if isinstance(obj, dict):
        return {k: _serialisable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialisable(i) for i in obj]
    if isinstance(obj, float) and (obj != obj or abs(obj) == float("inf")):
        return str(obj)
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)


def _print_run_summary(result: dict):
    status = result.get("status", "unknown").upper()
    icon = "🎉" if status == "SUCCESSFUL" else "✅" if status == "COMPLETED" else "⚠️"
    imp = result.get("improvement_pct", result.get("improvement", 0.0) * 100)
    print(f"\n{'='*60}")
    print(f"{icon}  Optimization Complete — {status}")
    print(f"{'='*60}")
    print(f"  Run ID          : {result.get('run_id', 'N/A')}")
    print(f"  Total Generations: {result.get('total_generations', 0)}")
    print(f"  Baseline Score  : {result.get('baseline_score', 0.0):.4f}")
    print(f"  Best Score      : {result.get('best_score', 0.0):.4f}")
    print(f"  Improvement     : {imp:+.2f}%")
    if result.get("confidence_warning"):
        print("  ⚠️  Improvement below success threshold — review carefully")
    print(f"{'='*60}")


def optimizer_write_results_atomic(result: dict, output_dir: Path) -> bool:
    """Display summary AND write results.json atomically (Req 9.7)."""
    _print_run_summary(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    results_file = output_dir / "results.json"
    try:
        tmp = results_file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_serialisable(result.get("export") or {}), f, indent=2)
        tmp.replace(results_file)
        print(f"\n📁 Detailed results written to: {results_file}")
        return True
    except OSError as exc:
        print(f"\n⚠️  Could not write results to {results_file}: {exc}", file=sys.stderr)
        return False


def optimizer_write_partial_results(result: dict, output_dir: Path):
    """Write partial results when run was interrupted/failed (Req 9.8)."""
    if result.get("baseline_candidate") is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    partial = output_dir / "partial_results.json"
    try:
        with open(partial, "w", encoding="utf-8") as f:
            json.dump(_serialisable(result.get("export") or {}), f, indent=2)
        print(f"\n⚠️  Partial results written to: {partial}")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Task 13.2 — config loading and validation
# ---------------------------------------------------------------------------

def _load_merge_config(config_path, cli_overrides: dict) -> dict:
    """Load YAML and apply CLI overrides (Req 15.6). None values are skipped."""
    raw: dict = {}
    if config_path:
        p = Path(config_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Config file not found: {p}")
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    for key, value in cli_overrides.items():
        if value is not None:
            raw[key] = value
    return raw


def _build_opt_config(raw: dict) -> dict:
    search = raw.get("search", {})
    metrics = raw.get("metrics", {})
    database = raw.get("database", {})
    patterns = metrics.get("patterns")
    metric_patterns = patterns if isinstance(patterns, list) else None
    return {
        "repo_path": raw.get("repo_path") or raw.get("repository", {}).get("local_path") or "",
        "target_file": raw.get("target_file") or "",
        "test_file": raw.get("test_file") or "",
        "max_iterations": search.get("max_iterations", 50),
        "patience": search.get("patience", 10),
        "success_threshold": metrics.get("success_threshold", 0.10),
        "db_path": database.get("path", ":memory:"),
        "search_strategy": {
            "strategy": search.get("strategy", "greedy"),
            "beam_width": search.get("beam_width"),
            "restart_interval": search.get("restart_interval"),
        },
        "metric_patterns": metric_patterns,
        "sandbox_cfg": raw.get("docker", {}),
    }


def _validate_opt_cfg(cfg: dict) -> list:
    errors = []
    if not cfg.get("repo_path"):
        errors.append("'repo_path' is required (or 'repository.local_path' in config)")
    if not cfg.get("target_file"):
        errors.append("'target_file' is required")
    if not cfg.get("test_file"):
        errors.append("'test_file' is required")
    return errors


def _build_llm_ensemble(raw: dict):
    """Build an LLMEnsemble from the config's `llm` section.

    Returns None when no LLM section / models are configured, in which case
    the OptimizerLoop runs without patch generation (baseline only).
    """
    llm_cfg = raw.get("llm") or {}
    models = llm_cfg.get("models") or []
    if not models:
        # Allow a single implicit model from a top-level `model` key
        single = llm_cfg.get("model")
        if single:
            models = [{"name": single, "weight": 1.0}]
    if not models:
        return None

    from openevolve.config import LLMModelConfig
    from openevolve.llm.ensemble import LLMEnsemble

    # Provider prompt caching (design §C2, R5.4/R5.5): threaded from the
    # `prompt.cache_static_prefix` flag (default on) down to every model so the
    # LLM layer knows whether to structure the static prefix as cacheable.
    prompt_cfg = raw.get("prompt") or {}
    cache_static_prefix = bool(prompt_cfg.get("cache_static_prefix", True))

    # Shared defaults applied to every model unless overridden per-model
    shared = {
        "api_base": llm_cfg.get("api_base"),
        "api_key": llm_cfg.get("api_key"),
        "temperature": llm_cfg.get("temperature", 0.7),
        "top_p": llm_cfg.get("top_p", 0.95),
        "max_tokens": llm_cfg.get("max_tokens", 4096),
        "timeout": llm_cfg.get("timeout", 90),
        "retries": llm_cfg.get("retries", 3),
        "retry_delay": llm_cfg.get("retry_delay", 5),
        "system_message": llm_cfg.get("system_message"),
        "reasoning_effort": llm_cfg.get("reasoning_effort"),
    }

    model_cfgs = []
    for m in models:
        merged = {**shared}
        merged.update({k: v for k, v in m.items() if v is not None})
        merged.setdefault("weight", 1.0)
        # LLMModelConfig.__post_init__ resolves ${VAR} in api_key from env
        model_cfgs.append(
            LLMModelConfig(
                name=merged.get("name"),
                api_base=merged.get("api_base"),
                api_key=merged.get("api_key"),
                weight=merged.get("weight", 1.0),
                system_message=merged.get("system_message"),
                temperature=merged.get("temperature"),
                top_p=merged.get("top_p"),
                max_tokens=merged.get("max_tokens"),
                timeout=merged.get("timeout"),
                retries=merged.get("retries"),
                retry_delay=merged.get("retry_delay"),
                reasoning_effort=merged.get("reasoning_effort"),
                cache_static_prefix=cache_static_prefix,
            )
        )

    return LLMEnsemble(model_cfgs)


# ---------------------------------------------------------------------------
# Task 13.1 — optimizer init
# ---------------------------------------------------------------------------

def _opt_cmd_init(args) -> int:
    from openevolve.config_validator import generate_template
    output = args.output or "optimizer.yaml"
    try:
        path = generate_template(output)
        print(f"✅ Configuration template written to: {path}")
        print(f"   Edit the file, then run:  optimizer run --config {path}")
        return 0
    except OSError as exc:
        print(f"❌ Could not write template: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Task 13.2 — optimizer run
# ---------------------------------------------------------------------------

def _opt_cmd_run(args) -> int:
    from openevolve.optimizer_loop import OptimizerLoop
    from openevolve.config_validator import validate_optimizer_config, ConfigValidationError
    from openevolve.report_generator import FinalReportWriter

    output_dir = Path(args.output or "optimizer_output")
    raw: dict = {}
    try:
        raw = _load_merge_config(args.config, {})
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"❌ Failed to load config: {exc}", file=sys.stderr)
        return 1

    # CLI overrides (Req 15.6)
    if args.max_iterations:
        raw.setdefault("search", {})["max_iterations"] = args.max_iterations
    if args.metric:
        raw.setdefault("metrics", {})["primary"] = args.metric

    # Validate 6-section contract when a full config file was provided
    if args.config:
        try:
            validate_optimizer_config(raw)
        except ConfigValidationError as exc:
            print(f"❌ Config validation failed:\n  {exc}", file=sys.stderr)
            return 1

    opt_cfg = _build_opt_config(raw)
    errors = _validate_opt_cfg(opt_cfg)
    if errors:
        for e in errors:
            print(f"❌ {e}", file=sys.stderr)
        return 1

    # Build the LLM ensemble so patch generation actually runs (Phase 2).
    # Without this the loop only records the baseline and every generation
    # fails at the "generate" phase.
    try:
        llm_ensemble = _build_llm_ensemble(raw)
    except Exception as exc:
        print(f"❌ Failed to initialize LLM ensemble: {exc}", file=sys.stderr)
        return 1
    if llm_ensemble is None:
        print(
            "⚠️  No LLM models configured (llm.models) — running baseline only. "
            "Add an llm section with models to enable evolution.",
            file=sys.stderr,
        )

    loop = OptimizerLoop(opt_cfg, llm_ensemble=llm_ensemble)
    start = time.monotonic()
    result = None

    print("🚀 Starting optimization run…")
    print(f"   Target    : {opt_cfg['target_file']}")
    print(f"   Max iter  : {opt_cfg['max_iterations']}")
    print(f"   Patience  : {opt_cfg['patience']}")
    print(f"   Output    : {output_dir}\n")

    # Wrap execute_generation for live progress (Task 13.3)
    orig_exec = loop.execute_generation

    def _wrapped(generation, baseline):
        candidate = orig_exec(generation, baseline)
        elapsed = time.monotonic() - start
        best = max((c.get("score") or 0.0 for c in loop._candidate_history), default=0.0)
        base = (loop._candidate_history[0].get("score") or 0.0) if loop._candidate_history else 0.0
        optimizer_print_progress(
            run_id=loop._run_id or "pending",
            generation=generation,
            max_iterations=loop.max_iterations,
            best_score=best,
            baseline_score=base,
            current_score=candidate.get("score"),
            elapsed=elapsed,
            recent_candidates=loop._candidate_history[-5:],
        )
        return candidate

    loop.execute_generation = _wrapped  # type: ignore[method-assign]

    try:
        result = loop.run()
    except KeyboardInterrupt:
        print("\n⚠️  Run interrupted.", file=sys.stderr)
        if loop._candidate_history:
            h = loop._candidate_history
            best = max(h, key=lambda c: c.get("score") or 0.0)
            partial = {
                "run_id": loop._run_id, "status": "interrupted",
                "best_candidate": best, "baseline_candidate": h[0],
                "total_generations": len(h) - 1, "improvement": 0.0,
                "improvement_pct": 0.0, "confidence_warning": True,
                "best_score": best.get("score") or 0.0,
                "baseline_score": h[0].get("score") or 0.0, "export": {},
            }
            optimizer_write_partial_results(partial, output_dir)
        return 130
    except Exception as exc:
        logger.error("Run failed: %s", exc, exc_info=True)
        print(f"\n❌ Run failed: {exc}", file=sys.stderr)
        return 1

    # Task 13.4 — atomic output
    success = optimizer_write_results_atomic(result, output_dir)
    try:
        FinalReportWriter(output_dir=output_dir / "report").write_all(result)
        print(f"📄 Report artefacts in: {output_dir / 'report'}")
    except Exception as exc:
        logger.warning("Could not write report artefacts: %s", exc)

    return 0 if success else 1


# ---------------------------------------------------------------------------
# Task 13.5 — optimizer resume
# ---------------------------------------------------------------------------

def _opt_cmd_resume(args) -> int:
    from openevolve.database import CandidateDatabase

    db_path = args.db or "optimizer.db"
    output_dir = Path(args.output or "optimizer_output")
    print(f"🔄 Resuming run: {args.run_id}  (db: {db_path})")
    try:
        db = CandidateDatabase(db_path)
        run = db.get_run(args.run_id)
        if run is None:
            print(f"❌ Run '{args.run_id}' not found in {db_path}", file=sys.stderr)
            return 1
        if run.get("status") not in ("running", "interrupted", "failed"):
            print(f"⚠️  Run '{args.run_id}' has status '{run['status']}' — nothing to resume.")
            return 0
        stored_cfg = run.get("config") or {}
        if not stored_cfg:
            print("❌ No configuration stored — cannot resume automatically.", file=sys.stderr)
            return 1
        opt_cfg = _build_opt_config(stored_cfg)
        export = db.export_run(args.run_id)
        candidates = export.get("candidates") or []
        last_gen = max((c["generation"] for c in candidates if not c.get("failed")), default=0)
        print(f"   Last good generation: {last_gen}")
        opt_cfg["max_iterations"] = opt_cfg["max_iterations"] - last_gen
        if opt_cfg["max_iterations"] <= 0:
            print("✅ Run already complete — nothing to resume.")
            return 0
        from openevolve.optimizer_loop import OptimizerLoop
        loop = OptimizerLoop(opt_cfg)
        loop._run_id = args.run_id
        loop.db = db
        loop._candidate_history = candidates
        result = loop.run()
        optimizer_write_results_atomic(result, output_dir)
        return 0
    except Exception as exc:
        print(f"❌ Resume failed: {exc}", file=sys.stderr)
        logger.error("Resume error: %s", exc, exc_info=True)
        return 1


# ---------------------------------------------------------------------------
# Task 13.6 — optimizer export
# ---------------------------------------------------------------------------

def _opt_cmd_export(args) -> int:
    from openevolve.database import CandidateDatabase

    db_path = args.db or "optimizer.db"
    fmt = (args.format or "json").lower()
    try:
        db = CandidateDatabase(db_path)
        run = db.get_run(args.run_id)
        if run is None:
            print(f"❌ Run '{args.run_id}' not found in {db_path}", file=sys.stderr)
            return 1
        export = db.export_run(args.run_id)
        if fmt == "json":
            out = Path(args.output or f"{args.run_id}_export.json")
            with open(out, "w", encoding="utf-8") as f:
                json.dump(_serialisable(export), f, indent=2)
            print(f"✅ Exported as JSON → {out}")
        elif fmt == "markdown":
            out = Path(args.output or f"{args.run_id}_export.md")
            _write_md_export(export, out)
            print(f"✅ Exported as Markdown → {out}")
        else:
            print(f"❌ Unknown format '{fmt}'. Use 'json' or 'markdown'.", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"❌ Export failed: {exc}", file=sys.stderr)
        return 1


def _write_md_export(export: dict, path: Path):
    run = export.get("run") or {}
    cands = export.get("candidates") or []
    audit = export.get("audit_log") or []
    lines = [
        f"# Optimization Run — {run.get('id', 'N/A')}",
        "",
        f"**Status**: {run.get('status', 'N/A')}  ",
        f"**Target Repo**: {run.get('target_repo', 'N/A')}  ",
        f"**Final Improvement**: {run.get('final_improvement', 'N/A')}  ",
        "",
        f"## Candidates ({len(cands)} total)",
        "",
        "| Gen | Score | Failed | Phase |",
        "|-----|-------|--------|-------|",
    ]
    for c in cands:
        lines.append(
            f"| {c.get('generation','?')} | {c.get('score') or 'N/A'} "
            f"| {c.get('failed',False)} | {c.get('failure_phase') or '—'} |"
        )
    lines += [
        "", f"## Audit Log ({len(audit)} events)", "",
        "| Timestamp | Event | Details |", "|-----------|-------|---------|",
    ]
    for e in audit:
        lines.append(
            f"| {e.get('timestamp','?')} | {e.get('event_type','?')} "
            f"| {str(e.get('event_data',''))[:80]} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# optimizer entry point — subcommand parser
# ---------------------------------------------------------------------------

def _build_optimizer_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="optimizer",
        description="OptimizerLoop — autonomous evolutionary code optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version="optimizer 1.0.0")
    sub = p.add_subparsers(dest="command", required=True)

    # init
    init_p = sub.add_parser("init", help="Generate a configuration template")
    init_p.add_argument("--output", "-o", default="optimizer.yaml",
                        help="Output path (default: optimizer.yaml)")

    # run
    run_p = sub.add_parser("run", help="Start an optimization run")
    run_p.add_argument("--config", "-c", help="Path to optimizer YAML config")
    run_p.add_argument("--max-iterations", "-i", type=int, dest="max_iterations")
    run_p.add_argument("--metric", "-m", help="Primary metric name")
    run_p.add_argument("--output", "-o", default="optimizer_output")
    run_p.add_argument("--log-level", default="INFO",
                       choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # resume
    res_p = sub.add_parser("resume", help="Resume an interrupted run")
    res_p.add_argument("--run-id", "-r", required=True, dest="run_id")
    res_p.add_argument("--db", help="Path to SQLite database")
    res_p.add_argument("--output", "-o", default="optimizer_output")

    # export
    exp_p = sub.add_parser("export", help="Export an optimization run")
    exp_p.add_argument("--run-id", "-r", required=True, dest="run_id")
    exp_p.add_argument("--db", help="Path to SQLite database")
    exp_p.add_argument("--format", "-f", default="json", choices=["json", "markdown"])
    exp_p.add_argument("--output", "-o", help="Output file path")

    # dashboard
    dash_p = sub.add_parser("dashboard", help="Launch dashboard (GitHub Pages + local server)")
    dash_p.add_argument("--run-id", "-r", dest="run_id",
                        help="Export this run to docs/data.json for GitHub Pages")
    dash_p.add_argument("--db", help="Path to SQLite database (default: optimizer.db)")
    dash_p.add_argument("--port", "-p", type=int, default=8080,
                        help="Local server port (default: 8080)")
    dash_p.add_argument("--open", action="store_true", dest="open_browser",
                        help="Open browser automatically")
    dash_p.add_argument("--docs-dir", default="docs",
                        help="Directory containing index.html (default: docs)")
    dash_p.add_argument("--no-server", action="store_true",
                        help="Only generate data.json, do not start local server")

    return p


# ---------------------------------------------------------------------------
# Task 15.6 — optimizer dashboard
# ---------------------------------------------------------------------------

def _opt_cmd_dashboard(args) -> int:
    """Export run data to docs/data.json and optionally serve locally (Req 9.1).

    Two modes:
      --no-server  → generate docs/data.json only (commit to GitHub Pages)
      default      → generate data.json + start http.server on --port
    """
    import http.server
    import threading
    import webbrowser
    import os as _os

    db_path = args.db or "optimizer.db"
    docs_dir = Path(args.docs_dir)
    run_id = args.run_id

    # ── Generate data.json ─────────────────────────────────────────────────
    data_json_path = docs_dir / "data.json"

    if run_id:
        from openevolve.database import CandidateDatabase
        try:
            db = CandidateDatabase(db_path)
            run = db.get_run(run_id)
            if run is None:
                print(f"❌ Run '{run_id}' not found in {db_path}", file=sys.stderr)
                return 1
            export = db.export_run(run_id)
            best = db.get_best_candidate(run_id=run_id)
            export["best_candidate"] = best
            docs_dir.mkdir(parents=True, exist_ok=True)
            with open(data_json_path, "w", encoding="utf-8") as f:
                json.dump(_serialisable(export), f, indent=2)
            print(f"✅ data.json written → {data_json_path}")
            print("   Commit docs/ to GitHub Pages to share this run.")
            db.close()
        except Exception as exc:
            print(f"❌ Failed to export data: {exc}", file=sys.stderr)
            return 1
    else:
        if not data_json_path.exists():
            print("⚠️  No --run-id given and no docs/data.json found.")
            print("   Run: optimizer dashboard --run-id <id>")

    if args.no_server:
        return 0

    # ── Start local HTTP server ────────────────────────────────────────────
    if not docs_dir.exists():
        print(f"❌ docs directory not found: {docs_dir}", file=sys.stderr)
        return 1

    orig_dir = _os.getcwd()
    _os.chdir(docs_dir)

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

    port = args.port
    server = http.server.HTTPServer(("", port), _Handler)
    url = f"http://localhost:{port}"
    if run_id:
        url += f"?run_id={run_id}"
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    print(f"🌐 Dashboard serving at: {url}")
    print("   Static GitHub Pages mode also available via docs/data.json")
    print("   Press Ctrl+C to stop\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n✅ Dashboard server stopped.")
    finally:
        _os.chdir(orig_dir)

    return 0


def optimizer_main() -> int:
    """Entry point for the `optimizer` CLI command."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass

    parser = _build_optimizer_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, getattr(args, "log_level", "INFO")),
        format="%(levelname)s | %(name)s | %(message)s",
    )

    return {
        "init": _opt_cmd_init,
        "run": _opt_cmd_run,
        "resume": _opt_cmd_resume,
        "export": _opt_cmd_export,
        "dashboard": _opt_cmd_dashboard,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
