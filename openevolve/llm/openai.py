"""
OpenAI API interface for LLMs

This module also supports a "manual mode" (human-in-the-loop) where prompts are written
to a task queue directory and the system waits for a corresponding *.answer.json file
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import openai

from openevolve.llm.base import LLMInterface

logger = logging.getLogger(__name__)


# ── Provider prompt-caching feature detection (design §C2, R5.4/R5.5) ─────────
# OpenAI-compatible endpoints (OpenAI, Groq, and friends) perform *automatic*
# prompt caching keyed on the literal leading prefix of the request — no
# per-block marking is required, we only need the stable static prefix to sit at
# the very start of the message content. Anthropic-style endpoints require an
# explicit ``cache_control`` marker on the cached content block. Anything we do
# not recognise degrades to a plain, uncached prompt.
_OPENAI_COMPATIBLE_HOST_MARKERS = (
    "openai.com",
    "groq.com",
    "googleapis.com",
    "openrouter.ai",
    "azure.com",
    "mistral.ai",
    "together.ai",
    "together.xyz",
    "deepinfra.com",
    "fireworks.ai",
    "perplexity.ai",
    "anyscale.com",
    "localhost",
    "127.0.0.1",
)

CACHE_CAPABILITY_OPENAI = "openai"
CACHE_CAPABILITY_ANTHROPIC = "anthropic"
CACHE_CAPABILITY_NONE = "none"


def detect_cache_capability(api_base: Optional[str]) -> str:
    """Feature-detect a provider's prompt-caching style from its API base URL.

    Returns one of:
      * ``"anthropic"`` — Anthropic-style; the static prefix is sent as a
        content block carrying an explicit ``cache_control`` marker.
      * ``"openai"`` — OpenAI-compatible (OpenAI, Groq, …); caching is automatic
        on the leading prefix, so no structural change is needed beyond keeping
        the static prefix at the start of the content.
      * ``"none"`` — unknown/unsupported; send a plain combined prompt (R5.5).

    Args:
        api_base: The configured provider base URL (may be ``None``).

    Returns:
        The detected capability string.
    """
    base = (api_base or "").lower()
    if not base:
        return CACHE_CAPABILITY_NONE
    if "anthropic" in base:
        return CACHE_CAPABILITY_ANTHROPIC
    if any(marker in base for marker in _OPENAI_COMPATIBLE_HOST_MARKERS):
        return CACHE_CAPABILITY_OPENAI
    return CACHE_CAPABILITY_NONE


def _iso_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _build_display_prompt(messages: List[Dict[str, str]]) -> str:
    """
    Render messages into a single plain-text prompt for the manual UI.
    """
    chunks: List[str] = []
    for m in messages:
        role = str(m.get("role", "user")).upper()
        content = m.get("content", "")
        chunks.append(f"### {role}\n{content}\n")
    return "\n".join(chunks).rstrip() + "\n"


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class OpenAILLM(LLMInterface):
    """LLM interface using OpenAI-compatible APIs"""

    def __init__(
        self,
        model_cfg: Optional[dict] = None,
    ):
        self.model = model_cfg.name
        self.system_message = model_cfg.system_message
        self.temperature = model_cfg.temperature
        self.top_p = model_cfg.top_p
        self.max_tokens = model_cfg.max_tokens
        self.timeout = model_cfg.timeout
        self.retries = model_cfg.retries
        self.retry_delay = model_cfg.retry_delay
        self.api_base = model_cfg.api_base
        self.api_key = model_cfg.api_key
        self.random_seed = getattr(model_cfg, "random_seed", None)
        self.reasoning_effort = getattr(model_cfg, "reasoning_effort", None)

        # ── Provider prompt caching (design §C2, R5.4/R5.5) ───────────────────
        # Behaviour flag threaded from ``prompt.cache_static_prefix`` (default
        # on). ``cache_capability`` is feature-detected from the endpoint.
        self.cache_static_prefix = bool(getattr(model_cfg, "cache_static_prefix", True))
        self.cache_capability = detect_cache_capability(self.api_base)

        # ── Token accounting (for cost budgeting / audit) ─────────────────────
        # Updated from each API response's `usage` field when available.
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.api_call_count = 0

        # Manual mode: enabled via llm.manual_mode in config.yaml
        self.manual_mode = (getattr(model_cfg, "manual_mode", False) is True)
        self.manual_queue_dir: Optional[Path] = None

        if self.manual_mode:
            qdir = getattr(model_cfg, "_manual_queue_dir", None)
            if not qdir:
                raise ValueError(
                    "Manual mode is enabled but manual_queue_dir is missing. "
                    "This should be injected by the OpenEvolve controller."
                )
            self.manual_queue_dir = Path(str(qdir)).expanduser().resolve()
            self.manual_queue_dir.mkdir(parents=True, exist_ok=True)
            self.client = None
        else:
            # Set up API client (normal mode)
            # OpenAI client requires max_retries to be int, not None
            max_retries = self.retries if self.retries is not None else 0
            self.client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                timeout=self.timeout,
                max_retries=max_retries,
            )

        # Only log unique models to reduce duplication
        if not hasattr(logger, "_initialized_models"):
            logger._initialized_models = set()

        if self.model not in logger._initialized_models:
            logger.info(f"Initialized OpenAI LLM with model: {self.model}")
            logger._initialized_models.add(self.model)

    def _apply_prompt_caching(
        self,
        messages: List[Dict[str, Any]],
        cache_prefix: Optional[str],
        enabled: bool,
    ) -> List[Dict[str, Any]]:
        """Structure the request so the static prefix is cacheable (R5.4/R5.5).

        ``cache_prefix`` is the run-stable leading portion of the final user
        message (by contract the message content equals ``cache_prefix +
        delta``). Behaviour by detected capability:

          * ``none``/disabled/no prefix → return ``messages`` unchanged. The
            content is already the plain combined prompt, so the request is
            byte-for-byte identical to sending the combined string (R5.5).
          * ``openai`` → return ``messages`` unchanged. Caching is automatic on
            the leading prefix, which already leads the content; the payload is
            therefore also byte-identical to the plain path.
          * ``anthropic`` → split the last user message whose content starts
            with ``cache_prefix`` into two content blocks, marking the prefix
            block with an explicit ``cache_control`` marker (R5.4).
        """
        if not enabled or not cache_prefix:
            return messages
        if self.cache_capability != CACHE_CAPABILITY_ANTHROPIC:
            # OpenAI-compatible (automatic caching) or unknown/unsupported: the
            # plain combined prompt already leads with the prefix — no change.
            return messages

        new_messages = list(messages)
        for i in range(len(new_messages) - 1, -1, -1):
            msg = new_messages[i]
            content = msg.get("content")
            if (
                msg.get("role") == "user"
                and isinstance(content, str)
                and content.startswith(cache_prefix)
            ):
                delta = content[len(cache_prefix):]
                blocks: List[Dict[str, Any]] = [
                    {
                        "type": "text",
                        "text": cache_prefix,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                if delta:
                    blocks.append({"type": "text", "text": delta})
                new_messages[i] = {**msg, "content": blocks}
                break
        return new_messages

    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate text from a prompt.

        Optional keyword ``cache_prefix`` may be supplied to mark the run-stable
        leading portion of ``prompt`` as cacheable (see
        :meth:`_apply_prompt_caching`). It is forwarded to
        :meth:`generate_with_context`.
        """
        return await self.generate_with_context(
            system_message=self.system_message,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )

    async def generate_with_context(
        self,
        system_message: str,
        messages: List[Dict[str, str]],
        *,
        cache_prefix: Optional[str] = None,
        cache_static_prefix: Optional[bool] = None,
        **kwargs,
    ) -> str:
        """Generate text using a system message and conversational context.

        Args:
            system_message: System prompt (omitted when empty).
            messages: Conversation messages.
            cache_prefix: Optional run-stable leading portion of the final user
                message to mark as cacheable. ``None`` sends a plain prompt.
            cache_static_prefix: Optional per-call override of the model's
                ``cache_static_prefix`` behaviour flag. ``None`` uses the model
                default.
        """
        # Resolve the effective caching behaviour and (optionally) restructure
        # the messages so the static prefix is cacheable (R5.4/R5.5).
        caching_enabled = (
            self.cache_static_prefix if cache_static_prefix is None else cache_static_prefix
        )
        messages = self._apply_prompt_caching(messages, cache_prefix, caching_enabled)

        # Prepare messages with system message.
        # Only include the system role when content is non-empty: some
        # providers (e.g. Groq) reject a system message with null content.
        formatted_messages = []
        if system_message:
            formatted_messages.append({"role": "system", "content": system_message})
        formatted_messages.extend(messages)

        # Set up generation parameters
        # Define OpenAI reasoning models that require max_completion_tokens
        # These models don't support temperature/top_p and use different parameters
        OPENAI_REASONING_MODEL_PREFIXES = (
            # O-series reasoning models
            "o1-",
            "o1",  # o1, o1-mini, o1-preview
            "o3-",
            "o3",  # o3, o3-mini, o3-pro
            "o4-",  # o4-mini
            # GPT-5 series are also reasoning models
            "gpt-5-",
            "gpt-5",  # gpt-5, gpt-5-mini, gpt-5-nano
            # The GPT OSS series are also reasoning models
            "gpt-oss-120b",
            "gpt-oss-20b",
        )

        # Check if this is an OpenAI reasoning model based on model name pattern
        # This works for all endpoints (OpenAI, Azure, OptiLLM, OpenRouter, etc.)
        model_lower = str(self.model).lower()
        is_openai_reasoning_model = model_lower.startswith(OPENAI_REASONING_MODEL_PREFIXES)

        if is_openai_reasoning_model:
            # For OpenAI reasoning models
            params = {
                "model": self.model,
                "messages": formatted_messages,
                "max_completion_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            # Add optional reasoning parameters if provided
            reasoning_effort = kwargs.get("reasoning_effort", self.reasoning_effort)
            if reasoning_effort is not None:
                params["reasoning_effort"] = reasoning_effort
            if "verbosity" in kwargs:
                params["verbosity"] = kwargs["verbosity"]
        else:
            # Standard parameters for all other models
            params = {
                "model": self.model,
                "messages": formatted_messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "top_p": kwargs.get("top_p", self.top_p),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }

            # Handle reasoning_effort for open source reasoning models.
            reasoning_effort = kwargs.get("reasoning_effort", self.reasoning_effort)
            if reasoning_effort is not None:
                params["reasoning_effort"] = reasoning_effort

        # Add seed parameter for reproducibility if configured
        # Skip seed parameter for Google AI Studio endpoint as it doesn't support it
        # Seed only makes sense for actual API calls
        seed = kwargs.get("seed", self.random_seed)
        if seed is not None and not self.manual_mode:
            if self.api_base == "https://generativelanguage.googleapis.com/v1beta/openai/":
                logger.warning(
                    "Skipping seed parameter as Google AI Studio endpoint doesn't support it. "
                    "Reproducibility may be limited."
                )
            else:
                params["seed"] = seed

        # Attempt the API call with retries
        retries = kwargs.get("retries", self.retries)
        retry_delay = kwargs.get("retry_delay", self.retry_delay)

        # Manual mode: no timeout unless explicitly passed by the caller
        if self.manual_mode:
            timeout = kwargs.get("timeout", None)
            return await self._manual_wait_for_answer(params, timeout=timeout)

        timeout = kwargs.get("timeout", self.timeout)

        for attempt in range(retries + 1):
            try:
                response = await asyncio.wait_for(self._call_api(params), timeout=timeout)
                return response
            except asyncio.TimeoutError:
                if attempt < retries:
                    logger.warning(f"Timeout on attempt {attempt + 1}/{retries + 1}. Retrying...")
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"All {retries + 1} attempts failed with timeout")
                    raise
            except Exception as e:
                if attempt < retries:
                    logger.warning(
                        f"Error on attempt {attempt + 1}/{retries + 1}: {str(e)}. Retrying..."
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    logger.error(f"All {retries + 1} attempts failed with error: {str(e)}")
                    raise

    async def _call_api(self, params: Dict[str, Any]) -> str:
        """Make the actual API call"""
        if self.client is None:
            raise RuntimeError("OpenAI client is not initialized (manual_mode enabled?)")

        # Use asyncio to run the blocking API call in a thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, lambda: self.client.chat.completions.create(**params)
        )
        # Record token usage for cost budgeting / audit (when the provider
        # returns it — OpenAI, Groq, and Google AI Studio all do).
        try:
            usage = getattr(response, "usage", None)
            if usage is not None:
                self.total_prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                self.total_completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
            self.api_call_count += 1
        except Exception:  # pragma: no cover - accounting must never break a run
            pass
        # Logging of system prompt, user message and response content
        logger = logging.getLogger(__name__)
        logger.debug(f"API parameters: {params}")
        logger.debug(f"API response: {response.choices[0].message.content}")
        return response.choices[0].message.content

    async def _manual_wait_for_answer(
        self, params: Dict[str, Any], timeout: Optional[Union[int, float]]
    ) -> str:
        """
        Manual mode: write a task JSON file and poll for *.answer.json
        If timeout is provided, we respect it; otherwise we wait indefinitely
        """

        if self.manual_queue_dir is None:
            raise RuntimeError("manual_queue_dir is not initialized")

        task_id = str(uuid.uuid4())
        messages = params.get("messages", [])
        display_prompt = _build_display_prompt(messages)

        task_payload: Dict[str, Any] = {
            "id": task_id,
            "created_at": _iso_now(),
            "model": params.get("model"),
            "display_prompt": display_prompt,
            "messages": messages,
            "meta": {
                "max_tokens": params.get("max_tokens"),
                "max_completion_tokens": params.get("max_completion_tokens"),
                "temperature": params.get("temperature"),
                "top_p": params.get("top_p"),
                "reasoning_effort": params.get("reasoning_effort"),
                "verbosity": params.get("verbosity"),
            },
        }

        task_path = self.manual_queue_dir / f"{task_id}.json"
        answer_path = self.manual_queue_dir / f"{task_id}.answer.json"

        _atomic_write_json(task_path, task_payload)
        logger.info(f"[manual_mode] Task enqueued: {task_path}")

        start = time.time()
        poll_interval = 0.5

        while True:
            if answer_path.exists():
                try:
                    data = json.loads(answer_path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"[manual_mode] Failed to parse answer JSON for {task_id}: {e}")
                    await asyncio.sleep(poll_interval)
                    continue

                answer = str(data.get("answer") or "")
                logger.info(f"[manual_mode] Answer received for {task_id}")
                return answer

            if timeout is not None and (time.time() - start) > float(timeout):
                raise asyncio.TimeoutError(
                    f"Manual mode timed out after {timeout} seconds waiting for answer of task {task_id}"
                )

            await asyncio.sleep(poll_interval)
