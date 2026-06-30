"""
Base LLM interface
"""

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


# Unified diff markers required for a valid patch.
_DIFF_START_RE = re.compile(r"^(?:---|\+\+\+|@@|diff --git)", re.MULTILINE)

# Code-fence patterns: ```diff, ```patch, or bare ``` (fallback).
_FENCE_RE = re.compile(
    r"```(?:diff|patch)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def extract_patch_from_response(response: str) -> Optional[str]:
    """Extract a unified-diff patch from an LLM response string.

    Search strategy (in order):
    1. Look for fenced code blocks labelled ``diff`` or ``patch``.
    2. Fall back to any bare ``` fence whose content looks like a patch.
    3. Return ``None`` if no valid patch is found.

    A candidate is accepted only when it contains at least one line starting
    with ``---``, ``+++``, or ``@@`` (unified diff markers).

    Args:
        response: Raw text returned by the LLM.

    Returns:
        The extracted patch string (without fences), or ``None``.

    Requirements: 2.3, 2.4
    """
    if not response:
        return None

    for match in _FENCE_RE.finditer(response):
        candidate = match.group(1).strip()
        if _DIFF_START_RE.search(candidate):
            return candidate

    return None


def retry_system_message(original_prompt: str, error: str) -> str:
    """Build a clarifying system message for a failed patch generation attempt.

    Appended to the original prompt so the LLM knows what went wrong and can
    avoid the same mistake.

    Args:
        original_prompt: The prompt that produced an unusable response.
        error: Short description of what failed (e.g. "no valid patch found").

    Returns:
        Augmented prompt string.

    Requirements: 2.5
    """
    return (
        f"{original_prompt}\n\n"
        "IMPORTANT: Your previous response did not contain a valid unified diff "
        f"patch ({error}). Please try again.\n"
        "Requirements:\n"
        "  • Wrap the patch in a ```diff ... ``` code block.\n"
        "  • The patch MUST start with '--- a/<file>' and '+++ b/<file>' lines.\n"
        "  • Include at least one @@ hunk header.\n"
        "  • Output ONLY the patch block — no prose before or after."
    )


class LLMInterface(ABC):
    """Abstract base class for LLM interfaces"""

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """Generate text from a prompt"""
        pass

    @abstractmethod
    async def generate_with_context(
        self, system_message: str, messages: List[Dict[str, str]], **kwargs
    ) -> str:
        """Generate text using a system message and conversational context"""
        pass
