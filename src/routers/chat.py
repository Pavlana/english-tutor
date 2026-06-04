"""Chat router — thin HTTP layer, no business logic."""

import uuid

from fastapi import APIRouter

from src import orchestrator
from src.db.engine import get_session
from src.db.repo import get_open_session
from src.models import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Accept a user message, run the orchestrator, and return a tutor reply.

    Args:
        request: Parsed ChatRequest with user_id, message, and optional session_id.

    Returns:
        ChatResponse with the agent's message and the current session_id.
    """
    try:
        with get_session() as db_session:
            result = await orchestrator.handle(
                request.message, request.user_id, db_session
            )
            open_ts = get_open_session(request.user_id, db_session)

        session_id = (
            open_ts.session_id
            if open_ts is not None
            else (request.session_id or str(uuid.uuid4()))
        )
        return ChatResponse(
            session_id=session_id,
            agent=result.agent,
            message=result.message,
            task_status=result.task_status,
            error=None,
        )
    except Exception:  # noqa: BLE001
        return ChatResponse(
            session_id=request.session_id or str(uuid.uuid4()),
            agent="orchestrator",
            message="Something went wrong on our side. Please try again in a moment.",
            task_status="in_progress",
            error="api_unavailable",
        )
