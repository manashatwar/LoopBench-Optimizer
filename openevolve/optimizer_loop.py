"""
OptimizerLoop: 7-phase autonomous evolutionary code-optimization orchestrator.

Integrates all OpenEvolve components into a closed-loop multi-generation
optimization cycle:

  Phase 1  Map     – RepoContextMapper builds LLM-ready repo context
  Phase 2  Generate – LLMEnsemble.generate_patch produces a unified diff
  Phase 3  Apply   – WorkspaceManager applies the patch to an isolated git worktree
  Phase 4  Test    – run_in_sandbox executes tests inside Docker
  Phase 5  Verify  – verify_output_streams + MetricParser extracts scores
  Phase 6  Record  – CandidateDatabase stores the attempt
  Phase 7  Select  – SearchStrategy picks baseline for next generation

Tasks: 10.1 – 10.7
Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 7.1, 7.2, 7.3, 7.6, 14.2 – 14.6
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _extract_code_block(text: str) -> str:
    """Extract the largest fenced code block from an LLM response.

    Returns the code inside the biggest ```lang ... ``` block. If there are no
    fences, returns the whole text stripped. Language hints on the opening
    fence line are dropped.
    """
    if not text:
        return ""
    import re
    blocks = re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.DOTALL)
    if blocks:
        return max(blocks, key=len).rstrip("\n") + "\n"
    return text.strip() + "\n"


# Sentinel markers for Aider-style edit blocks.
_SR_SEARCH = "<<<<<<< SEARCH"
_SR_DIVIDER = "======="
_SR_REPLACE = ">>>>>>> REPLACE"


def _parse_search_replace_blocks(text: str):
    """Parse Aider-style SEARCH/REPLACE blocks from an LLM response.

    Returns a list of ``(search, replace)`` string pairs. Tolerates the blocks
    being wrapped in Markdown code fences.
    """
    if not text:
        return []
    pairs = []
    lines = text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip() == _SR_SEARCH:
            search_lines = []
            i += 1
            while i < n and lines[i].strip() != _SR_DIVIDER:
                search_lines.append(lines[i])
                i += 1
            # skip the divider
            i += 1
            replace_lines = []
            while i < n and lines[i].strip() != _SR_REPLACE:
                replace_lines.append(lines[i])
                i += 1
            # skip the closing marker
            i += 1
            pairs.append(("\n".join(search_lines), "\n".join(replace_lines)))
        else:
            i += 1
    return pairs


def _apply_search_replace(original: str, blocks):
    """Apply SEARCH/REPLACE ``blocks`` to ``original``.

    Tries an exact substring replacement first, then a whitespace-tolerant
    fuzzy match (trailing whitespace per line ignored). Returns
    ``(new_content, error)`` where ``error`` is None on success.
    """
    if not blocks:
        return original, "no SEARCH/REPLACE blocks found"

    content = original
    for idx, (search, replace) in enumerate(blocks):
        if search == "":
            # Empty search = prepend (rare); skip to stay safe.
            return content, f"block {idx + 1} has an empty SEARCH section"

        if search in content:
            content = content.replace(search, replace, 1)
            continue

        # Fuzzy: compare with trailing whitespace stripped per line.
        def _norm(s: str) -> str:
            return "\n".join(line.rstrip() for line in s.splitlines())

        norm_search = _norm(search)
        matched = False
        # Slide a window over the content lines to find a fuzzy match.
        search_line_count = len(search.splitlines())
        raw_lines = content.split("\n")
        for start in range(len(raw_lines) - search_line_count + 1):
            window = "\n".join(raw_lines[start:start + search_line_count])
            if _norm(window) == norm_search:
                new_block = replace
                raw_lines[start:start + search_line_count] = new_block.split("\n")
                content = "\n".join(raw_lines)
                matched = True
                break
        if not matched:
            return content, f"block {idx + 1} SEARCH text not found in file"

    if content == original:
        return content, "SEARCH/REPLACE produced no change"
    return content, None

# ---------------------------------------------------------------------------
# Lazy imports so unit tests can stub heavy components without full env setup
# ---------------------------------------------------------------------------

def _import_db():
    from openevolve.database import CandidateDatabase
    return CandidateDatabase


def _import_search_strategy():
    from openevolve.search_strategy import create_strategy
    return create_strategy


def _import_metric_parser():
    from openevolve.metric_parser import MetricParser, MetricPattern
    return MetricParser, MetricPattern


def _import_workspace_manager():
    from openevolve.workspace_manager import WorkspaceManager
    return WorkspaceManager


def _import_repo_mapper():
    from openevolve.repo_mapper.mapper import RepoContextMapper
    from openevolve.repo_mapper.models import RepoMapperConfig
    return RepoContextMapper, RepoMapperConfig


def _import_optimizer_prompt():
    from openevolve.repo_mapper.optimizer_prompt import create_optimizer_prompt
    return create_optimizer_prompt


def _import_sandbox():
    from sandbox.runner import run_in_sandbox, verify_output_streams
    return run_in_sandbox, verify_output_streams


# ---------------------------------------------------------------------------
# Task 10.1 – OptimizerLoop class with component initialization
# ---------------------------------------------------------------------------

class OptimizerLoop:
    """7-phase autonomous evolutionary optimization orchestrator.

    Args:
        config: Plain configuration dictionary (see module docstring for keys).
        llm_ensemble: Injected LLMEnsemble instance.  When *None* patch
            generation is skipped and a ``"no_llm"`` failure is recorded —
            this makes the class unit-testable without a live LLM.
    """

    def __init__(self, config: Dict[str, Any], *, llm_ensemble=None):
        # ── Core paths ──────────────────────────────────────────────────────
        self.repo_path: str = config["repo_path"]
        self.target_file: str = config["target_file"]
        self.test_file: str = config["test_file"]

        # ── Loop hyper-parameters ────────────────────────────────────────────
        self.max_iterations: int = int(config.get("max_iterations", 50))
        self.patience: int = int(config.get("patience", 10))
        self.success_threshold: float = float(config.get("success_threshold", 0.10))

        # ── Raw config (kept for export) ─────────────────────────────────────
        self._config = config

        # ── Database ─────────────────────────────────────────────────────────
        db_path: str = config.get("db_path", ":memory:")
        CandidateDatabase = _import_db()
        self.db = CandidateDatabase(db_path)

        # ── Search strategy ───────────────────────────────────────────────────
        create_strategy = _import_search_strategy()
        strategy_cfg = config.get("search_strategy", {"strategy": "greedy"})
        if isinstance(strategy_cfg, str):
            strategy_cfg = {"strategy": strategy_cfg}
        self.search_strategy = create_strategy(strategy_cfg)

        # ── Metric parser ─────────────────────────────────────────────────────
        MetricParser, MetricPattern = _import_metric_parser()
        metric_patterns_cfg = config.get("metric_patterns")
        if metric_patterns_cfg:
            patterns = [MetricPattern(**p) for p in metric_patterns_cfg]
            self.metric_parser: Optional[Any] = MetricParser(patterns=patterns)
        else:
            self.metric_parser = None

        # ── LLM ensemble (injected) ───────────────────────────────────────────
        self.llm_ensemble = llm_ensemble

        # ── Sandbox cfg (passed through to run_in_sandbox) ────────────────────
        self.sandbox_cfg: Dict[str, Any] = config.get("sandbox_cfg") or {}

        # ── Generation mode ───────────────────────────────────────────────────
        # "diff": LLM returns a unified diff applied via git apply (fragile).
        # "full": LLM returns the complete improved file; the diff is computed
        #         with difflib (always valid) and tested via the sandbox.
        # "search_replace": LLM returns Aider-style SEARCH/REPLACE blocks applied
        #         by string matching (token-efficient for large files).
        # "auto": pick "full" for small files, "search_replace" for large ones.
        self.rewrite_mode: str = config.get("rewrite_mode", "diff")
        self.full_rewrite_max_lines: int = int(config.get("full_rewrite_max_lines", 300))

        # ── Internal state ────────────────────────────────────────────────────
        self._run_id: Optional[str] = None
        self._candidate_history: List[Dict[str, Any]] = []

        logger.info(
            "OptimizerLoop initialized: repo=%s target=%s max_iter=%d patience=%d mode=%s",
            self.repo_path, self.target_file, self.max_iterations, self.patience,
            self.rewrite_mode,
        )

    # -------------------------------------------------------------------------
    # Task 10.2 – Baseline establishment
    # -------------------------------------------------------------------------

    def establish_baseline(self) -> Dict[str, Any]:
        """Test the unmodified code and record it as generation-0 candidate.

        Returns the inserted candidate dict (includes ``id``, ``score``, etc.).
        """
        run_in_sandbox, verify_output_streams = _import_sandbox()

        logger.info("Establishing baseline on original target file…")
        result = run_in_sandbox(
            program_path=self.target_file,
            test_file=self.test_file,
            sandbox_cfg=self.sandbox_cfg,
            repo_root=self.repo_path,
        )

        streams_ok = verify_output_streams(result.get("stdout"), result.get("stderr"))
        failed = not streams_ok or result.get("exit_code", 1) != 0

        metrics: Dict[str, Any] = {}
        if streams_ok and not failed and self.metric_parser is not None:
            combined = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
            metrics = self.metric_parser.parse(combined)
        elif streams_ok:
            # Use sandbox score fields directly as metrics
            for key in ("combined_score", "correctness", "speed_score"):
                if result.get(key) is not None:
                    metrics[key] = result[key]

        score = metrics.get("combined_score") or result.get("combined_score") or 0.0
        if failed:
            score = 0.0

        failure_phase = None
        error_message = None
        if not streams_ok:
            failure_phase = "test"
            error_message = "Output stream capture failed during baseline"
        elif result.get("exit_code", 0) != 0:
            failure_phase = "test"
            error_message = f"Baseline tests failed with exit_code={result.get('exit_code')}"

        candidate_id = self.db.insert_candidate(
            generation=0,
            parent_id=None,
            patch_content="",
            applied=True,
            tested=streams_ok,
            exit_code=result.get("exit_code"),
            stdout=result.get("stdout"),
            stderr=result.get("stderr"),
            execution_time=result.get("execution_time"),
            metrics=metrics,
            score=score,
            failed=failed,
            failure_phase=failure_phase,
            error_message=error_message,
        )

        candidate = self.db.get_candidate(candidate_id)
        self._candidate_history.append(candidate)
        logger.info(
            "Baseline recorded id=%s score=%.4f failed=%s",
            candidate_id, score, failed,
        )
        return candidate

    # -------------------------------------------------------------------------
    # Task 10.3 – Single generation execution (7-phase cycle)
    # -------------------------------------------------------------------------

    def execute_generation(
        self,
        generation: int,
        baseline_candidate: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute one full generation cycle and return the resulting candidate dict.

        Always records a candidate to the database even on failure so the full
        audit trail is preserved.

        Args:
            generation: 1-based generation counter.
            baseline_candidate: Current best candidate (used for parent lineage
                and as context for the prompt).
        """
        run_in_sandbox, verify_output_streams = _import_sandbox()

        # Robust generation modes that avoid fragile unified-diff parsing.
        mode = self.rewrite_mode
        if mode == "auto":
            try:
                line_count = len(Path(self.target_file).read_text(encoding="utf-8").splitlines())
            except OSError:
                line_count = 0
            mode = "full" if line_count <= self.full_rewrite_max_lines else "search_replace"
            logger.info(
                "auto mode → %s (target has %d lines, threshold=%d)",
                mode, line_count, self.full_rewrite_max_lines,
            )
        if mode == "full":
            return self._execute_generation_full_rewrite(generation, baseline_candidate)
        if mode == "search_replace":
            return self._execute_generation_search_replace(generation, baseline_candidate)

        baseline_id = baseline_candidate["id"]
        baseline_metrics = baseline_candidate.get("metrics") or {}

        # ── Phase 1: Map repository context ───────────────────────────────────
        context_map = None
        self.db.log_event(
            "generation_start",
            {"generation": generation, "baseline_id": baseline_id},
        )
        try:
            RepoContextMapper, RepoMapperConfig = _import_repo_mapper()
            mapper = RepoContextMapper(RepoMapperConfig())
            context_map = mapper.get_context_map(
                repo_path=Path(self.repo_path),
                target_file=Path(self.target_file),
            )
            logger.debug("Phase 1 complete: context mapped gen=%d", generation)
        except Exception as exc:
            logger.warning("Phase 1 (map) failed gen=%d: %s — continuing", generation, exc)
            context_map = None

        # ── Phase 2: Generate patch via LLM ───────────────────────────────────
        patch: Optional[str] = None
        if self.llm_ensemble is None:
            logger.warning("Phase 2 skipped: no llm_ensemble injected (gen=%d)", generation)
            failure_cid = self.db.insert_candidate(
                generation=generation,
                parent_id=baseline_id,
                patch_content="",
                failed=True,
                failure_phase="generate",
                error_message="No LLM ensemble configured",
            )
            return self.db.get_candidate(failure_cid)

        try:
            failure_history = self.db.get_recent_failures(
                window=5, run_id=self._run_id
            )
            if context_map is not None:
                create_optimizer_prompt = _import_optimizer_prompt()
                prompt = create_optimizer_prompt(
                    context_map=context_map,
                    baseline_metrics=baseline_metrics,
                    failure_history=failure_history,
                )
            else:
                # Minimal fallback prompt when mapper unavailable
                metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
                failures_str = "\n".join(f"- {m}" for m in failure_history) or "None"
                prompt = (
                    "You are optimizing Python code for performance.\n\n"
                    f"Target File: {self.target_file}\n"
                    f"Current Performance: {metrics_str}\n\n"
                    f"Recent Failures:\n{failures_str}\n\n"
                    "Generate a git patch in unified diff format to improve performance."
                )

            patch = asyncio.run(self.llm_ensemble.generate_patch(prompt))
            logger.debug(
                "Phase 2 complete: patch generated gen=%d len=%s",
                generation, len(patch) if patch else 0,
            )
            self.db.log_event(
                "patch_generated",
                {"generation": generation, "patch_length": len(patch) if patch else 0,
                 "has_patch": patch is not None},
            )
        except Exception as exc:
            logger.warning("Phase 2 (generate) failed gen=%d: %s", generation, exc)
            failure_cid = self.db.insert_candidate(
                generation=generation,
                parent_id=baseline_id,
                patch_content="",
                failed=True,
                failure_phase="generate",
                error_message=str(exc),
            )
            return self.db.get_candidate(failure_cid)

        if not patch:
            failure_cid = self.db.insert_candidate(
                generation=generation,
                parent_id=baseline_id,
                patch_content="",
                failed=True,
                failure_phase="generate",
                error_message="LLM returned empty patch",
            )
            return self.db.get_candidate(failure_cid)

        # Pre-register candidate so subsequent updates have a target row
        candidate_id = self.db.insert_candidate(
            generation=generation,
            parent_id=baseline_id,
            patch_content=patch,
            applied=False,
            tested=False,
            failed=False,
        )

        WorkspaceManager = _import_workspace_manager()
        worktree_path: Optional[str] = None

        try:
            # ── Phase 3: Apply patch via WorkspaceManager ─────────────────────
            wm = WorkspaceManager(repo_root=self.repo_path)
            with wm as worktree_path:
                apply_result = wm.apply_patch(worktree_path, patch)
                if not apply_result.success:
                    self.db.update_candidate_results(
                        candidate_id,
                        applied=False,
                        failed=True,
                        failure_phase="apply",
                        error_message=apply_result.error_output or "Patch apply failed",
                    )
                    logger.warning(
                        "Phase 3 (apply) failed gen=%d: %s",
                        generation, apply_result.error_output,
                    )
                    candidate = self.db.get_candidate(candidate_id)
                    self._candidate_history.append(candidate)
                    return candidate

                self.db.update_candidate_results(candidate_id, applied=True)
                logger.debug("Phase 3 complete: patch applied gen=%d", generation)
                self.db.log_event(
                    "patch_applied",
                    {"generation": generation, "candidate_id": candidate_id, "success": True},
                    candidate_id=candidate_id,
                )

                # ── Phase 4: Run tests via run_in_sandbox ─────────────────────
                result = run_in_sandbox(
                    program_path=self.target_file,
                    test_file=self.test_file,
                    sandbox_cfg=self.sandbox_cfg,
                    repo_root=self.repo_path,
                    worktree_path=worktree_path,
                )

                # ── Phase 5: Verify streams + extract metrics ─────────────────
                streams_ok = verify_output_streams(
                    result.get("stdout"), result.get("stderr")
                )
                if not streams_ok:
                    self.db.update_candidate_results(
                        candidate_id,
                        tested=False,
                        failed=True,
                        failure_phase="test",
                        error_message="Output stream capture failed",
                        stdout=result.get("stdout"),
                        stderr=result.get("stderr"),
                        exit_code=result.get("exit_code"),
                        execution_time=result.get("execution_time"),
                    )
                    logger.warning("Phase 5 stream verification failed gen=%d", generation)
                    candidate = self.db.get_candidate(candidate_id)
                    self._candidate_history.append(candidate)
                    return candidate

                combined_output = (result.get("stdout") or "") + "\n" + (result.get("stderr") or "")
                metrics: Dict[str, Any] = {}
                if self.metric_parser is not None and result.get("exit_code") == 0:
                    metrics = self.metric_parser.parse(combined_output)
                else:
                    for key in ("combined_score", "correctness", "speed_score"):
                        if result.get(key) is not None:
                            metrics[key] = result[key]

                score = metrics.get("combined_score") or result.get("combined_score") or 0.0
                failed = result.get("exit_code", 1) != 0
                if failed:
                    score = 0.0

                failure_phase = None
                error_message = None
                if failed:
                    failure_phase = "test"
                    error_message = f"Tests failed with exit_code={result.get('exit_code')}"

                # ── Phase 6: Record to database ───────────────────────────────
                self.db.update_candidate_results(
                    candidate_id,
                    tested=True,
                    applied=True,
                    exit_code=result.get("exit_code"),
                    stdout=result.get("stdout"),
                    stderr=result.get("stderr"),
                    execution_time=result.get("execution_time"),
                    metrics=metrics,
                    score=score,
                    failed=failed,
                    failure_phase=failure_phase,
                    error_message=error_message,
                )
                logger.info(
                    "Phase 6 complete: candidate recorded id=%s gen=%d score=%.4f failed=%s",
                    candidate_id, generation, score, failed,
                )
                self.db.log_event(
                    "test_executed",
                    {"generation": generation, "exit_code": result.get("exit_code"),
                     "execution_time": result.get("execution_time"), "failed": failed},
                    candidate_id=candidate_id,
                )
                if metrics:
                    self.db.log_event(
                        "metrics_extracted",
                        {"generation": generation, "metrics": metrics, "score": score},
                        candidate_id=candidate_id,
                    )

        except (KeyboardInterrupt, SystemExit):
            # Critical: re-raise after marking candidate failed
            try:
                self.db.update_candidate_results(
                    candidate_id,
                    failed=True,
                    failure_phase="critical",
                    error_message="Run interrupted",
                )
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.error("Unhandled error in execute_generation gen=%d: %s", generation, exc)
            try:
                self.db.update_candidate_results(
                    candidate_id,
                    failed=True,
                    failure_phase="unknown",
                    error_message=str(exc),
                )
            except Exception:
                pass

        # ── Phase 7: Return candidate dict ────────────────────────────────────
        candidate = self.db.get_candidate(candidate_id)
        self._candidate_history.append(candidate)
        return candidate

    # -------------------------------------------------------------------------
    # Full-file rewrite generation (robust, diff computed via difflib)
    # -------------------------------------------------------------------------

    def _build_full_rewrite_prompt(
        self, original: str, baseline_metrics: Dict[str, Any], failures: list
    ) -> str:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
        failures_str = "\n".join(f"- {m}" for m in failures) or "None"
        return (
            "You are optimizing a Python file for performance while keeping all "
            "tests passing.\n\n"
            f"Current performance metrics: {metrics_str}\n"
            f"Recent failed attempts:\n{failures_str}\n\n"
            "Rules:\n"
            "  1. Keep ALL public function/class names and signatures unchanged.\n"
            "  2. Do not change any code outside what is needed for the speed-up.\n"
            "  3. The output MUST be the COMPLETE file, ready to run as-is.\n"
            "  4. Return ONLY the full file inside a single ```python code block.\n\n"
            "Here is the current file:\n\n"
            "```python\n"
            f"{original}\n"
            "```\n"
        )

    def _build_search_replace_prompt(
        self, original: str, baseline_metrics: Dict[str, Any], failures: list
    ) -> str:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
        failures_str = "\n".join(f"- {m}" for m in failures) or "None"
        return (
            "You are optimizing a Python file for performance while keeping all "
            "tests passing. Edit ONLY the minimal region(s) needed.\n\n"
            f"Current performance metrics: {metrics_str}\n"
            f"Recent failed attempts:\n{failures_str}\n\n"
            "Return one or more SEARCH/REPLACE blocks in EXACTLY this format:\n\n"
            "<<<<<<< SEARCH\n"
            "<exact lines copied verbatim from the file>\n"
            "=======\n"
            "<the replacement lines>\n"
            ">>>>>>> REPLACE\n\n"
            "Rules:\n"
            "  1. The SEARCH section MUST match the current file text exactly.\n"
            "  2. Keep public function/class names and signatures unchanged.\n"
            "  3. Emit only the blocks — no prose, no full-file dump.\n\n"
            "Here is the current file:\n\n"
            "```python\n"
            f"{original}\n"
            "```\n"
        )

    def _finalize_rewrite_candidate(
        self,
        generation: int,
        baseline_id: str,
        original: str,
        new_content: str,
    ) -> Dict[str, Any]:
        """Shared tail: compute diff, test in sandbox, score, record candidate."""
        import difflib
        import tempfile

        run_in_sandbox, verify_output_streams = _import_sandbox()
        target_path = Path(self.target_file)

        if not new_content.strip() or new_content.strip() == original.strip():
            cid = self.db.insert_candidate(
                generation=generation, parent_id=baseline_id, patch_content="",
                failed=True, failure_phase="generate",
                error_message="LLM produced no usable change",
            )
            candidate = self.db.get_candidate(cid)
            self._candidate_history.append(candidate)
            return candidate

        # Compute a guaranteed-valid unified diff
        try:
            rel = target_path.resolve().relative_to(Path(self.repo_path).resolve())
            rel_str = str(rel).replace("\\", "/")
        except ValueError:
            rel_str = target_path.name
        patch = "".join(difflib.unified_diff(
            original.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"a/{rel_str}", tofile=f"b/{rel_str}",
        ))

        # Test the improved file via the sandbox (temp-copy mode)
        with tempfile.TemporaryDirectory(prefix="loopbench_rw_") as td:
            improved = Path(td) / target_path.name
            improved.write_text(new_content, encoding="utf-8")
            result = run_in_sandbox(
                program_path=str(improved),
                test_file=self.test_file,
                sandbox_cfg=self.sandbox_cfg,
                repo_root=self.repo_path,
            )

        streams_ok = verify_output_streams(result.get("stdout"), result.get("stderr"))
        metrics: Dict[str, Any] = {}
        for key in ("combined_score", "correctness", "speed_score", "speed_ms"):
            if result.get(key) is not None:
                metrics[key] = result[key]
        score = metrics.get("combined_score") or result.get("combined_score") or 0.0
        failed = (not streams_ok) or result.get("exit_code", 1) != 0
        if failed:
            score = 0.0
        cid = self.db.insert_candidate(
            generation=generation, parent_id=baseline_id, patch_content=patch,
            applied=True, tested=streams_ok, exit_code=result.get("exit_code"),
            stdout=result.get("stdout"), stderr=result.get("stderr"),
            execution_time=result.get("execution_time"), metrics=metrics,
            score=score, failed=failed,
            failure_phase="test" if failed else None,
            error_message=(f"tests failed exit={result.get('exit_code')}" if failed else None),
        )
        candidate = self.db.get_candidate(cid)
        self._candidate_history.append(candidate)
        logger.info(
            "Rewrite candidate recorded id=%s gen=%d score=%.4f failed=%s",
            cid, generation, score, failed,
        )
        return candidate

    def _read_target_or_fail(self, generation: int, baseline_id: str):
        """Return (original, None) or (None, failure_candidate)."""
        try:
            return Path(self.target_file).read_text(encoding="utf-8"), None
        except OSError as exc:
            cid = self.db.insert_candidate(
                generation=generation, parent_id=baseline_id, patch_content="",
                failed=True, failure_phase="generate",
                error_message=f"cannot read target file: {exc}",
            )
            return None, self.db.get_candidate(cid)

    def _generate_or_fail(self, generation: int, baseline_id: str, prompt: str):
        """Return (response, None) or (None, failure_candidate)."""
        try:
            return asyncio.run(self.llm_ensemble.generate(prompt)), None
        except Exception as exc:
            cid = self.db.insert_candidate(
                generation=generation, parent_id=baseline_id, patch_content="",
                failed=True, failure_phase="generate", error_message=str(exc),
            )
            return None, self.db.get_candidate(cid)

    def _execute_generation_full_rewrite(
        self, generation: int, baseline_candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate a full replacement file, compute a valid diff, test it."""
        baseline_id = baseline_candidate["id"]
        baseline_metrics = baseline_candidate.get("metrics") or {}

        original, fail = self._read_target_or_fail(generation, baseline_id)
        if fail is not None:
            return fail

        failures = self.db.get_recent_failures(window=5, run_id=self._run_id)
        prompt = self._build_full_rewrite_prompt(original, baseline_metrics, failures)
        response, fail = self._generate_or_fail(generation, baseline_id, prompt)
        if fail is not None:
            return fail

        new_content = _extract_code_block(response) or ""
        return self._finalize_rewrite_candidate(
            generation, baseline_id, original, new_content
        )

    def _execute_generation_search_replace(
        self, generation: int, baseline_candidate: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate SEARCH/REPLACE blocks, apply by matching, compute diff, test."""
        baseline_id = baseline_candidate["id"]
        baseline_metrics = baseline_candidate.get("metrics") or {}

        original, fail = self._read_target_or_fail(generation, baseline_id)
        if fail is not None:
            return fail

        failures = self.db.get_recent_failures(window=5, run_id=self._run_id)
        prompt = self._build_search_replace_prompt(original, baseline_metrics, failures)
        response, fail = self._generate_or_fail(generation, baseline_id, prompt)
        if fail is not None:
            return fail

        blocks = _parse_search_replace_blocks(response or "")
        new_content, err = _apply_search_replace(original, blocks)
        if err is not None:
            cid = self.db.insert_candidate(
                generation=generation, parent_id=baseline_id, patch_content="",
                failed=True, failure_phase="apply",
                error_message=f"search/replace failed: {err}",
            )
            candidate = self.db.get_candidate(cid)
            self._candidate_history.append(candidate)
            return candidate

        return self._finalize_rewrite_candidate(
            generation, baseline_id, original, new_content
        )

    # -------------------------------------------------------------------------
    # Task 10.4 – Multi-generation loop with early stopping
    # -------------------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """Execute the complete optimization run.

        Returns:
            Result dict containing ``run_id``, ``best_candidate``,
            ``baseline_candidate``, ``total_generations``, ``improvement``,
            ``status``, and the full ``export``.
        """
        # 1. Create run in db
        self._run_id = self.db.create_run(
            target_repo=self.repo_path,
            config=self._config,
            success_threshold=self.success_threshold,
        )
        logger.info("Run started run_id=%s", self._run_id)

        # 2. Establish baseline
        baseline_candidate = self.establish_baseline()
        baseline_candidate["id"]
        baseline_score: float = baseline_candidate.get("score") or 0.0

        best_candidate = baseline_candidate
        best_score: float = baseline_score
        generations_without_improvement: int = 0
        total_generations: int = 0
        final_status = "completed"

        # 3. Main generation loop
        for generation in range(1, self.max_iterations + 1):
            total_generations = generation
            try:
                candidate = self.execute_generation(generation, best_candidate)
            except (KeyboardInterrupt, SystemExit):
                logger.warning("Critical interrupt at generation %d — saving partial results", generation)
                final_status = "interrupted"
                break
            except Exception as exc:
                # Non-critical error: log and continue
                logger.error("Generation %d raised unexpected error: %s — continuing", generation, exc)
                generations_without_improvement += 1
                if generations_without_improvement >= self.patience:
                    logger.info("Early stopping (patience=%d) triggered at gen=%d", self.patience, generation)
                    break
                continue

            candidate_score: float = candidate.get("score") or 0.0

            # 3b. Update best candidate
            if candidate_score > best_score:
                best_candidate = candidate
                best_score = candidate_score
                generations_without_improvement = 0
                logger.info(
                    "New best score=%.4f at generation=%d (prev=%.4f)",
                    best_score, generation, baseline_score,
                )
            else:
                generations_without_improvement += 1

            # 3d. Early stopping check
            if generations_without_improvement >= self.patience:
                logger.info(
                    "Early stopping triggered: %d consecutive non-improving generations (patience=%d)",
                    generations_without_improvement, self.patience,
                )
                break

            # 3e. Select next baseline using search strategy
            if self._candidate_history:
                try:
                    next_baseline = self.search_strategy.select_baseline(
                        self._candidate_history, generation
                    )
                    best_candidate = next_baseline
                except Exception as exc:
                    logger.warning("Strategy.select_baseline failed gen=%d: %s — keeping current best", generation, exc)

        # 4. Complete run in db
        improvement = (best_score - baseline_score) / max(abs(baseline_score), 1e-9)
        if improvement > self.success_threshold:
            final_status = "successful"

        self.db.complete_run(
            run_id=self._run_id,
            status=final_status,
            final_improvement=improvement,
        )
        logger.info(
            "Run complete run_id=%s status=%s improvement=%.4f total_generations=%d",
            self._run_id, final_status, improvement, total_generations,
        )

        # 5. Build final report and return
        report = self.generate_final_report(
            best_candidate=best_candidate,
            baseline_candidate=baseline_candidate,
            total_generations=total_generations,
        )
        export = self.db.export_run(run_id=self._run_id)
        report["export"] = export
        return report

    # -------------------------------------------------------------------------
    # Task 11.1 – Final report generation
    # -------------------------------------------------------------------------

    def generate_final_report(
        self,
        best_candidate: Dict[str, Any],
        baseline_candidate: Dict[str, Any],
        total_generations: int = 0,
    ) -> Dict[str, Any]:
        """Calculate improvement, assign run status, return report dict.

        Task 11.1 — Requirements 7.5, 17.4, 17.5, 17.6

        Returns:
            Report dict with ``status``, ``improvement``, ``improvement_pct``,
            ``baseline_score``, ``best_score``, ``total_generations``,
            ``run_id``, ``confidence_warning``, ``best_candidate``,
            ``baseline_candidate``.
        """
        from openevolve.report_generator import generate_final_report as _gen_report

        return _gen_report(
            best_candidate=best_candidate,
            baseline_candidate=baseline_candidate,
            success_threshold=self.success_threshold,
            total_generations=total_generations,
            run_id=self._run_id,
        )
