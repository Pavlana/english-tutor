"""Pydantic request/response models for the API."""

from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Incoming chat request from the client."""

    user_id: str
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Outgoing chat response returned to the client."""

    session_id: str
    agent: str
    message: str
    task_status: str
    error: str | None = None  # None on success, error type on failure


class AgentResult(BaseModel):
    """Shared return contract for every agent in the system.

    Every agent function must return exactly this type. The shape is frozen
    and will not change in later phases.

    Attributes:
        message: The text to send back to the user.
        agent: Which agent produced this result (e.g. "onboarding", "listening").
        task_status: Status of the task just handled; must be one of the
            TASK_STATUSES values ("not_started", "in_progress", "complete",
            "skipped").
        usage: Raw usage object from the Anthropic SDK, or None if the agent
            made no model call (stubs return None).
    """

    message: str
    agent: str
    task_status: str
    usage: Any = None
