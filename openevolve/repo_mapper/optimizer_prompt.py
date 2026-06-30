"""
Prompt builder for the OptimizerLoop (Task 5.2).

Combines repository context, baseline performance metrics, optimization goal,
and recent failure history into a single LLM-ready prompt string.
"""

from openevolve.repo_mapper.models import ContextMap


def create_optimizer_prompt(
    context_map: ContextMap,
    baseline_metrics: dict,
    failure_history: list[str],
    optimization_goal: str = "Improve execution performance",
) -> str:
    """Build the full LLM prompt for the OptimizerLoop.

    Combines:
    1. The repo context section from ``context_map.to_prompt_section()``
    2. Current performance baseline (``baseline_metrics``)
    3. Optimization goal
    4. Recent failure history (if any)
    5. Instruction to output a unified diff patch

    Args:
        context_map: Repository context produced by :class:`RepoContextMapper`.
        baseline_metrics: Dict of metric name → value representing current
            performance.  Formatted as ``key=value`` pairs.  When empty the
            string ``"No baseline yet"`` is used instead.
        failure_history: List of error/failure message strings from
            ``ProgramDatabase.get_recent_failures()``.  When the list is empty
            the section reads ``"None"``.
        optimization_goal: Free-text description of what to optimise for.

    Returns:
        Complete prompt string ready to send to the LLM.
    """
    # --- 1. Format baseline metrics ---
    if baseline_metrics:
        metrics_str = ", ".join(
            f"{k}={v}" for k, v in baseline_metrics.items()
        )
    else:
        metrics_str = "No baseline yet"

    # --- 2. Format failure history ---
    if failure_history:
        failures_str = "\n".join(f"- {msg}" for msg in failure_history)
    else:
        failures_str = "None"

    # --- 3. Repo context section ---
    repo_context_section = context_map.to_prompt_section()

    # --- 4. Assemble prompt using the canonical template ---
    prompt = (
        "You are optimizing Python code for performance.\n"
        "\n"
        f"Target File: {context_map.target_file}\n"
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

    return prompt
