from pydantic import BaseModel


class ChatRequest(BaseModel):
    user_id: str
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    session_id: str
    agent: str
    message: str
    task_status: str
    error: str | None = None # None on success, error type on failure