"""
Prompt builder for the OptimizerLoop (Task 5.2, Task 6).

Combines repository context, baseline performance metrics, optimization goal,
recent failure history, and (optionally) baseline profiler hotspots into an
LLM-ready prompt.

Task 6 (design §C2, requirements R4/R5) splits prompt construction into two
parts so per-call context stays bounded and cacheable across many loops:

  * A **static prefix** — stable across every generation of a single run. It
    holds the *target file identity* and, when profiling is enabled, the
    *baseline hotspot summary*. Because provider prompt-caching keys on a
    literal leading substring, the cacheable "static prefix" MUST sit at the
    very start of the prompt; that is why the high-signal target + hotspot
    block anchors the TOP edge of the prompt (R5.1, R5.3). Keeping the highest
    -signal content at an edge — never buried in the middle — mitigates the
    "lost-in-the-middle" long-context degradation noted in the design overview.
  * A **dynamic delta** — the per-generation content: current/baseline metrics,
    the curated repo-context neighbors, recent failures, and the closing
    instruction. This is what varies call-to-call.

Backward compatibility (CRITICAL): when no hotspots are supplied (profiling
off), ``build_prompt_parts`` composes to a byte-for-byte identical string to
the pre-Task-6 template — the hotspot block is omitted entirely.
"""

from typing import List, Optional, Tuple

from openevolve.profiler import Hotspot, format_hotspots
from openevolve.repo_mapper.models import ContextMap


def build_prompt_parts(
    context_map: ContextMap,
    baseline_metrics: dict,
    failure_history: list[str],
    optimization_goal: str = "Improve execution performance",
    language: str = "Python",
    hotspots: Optional[List[Hotspot]] = None,
) -> Tuple[str, str]:
    """Build the (static_prefix, dynamic_delta) pair for the OptimizerLoop.

    The static prefix is stable for the whole run (target identity + baseline
    hotspots); the dynamic delta carries the per-generation metrics, curated
    neighbors, failures, and the closing instruction.

    Args:
        context_map: Repository context produced by :class:`RepoContextMapper`.
        baseline_metrics: Dict of metric name → value representing current
            performance.  Formatted as ``key=value`` pairs.  When empty the
            string ``"No baseline yet"`` is used instead.
        failure_history: List of error/failure message strings.  When empty the
            section reads ``"None"``.
        optimization_goal: Free-text description of what to optimise for.
        language: The language of the target file (defaults to ``"Python"``).
        hotspots: Optional list of baseline profiler hotspots.  When ``None`` or
            empty, the hotspot section is omitted and the composed prompt is
            byte-for-byte identical to the pre-Task-6 output (R4.5).

    Returns:
        A ``(static_prefix, dynamic_delta)`` tuple.  ``static_prefix +
        dynamic_delta`` is the complete prompt.
    """
    # --- 1. Format baseline metrics (dynamic) ---
    if baseline_metrics:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items())
    else:
        metrics_str = "No baseline yet"

    # --- 2. Format failure history (dynamic) ---
    if failure_history:
        failures_str = "\n".join(f"- {msg}" for msg in failure_history)
    else:
        failures_str = "None"

    # --- 3. Repo context section (stable, but placed after metrics in the
    #        canonical template so it lives in the delta for byte-compat) ---
    repo_context_section = context_map.to_prompt_section()

    # --- 4. Hotspot summary (stable). Empty string when there are no hotspots
    #        so the static prefix collapses to the pre-Task-6 head exactly. ---
    hotspot_summary = format_hotspots(hotspots)

    # --- 5. STATIC PREFIX: intro + target identity (+ hotspots at the TOP
    #        edge). This is the cacheable, run-stable portion. ---
    static_prefix = (
        f"You are an expert {language} programmer optimizing {language} code for "
        f"performance. Emit valid {language} only — never use constructs from "
        "other languages.\n"
        "\n"
        f"Target File: {context_map.target_file}\n"
    )
    if hotspot_summary:
        # High-signal profiler grounding rides with the target at the top edge.
        static_prefix = f"{static_prefix}\n{hotspot_summary}\n"

    # --- 6. DYNAMIC DELTA: metrics + goal + neighbors + failures + closing.
    #        With no hotspots, static_prefix + dynamic_delta == old template. ---
    dynamic_delta = (
        f"Current Performance: {metrics_str}\n"
        f"Optimization Goal: {optimization_goal}\n"
        "\n"
        f"{repo_context_section}\n"
        "\n"
        "Recent Failures:\n"
        f"{failures_str}\n"
        "\n"
        "Generate a git patch in unified diff format (starting with --- and +++) "
        "to improve performance.\n"
        "Focus on algorithmic improvements. "
        "Output only the patch inside a ```diff code block."
    )

    return static_prefix, dynamic_delta


def create_optimizer_prompt(
    context_map: ContextMap,
    baseline_metrics: dict,
    failure_history: list[str],
    optimization_goal: str = "Improve execution performance",
    language: str = "Python",
    hotspots: Optional[List[Hotspot]] = None,
) -> str:
    """Build the full LLM prompt for the OptimizerLoop.

    Backward-compatible full-prompt entry point: composes the static prefix and
    dynamic delta from :func:`build_prompt_parts`.  When ``hotspots`` is ``None``
    or empty the result is byte-for-byte identical to the pre-Task-6 template.

    Combines:
    1. The repo context section from ``context_map.to_prompt_section()``
    2. Current performance baseline (``baseline_metrics``)
    3. Optimization goal
    4. Recent failure history (if any)
    5. Baseline profiler hotspots (if any)
    6. Instruction to output a unified diff patch

    Args:
        context_map: Repository context produced by :class:`RepoContextMapper`.
        baseline_metrics: Dict of metric name → value representing current
            performance.  Formatted as ``key=value`` pairs.  When empty the
            string ``"No baseline yet"`` is used instead.
        failure_history: List of error/failure message strings from
            ``ProgramDatabase.get_recent_failures()``.  When the list is empty
            the section reads ``"None"``.
        optimization_goal: Free-text description of what to optimise for.
        language: The language of the target file (defaults to ``"Python"``).
        hotspots: Optional list of baseline profiler hotspots.

    Returns:
        Complete prompt string ready to send to the LLM.
    """
    static_prefix, dynamic_delta = build_prompt_parts(
        context_map=context_map,
        baseline_metrics=baseline_metrics,
        failure_history=failure_history,
        optimization_goal=optimization_goal,
        language=language,
        hotspots=hotspots,
    )
    return static_prefix + dynamic_delta
