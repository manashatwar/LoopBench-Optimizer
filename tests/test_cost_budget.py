"""Tests for cost/token budgeting in the OptimizerLoop (hero mode)."""
import pytest

from openevolve.optimizer_loop import OptimizerLoop


class FakeEnsemble:
    """Duck-typed stand-in for LLMEnsemble exposing usage_totals()."""

    def __init__(self, prompt=0, completion=0, calls=0):
        self._p, self._c, self._calls = prompt, completion, calls

    def set(self, prompt, completion, calls):
        self._p, self._c, self._calls = prompt, completion, calls

    def usage_totals(self):
        return {
            "prompt_tokens": self._p,
            "completion_tokens": self._c,
            "total_tokens": self._p + self._c,
            "api_calls": self._calls,
        }


def _loop(tmp_path, ensemble, **budget):
    cfg = {
        "repo_path": str(tmp_path),
        "target_file": str(tmp_path / "prog.py"),
        "test_file": str(tmp_path / "test_prog.py"),
        "db_path": ":memory:",
        **budget,
    }
    return OptimizerLoop(cfg, llm_ensemble=ensemble)


class TestBudgetSnapshot:
    def test_cost_estimate_uses_pricing(self, tmp_path):
        loop = _loop(
            tmp_path, FakeEnsemble(prompt=2000, completion=1000, calls=3),
            usd_per_1k_prompt=0.5, usd_per_1k_completion=1.0,
        )
        snap = loop._budget_snapshot()
        assert snap["total_tokens"] == 3000
        assert snap["api_calls"] == 3
        # 2.0 * 0.5 + 1.0 * 1.0 = 2.0
        assert snap["cost_usd"] == pytest.approx(2.0)

    def test_zero_pricing_gives_zero_cost(self, tmp_path):
        loop = _loop(tmp_path, FakeEnsemble(prompt=9999, completion=9999))
        assert loop._budget_snapshot()["cost_usd"] == 0.0

    def test_no_ensemble_is_safe(self, tmp_path):
        loop = _loop(tmp_path, None)
        snap = loop._budget_snapshot()
        assert snap["total_tokens"] == 0
        assert snap["cost_usd"] == 0.0


class TestBudgetExceeded:
    def test_no_budget_never_exceeds(self, tmp_path):
        loop = _loop(tmp_path, FakeEnsemble(prompt=10**9, completion=10**9))
        exceeded, _ = loop._budget_exceeded()
        assert exceeded is False

    def test_token_budget_trips(self, tmp_path):
        loop = _loop(tmp_path, FakeEnsemble(prompt=600, completion=500), max_tokens_total=1000)
        exceeded, reason = loop._budget_exceeded()
        assert exceeded is True
        assert "token budget" in reason

    def test_token_budget_under_limit(self, tmp_path):
        loop = _loop(tmp_path, FakeEnsemble(prompt=100, completion=100), max_tokens_total=1000)
        assert loop._budget_exceeded()[0] is False

    def test_usd_budget_trips(self, tmp_path):
        loop = _loop(
            tmp_path, FakeEnsemble(prompt=4000, completion=0),
            max_usd=1.0, usd_per_1k_prompt=0.5,
        )
        # 4.0 * 0.5 = 2.0 >= 1.0
        exceeded, reason = loop._budget_exceeded()
        assert exceeded is True
        assert "cost budget" in reason

    def test_usd_budget_under_limit(self, tmp_path):
        loop = _loop(
            tmp_path, FakeEnsemble(prompt=1000, completion=0),
            max_usd=5.0, usd_per_1k_prompt=0.5,
        )
        assert loop._budget_exceeded()[0] is False
