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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confidence-based (dispersion-aware) speed gate — design §C1 / Requirement 2.
#
# These are module-level pure helpers so the gate is directly unit- and
# property-testable without spinning up the full loop.
# ---------------------------------------------------------------------------

# Default minimum relative median improvement required to accept a candidate
# (metric.min_effect, i.e. 3%).
DEFAULT_MIN_EFFECT = 0.03

# Speed-distribution fields the gate reads off a candidate's metrics (written by
# the sandbox per design §C1). ``speed_ms`` is the back-compat median alias.
_SPEED_DIST_KEYS = ("speed_ms", "speed_ms_median", "speed_ms_stddev")


def _dist_median(dist: Optional[Dict[str, Any]]) -> Optional[float]:
    """Extract the median speed (ms) from a distribution/metrics mapping.

    Prefers ``speed_ms_median`` and falls back to the back-compat ``speed_ms``.
    Returns ``None`` when no usable numeric median is present.
    """
    if not isinstance(dist, dict):
        return None
    for key in ("speed_ms_median", "speed_ms"):
        val = dist.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
    return None


def _dist_stddev(dist: Optional[Dict[str, Any]]) -> float:
    """Extract the sample stddev (ms) from a distribution/metrics mapping.

    Missing or non-numeric stddev is treated as 0.0 (the ``repeats=1`` case),
    which makes the dispersion guard collapse to a pure median comparison.
    """
    if isinstance(dist, dict):
        val = dist.get("speed_ms_stddev")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return max(float(val), 0.0)
    return 0.0


def accept_as_new_best(
    base_dist: Optional[Dict[str, Any]],
    cand_dist: Optional[Dict[str, Any]],
    min_effect: float = DEFAULT_MIN_EFFECT,
) -> bool:
    """Return whether a candidate clears the C1 dispersion-aware speed gate.

    A candidate is accepted as a new best ONLY IF BOTH hold (design §C1,
    Requirement 2, correctness properties CP1/CP2):

      (a) relative median improvement >= ``min_effect``:
          ``(median_base - median_cand) / median_base >= min_effect``
      (b) ``median_cand + max(stddev_cand, stddev_base) < median_base``

    ``base_dist`` / ``cand_dist`` are mappings exposing ``speed_ms_median``
    (or the back-compat ``speed_ms``) and ``speed_ms_stddev``. When either
    median is missing or the baseline median is non-positive the gate returns
    ``False`` so the caller can fall back to score-based selection (backward
    compatibility / non-speed metrics).

    With ``repeats=1`` both stddevs are 0, so condition (b) reduces to
    ``median_cand < median_base`` — implied by (a) for any ``min_effect > 0`` —
    and the gate collapses to "median improvement >= min_effect".
    """
    median_base = _dist_median(base_dist)
    median_cand = _dist_median(cand_dist)
    if median_base is None or median_cand is None or median_base <= 0:
        return False

    stddev_base = _dist_stddev(base_dist)
    stddev_cand = _dist_stddev(cand_dist)

    # (a) relative median improvement must clear the minimum effect size.
    rel_improvement = (median_base - median_cand) / median_base
    if rel_improvement < min_effect:
        return False

    # (b) the candidate's dispersion band must sit strictly below the baseline.
    if median_cand + max(stddev_cand, stddev_base) >= median_base:
        return False

    return True


def revalidation_holds(
    base_dist: Optional[Dict[str, Any]],
    revalidated_dist: Optional[Dict[str, Any]],
    min_effect: float = DEFAULT_MIN_EFFECT,
) -> bool:
    """Whether a winner's re-measured gain still holds (design §C1, R3, CP3).

    Pure, testable decision for final revalidation: the winner's re-measured
    distribution must still clear the SAME dispersion-aware speed gate against
    the baseline distribution that governed acceptance during the loop. This
    delegates to :func:`accept_as_new_best` so the gate math is single-sourced
    and revalidation stays consistent with CP1/CP2 — no duplicated arithmetic.

    Returns ``True`` only when the revalidated median improvement over baseline
    is still >= ``min_effect`` AND ``median_reval + max(stddev_reval,
    stddev_base) < median_base``. A missing/failed revalidation distribution
    (e.g. no usable median) yields ``False`` so the caller downgrades the run.
    """
    return accept_as_new_best(base_dist, revalidated_dist, min_effect)


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


def _import_build_prompt_parts():
    from openevolve.repo_mapper.optimizer_prompt import build_prompt_parts
    return build_prompt_parts


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

        # ── Statistical speed gate (design §C1, Requirement 2) ────────────────
        # Minimum relative median improvement (metric.min_effect) a candidate
        # must clear to be accepted as a new best. Default 0.03 (3%).
        try:
            self.min_effect: float = float(config.get("min_effect", DEFAULT_MIN_EFFECT))
        except (TypeError, ValueError):
            self.min_effect = DEFAULT_MIN_EFFECT

        # ── Final winner revalidation (design §C1, Requirement 3) ─────────────
        # After the loop, re-run the winning candidate M times in the sandbox
        # (no LLM calls) and keep status="successful" only if the gain still
        # holds under re-measurement. ON by default (R3.6); ``--no-revalidate``
        # opts out. ``revalidate_runs`` is M (default 7, R3.1).
        self.revalidate: bool = bool(config.get("revalidate", True))
        try:
            self.revalidate_runs: int = int(config.get("revalidate_runs", 7))
        except (TypeError, ValueError):
            self.revalidate_runs = 7
        if self.revalidate_runs < 1:
            self.revalidate_runs = 7

        # ── Cost / token budgeting ────────────────────────────────────────────
        # The loop stops early when either budget is set and reached. Token
        # counts come from the LLM provider's usage field; the USD estimate
        # additionally needs per-1k pricing (0 → USD budget is inactive).
        self.max_tokens_total: Optional[int] = config.get("max_tokens_total")
        self.max_usd: Optional[float] = config.get("max_usd")
        self.usd_per_1k_prompt: float = float(config.get("usd_per_1k_prompt", 0.0) or 0.0)
        self.usd_per_1k_completion: float = float(config.get("usd_per_1k_completion", 0.0) or 0.0)

        # Global wall-clock deadline for the whole run (None = unlimited).
        self.max_runtime_seconds: Optional[float] = config.get("max_runtime_seconds")

        # ── Metric selection ──────────────────────────────────────────────────
        # Which metric the loop maximizes. Defaults to combined_score. Common
        # latency aliases resolve to speed_score (higher = faster).
        self.metric_name: str = config.get("metric_name") or "combined_score"

        # ── Generation mode ───────────────────────────────────────────────────
        # "diff": LLM returns a unified diff applied via git apply (fragile).
        # "full": LLM returns the complete improved file; the diff is computed
        #         with difflib (always valid) and tested via the sandbox.
        # "search_replace": LLM returns Aider-style SEARCH/REPLACE blocks applied
        #         by string matching (token-efficient for large files).
        # "auto": pick "full" for small files, "search_replace" for large ones.
        self.rewrite_mode: str = config.get("rewrite_mode", "diff")
        self.full_rewrite_max_lines: int = int(config.get("full_rewrite_max_lines", 300))

        # ── Language-aware prompting ──────────────────────────────────────────
        # The language of the file being optimized (from the target extension).
        # Defaults to Python so existing behavior is unchanged.
        self.language: str = config.get("language") or "Python"

        # ── Baseline profiler hotspots (design §C2, Requirements R4/R5) ───────
        # Computed ONCE from the baseline sandbox result and reused across every
        # generation's prompt (cached static prefix). Empty list when profiling
        # is off, in which case prompts are byte-for-byte identical to today.
        self.hotspots: List[Dict[str, Any]] = []

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

        # Carry the C1 speed distribution so best-selection can apply the gate.
        self._merge_speed_distribution(metrics, result)

        # Capture baseline profiler hotspots ONCE (design §C2, R4). Present only
        # when sandbox.profile is enabled; otherwise the key is absent and
        # self.hotspots stays [] so prompts are unchanged (R4.5).
        self.hotspots = result.get("hotspots") or []

        score = self._score_from_metrics(metrics, result)
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
            # ``cache_prefix`` is the run-stable static prefix; passing it lets
            # the LLM layer structure the request so the prefix is cacheable
            # (design §C2). ``None`` on the fallback path → plain prompt.
            cache_prefix: Optional[str] = None
            if context_map is not None:
                build_prompt_parts = _import_build_prompt_parts()
                static_prefix, dynamic_delta = build_prompt_parts(
                    context_map=context_map,
                    baseline_metrics=baseline_metrics,
                    failure_history=failure_history,
                    language=self.language,
                    hotspots=self.hotspots,
                )
                prompt = static_prefix + dynamic_delta
                cache_prefix = static_prefix
            else:
                # Minimal fallback prompt when mapper unavailable
                metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
                failures_str = "\n".join(f"- {m}" for m in failure_history) or "None"
                prompt = (
                    f"You are optimizing {self.language} code for performance.\n\n"
                    f"Target File: {self.target_file}\n"
                    f"Current Performance: {metrics_str}\n\n"
                    f"Recent Failures:\n{failures_str}\n\n"
                    "Generate a git patch in unified diff format to improve performance."
                )

            patch = asyncio.run(
                self.llm_ensemble.generate_patch(prompt, cache_prefix=cache_prefix)
            )
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

                # Carry the C1 speed distribution for the best-selection gate.
                self._merge_speed_distribution(metrics, result)

                score = self._score_from_metrics(metrics, result)
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

    def _hotspot_block(self) -> str:
        """Return the baseline hotspot summary for a rewrite prompt's static
        portion, or ``""`` when profiling produced no hotspots.

        When non-empty the block ends with a blank-line separator so it can be
        interpolated directly. When empty, the enclosing prompt is byte-for-byte
        identical to the pre-Task-6 output (design §C2, R4.5).
        """
        from openevolve.profiler import format_hotspots

        summary = format_hotspots(self.hotspots)
        return f"{summary}\n\n" if summary else ""

    def _build_full_rewrite_prompt(
        self, original: str, baseline_metrics: Dict[str, Any], failures: list
    ) -> str:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
        failures_str = "\n".join(f"- {m}" for m in failures) or "None"
        lang = self.language
        fence = lang.lower()
        # Baseline hotspots are static (computed once) — grounding rides at the
        # TOP edge, after the intro. Empty => byte-identical to pre-Task-6 output.
        hotspot_block = self._hotspot_block()
        return (
            f"You are an expert {lang} programmer optimizing a {lang} file for "
            "performance while keeping all tests passing.\n\n"
            f"{hotspot_block}"
            f"Current performance metrics: {metrics_str}\n"
            f"Recent failed attempts:\n{failures_str}\n\n"
            "Rules:\n"
            "  1. Keep ALL public function/class names, signatures, and observable "
            "behavior unchanged (every test must still pass).\n"
            "  2. You MAY replace the ENTIRE algorithm or data structures with a "
            "faster approach — a better-complexity algorithm (e.g. O(n^2) -> "
            "O(n log n)), memoization, or a language built-in. Don't limit "
            "yourself to surface tweaks; rewrite the approach when that is what "
            "makes it faster.\n"
            f"  3. Emit valid {lang} only — use {lang} syntax and idioms, never "
            "constructs from other languages.\n"
            "  4. The output MUST be the COMPLETE file, ready to run as-is.\n"
            f"  5. Return ONLY the full file inside a single ```{fence} code block.\n\n"
            "Here is the current file:\n\n"
            f"```{fence}\n"
            f"{original}\n"
            "```\n"
        )

    def _build_search_replace_prompt(
        self, original: str, baseline_metrics: Dict[str, Any], failures: list
    ) -> str:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items()) or "N/A"
        failures_str = "\n".join(f"- {m}" for m in failures) or "None"
        lang = self.language
        fence = lang.lower()
        # Baseline hotspots are static (computed once) — grounding rides at the
        # TOP edge, after the intro. Empty => byte-identical to pre-Task-6 output.
        hotspot_block = self._hotspot_block()
        return (
            f"You are an expert {lang} programmer optimizing a {lang} file for "
            "performance while keeping all tests passing. Edit the minimal "
            "region(s) needed — but that region can be an entire function body "
            "if a faster algorithm requires it.\n\n"
            f"{hotspot_block}"
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
            "  2. Keep public function/class names, signatures, and behavior "
            "unchanged, but you MAY rewrite a whole function with a "
            "better-complexity algorithm or data structure when that is the "
            "real speed-up (don't settle for surface tweaks).\n"
            f"  3. Emit valid {lang} only — use {lang} syntax and idioms, never "
            "constructs from other languages.\n"
            "  4. Emit only the blocks — no prose, no full-file dump.\n\n"
            "Here is the current file:\n\n"
            f"```{fence}\n"
            f"{original}\n"
            "```\n"
        )

    def _test_rewrite_candidate(
        self, new_content: str, target_path: Path, rel_str: str
    ) -> Dict[str, Any]:
        """Run a rewritten candidate in an isolated git worktree, then in the
        Docker sandbox — the same isolation path used by diff mode.

        A fresh worktree is created per candidate, the evolved file is written
        at its real path inside the worktree, and the sandbox mounts that
        worktree read-only. When the target isn't in a git repo (worktree
        creation fails), it falls back to a disposable temp-dir copy so the
        loop still runs.
        """
        run_in_sandbox, _ = _import_sandbox()
        WorkspaceManager = _import_workspace_manager()
        try:
            wm = WorkspaceManager(repo_root=self.repo_path)
            with wm as worktree_path:
                wt_file = Path(worktree_path) / rel_str
                wt_file.parent.mkdir(parents=True, exist_ok=True)
                wt_file.write_text(new_content, encoding="utf-8")
                return run_in_sandbox(
                    program_path=str(wt_file),
                    test_file=self.test_file,
                    sandbox_cfg=self.sandbox_cfg,
                    repo_root=self.repo_path,
                    worktree_path=str(worktree_path),
                )
        except Exception as exc:
            logger.warning(
                "Worktree isolation unavailable (%s) — falling back to temp-copy", exc
            )
            import tempfile
            with tempfile.TemporaryDirectory(prefix="loopbench_rw_") as td:
                improved = Path(td) / target_path.name
                improved.write_text(new_content, encoding="utf-8")
                return run_in_sandbox(
                    program_path=str(improved),
                    test_file=self.test_file,
                    sandbox_cfg=self.sandbox_cfg,
                    repo_root=self.repo_path,
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

        # Evaluate the rewritten candidate inside an isolated, disposable git
        # worktree (same isolation path as diff mode). Falls back to a temp-dir
        # copy only when the target isn't in a git repo.
        result = self._test_rewrite_candidate(new_content, target_path, rel_str)

        streams_ok = verify_output_streams(result.get("stdout"), result.get("stderr"))
        metrics: Dict[str, Any] = {}
        for key in ("combined_score", "correctness", "speed_score", "speed_ms"):
            if result.get(key) is not None:
                metrics[key] = result[key]
        # Carry the C1 speed distribution for the best-selection gate.
        self._merge_speed_distribution(metrics, result)
        score = self._score_from_metrics(metrics, result)
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

    def _score_from_metrics(
        self, metrics: Optional[Dict[str, Any]], result: Optional[Dict[str, Any]]
    ) -> float:
        """Resolve the optimization score for the configured metric.

        Uses ``metric_name`` when the evaluator/sandbox actually emits that key
        (this is how a user's custom metric — throughput, memory, accuracy — is
        honored), otherwise falls back to combined_score. Returns 0.0 when
        nothing usable is found.

        Note: combined_score already folds in both correctness and speed, so
        latency-style optimization works with the default without a special
        alias — and it stays correct for pure-correctness evaluators that emit
        no speed marker (where speed_score would be 0).
        """
        if self.metric_name and self.metric_name != "combined_score":
            for src in (metrics, result):
                if isinstance(src, dict):
                    val = src.get(self.metric_name)
                    if isinstance(val, (int, float)):
                        return float(val)
        for src in (metrics, result):
            if isinstance(src, dict):
                val = src.get("combined_score")
                if isinstance(val, (int, float)):
                    return float(val)
        return 0.0

    @staticmethod
    def _merge_speed_distribution(
        metrics: Dict[str, Any], result: Optional[Dict[str, Any]]
    ) -> None:
        """Copy the C1 speed-distribution fields from a sandbox result onto metrics.

        The dispersion-aware speed gate reads median/stddev off a candidate's
        stored ``metrics``; mirroring them here (when the sandbox reported them)
        makes best-selection able to apply the gate regardless of which
        metric-extraction path ran. Values already present on ``metrics`` win.
        """
        if not isinstance(result, dict):
            return
        for key in _SPEED_DIST_KEYS:
            val = result.get(key)
            if val is not None and metrics.get(key) is None:
                metrics[key] = val

    def _speed_gate_metric(self) -> bool:
        """Whether the configured metric is speed-oriented (gate applies).

        The C1 speed gate reasons over the measured speed distribution, so it
        only governs selection for the default combined_score / speed metrics.
        Custom metrics fall back to plain score comparison.
        """
        name = (self.metric_name or "combined_score").lower()
        return name in ("combined_score", "speed_score", "speed_ms")

    def _is_new_best(
        self,
        best_candidate: Dict[str, Any],
        candidate: Dict[str, Any],
        candidate_score: float,
        best_score: float,
    ) -> bool:
        """Decide whether ``candidate`` should replace the current best.

        When a speed distribution is available for both the incumbent best and
        the candidate (and the metric is speed-oriented), the Phase-1 statistical
        gate applies: accept only when the correctness gate passes (candidate not
        failed) AND the dispersion-aware speed gate accepts (design §C1,
        Requirement 2; correctness property CP1). Otherwise fall back to plain
        score comparison, preserving current behavior for correctness-only or
        non-speed metrics.
        """
        if candidate.get("failed"):
            return False

        base_metrics = best_candidate.get("metrics") if isinstance(best_candidate, dict) else None
        cand_metrics = candidate.get("metrics") if isinstance(candidate, dict) else None
        gate_applies = (
            self._speed_gate_metric()
            and _dist_median(base_metrics) is not None
            and _dist_median(cand_metrics) is not None
        )
        if gate_applies:
            return accept_as_new_best(base_metrics, cand_metrics, self.min_effect)

        return candidate_score > best_score

    # -------------------------------------------------------------------------
    # Task 3.1 – Final revalidation of the winner (design §C1, Requirement 3)
    # -------------------------------------------------------------------------

    def _revalidate_winner(
        self, best_candidate: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Re-run the winning candidate M times in the sandbox (NO LLM calls).

        Orchestrates the M-run re-measurement of the winner by reusing the exact
        same ``run_in_sandbox`` path used for scoring (design §C1, R3.1/R3.2):
        the winner's patch is re-applied to an isolated worktree and the speed
        workload is re-run ``self.revalidate_runs`` times, producing a fresh
        speed distribution. No prompts are built and the LLM ensemble is never
        touched.

        Returns:
            The revalidated speed-distribution mapping (``speed_ms_median`` /
            ``speed_ms_stddev`` / ``speed_ms_samples`` / ``runs``) when the
            re-measurement ran. When the winner regresses on correctness under
            re-measurement, a distribution with no usable median is returned so
            the hold-check fails (gain does not hold). Returns ``None`` when
            revalidation could not be performed (no winning patch, patch failed
            to re-apply, or a sandbox error) — the caller then leaves the run
            status unchanged.
        """
        patch = (best_candidate or {}).get("patch_content") or ""
        if not patch.strip():
            return None

        run_in_sandbox, verify_output_streams = _import_sandbox()
        WorkspaceManager = _import_workspace_manager()

        # Same sandbox path as scoring, but with M repeats for re-measurement.
        reval_cfg: Dict[str, Any] = dict(self.sandbox_cfg)
        reval_cfg["repeats"] = self.revalidate_runs

        try:
            wm = WorkspaceManager(repo_root=self.repo_path)
            with wm as worktree_path:
                apply_result = wm.apply_patch(worktree_path, patch)
                if not apply_result.success:
                    logger.warning(
                        "Revalidation skipped: winner patch failed to re-apply: %s",
                        apply_result.error_output,
                    )
                    return None
                result = run_in_sandbox(
                    program_path=self.target_file,
                    test_file=self.test_file,
                    sandbox_cfg=reval_cfg,
                    repo_root=self.repo_path,
                    worktree_path=worktree_path,
                )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Revalidation sandbox run failed: %s — status unchanged", exc)
            return None

        if not verify_output_streams(result.get("stdout"), result.get("stderr")):
            logger.warning("Revalidation: output stream verification failed — status unchanged")
            return None

        if result.get("exit_code", 1) != 0:
            # Correctness regressed under re-measurement → the gain does not hold.
            logger.warning(
                "Revalidation: winner FAILED correctness on re-measurement "
                "(exit_code=%s) — gain does not hold.",
                result.get("exit_code"),
            )
            return {
                "speed_ms_median": None,
                "speed_ms_stddev": None,
                "speed_ms_samples": [],
                "runs": 0,
            }

        revalidated_dist: Dict[str, Any] = {}
        self._merge_speed_distribution(revalidated_dist, result)
        return revalidated_dist

    def _budget_snapshot(self) -> Dict[str, Any]:
        """Current cumulative token usage and estimated cost from the LLM."""
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "api_calls": 0}
        if self.llm_ensemble is not None and hasattr(self.llm_ensemble, "usage_totals"):
            try:
                usage = dict(self.llm_ensemble.usage_totals())
            except Exception:
                pass
        cost = (
            (usage.get("prompt_tokens", 0) / 1000.0) * self.usd_per_1k_prompt
            + (usage.get("completion_tokens", 0) / 1000.0) * self.usd_per_1k_completion
        )
        usage["cost_usd"] = round(cost, 6)
        return usage

    def _budget_exceeded(self) -> Tuple[bool, str]:
        """Return (exceeded, reason) based on configured token / USD budgets."""
        snap = self._budget_snapshot()
        if self.max_tokens_total and snap["total_tokens"] >= int(self.max_tokens_total):
            return True, (
                f"token budget reached ({snap['total_tokens']} >= {self.max_tokens_total})"
            )
        if self.max_usd and snap["cost_usd"] >= float(self.max_usd):
            return True, (
                f"cost budget reached (${snap['cost_usd']:.4f} >= ${float(self.max_usd):.4f})"
            )
        return False, ""

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
        # The candidate the next generation is built from. The search strategy
        # may move this around for exploration; it is kept SEPARATE from
        # ``best_candidate`` so the reported best is always the highest-scoring
        # candidate and never regresses below the baseline.
        current_baseline = baseline_candidate
        generations_without_improvement: int = 0
        total_generations: int = 0
        final_status = "completed"
        stopped_on_budget = False
        stopped_on_time = False
        run_start = time.monotonic()

        # 3. Main generation loop
        for generation in range(1, self.max_iterations + 1):
            # 3a-0. Wall-clock deadline gate.
            if self.max_runtime_seconds:
                elapsed = time.monotonic() - run_start
                if elapsed >= float(self.max_runtime_seconds):
                    logger.info(
                        "Stopping before generation %d: runtime limit reached (%.1fs >= %ss)",
                        generation, elapsed, self.max_runtime_seconds,
                    )
                    final_status = "time_exhausted"
                    stopped_on_time = True
                    self.db.log_event(
                        "time_exhausted",
                        {"generation": generation, "elapsed_s": round(elapsed, 2)},
                    )
                    break

            # 3a. Cost/token budget gate — stop before spending more.
            exceeded, reason = self._budget_exceeded()
            if exceeded:
                logger.info("Stopping before generation %d: %s", generation, reason)
                final_status = "budget_exhausted"
                stopped_on_budget = True
                self.db.log_event("budget_exhausted", {"generation": generation, "reason": reason})
                break

            total_generations = generation
            pre_budget = self._budget_snapshot()
            try:
                candidate = self.execute_generation(generation, current_baseline)
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

            # 3a-bis. Record this generation's token/cost delta to the audit log.
            post_budget = self._budget_snapshot()
            try:
                self.db.log_event(
                    "generation_cost",
                    {
                        "generation": generation,
                        "prompt_tokens": post_budget["prompt_tokens"] - pre_budget["prompt_tokens"],
                        "completion_tokens": post_budget["completion_tokens"] - pre_budget["completion_tokens"],
                        "cost_usd": round(post_budget["cost_usd"] - pre_budget["cost_usd"], 6),
                        "cumulative_cost_usd": post_budget["cost_usd"],
                        "cumulative_tokens": post_budget["total_tokens"],
                    },
                    candidate_id=candidate.get("id"),
                )
            except Exception:
                pass

            # 3b. Update best candidate — dispersion-aware speed gate (§C1, R2).
            if self._is_new_best(best_candidate, candidate, candidate_score, best_score):
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

            # 3e. Select the baseline for the NEXT generation using the search
            # strategy. This only steers exploration — it must not overwrite
            # ``best_candidate`` (the highest-scoring candidate found so far).
            if self._candidate_history:
                try:
                    current_baseline = self.search_strategy.select_baseline(
                        self._candidate_history, generation
                    )
                except Exception as exc:
                    logger.warning("Strategy.select_baseline failed gen=%d: %s — keeping current best", generation, exc)
                    current_baseline = best_candidate

        # 4. Final revalidation of the winner (design §C1, Requirement 3, CP3).
        # Re-run the winning candidate M times in the sandbox (no LLM calls) and
        # keep "successful" only if the re-measured gain still clears the same
        # dispersion-aware speed gate against the baseline distribution.
        baseline_dist = (
            baseline_candidate.get("metrics") if isinstance(baseline_candidate, dict) else None
        )
        winner_has_patch = bool(((best_candidate or {}).get("patch_content") or "").strip())
        revalidation_performed = False
        revalidation_held = False
        revalidated_dist: Optional[Dict[str, Any]] = None
        should_revalidate = (
            self.revalidate
            and winner_has_patch
            and best_candidate is not baseline_candidate
            and final_status != "interrupted"
            and self._speed_gate_metric()
            and _dist_median(baseline_dist) is not None
        )
        if should_revalidate:
            logger.info(
                "Final revalidation: re-running the winner %d× in the sandbox (no LLM calls)",
                self.revalidate_runs,
            )
            revalidated_dist = self._revalidate_winner(best_candidate)
            if revalidated_dist is not None:
                revalidation_performed = True
                revalidation_held = revalidation_holds(
                    baseline_dist, revalidated_dist, self.min_effect
                )
                self.db.log_event(
                    "winner_revalidated",
                    {
                        "runs": self.revalidate_runs,
                        "held": revalidation_held,
                        "baseline_median": _dist_median(baseline_dist),
                        "revalidated_median": _dist_median(revalidated_dist),
                        "revalidated_stddev": _dist_stddev(revalidated_dist),
                        "min_effect": self.min_effect,
                    },
                    candidate_id=(best_candidate or {}).get("id"),
                )

        # 5. Complete run in db
        improvement = (best_score - baseline_score) / max(abs(baseline_score), 1e-9)
        if improvement > self.success_threshold:
            final_status = "successful"

        # Revalidation soundness (R3.3/R3.4, CP3): a run stays "successful" only
        # if the winner's gain still holds under re-measurement. When it does
        # not, downgrade and log the reason clearly.
        revalidation_downgraded = (
            revalidation_performed and final_status == "successful" and not revalidation_held
        )
        if revalidation_downgraded:
            logger.warning(
                "Winner revalidation FAILED: re-measured gain no longer clears the speed "
                "gate (baseline_median=%s, revalidated_median=%s, revalidated_stddev=%s, "
                "min_effect=%.3f) — downgrading status from 'successful' to "
                "'revalidation_failed'.",
                _dist_median(baseline_dist),
                _dist_median(revalidated_dist),
                _dist_stddev(revalidated_dist),
                self.min_effect,
            )
            final_status = "revalidation_failed"

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

        # Reflect the final revalidation decision in the report (R3.3/R3.4, CP3).
        report["revalidation"] = {
            "enabled": self.revalidate,
            "performed": revalidation_performed,
            "held": revalidation_held,
            "runs": self.revalidate_runs if revalidation_performed else 0,
            "revalidated_dist": revalidated_dist,
        }
        if revalidation_downgraded:
            report["status"] = "revalidation_failed"

        # Attach cost / token usage summary for the report and dashboard.
        snap = self._budget_snapshot()
        report["cost"] = {
            "prompt_tokens": snap["prompt_tokens"],
            "completion_tokens": snap["completion_tokens"],
            "total_tokens": snap["total_tokens"],
            "api_calls": snap.get("api_calls", 0),
            "cost_usd": snap["cost_usd"],
            "max_usd": self.max_usd,
            "max_tokens_total": self.max_tokens_total,
            "stopped_on_budget": stopped_on_budget,
            "stopped_on_time": stopped_on_time,
            "max_runtime_seconds": self.max_runtime_seconds,
            "elapsed_seconds": round(time.monotonic() - run_start, 2),
        }
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
