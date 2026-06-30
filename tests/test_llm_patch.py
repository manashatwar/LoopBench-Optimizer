"""
Tests for LLMClient patch extraction and generation (Tasks 6.1, 6.2, 6.3).

Task 6.1  — extract_patch_from_response() in openevolve/llm/base.py
Task 6.2  — LLMEnsemble.generate_patch() and retry_with_clarification()
Task 6.3  — Verify existing retry / provider infrastructure
"""

import asyncio
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openevolve.llm.base import (
    LLMInterface,
    extract_patch_from_response,
    retry_system_message,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_PATCH = """\
--- a/src/main.py
+++ b/src/main.py
@@ -1,4 +1,4 @@
-def slow():
-    return sum(range(1000))
+def fast():
+    return 500 * 999 // 2
"""


def _wrap_diff(content: str) -> str:
    return f"```diff\n{content}\n```"


def _wrap_patch(content: str) -> str:
    return f"```patch\n{content}\n```"


def _wrap_plain(content: str) -> str:
    return f"```\n{content}\n```"


# ---------------------------------------------------------------------------
# Task 6.1 — extract_patch_from_response
# ---------------------------------------------------------------------------

class TestExtractPatchFromResponse:
    """Unit tests for extract_patch_from_response() — Requirements 2.3, 2.4."""

    # ── Happy paths ────────────────────────────────────────────────────────

    def test_extracts_diff_fenced_block(self):
        """Extracts patch from ```diff ... ``` block."""
        response = f"Here is the fix:\n{_wrap_diff(VALID_PATCH)}\nDone."
        result = extract_patch_from_response(response)
        assert result is not None
        assert "--- a/src/main.py" in result
        assert "+++ b/src/main.py" in result

    def test_extracts_patch_fenced_block(self):
        """Extracts patch from ```patch ... ``` block."""
        response = _wrap_patch(VALID_PATCH)
        result = extract_patch_from_response(response)
        assert result is not None
        assert "@@" in result

    def test_extracts_bare_fenced_block_with_diff_markers(self):
        """Falls back to bare ``` block when content has diff markers."""
        response = f"```\n{VALID_PATCH}\n```"
        result = extract_patch_from_response(response)
        assert result is not None
        assert "---" in result

    def test_strips_surrounding_whitespace(self):
        """Leading/trailing whitespace inside fence is stripped."""
        response = f"```diff\n\n\n{VALID_PATCH}\n\n```"
        result = extract_patch_from_response(response)
        assert result is not None
        assert not result.startswith("\n")

    def test_returns_first_patch_when_multiple_blocks(self):
        """When multiple fenced blocks exist, the first valid one is returned."""
        second_patch = VALID_PATCH.replace("main.py", "utils.py")
        response = (
            f"First:\n{_wrap_diff(VALID_PATCH)}\n"
            f"Second:\n{_wrap_diff(second_patch)}\n"
        )
        result = extract_patch_from_response(response)
        assert result is not None
        assert "main.py" in result  # first block wins

    def test_case_insensitive_fence_label(self):
        """```DIFF and ```Patch labels are accepted."""
        response = f"```DIFF\n{VALID_PATCH}\n```"
        result = extract_patch_from_response(response)
        assert result is not None

    # ── Rejection paths ────────────────────────────────────────────────────

    def test_returns_none_for_empty_string(self):
        assert extract_patch_from_response("") is None

    def test_returns_none_for_none_input(self):
        assert extract_patch_from_response(None) is None  # type: ignore[arg-type]

    def test_returns_none_when_no_fence(self):
        """Plain prose without code fence returns None."""
        assert extract_patch_from_response("Change line 5 to use += 1") is None

    def test_returns_none_for_fenced_block_without_diff_markers(self):
        """A fenced block containing plain Python (no ---, +++, @@) is rejected."""
        response = "```diff\ndef foo():\n    pass\n```"
        assert extract_patch_from_response(response) is None

    def test_returns_none_for_json_fence(self):
        """A ```json block without diff markers is rejected."""
        response = '```json\n{"key": "value"}\n```'
        assert extract_patch_from_response(response) is None

    # ── Content fidelity ───────────────────────────────────────────────────

    def test_preserves_patch_content_exactly(self):
        """Extracted patch matches source content (no extra lines added)."""
        response = _wrap_diff(VALID_PATCH)
        result = extract_patch_from_response(response)
        # Normalise trailing newline for comparison
        assert result.strip() == VALID_PATCH.strip()


# ---------------------------------------------------------------------------
# Task 6.1 — retry_system_message
# ---------------------------------------------------------------------------

class TestRetrySystemMessage:
    """retry_system_message() augments the prompt with error context."""

    def test_contains_original_prompt(self):
        original = "Optimise src/foo.py for speed"
        msg = retry_system_message(original, "no patch found")
        assert original in msg

    def test_contains_error_description(self):
        msg = retry_system_message("prompt", "syntax error in patch")
        assert "syntax error in patch" in msg

    def test_contains_diff_instruction(self):
        msg = retry_system_message("prompt", "err")
        assert "```diff" in msg

    def test_contains_unified_diff_mention(self):
        msg = retry_system_message("prompt", "err")
        assert "---" in msg or "unified diff" in msg.lower()

    def test_result_longer_than_original(self):
        original = "short prompt"
        msg = retry_system_message(original, "err")
        assert len(msg) > len(original)


# ---------------------------------------------------------------------------
# Task 6.2 — LLMEnsemble.generate_patch / retry_with_clarification
# ---------------------------------------------------------------------------

def _make_ensemble(responses: List[Optional[str]]):
    """
    Build a minimal LLMEnsemble whose `generate` coroutine returns successive
    values from *responses*.  Uses object.__new__ to bypass __init__ and avoid
    real OpenAI client construction.
    """
    from openevolve.llm.ensemble import LLMEnsemble
    from openevolve.config import LLMModelConfig
    import random as _random
    from unittest.mock import MagicMock

    iter_resp = iter(responses)

    async def _fake_generate(prompt, **kwargs):
        try:
            val = next(iter_resp)
        except StopIteration:
            val = responses[-1]
        if val is None:
            raise RuntimeError("Simulated API failure")
        return val

    mock_model = MagicMock()
    mock_model.generate = _fake_generate
    mock_model.weight = 1.0
    # _sample_model does vars(sampled_model)['model'] for logging; satisfy it:
    mock_model.model = "mock-model"

    ensemble = object.__new__(LLMEnsemble)
    ensemble.models_cfg = []
    ensemble.models = [mock_model]
    ensemble.weights = [1.0]
    ensemble.random_state = _random.Random(0)
    # Override _sample_model so it always returns our mock
    ensemble._sample_model = lambda: mock_model
    return ensemble


class TestGeneratePatch:
    """LLMEnsemble.generate_patch() — Requirements 2.3, 2.4, 2.5, 14.1."""

    def test_returns_patch_on_first_attempt(self):
        """Valid patch returned on the first LLM call."""
        ensemble = _make_ensemble([_wrap_diff(VALID_PATCH)])
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", backoff_base=0)
        )
        assert result is not None
        assert "--- a/src/main.py" in result

    def test_retries_when_first_response_lacks_patch(self):
        """Retries when initial response has no patch; succeeds on second."""
        ensemble = _make_ensemble(["No patch here.", _wrap_diff(VALID_PATCH)])
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", backoff_base=0)
        )
        assert result is not None

    def test_returns_none_after_all_retries_fail(self):
        """Returns None when every attempt produces a response without a patch."""
        ensemble = _make_ensemble(["no patch"] * 10)
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", max_retries=2, backoff_base=0)
        )
        assert result is None

    def test_retries_on_api_error(self):
        """API errors are caught; next attempt with backoff succeeds."""
        ensemble = _make_ensemble([None, _wrap_diff(VALID_PATCH)])
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", max_retries=2, backoff_base=0)
        )
        assert result is not None

    def test_returns_none_when_all_attempts_raise(self):
        """Returns None when all attempts raise an API error."""
        ensemble = _make_ensemble([None, None, None, None])
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", max_retries=2, backoff_base=0)
        )
        assert result is None

    def test_backoff_schedule(self):
        """generate_patch awaits increasing delays between retries."""
        delays = []

        async def capturing_sleep(delay):
            delays.append(delay)

        ensemble = _make_ensemble(["no patch", "no patch", _wrap_diff(VALID_PATCH)])
        with patch("openevolve.llm.ensemble.asyncio.sleep", side_effect=capturing_sleep):
            asyncio.get_event_loop().run_until_complete(
                ensemble.generate_patch("prompt", max_retries=3, backoff_base=1.0)
            )

        # Slept before attempts 2 and 3 (patch found on attempt 3 → 2 sleeps)
        assert len(delays) == 2
        assert delays[0] == 1.0   # attempt 2: 1.0 * 2^0
        assert delays[1] == 2.0   # attempt 3: 1.0 * 2^1

    def test_max_retries_respected(self):
        """No more than max_retries + 1 generate() calls are made."""
        call_count = 0

        async def counting_generate(prompt, **kwargs):
            nonlocal call_count
            call_count += 1
            return "no patch here"

        ensemble = _make_ensemble(["placeholder"])
        # Replace the model's generate on the mocked _sample_model return value
        ensemble._sample_model().generate = counting_generate

        asyncio.get_event_loop().run_until_complete(
            ensemble.generate_patch("prompt", max_retries=2, backoff_base=0)
        )
        assert call_count == 3  # 1 initial + 2 retries


class TestRetryWithClarification:
    """LLMEnsemble.retry_with_clarification() — Requirements 2.5."""

    def test_injects_error_in_prompt(self):
        """Error string appears in the prompt sent to the LLM on retry."""
        received_prompts = []

        async def capturing_generate(prompt, **kwargs):
            received_prompts.append(prompt)
            return _wrap_diff(VALID_PATCH)

        ensemble = _make_ensemble(["placeholder"])
        ensemble.models[0].generate = capturing_generate

        asyncio.get_event_loop().run_until_complete(
            ensemble.retry_with_clarification(
                "original prompt",
                "patch did not apply cleanly",
                backoff_base=0,
            )
        )
        assert any("patch did not apply cleanly" in p for p in received_prompts)

    def test_returns_patch_on_success(self):
        ensemble = _make_ensemble([_wrap_diff(VALID_PATCH)])
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.retry_with_clarification("prompt", "err", backoff_base=0)
        )
        assert result is not None
        assert "---" in result

    def test_returns_none_when_all_retries_fail(self):
        ensemble = _make_ensemble(["no patch"] * 10)
        result = asyncio.get_event_loop().run_until_complete(
            ensemble.retry_with_clarification(
                "prompt", "err", max_retries=1, backoff_base=0
            )
        )
        assert result is None


# ---------------------------------------------------------------------------
# Task 6.3 — Verify existing provider/retry infrastructure
# ---------------------------------------------------------------------------

class TestProviderInfrastructure:
    """Verify the existing retry/provider infrastructure still works (Task 6.3)."""

    def test_llm_interface_is_abstract(self):
        """LLMInterface cannot be instantiated directly."""
        with pytest.raises(TypeError):
            LLMInterface()  # type: ignore[abstract]

    def test_llm_interface_has_generate(self):
        assert hasattr(LLMInterface, "generate")

    def test_llm_interface_has_generate_with_context(self):
        assert hasattr(LLMInterface, "generate_with_context")

    def test_openai_provider_importable(self):
        from openevolve.llm.openai import OpenAILLM
        assert OpenAILLM is not None

    def test_ensemble_importable(self):
        from openevolve.llm.ensemble import LLMEnsemble
        assert LLMEnsemble is not None

    def test_extract_patch_importable_from_base(self):
        """extract_patch_from_response is public from base module."""
        from openevolve.llm.base import extract_patch_from_response as epfr
        assert callable(epfr)

    def test_retry_system_message_importable_from_base(self):
        from openevolve.llm.base import retry_system_message as rsm
        assert callable(rsm)

    def test_ensemble_has_generate_patch(self):
        from openevolve.llm.ensemble import LLMEnsemble
        assert hasattr(LLMEnsemble, "generate_patch")

    def test_ensemble_has_retry_with_clarification(self):
        from openevolve.llm.ensemble import LLMEnsemble
        assert hasattr(LLMEnsemble, "retry_with_clarification")

    def test_config_supports_temperature(self):
        """LLMModelConfig supports temperature field (Req 2.6)."""
        from openevolve.config import LLMModelConfig
        cfg = LLMModelConfig()
        cfg.temperature = 0.7
        assert cfg.temperature == 0.7

    def test_config_supports_max_tokens(self):
        from openevolve.config import LLMModelConfig
        cfg = LLMModelConfig()
        cfg.max_tokens = 4096
        assert cfg.max_tokens == 4096
