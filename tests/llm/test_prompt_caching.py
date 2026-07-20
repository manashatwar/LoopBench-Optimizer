"""
Tests for provider prompt caching in the LLM layer (Task 7, design §C2).

Validates: Requirements 5.4, 5.5

These tests exercise :class:`OpenAILLM`'s cacheable-prefix structuring:
  * caching OFF (flag off or unsupported provider) → the payload sent to the
    API is byte-for-byte identical to sending the plain combined prompt (R5.5);
  * caching ON with an OpenAI/Groq-style endpoint → the static prefix leads the
    message content (automatic caching is prefix-keyed);
  * caching ON with an Anthropic-style endpoint → the prefix content block
    carries an explicit ``cache_control`` marker (R5.4);
  * an unknown provider degrades to the plain prompt without error.

No real network calls are made: :meth:`OpenAILLM._call_api` is patched to
capture the ``params`` that would have been sent.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import patch

from openevolve.config import LLMModelConfig
from openevolve.llm.openai import OpenAILLM, detect_cache_capability


STATIC_PREFIX = (
    "You are an expert Python programmer optimizing Python code for performance.\n"
    "\n"
    "Target File: src/hot.py\n"
    "\nHotspots:\n- hot_loop (0.5s)\n"
)
DYNAMIC_DELTA = (
    "Current Performance: speed_ms=460\n"
    "Optimization Goal: Improve execution performance\n"
    "\n"
    "Recent Failures:\nNone\n"
    "\n"
    "Generate a git patch in unified diff format.\n"
)
COMBINED = STATIC_PREFIX + DYNAMIC_DELTA


def _make_llm(api_base: str, *, cache_static_prefix: bool = True) -> OpenAILLM:
    """Build an OpenAILLM against a given endpoint without network calls."""
    cfg = LLMModelConfig(
        name="test-model",
        api_base=api_base,
        api_key="test-key",
        temperature=0.7,
        top_p=0.95,
        max_tokens=256,
        timeout=30,
        retries=0,
        retry_delay=0,
        cache_static_prefix=cache_static_prefix,
    )
    return OpenAILLM(cfg)


def _capture_params(llm: OpenAILLM) -> Dict[str, Any]:
    """Patch ``_call_api`` to capture params and drive a single generation."""
    captured: Dict[str, Any] = {}

    async def fake_call_api(params: Dict[str, Any]) -> str:
        captured["params"] = params
        return "ok"

    with patch.object(llm, "_call_api", side_effect=fake_call_api):
        asyncio.run(llm.generate(COMBINED, cache_prefix=STATIC_PREFIX))
    return captured["params"]


def _last_user_message(params: Dict[str, Any]) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = params["messages"]
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg
    raise AssertionError("no user message found")


# ── Feature detection ─────────────────────────────────────────────────────────

def test_detect_capability_openai_and_groq():
    assert detect_cache_capability("https://api.openai.com/v1") == "openai"
    assert detect_cache_capability("https://api.groq.com/openai/v1") == "openai"


def test_detect_capability_anthropic():
    assert detect_cache_capability("https://api.anthropic.com/v1") == "anthropic"


def test_detect_capability_unknown_and_empty():
    assert detect_cache_capability("https://example.invalid/v1") == "none"
    assert detect_cache_capability(None) == "none"
    assert detect_cache_capability("") == "none"


# ── Caching OFF: byte-identical payload (R5.5) ─────────────────────────────────

def test_caching_off_flag_is_byte_identical_to_plain_prompt():
    """Flag off → content equals the plain combined prompt string exactly."""
    llm = _make_llm("https://api.groq.com/openai/v1", cache_static_prefix=False)
    params = _capture_params(llm)
    content = _last_user_message(params)["content"]
    assert isinstance(content, str)
    assert content == COMBINED


def test_caching_off_unsupported_provider_is_byte_identical():
    """Unknown provider → plain combined prompt even with the flag on (R5.5)."""
    llm = _make_llm("https://example.invalid/v1", cache_static_prefix=True)
    params = _capture_params(llm)
    content = _last_user_message(params)["content"]
    assert isinstance(content, str)
    assert content == COMBINED


def test_no_cache_prefix_is_byte_identical():
    """No cache_prefix supplied → plain prompt regardless of endpoint."""
    llm = _make_llm("https://api.groq.com/openai/v1", cache_static_prefix=True)
    captured: Dict[str, Any] = {}

    async def fake_call_api(params: Dict[str, Any]) -> str:
        captured["params"] = params
        return "ok"

    with patch.object(llm, "_call_api", side_effect=fake_call_api):
        asyncio.run(llm.generate(COMBINED))  # no cache_prefix
    content = _last_user_message(captured["params"])["content"]
    assert content == COMBINED


# ── Caching ON, OpenAI/Groq: prefix leads the content ──────────────────────────

def test_caching_on_openai_prefix_leads_content():
    """OpenAI/Groq automatic caching → the static prefix leads the content."""
    llm = _make_llm("https://api.groq.com/openai/v1", cache_static_prefix=True)
    params = _capture_params(llm)
    content = _last_user_message(params)["content"]
    # Structured to be cache-friendly: prefix at the very start (and, since
    # OpenAI caching is automatic, the payload stays byte-identical to plain).
    assert isinstance(content, str)
    assert content.startswith(STATIC_PREFIX)
    assert content == COMBINED


# ── Caching ON, Anthropic: cache_control marker on the prefix block ────────────

def test_caching_on_anthropic_marks_prefix_block():
    """Anthropic endpoint → prefix content block carries cache_control (R5.4)."""
    llm = _make_llm("https://api.anthropic.com/v1", cache_static_prefix=True)
    params = _capture_params(llm)
    content = _last_user_message(params)["content"]

    assert isinstance(content, list)
    # First block is the cached static prefix.
    prefix_block = content[0]
    assert prefix_block["type"] == "text"
    assert prefix_block["text"] == STATIC_PREFIX
    assert prefix_block.get("cache_control") == {"type": "ephemeral"}
    # Second block is the (uncached) dynamic delta.
    assert content[1]["text"] == DYNAMIC_DELTA
    assert "cache_control" not in content[1]


def test_caching_off_anthropic_is_byte_identical():
    """Flag off on Anthropic → plain string content, no cache_control (R5.5)."""
    llm = _make_llm("https://api.anthropic.com/v1", cache_static_prefix=False)
    params = _capture_params(llm)
    content = _last_user_message(params)["content"]
    assert isinstance(content, str)
    assert content == COMBINED


# ── Per-call override ──────────────────────────────────────────────────────────

def test_per_call_override_disables_caching():
    """cache_static_prefix=False kwarg overrides an enabled model default."""
    llm = _make_llm("https://api.anthropic.com/v1", cache_static_prefix=True)
    captured: Dict[str, Any] = {}

    async def fake_call_api(params: Dict[str, Any]) -> str:
        captured["params"] = params
        return "ok"

    with patch.object(llm, "_call_api", side_effect=fake_call_api):
        asyncio.run(
            llm.generate_with_context(
                system_message="",
                messages=[{"role": "user", "content": COMBINED}],
                cache_prefix=STATIC_PREFIX,
                cache_static_prefix=False,
            )
        )
    content = _last_user_message(captured["params"])["content"]
    assert content == COMBINED
