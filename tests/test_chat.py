from unittest.mock import patch, MagicMock
from anthropic import InternalServerError
from fastapi.testclient import TestClient
from src.main import app

client = TestClient(app)

def test_health():
    #call the /health api
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_retries_on_500():
    with patch("src.api_client.client.messages.create") as mock_create:
        mock_create.side_effect = InternalServerError(
            message="server error",
            response=MagicMock(status_code=500), 
            body={},
        )
        response = client.post(
            "/chat", 
            json={"user_id": "test-user", "message": "hi"}
        )
        assert mock_create.call_count == 3
        assert response.json()['error'] == "api_unavailable"
        assert response.json()['task_status'] == "in_progress"
    