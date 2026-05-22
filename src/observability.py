"""Cost calculation and LLM call logging."""

from src.db.engine import get_session
from src.db.schemas import LlmCall

# Per-million-token rates: {model: (input_usd, output_usd)}
_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return the USD cost for a single API call.

    Args:
        model: Anthropic model ID.
        input_tokens: Number of input tokens billed.
        output_tokens: Number of output tokens billed.

    Returns:
        Cost in USD, rounded to 8 decimal places.
    """
    input_rate, output_rate = _RATES[model]
    return round(
        (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000,
        8,
    )


def write_llm_call(
    model: str,
    agent: str,
    session_id: str | None,
    usage: object,
    latency_ms: int,
) -> None:
    """Persist one LlmCall row to the database.

    Args:
        model: Anthropic model ID used for the call.
        agent: Label of the agent that made the call.
        session_id: Optional session identifier.
        usage: The usage object from the Anthropic response
            (must have .input_tokens and .output_tokens).
        latency_ms: Wall-clock latency of the API call in milliseconds.
    """
    input_tokens: int = usage.input_tokens  # type: ignore[union-attr]
    output_tokens: int = usage.output_tokens  # type: ignore[union-attr]
    row = LlmCall(
        model=model,
        agent=agent,
        session_id=session_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=compute_cost(model, input_tokens, output_tokens),
        latency_ms=latency_ms,
    )
    with get_session() as db:
        db.add(row)
        db.commit()
