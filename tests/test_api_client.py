from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings


@pytest.mark.asyncio
async def test_call_anthropic_returns_text_and_usage():
    fake_usage = MagicMock(input_tokens=10, output_tokens=20)
    fake_block = MagicMock(type="text", text="hello")
    fake_response = MagicMock(content=[fake_block], usage=fake_usage)

    with (
        patch(
            "src.api_client._client.messages.create", new_callable=AsyncMock
        ) as mock_create,
        patch("src.api_client.write_llm_call"),
    ):
        mock_create.return_value = fake_response

        from src.api_client import call_anthropic

        result = await call_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            tier="sonnet",
            agent="test",
        )

    assert isinstance(result, tuple)
    text, usage = result
    assert isinstance(text, str)
    assert usage is fake_usage

    _, call_kwargs = mock_create.call_args
    assert call_kwargs["model"] == settings.model_sonnet


@pytest.mark.asyncio
async def test_call_anthropic_writes_llm_call():
    fake_usage = MagicMock(input_tokens=10, output_tokens=20)
    fake_block = MagicMock(type="text", text="hello")
    fake_response = MagicMock(content=[fake_block], usage=fake_usage)

    with (
        patch(
            "src.api_client._client.messages.create", new_callable=AsyncMock
        ) as mock_create,
        patch("src.api_client.write_llm_call") as mock_write,
    ):
        mock_create.return_value = fake_response

        from src.api_client import call_anthropic

        await call_anthropic(
            messages=[{"role": "user", "content": "hi"}],
            tier="sonnet",
            agent="test_agent",
            session_id="sess-123",
        )

    mock_write.assert_called_once()
    args = mock_write.call_args[0]
    assert args[0] == settings.model_sonnet  # model
    assert args[1] == "test_agent"  # agent
    assert args[2] == "sess-123"  # session_id
    assert args[3] is fake_usage  # usage
    assert isinstance(args[4], int)  # latency_ms
    assert args[4] >= 0
