"""Tier-aware async Anthropic client — the only path to the model."""

from typing import Any, Literal

from anthropic import APIConnectionError, AsyncAnthropic, InternalServerError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from src.config import settings

_client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

_TIER_CONFIG: dict[str, tuple[str, int, int]] = {
    "opus": (settings.model_opus, settings.max_tokens_opus, settings.timeout_opus),
    "sonnet": (
        settings.model_sonnet,
        settings.max_tokens_sonnet,
        settings.timeout_sonnet,
    ),
    "haiku": (
        settings.model_haiku,
        settings.max_tokens_haiku,
        settings.timeout_haiku,
    ),
}


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, (InternalServerError, APIConnectionError))


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retryable),
)
async def call_anthropic(
    messages: list[dict],
    tier: Literal["opus", "sonnet", "haiku"],
    agent: str,
    system: str | None = None,
    session_id: str | None = None,
) -> tuple[str, Any]:
    """Call the Anthropic API at the given tier and return (text, usage).

    Args:
        messages: Full conversation list sent to the API.
        tier: Model tier — determines model ID, max_tokens, and timeout.
        agent: Label for the calling agent (e.g. "onboarding", "listening").
        system: Optional system prompt.
        session_id: Optional session identifier for observability.

    Returns:
        A tuple of (text_content, response.usage).

    Raises:
        ValueError: If the response contains no text block.
    """
    model, max_tokens, timeout = _TIER_CONFIG[tier]

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "timeout": float(timeout),
    }
    if system:
        kwargs["system"] = system

    response = await _client.messages.create(**kwargs)

    for block in response.content:
        if block.type == "text":
            return block.text, response.usage

    raise ValueError("No text block in response")
