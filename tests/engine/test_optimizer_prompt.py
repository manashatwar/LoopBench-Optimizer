"""
Unit tests for openevolve/repo_mapper/optimizer_prompt.py (Task 5.2).

Verifies that create_optimizer_prompt() assembles a correct LLM prompt from
a ContextMap, baseline metrics, failure history, and optimization goal.
"""

from pathlib import Path

import pytest

from openevolve.repo_mapper.models import (
    ContextMap,
    FileDescriptor,
    RepoMapperConfig,
)
from openevolve.repo_mapper.optimizer_prompt import create_optimizer_prompt


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_context_map(target_name: str = "main.py") -> ContextMap:
    """Construct a minimal ContextMap sufficient for prompt building."""
    target_path = Path(target_name)
    descriptor = FileDescriptor(
        file_path=target_path,
        role="main",
        summary="Main optimization target.",
        functions=["run"],
        loc=20,
    )
    return ContextMap(
        target_file=target_path,
        target_descriptor=descriptor,
        relevant_files=[],
        repository_tree="repo/\n  main.py  <- Target",
        token_count=50,
    )


@pytest.fixture
def ctx():
    return _make_context_map("main.py")


@pytest.fixture
def metrics():
    return {"execution_time": 1.23, "throughput": 456.7}


@pytest.fixture
def failures():
    return ["Patch failed to apply", "Syntax error on line 5"]


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

class TestPromptStructure:
    """Verify mandatory sections are present in all prompts."""

    def test_prompt_is_string(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_contains_target_file_label(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "Target File:" in prompt

    def test_target_filename_in_prompt(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "main.py" in prompt

    def test_contains_diff_instruction(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "```diff" in prompt

    def test_contains_repo_context_section(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "## Repository Context" in prompt

    def test_contains_current_performance_label(self, ctx, metrics):
        prompt = create_optimizer_prompt(ctx, metrics, [])
        assert "Current Performance:" in prompt

    def test_contains_recent_failures_label(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "Recent Failures:" in prompt


# ---------------------------------------------------------------------------
# Baseline metrics formatting
# ---------------------------------------------------------------------------

class TestBaselineMetrics:
    """Baseline dict is formatted as key=value pairs or sentinel."""

    def test_metric_values_appear_in_prompt(self, ctx, metrics):
        prompt = create_optimizer_prompt(ctx, metrics, [])
        assert "1.23" in prompt
        assert "456.7" in prompt

    def test_metric_keys_appear_in_prompt(self, ctx, metrics):
        prompt = create_optimizer_prompt(ctx, metrics, [])
        assert "execution_time" in prompt
        assert "throughput" in prompt

    def test_empty_metrics_shows_no_baseline(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "No baseline yet" in prompt

    def test_single_metric(self, ctx):
        prompt = create_optimizer_prompt(ctx, {"latency_ms": 99.0}, [])
        assert "latency_ms=99.0" in prompt


# ---------------------------------------------------------------------------
# Failure history formatting
# ---------------------------------------------------------------------------

class TestFailureHistory:
    """Failure messages appear in prompt; empty list shows 'None'."""

    def test_failure_messages_in_prompt(self, ctx, failures):
        prompt = create_optimizer_prompt(ctx, {}, failures)
        for msg in failures:
            assert msg in prompt

    def test_empty_failures_shows_none(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "None" in prompt

    def test_multiple_failures_all_present(self, ctx):
        msgs = ["error A", "error B", "error C"]
        prompt = create_optimizer_prompt(ctx, {}, msgs)
        for msg in msgs:
            assert msg in prompt

    def test_single_failure(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, ["Only one failure"])
        assert "Only one failure" in prompt


# ---------------------------------------------------------------------------
# Optimization goal
# ---------------------------------------------------------------------------

class TestOptimizationGoal:
    """Optimization goal string appears in the prompt."""

    def test_default_goal(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "Improve execution performance" in prompt

    def test_custom_goal(self, ctx):
        goal = "Reduce memory allocation"
        prompt = create_optimizer_prompt(ctx, {}, [], optimization_goal=goal)
        assert goal in prompt


# ---------------------------------------------------------------------------
# Repo context section
# ---------------------------------------------------------------------------

class TestRepoContextSection:
    """The repo context from to_prompt_section() is embedded in the prompt."""

    def test_repo_context_header_present(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        assert "## Repository Context" in prompt

    def test_repo_tree_content_in_prompt(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        # The tree string set in the fixture should appear
        assert "repo/" in prompt or "main.py" in prompt

    def test_target_file_descriptor_in_prompt(self, ctx):
        prompt = create_optimizer_prompt(ctx, {}, [])
        # FileDescriptor.to_string() emits "### Target File" via to_prompt_section
        assert "### Target File" in prompt

    def test_different_target_filename(self):
        ctx2 = _make_context_map("evaluator.py")
        prompt = create_optimizer_prompt(ctx2, {}, [])
        assert "evaluator.py" in prompt


# ---------------------------------------------------------------------------
# Task 6 — static prefix + dynamic delta split, hotspots, backward compat
# (design §C2, requirements R4/R5)
# ---------------------------------------------------------------------------

from openevolve.repo_mapper.optimizer_prompt import build_prompt_parts  # noqa: E402
from openevolve.profiler import format_hotspots  # noqa: E402


def _sample_hotspots():
    """A small, deterministic hotspot list mirroring profiler output."""
    return [
        {"function": "mod.py:10(hot_loop)", "tottime": 0.512, "cumtime": 0.9, "ncalls": 1000},
        {"function": "mod.py:42(helper)", "tottime": 0.130, "cumtime": 0.2, "ncalls": 500},
    ]


def _pre_task6_prompt(
    context_map,
    baseline_metrics,
    failure_history,
    optimization_goal="Improve execution performance",
    language="Python",
) -> str:
    """Reconstruct the EXACT pre-Task-6 template for the regression baseline."""
    if baseline_metrics:
        metrics_str = ", ".join(f"{k}={v}" for k, v in baseline_metrics.items())
    else:
        metrics_str = "No baseline yet"
    failures_str = "\n".join(f"- {msg}" for msg in failure_history) if failure_history else "None"
    repo_context_section = context_map.to_prompt_section()
    return (
        f"You are an expert {language} programmer optimizing {language} code for "
        f"performance. Emit valid {language} only — never use constructs from "
        "other languages.\n"
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


class TestBackwardCompatibility:
    """With no hotspots, output must be byte-for-byte identical to pre-Task-6."""

    def test_empty_hotspots_matches_legacy_template(self, ctx, metrics, failures):
        expected = _pre_task6_prompt(ctx, metrics, failures)
        assert create_optimizer_prompt(ctx, metrics, failures) == expected

    def test_empty_hotspots_no_metrics_no_failures(self, ctx):
        expected = _pre_task6_prompt(ctx, {}, [])
        assert create_optimizer_prompt(ctx, {}, []) == expected

    def test_explicit_empty_hotspots_arg_matches_legacy(self, ctx, metrics, failures):
        expected = _pre_task6_prompt(ctx, metrics, failures)
        assert create_optimizer_prompt(ctx, metrics, failures, hotspots=[]) == expected

    def test_none_hotspots_arg_matches_legacy(self, ctx, metrics, failures):
        expected = _pre_task6_prompt(ctx, metrics, failures)
        assert create_optimizer_prompt(ctx, metrics, failures, hotspots=None) == expected

    def test_no_hotspot_section_when_absent(self, ctx, metrics, failures):
        prompt = create_optimizer_prompt(ctx, metrics, failures)
        assert "top hotspots by self-time" not in prompt


class TestPrefixDeltaComposition:
    """combined prompt == static_prefix + dynamic_delta (composition consistent)."""

    def test_composition_no_hotspots(self, ctx, metrics, failures):
        prefix, delta = build_prompt_parts(ctx, metrics, failures)
        combined = create_optimizer_prompt(ctx, metrics, failures)
        assert prefix + delta == combined

    def test_composition_with_hotspots(self, ctx, metrics, failures):
        hs = _sample_hotspots()
        prefix, delta = build_prompt_parts(ctx, metrics, failures, hotspots=hs)
        combined = create_optimizer_prompt(ctx, metrics, failures, hotspots=hs)
        assert prefix + delta == combined

    def test_composition_equals_legacy_when_empty(self, ctx, metrics, failures):
        prefix, delta = build_prompt_parts(ctx, metrics, failures)
        assert prefix + delta == _pre_task6_prompt(ctx, metrics, failures)


class TestStaticPrefixStability:
    """Static prefix is stable across generations; the delta carries per-gen data."""

    def test_prefix_stable_delta_varies_across_generations(self, ctx):
        hs = _sample_hotspots()
        # Simulate three generations with changing metrics + failures.
        gens = [
            ({"speed_ms": 460.0}, ["gen1 failure"]),
            ({"speed_ms": 441.0}, ["gen2 failure a", "gen2 failure b"]),
            ({"speed_ms": 430.0}, []),
        ]
        prefixes = []
        deltas = []
        for gen_metrics, gen_failures in gens:
            prefix, delta = build_prompt_parts(ctx, gen_metrics, gen_failures, hotspots=hs)
            prefixes.append(prefix)
            deltas.append(delta)

        # Prefix identical across every generation (stable / cacheable).
        assert len(set(prefixes)) == 1
        # Delta changes with per-generation metrics/failures.
        assert len(set(deltas)) == len(deltas)

    def test_prefix_stable_without_hotspots(self, ctx):
        p1, d1 = build_prompt_parts(ctx, {"speed_ms": 460.0}, ["a"])
        p2, d2 = build_prompt_parts(ctx, {"speed_ms": 441.0}, ["b", "c"])
        assert p1 == p2
        assert d1 != d2


class TestHotspotPlacement:
    """Hotspots live in the static prefix, at the TOP edge with the target."""

    def test_hotspots_appear_in_static_prefix(self, ctx, metrics, failures):
        hs = _sample_hotspots()
        prefix, delta = build_prompt_parts(ctx, metrics, failures, hotspots=hs)
        summary = format_hotspots(hs)
        assert summary in prefix
        assert summary not in delta

    def test_hotspots_and_target_at_top_edge(self, ctx, metrics, failures):
        hs = _sample_hotspots()
        prompt = create_optimizer_prompt(ctx, metrics, failures, hotspots=hs)
        idx_target = prompt.index("Target File:")
        idx_hotspots = prompt.index("top hotspots by self-time")
        idx_metrics = prompt.index("Current Performance:")
        idx_failures = prompt.index("Recent Failures:")
        # Target first, then hotspots, both ahead of the dynamic middle content.
        assert idx_target < idx_hotspots < idx_metrics < idx_failures

    def test_hotspot_function_names_present(self, ctx, metrics, failures):
        hs = _sample_hotspots()
        prompt = create_optimizer_prompt(ctx, metrics, failures, hotspots=hs)
        assert "hot_loop" in prompt
        assert "helper" in prompt
