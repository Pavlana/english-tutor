"""Pydantic request/response models for the API."""

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
