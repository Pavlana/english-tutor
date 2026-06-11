"""Tests for src/tools/dictionary.py.

Each test mocks _fetch() so no real network calls are made.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.tools.dictionary import lookup

_FETCH = "src.tools.dictionary._fetch"

# A minimal realistic API response for the word "stringent".
_MOCK_RESPONSE: list[dict] = [
    {
        "word": "stringent",
        "meanings": [
            {
                "partOfSpeech": "adjective",
                "definitions": [
                    {
                        "definition": (
                            "(of regulations, requirements, or conditions)"
                            " strict, precise, and exacting."
                        ),
                        "example": "stringent safety requirements",
                    }
                ],
            }
        ],
    }
]

# A response with no example field.
_MOCK_NO_EXAMPLE: list[dict] = [
    {
        "word": "equitable",
        "meanings": [
            {
                "partOfSpeech": "adjective",
                "definitions": [
                    {
                        "definition": "fair and impartial.",
                    }
                ],
            }
        ],
    }
]

# A response with no partOfSpeech.
_MOCK_NO_POS: list[dict] = [
    {
        "word": "austerity",
        "meanings": [
            {
                "definitions": [
                    {
                        "definition": "sternness or severity of manner or attitude.",
                        "example": "he was noted for his austerity",
                    }
                ],
            }
        ],
    }
]


@pytest.mark.asyncio
async def test_lookup_returns_entry_on_success() -> None:
    """lookup() returns a populated DictionaryEntry when _fetch succeeds."""
    with patch(_FETCH, new=AsyncMock(return_value=_MOCK_RESPONSE)):
        result = await lookup("stringent")

    assert result is not None
    assert result["word"] == "stringent"
    assert "strict" in result["definition"]
    assert result["example"] == "stringent safety requirements"
    assert result["part_of_speech"] == "adjective"


@pytest.mark.asyncio
async def test_lookup_returns_none_when_fetch_returns_none() -> None:
    """lookup() returns None when _fetch returns None (network/timeout/404)."""
    with patch(_FETCH, new=AsyncMock(return_value=None)):
        result = await lookup("xyznotaword")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_returns_none_on_empty_list() -> None:
    """lookup() returns None when _fetch returns an empty list."""
    with patch(_FETCH, new=AsyncMock(return_value=[])):
        result = await lookup("something")

    assert result is None


@pytest.mark.asyncio
async def test_lookup_example_is_none_when_absent() -> None:
    """lookup() sets example=None when the API response has no example."""
    with patch(_FETCH, new=AsyncMock(return_value=_MOCK_NO_EXAMPLE)):
        result = await lookup("equitable")

    assert result is not None
    assert result["example"] is None
    assert result["definition"] == "fair and impartial."


@pytest.mark.asyncio
async def test_lookup_part_of_speech_is_none_when_absent() -> None:
    """lookup() sets part_of_speech=None when the meaning has no partOfSpeech."""
    with patch(_FETCH, new=AsyncMock(return_value=_MOCK_NO_POS)):
        result = await lookup("austerity")

    assert result is not None
    assert result["part_of_speech"] is None
    assert result["word"] == "austerity"


@pytest.mark.asyncio
async def test_lookup_returns_none_on_malformed_response() -> None:
    """lookup() returns None when the API response is structurally unexpected."""
    malformed: list[dict] = [{"word": "broken", "meanings": []}]
    with patch(_FETCH, new=AsyncMock(return_value=malformed)):
        result = await lookup("broken")

    assert result is None
