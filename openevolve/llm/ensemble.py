"""
Model ensemble for LLMs
"""

import asyncio
import logging
import random
from typing import Dict, List, Optional

from openevolve.llm.base import (
    LLMInterface,
    extract_patch_from_response,
    retry_system_message,
)
from openevolve.llm.openai import OpenAILLM
from openevolve.config import LLMModelConfig

logger = logging.getLogger(__name__)

# Retry parameters for patch generation (Requirements 2.5, 14.1)
_PATCH_MAX_RETRIES = 3
_PATCH_BACKOFF_BASE = 1.0  # seconds; doubles each attempt: 1s, 2s, 4s


class LLMEnsemble:
    """Ensemble of LLMs"""

    def __init__(self, models_cfg: List[LLMModelConfig]):
        self.models_cfg = models_cfg

        # Initialize models from the configuration
        self.models = [
            model_cfg.init_client(model_cfg) if model_cfg.init_client else OpenAILLM(model_cfg)
            for model_cfg in models_cfg
        ]

        # Extract and normalize model weights
        self.weights = [model.weight for model in models_cfg]
        total = sum(self.weights)
        self.weights = [w / total for w in self.weights]

        # Set up random state for deterministic model selection
        self.random_state = random.Random()
        if (
            models_cfg
            and hasattr(models_cfg[0], "random_seed")
            and models_cfg[0].random_seed is not None
        ):
            self.random_state.seed(models_cfg[0].random_seed)
            logger.debug(
                f"LLMEnsemble: Set random seed to {models_cfg[0].random_seed}"
            )

        if len(models_cfg) > 1 or not hasattr(logger, "_ensemble_logged"):
            logger.info(
                "Initialized LLM ensemble with models: "
                + ", ".join(
                    f"{model.name} (weight: {weight:.2f})"
                    for model, weight in zip(models_cfg, self.weights)
                )
            )
            logger._ensemble_logged = True

    # ------------------------------------------------------------------
    # Core generation
    # ------------------------------------------------------------------

    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate text using a randomly selected model based on weights"""
        model = self._sample_model()
        return await model.generate(prompt, **kwargs)

    async def generate_with_context(
        self, system_message: str, messages: List[Dict[str, str]], **kwargs
    ) -> str:
        """Generate text using a system message and conversational context"""
        model = self._sample_model()
        return await model.generate_with_context(system_message, messages, **kwargs)

    def _sample_model(self) -> LLMInterface:
        """Sample a model from the ensemble based on weights"""
        index = self.random_state.choices(range(len(self.models)), weights=self.weights, k=1)[0]
        sampled_model = self.models[index]
        logger.info(f"Sampled model: {vars(sampled_model)['model']}")
        return sampled_model

    async def generate_multiple(self, prompt: str, n: int, **kwargs) -> List[str]:
        """Generate multiple texts in parallel"""
        tasks = [self.generate(prompt, **kwargs) for _ in range(n)]
        return await asyncio.gather(*tasks)

    async def parallel_generate(self, prompts: List[str], **kwargs) -> List[str]:
        """Generate responses for multiple prompts in parallel"""
        tasks = [self.generate(prompt, **kwargs) for prompt in prompts]
        return await asyncio.gather(*tasks)

    async def generate_all_with_context(
        self, system_message: str, messages: List[Dict[str, str]], **kwargs
    ) -> str:
        """Generate text using all available models"""
        responses = []
        for model in self.models:
            responses.append(
                await model.generate_with_context(system_message, messages, **kwargs)
            )
        return responses

    # ------------------------------------------------------------------
    # Patch generation — Task 6.2 (Requirements 2.3, 2.4, 2.5, 14.1)
    # ------------------------------------------------------------------

    async def generate_patch(
        self,
        prompt: str,
        *,
        max_retries: int = _PATCH_MAX_RETRIES,
        backoff_base: float = _PATCH_BACKOFF_BASE,
        **kwargs,
    ) -> Optional[str]:
        """Generate an LLM response and extract a unified-diff patch.

        Calls the ensemble, extracts a patch via
        :func:`~openevolve.llm.base.extract_patch_from_response`, and retries
        with clarifying instructions when the response lacks a valid patch.

        Retry schedule (exponential back-off):
          attempt 1 → wait backoff_base * 1 s
          attempt 2 → wait backoff_base * 2 s
          attempt 3 → wait backoff_base * 4 s

        Args:
            prompt: Full prompt string.
            max_retries: Maximum number of additional attempts (default 3).
            backoff_base: Base delay in seconds (default 1 s).
            **kwargs: Forwarded to :meth:`generate`.

        Returns:
            Extracted patch string, or ``None`` if all attempts failed.
        """
        current_prompt = prompt
        last_error = "no valid patch found in response"

        for attempt in range(max_retries + 1):
            if attempt > 0:
                delay = backoff_base * (2 ** (attempt - 1))
                logger.info(
                    "generate_patch: retry %d/%d after %.1fs (last error: %s)",
                    attempt, max_retries, delay, last_error,
                )
                await asyncio.sleep(delay)
                current_prompt = retry_system_message(prompt, last_error)

            try:
                response = await self.generate(current_prompt, **kwargs)
            except Exception as exc:
                last_error = f"LLM API error: {exc}"
                logger.warning("generate_patch attempt %d failed: %s", attempt + 1, exc)
                continue

            patch = extract_patch_from_response(response)
            if patch is not None:
                logger.info(
                    "generate_patch: valid patch extracted on attempt %d", attempt + 1
                )
                return patch

            last_error = "no valid unified diff found in response"
            logger.warning(
                "generate_patch attempt %d: no valid patch in response (len=%d)",
                attempt + 1, len(response),
            )

        logger.error(
            "generate_patch: all %d attempt(s) failed. Last error: %s",
            max_retries + 1, last_error,
        )
        return None

    async def retry_with_clarification(
        self,
        original_prompt: str,
        error: str,
        *,
        max_retries: int = _PATCH_MAX_RETRIES,
        backoff_base: float = _PATCH_BACKOFF_BASE,
        **kwargs,
    ) -> Optional[str]:
        """Retry patch generation with clarifying error context injected.

        Args:
            original_prompt: The prompt used in the failed attempt.
            error: Description of the failure to feed back to the LLM.
            max_retries: Maximum additional attempts (default 3).
            backoff_base: Base exponential back-off delay in seconds.
            **kwargs: Forwarded to :meth:`generate`.

        Returns:
            Extracted patch string, or ``None`` if all retries failed.
        """
        clarified_prompt = retry_system_message(original_prompt, error)
        return await self.generate_patch(
            clarified_prompt,
            max_retries=max_retries,
            backoff_base=backoff_base,
            **kwargs,
        )
