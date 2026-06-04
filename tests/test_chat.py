from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from src.main import app
from src.models import AgentResult

client = TestClient(app)

_PATCH_HANDLE = "src.orchestrator.handle"
_PATCH_OPEN_SESSION = "src.routers.chat.get_open_session"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_response():
    """POST /chat returns 200 with message and task_status from orchestrator."""
    canned = AgentResult(
        message="Hello, student!",
        agent="listening",
        task_status="complete",
        usage=None,
    )
    fake_session = MagicMock()
    fake_session.session_id = "sess-abc"

    with (
        patch(_PATCH_HANDLE, new_callable=AsyncMock) as mock_handle,
        patch(_PATCH_OPEN_SESSION, return_value=fake_session),
    ):
        mock_handle.return_value = canned
        response = client.post(
            "/chat",
            json={"user_id": "test-user", "message": "hi"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Hello, student!"
    assert body["agent"] == "listening"
    assert body["task_status"] == "complete"
    assert body["error"] is None
    assert body["session_id"] == "sess-abc"


def test_retries_on_500():
    """Orchestrator failure returns api_unavailable error response."""
    with patch(_PATCH_HANDLE, new_callable=AsyncMock) as mock_handle:
        mock_handle.side_effect = RuntimeError("downstream failure")
        response = client.post(
            "/chat",
            json={"user_id": "test-user", "message": "hi"},
        )

    assert response.json()["error"] == "api_unavailable"
    assert response.json()["task_status"] == "in_progress"
