"""Tests for src/topic_generator.py."""

from unittest.mock import AsyncMock, patch

import pytest

from src.topic_generator import generate_topic

_PROFILE = {
    "cefr_level": "B1",
    "interests": ["technology", "travel"],
    "grammar_gaps": ["passive voice"],
}

_LOG = {
    "grammar_gap_summary": "Needs work on passive voice.",
    "recommended_topic_tags": ["technology"],
    "session_notes": "Good progress.",
}

_PATCH = "src.topic_generator.call_anthropic"


@pytest.mark.asyncio
async def test_generate_topic_returns_mocked_string():
    with patch(_PATCH, new_callable=AsyncMock) as mock_call:
        mock_call.return_value = ("remote work", object())
        result = await generate_topic(_PROFILE, _LOG)

    assert result == "remote work"


@pytest.mark.asyncio
async def test_generate_topic_calls_opus_tier():
    with patch(_PATCH, new_callable=AsyncMock) as mock_call:
        mock_call.return_value = ("urban gardening", object())
        await generate_topic(_PROFILE, _LOG)

    _, kwargs = mock_call.call_args
    assert kwargs.get("tier") == "opus" or mock_call.call_args[0][1] == "opus"


@pytest.mark.asyncio
async def test_generate_topic_strips_whitespace():
    with patch(_PATCH, new_callable=AsyncMock) as mock_call:
        mock_call.return_value = ("  climate change  ", object())
        result = await generate_topic(_PROFILE, _LOG)

    assert result == "climate change"
