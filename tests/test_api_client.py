from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import settings


@pytest.mark.asyncio
async def test_call_anthropic_returns_text_and_usage():
    fake_usage = MagicMock()
    fake_block = MagicMock(type="text", text="hello")
    fake_response = MagicMock(content=[fake_block], usage=fake_usage)

    with patch(
        "src.api_client._client.messages.create", new_callable=AsyncMock
    ) as mock_create:
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
