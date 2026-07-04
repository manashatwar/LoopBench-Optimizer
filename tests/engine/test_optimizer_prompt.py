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
