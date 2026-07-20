"""
LoopBench hero command implementation.

    loopbench run --target <url|path> --metric <name>

Clones (or uses) a repo, runs the OptimizerLoop with a real LLM, and emits:
  - <output>/best.patch          the best candidate's unified diff
  - <output>/report/*            validation report + README + PR description
  - <output>/results.json        full run export
  - docs/data.json               dashboard data
  - <output>/test_log.txt        proof that tests ran (pass/fail + output)
"""

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

_REPO_ROOT = Path(__file__).parent.parent.resolve()


def _load_dotenv() -> None:
    """Load KEY=VALUE pairs from REPO_ROOT/.env into os.environ.

    Shell-exported variables take precedence (setdefault), matching standard
    dotenv behaviour. Strips surrounding quotes from values.
    """
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] in ('"', "'") and v[-1] == v[0]:
            v = v[1:-1]
        if k:
            os.environ.setdefault(k, v)


def _default_llm_cfg() -> Dict[str, Any]:
    """LLM config driven by .env (LLM_API_BASE / LLM_MODEL), Gemini fallback.

    The API key is always resolved from ${GEMINI_API_KEY} (the var name used
    in .env) regardless of provider.
    """
    api_base = os.environ.get(
        "LLM_API_BASE", "https://generativelanguage.googleapis.com/v1beta/openai/"
    )
    model = os.environ.get("LLM_MODEL", "gemini-2.0-flash")
    return {
        "api_base": api_base,
        "api_key": "${GEMINI_API_KEY}",
        "models": [{"name": model, "weight": 1.0}],
        "system_message": (
            "You are an expert software performance engineer. Given repository "
            "context and a target file, produce a single valid unified diff patch "
            "(compatible with `git apply`) that improves performance while keeping "
            "all tests passing. Output only the patch inside a ```diff code block."
        ),
        "temperature": 0.7,
        "max_tokens": 2048,
        "timeout": 90,
        "retries": 3,
        "retry_delay": 30,
    }


def _is_url(target: str) -> bool:
    return target.startswith(("http://", "https://", "git@", "ssh://"))


def _autodetect_target_file(repo_path: Path, lang: str) -> Optional[Path]:
    """Best-effort: pick a single obvious source file to optimize."""
    # 1. Common example convention
    for name in ("initial_program.py", "main.py", "app.py"):
        matches = list(repo_path.rglob(name))
        if len(matches) == 1:
            return matches[0]
        if matches:
            return matches[0]
    # 2. Single top-level .py (excluding tests / setup)
    ext = {
        "python": ".py", "javascript": ".js", "typescript": ".ts",
        "go": ".go", "rust": ".rs",
    }.get(lang, ".py")
    candidates = [
        p for p in repo_path.rglob(f"*{ext}")
        if "test" not in p.name.lower()
        and p.name not in ("setup.py", "conftest.py")
        and ".venv" not in p.parts
        and "site-packages" not in p.parts
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _autodetect_test_file(repo_path: Path, target_file: Path) -> Optional[Path]:
    """Locate a pytest file near the target (test_*.py convention)."""
    near = list(target_file.parent.glob("test_*.py"))
    if near:
        return near[0]
    anywhere = [
        p for p in repo_path.rglob("test_*.py")
        if ".venv" not in p.parts and "site-packages" not in p.parts
    ]
    return anywhere[0] if anywhere else None


def _write_test_log(out_dir: Path, result: Dict[str, Any]) -> Path:
    """Write proof-of-tests log from the best candidate's captured output."""
    best = result.get("best_candidate") or {}
    baseline = result.get("baseline_candidate") or {}
    src = best if (best.get("stdout") or best.get("stderr")) else baseline

    lines = [
        "LoopBench — Test Integrity Log",
        "=" * 50,
        f"Run ID        : {result.get('run_id', 'N/A')}",
        f"Status        : {result.get('status', 'N/A')}",
        f"Baseline score: {result.get('baseline_score', 0.0):.6f}",
        f"Best score    : {result.get('best_score', 0.0):.6f}",
        f"Exit code      : {src.get('exit_code')}",
        f"Failed         : {src.get('failed')}",
        f"Failure phase  : {src.get('failure_phase') or '—'}",
        "",
        "----- STDOUT -----",
        (src.get("stdout") or "(no stdout captured)"),
        "",
        "----- STDERR -----",
        (src.get("stderr") or "(no stderr captured)"),
    ]
    path = out_dir / "test_log.txt"
    path.write_text("\n".join(str(x) for x in lines), encoding="utf-8")
    return path


def _write_dashboard_data(db_path: str, run_id: str) -> Optional[Path]:
    """Export the run to docs/data.json for the GitHub Pages dashboard."""
    if not run_id:
        return None
    try:
        from openevolve.config import DatabaseConfig
        from openevolve.database import ProgramDatabase
        from openevolve.cli import _serialisable

        db = ProgramDatabase(DatabaseConfig(db_path=db_path))
        export = db.export_run(run_id)
        try:
            export["best_candidate"] = db.get_best_candidate(run_id=run_id)
        except Exception:
            pass
        docs_dir = _REPO_ROOT / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        data_json = docs_dir / "data.json"
        with open(data_json, "w", encoding="utf-8") as f:
            json.dump(_serialisable(export), f, indent=2)
        try:
            db.close()
        except Exception:
            pass
        return data_json
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[LoopBench] WARNING: could not write docs/data.json: {exc}")
        return None


def run_target_pipeline(args: argparse.Namespace) -> int:
    """Hero command: clone/use repo, optimize, emit patch + dashboard + log."""
    from openevolve.repo_manager import (
        clone_repository, detect_language, detect_test_framework,
    )
    from openevolve.optimizer_loop import OptimizerLoop
    from openevolve.cli import _build_llm_ensemble, optimizer_write_results_atomic
    from openevolve.report_generator import FinalReportWriter

    target = args.target.strip()
    metric = getattr(args, "metric", None) or "combined_score"
    clone_dir: Optional[str] = None

    # Load .env so GEMINI_API_KEY / LLM_API_BASE / LLM_MODEL are available.
    _load_dotenv()

    try:
        # 1. Resolve repo
        if _is_url(target):
            clone_dir = tempfile.mkdtemp(prefix="loopbench_clone_")
            print(f"[LoopBench] Cloning {target} …")
            repo_path = clone_repository(
                target, Path(clone_dir) / "repo",
                auth_token=os.environ.get("GITHUB_TOKEN"),
            )
        else:
            repo_path = Path(target).resolve()
            if not repo_path.exists():
                print(f"[LoopBench] ERROR: target path not found: {repo_path}")
                return 1
        repo_path = Path(repo_path)

        # 2. Detect language + test command
        lang = detect_language(repo_path)
        fw = detect_test_framework(repo_path)
        print(f"[LoopBench] Language: {lang} | Test framework: {fw.name}")

        # 3. Resolve target + test file
        if args.target_file:
            target_path = (repo_path / args.target_file).resolve()
        else:
            target_path = _autodetect_target_file(repo_path, lang)
        if not target_path or not Path(target_path).exists():
            print("[LoopBench] ERROR: could not determine which file to optimize. "
                  "Pass --target-file <path relative to repo>.")
            return 1
        target_path = Path(target_path)

        # Language of the file being optimized — drives language-aware prompting.
        # Derived from the TARGET FILE extension, not the repo's dominant language
        # (e.g. the Vyper repo is mostly Python, but crowdfund.vy is Vyper).
        _LANG_BY_EXT = {
            ".py": "Python", ".vy": "Vyper", ".sol": "Solidity",
            ".rs": "Rust", ".go": "Go", ".js": "JavaScript", ".ts": "TypeScript",
            ".java": "Java", ".c": "C", ".cpp": "C++", ".cc": "C++", ".rb": "Ruby",
        }
        target_lang = _LANG_BY_EXT.get(
            target_path.suffix.lower(), (lang.capitalize() if lang else "Python")
        )
        print(f"[LoopBench] Target lang : {target_lang}")

        # ── Run mode: stdin/stdout scripts (competitive programming, CLI tools) ─
        # If I/O test cases are provided (or auto-detected), generate a pytest
        # harness that runs the target as a SUBPROCESS instead of importing it.
        # This lets LoopBench optimize scripts that read stdin at module top
        # level (which would otherwise crash the default import-based harness).
        run_mode_info = None
        try:
            from loopbench.io_harness import maybe_build_io_harness
            run_mode_info = maybe_build_io_harness(
                getattr(args, "io_tests", None),
                target_path,
                Path(tempfile.mkdtemp(prefix="loopbench_io_")),
            )
        except Exception as exc:
            print(f"[LoopBench] ERROR: could not build I/O harness: {exc}")
            return 1

        # An explicit test/evaluator file (from a loopbench.yaml target.evaluator
        # when optimizing an external repo) is injected into the sandbox next to
        # the target. It lives in the user's job dir, not the cloned repo.
        explicit_test = getattr(args, "test_file", None)
        if explicit_test:
            test_path = Path(explicit_test).resolve()
            if not test_path.exists():
                print(f"[LoopBench] ERROR: test/evaluator file not found: {test_path}")
                return 1
            print(f"[LoopBench] Evaluator   : {test_path}")
        elif run_mode_info is not None:
            test_path = Path(run_mode_info["test_path"])
            print(
                f"[LoopBench] Run mode enabled ({run_mode_info['reason']}): "
                f"{run_mode_info['n_cases']} I/O case(s) — testing via subprocess"
            )
        else:
            test_path = _autodetect_test_file(repo_path, target_path)
            # Nudge: if the target reads stdin at top level, it can't be imported
            # for testing. Warn the user toward run mode instead of a silent 0%.
            try:
                from loopbench.io_harness import detect_stdin_usage
                if detect_stdin_usage(str(target_path)):
                    print(
                        "[LoopBench] ⚠️  This script reads from stdin at top level, so it "
                        "cannot be imported for testing.\n"
                        "            Provide stdin/stdout cases with "
                        "--io-tests <cases.json> to optimize it in run mode\n"
                        "            (or place io_tests.json next to the file). "
                        "See docs/defining-benchmarks.md — Option D."
                    )
            except Exception:
                pass
        # Only an EXPLICIT --test-command overrides the sandbox default (which
        # already includes the resolved test path and -s so speed markers show).
        # The auto-detected fw.test_command is not forced here.
        test_cmd = args.test_command

        # Ensure target is a Git repository, fallback to initializing a temporary one if needed
        if not _is_url(target):
            import subprocess
            original_repo_path = repo_path
            try:
                git_root = subprocess.check_output(
                    ["git", "rev-parse", "--show-toplevel"],
                    cwd=str(repo_path),
                    text=True,
                    stderr=subprocess.DEVNULL
                ).strip()
                git_root_path = Path(git_root).resolve()
                if git_root_path != repo_path:
                    repo_path = git_root_path
                    print(f"[LoopBench] Detected Git root at: {repo_path}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                temp_repo_dir = Path(tempfile.mkdtemp(prefix="loopbench_temp_git_"))
                repo_path_copy = (temp_repo_dir / "repo").resolve()
                print(f"[LoopBench] Target is not a Git repository. Creating a temporary Git copy at: {repo_path_copy}")
                shutil.copytree(original_repo_path, repo_path_copy, symlinks=True, ignore_dangling_symlinks=True)
                
                target_path = (repo_path_copy / target_path.relative_to(original_repo_path)).resolve()
                # Only relocate the test file if it lives inside the repo. The
                # run-mode I/O harness is generated in a separate temp dir and
                # must stay where it is (the sandbox copies it in by basename).
                if test_path:
                    try:
                        rel_test = Path(test_path).resolve().relative_to(original_repo_path.resolve())
                        test_path = (repo_path_copy / rel_test).resolve()
                    except ValueError:
                        pass  # harness / external test file — leave as-is
                repo_path = repo_path_copy
                
                try:
                    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, stdout=subprocess.DEVNULL)
                    subprocess.run(["git", "config", "user.email", "loopbench@example.com"], cwd=str(repo_path), check=True)
                    subprocess.run(["git", "config", "user.name", "LoopBench"], cwd=str(repo_path), check=True)
                    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, stdout=subprocess.DEVNULL)
                    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(repo_path), check=True, stdout=subprocess.DEVNULL)
                    print("[LoopBench] Initialized temporary Git repository.")
                except Exception as e:
                    print(f"[LoopBench] ERROR: failed to initialize temporary Git repo: {e}")
                    return 1

        print(f"[LoopBench] Target file : {target_path}")
        print(f"[LoopBench] Test file   : {test_path or '(none detected)'}")
        print(f"[LoopBench] Test command: {test_cmd or '(default pytest)'}")
        print(f"[LoopBench] Metric      : {metric}")

        # Detect third-party dependencies so the sandbox can run the code.
        # Priority: --pip > requirements.txt > imports scanned across the repo.
        try:
            from loopbench.deps import resolve_deps_with_source
            # None = not specified (auto-detect); "" or [] = explicitly no deps.
            pip_attr = getattr(args, "pip", None)
            explicit_pip = None if pip_attr is None else str(pip_attr).split()
            pip_pkgs, dep_source = resolve_deps_with_source(
                Path(repo_path), Path(target_path), explicit=explicit_pip
            )
        except Exception as exc:
            print(f"[LoopBench] WARNING: dependency detection failed: {exc}")
            pip_pkgs, dep_source = [], "none"
        if pip_pkgs:
            print(f"[LoopBench] Dependencies: {', '.join(pip_pkgs)}  (from {dep_source})")
            if "best-effort" in dep_source:
                print("[LoopBench]   note: inferred from imports — if a package fails to "
                      "install, pin deps with --pip or a requirements.txt/pyproject.toml")

        # 4. Build config + LLM ensemble
        db_dir = tempfile.mkdtemp(prefix="loopbench_db_")
        db_path = str(Path(db_dir) / "loopbench.db")
        opt_cfg = {
            "repo_path": str(repo_path),
            "target_file": str(target_path),
            "test_file": str(test_path) if test_path else str(target_path),
            "max_iterations": args.iterations or 5,
            "patience": 5,
            "success_threshold": 0.05,
            "db_path": db_path,
            "language": target_lang,
            "search_strategy": {"strategy": "greedy"},
            "metric_patterns": None,
            "rewrite_mode": "auto",
            "full_rewrite_max_lines": 300,
            "sandbox_cfg": {"test_command": test_cmd, "timeout": 120, "pip_install": pip_pkgs},
        }

        raw: Dict[str, Any] = {}
        if getattr(args, "config", None) and Path(args.config).exists():
            with open(args.config, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        if not raw.get("llm"):
            raw["llm"] = _default_llm_cfg()

        # ── Statistical speed gate config (design §C1) ─────────────────────────
        # Thread sandbox.repeats (K-run measurement, default 1) and
        # metric.min_effect (minimum relative median improvement, default 0.03)
        # from loopbench.yaml into the sandbox cfg / OptimizerLoop.
        yaml_sandbox = raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {}
        yaml_metric = raw.get("metric") if isinstance(raw.get("metric"), dict) else {}
        repeats = yaml_sandbox.get("repeats", 1)
        try:
            repeats = int(repeats)
        except (TypeError, ValueError):
            repeats = 1
        opt_cfg["sandbox_cfg"]["repeats"] = repeats if repeats >= 1 else 1
        min_effect = yaml_metric.get("min_effect", 0.03)
        try:
            opt_cfg["min_effect"] = float(min_effect)
        except (TypeError, ValueError):
            opt_cfg["min_effect"] = 0.03
        if repeats > 1:
            print(f"[LoopBench] Speed repeats: {opt_cfg['sandbox_cfg']['repeats']} runs/candidate")
        print(f"[LoopBench] Min effect   : {opt_cfg['min_effect']:.3f} "
              "(min relative median speedup to accept)")

        # ── Profiler hotspot budget (design §C2) ───────────────────────────────
        # `prompt.max_hotspots` is the single source of truth for how many
        # baseline hotspots surface in the prompt. Thread it into the sandbox
        # cfg (the runner's `_max_hotspots_from_cfg` reads `max_hotspots`), so
        # the prompt-side config governs the sandbox-side truncation.
        from openevolve.profiler import DEFAULT_MAX_HOTSPOTS
        yaml_prompt = raw.get("prompt") if isinstance(raw.get("prompt"), dict) else {}
        max_hotspots = yaml_prompt.get("max_hotspots", DEFAULT_MAX_HOTSPOTS)
        try:
            max_hotspots = int(max_hotspots)
        except (TypeError, ValueError):
            max_hotspots = DEFAULT_MAX_HOTSPOTS
        opt_cfg["sandbox_cfg"]["max_hotspots"] = (
            max_hotspots if max_hotspots >= 0 else DEFAULT_MAX_HOTSPOTS
        )

        # ── Final winner revalidation (design §C1, Requirement 3) ──────────────
        # ON by default; re-runs the winner M times in the sandbox after the loop
        # (no LLM calls) and keeps the run "successful" only if the gain holds.
        # CLI --no-revalidate / --revalidate-runs win; loopbench.yaml `sandbox`
        # may set defaults (revalidate / revalidate_runs).
        revalidate = getattr(args, "revalidate", True)
        if yaml_sandbox.get("revalidate") is not None:
            revalidate = bool(yaml_sandbox.get("revalidate"))
        opt_cfg["revalidate"] = bool(revalidate)
        reval_runs = getattr(args, "revalidate_runs", 7)
        if yaml_sandbox.get("revalidate_runs") is not None:
            reval_runs = yaml_sandbox.get("revalidate_runs")
        try:
            reval_runs = int(reval_runs)
        except (TypeError, ValueError):
            reval_runs = 7
        opt_cfg["revalidate_runs"] = reval_runs if reval_runs >= 1 else 7
        if opt_cfg["revalidate"]:
            print(f"[LoopBench] Revalidate   : winner re-run {opt_cfg['revalidate_runs']}× "
                  "after loop (no LLM)")
        else:
            print("[LoopBench] Revalidate   : disabled (--no-revalidate)")

        # ── Constraints (CLI flags override loopbench.yaml constraints) ────────
        constraints = raw.get("constraints") if isinstance(raw.get("constraints"), dict) else {}
        opt_cfg["metric_name"] = metric
        opt_cfg["max_tokens_total"] = getattr(args, "max_tokens", None) or constraints.get("max_tokens_total")
        opt_cfg["max_usd"] = getattr(args, "max_cost", None) or constraints.get("max_token_cost_usd")
        opt_cfg["max_runtime_seconds"] = getattr(args, "max_runtime", None) or constraints.get("max_runtime_seconds")
        opt_cfg["usd_per_1k_prompt"] = constraints.get("usd_per_1k_prompt", 0.0)
        opt_cfg["usd_per_1k_completion"] = constraints.get("usd_per_1k_completion", 0.0)
        if opt_cfg["max_tokens_total"]:
            print(f"[LoopBench] Token budget : {opt_cfg['max_tokens_total']} tokens")
        if opt_cfg["max_usd"]:
            print(f"[LoopBench] Cost budget  : ${float(opt_cfg['max_usd']):.4f}")
        if opt_cfg["max_runtime_seconds"]:
            print(f"[LoopBench] Runtime limit: {opt_cfg['max_runtime_seconds']}s")

        # ── Search strategy (CLI --strategy overrides loopbench.yaml `search`) ─
        # Default is `auto`: starts greedy and deterministically escalates to
        # restart/diversify on a plateau — no extra LLM calls.
        search_cfg = raw.get("search") if isinstance(raw.get("search"), dict) else {}
        strategy_name = getattr(args, "strategy", None) or search_cfg.get("strategy") or "auto"
        strategy_cfg: Dict[str, Any] = {"strategy": str(strategy_name).lower()}
        for key in ("restart_patience", "diversify_patience", "beam_width", "restart_interval"):
            if search_cfg.get(key) is not None:
                strategy_cfg[key] = search_cfg[key]
        opt_cfg["search_strategy"] = strategy_cfg
        print(f"[LoopBench] Search       : {strategy_cfg['strategy']}")

        try:
            ensemble = _build_llm_ensemble(raw)
        except Exception as exc:
            print(f"[LoopBench] ERROR: failed to initialize LLM: {exc}")
            return 1
        if ensemble is None:
            print("[LoopBench] ERROR: no LLM models configured.")
            return 1

        # 5. Run the optimizer loop
        print("\n[LoopBench] 🚀 Starting optimization run…\n")
        loop = OptimizerLoop(opt_cfg, llm_ensemble=ensemble)
        result = loop.run()

        # 6. Emit artifacts
        out_dir = Path(args.output or (_REPO_ROOT / "loopbench_output")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # 6a. report artifacts (patch, validation, readme, pr)
        report_paths = {}
        try:
            report_paths = FinalReportWriter(output_dir=out_dir / "report").write_all(result)
        except Exception as exc:
            print(f"[LoopBench] WARNING: report generation failed: {exc}")

        # 6b. top-level best.patch
        best_patch = (result.get("best_candidate") or {}).get("patch_content") or ""
        patch_file = out_dir / "best.patch"
        patch_file.write_text(best_patch, encoding="utf-8")

        # 6c. results.json
        optimizer_write_results_atomic(result, out_dir)

        # 6d. dashboard data.json
        data_json = _write_dashboard_data(db_path, result.get("run_id") or "")

        # 6e. test integrity log
        test_log = _write_test_log(out_dir, result)

        # 7. Summary
        baseline = result.get("baseline_score", 0.0)
        best = result.get("best_score", 0.0)
        imp = result.get("improvement_pct")
        if imp is None:
            imp = ((best - baseline) / abs(baseline) * 100) if baseline else 0.0

        print("\n" + "=" * 60)
        print("✅  LoopBench run complete")
        print("=" * 60)
        print(f"  Baseline score : {baseline:.6f}")
        print(f"  Best score     : {best:.6f}")
        print(f"  Improvement    : {imp:+.2f}%")
        if not best_patch.strip():
            print("  ⚠️  No improving patch was found (best == baseline).")
        cost = result.get("cost") or {}
        if cost:
            tokens = cost.get("total_tokens", 0)
            usd = cost.get("cost_usd", 0.0)
            line = f"  Tokens used    : {tokens:,} ({cost.get('api_calls', 0)} API calls)"
            if usd:
                line += f"  |  est. cost ${usd:.4f}"
            print(line)
            if cost.get("stopped_on_budget"):
                print("  ⏹️  Run stopped early: cost/token budget reached.")
            if cost.get("stopped_on_time"):
                print("  ⏹️  Run stopped early: runtime limit reached.")
        print("-" * 60)
        print("  Artifacts:")
        print(f"    Patch      : {patch_file}")
        if report_paths.get("validation"):
            print(f"    Validation : {report_paths['validation']}")
        if data_json:
            print(f"    Dashboard  : {data_json}")
        print(f"    Test log   : {test_log}")
        print("=" * 60)
        return 0

    except Exception as exc:
        import traceback
        print(f"[LoopBench] ERROR: {exc}")
        traceback.print_exc()
        return 1
    finally:
        if clone_dir:
            shutil.rmtree(clone_dir, ignore_errors=True)
