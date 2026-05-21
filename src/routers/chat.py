from fastapi import APIRouter
from src.models import ChatRequest, ChatResponse
from src.api_client import call_anthropic
from tenacity import RetryError
import uuid

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # No LLM logic yet — stub response only
    session_id = request.session_id or str(uuid.uuid4())
    try:
        reply = await call_anthropic(
            messages=[{"role": "user", "content": request.message}]
        )

        return ChatResponse(
            session_id=session_id,
            agent="orchestrator",
            message=reply,
            task_status="in_progress",
            error=None
        )
    except RetryError:
        return ChatResponse(
            session_id=session_id,
            agent="orchestrator",
            message="Something went wrong on our side. Please try again in a moment.",
            task_status="in_progress",
            error="api_unavailable" 
        )
