"""Chat router — thin HTTP layer, no business logic."""

import uuid

from fastapi import APIRouter
from tenacity import RetryError

from src.api_client import call_anthropic
from src.models import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Accept a user message and return a tutor reply."""
    session_id = request.session_id or str(uuid.uuid4())
    try:
        reply, _ = await call_anthropic(
            messages=[{"role": "user", "content": request.message}],
            tier="sonnet",
            agent="orchestrator",
        )

        return ChatResponse(
            session_id=session_id,
            agent="orchestrator",
            message=reply,
            task_status="in_progress",
            error=None,
        )
    except RetryError:
        return ChatResponse(
            session_id=session_id,
            agent="orchestrator",
            message="Something went wrong on our side. Please try again in a moment.",
            task_status="in_progress",
            error="api_unavailable",
        )
