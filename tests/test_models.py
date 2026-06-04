"""Tests for Pydantic models in src/models.py."""

from src.models import AgentResult, ChatRequest, ChatResponse


def test_agent_result_fields_are_accessible():
    result = AgentResult(
        message="Great work!",
        agent="listening",
        task_status="complete",
        usage=None,
    )
    assert result.message == "Great work!"
    assert result.agent == "listening"
    assert result.task_status == "complete"
    assert result.usage is None


def test_agent_result_usage_can_hold_arbitrary_value():
    fake_usage = {"input_tokens": 10, "output_tokens": 20}
    result = AgentResult(
        message="ok",
        agent="grammar",
        task_status="skipped",
        usage=fake_usage,
    )
    assert result.usage == fake_usage


def test_chat_request_is_importable_and_unchanged():
    req = ChatRequest(user_id="u-1", message="hello")
    assert req.user_id == "u-1"
    assert req.message == "hello"
    assert req.session_id is None


def test_chat_response_is_importable_and_unchanged():
    resp = ChatResponse(
        session_id="sess-1",
        agent="orchestrator",
        message="Hi",
        task_status="in_progress",
    )
    assert resp.session_id == "sess-1"
    assert resp.error is None
