from unittest.mock import AsyncMock, MagicMock, patch

from anthropic import InternalServerError
from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_returns_response():
    """POST /chat returns 200 with message and task_status after api_client refactor."""
    fake_usage = MagicMock(input_tokens=10, output_tokens=20)
    fake_block = MagicMock(type="text", text="Hello, student!")
    fake_response = MagicMock(content=[fake_block], usage=fake_usage)

    with (
        patch(
            "src.api_client._client.messages.create", new_callable=AsyncMock
        ) as mock_create,
        patch("src.api_client.write_llm_call"),
    ):
        mock_create.return_value = fake_response
        response = client.post(
            "/chat",
            json={"user_id": "test-user", "message": "hi"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["message"] == "Hello, student!"
    assert body["agent"] == "orchestrator"
    assert body["task_status"] == "in_progress"
    assert body["error"] is None
    assert "session_id" in body


def test_retries_on_500():
    """Three SDK failures exhaust retries and return api_unavailable error."""
    with patch(
        "src.api_client._client.messages.create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.side_effect = InternalServerError(
            message="server error",
            response=MagicMock(status_code=500),
            body={},
        )
        response = client.post(
            "/chat",
            json={"user_id": "test-user", "message": "hi"},
        )
        assert mock_create.call_count == 3
        assert response.json()["error"] == "api_unavailable"
        assert response.json()["task_status"] == "in_progress"
