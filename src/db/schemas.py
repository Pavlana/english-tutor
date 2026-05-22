"""SQLModel table definitions."""

from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


class LlmCall(SQLModel, table=True):
    """One row per Anthropic API call — the core observability record."""

    id: int | None = Field(default=None, primary_key=True)
    model: str
    agent: str
    session_id: str | None = None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
