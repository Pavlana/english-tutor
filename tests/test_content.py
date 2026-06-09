"""Fallback-rung tests for src/tools/content.py.

Each rung (_try_youtube, _try_guardian, _try_topic_only) is tested in
isolation by mocking external library calls. No real network requests are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.tools.content import ContentResult, _try_topic_only, acquire_content

# Patch targets — module-level references inside src.tools.content.
_YDL = "src.tools.content.yt_dlp.YoutubeDL"
_TRANSCRIPT_API = "src.tools.content.YouTubeTranscriptApi"
_HTTPX_GET = "src.tools.content.httpx.get"
_TRAFILATURA = "src.tools.content.trafilatura.extract"
_SETTINGS = "src.tools.content.settings"

_ARTICLE_HTML = "<p>Article body text about regulations.</p>"
_ARTICLE_TEXT = "Article body text about regulations."
_TRANSCRIPT_TEXT = "Hello and welcome to today's video on regulations."


def _make_ydl_mock(video_id: str | None) -> MagicMock:
    """Return a mock YoutubeDL context manager.

    If video_id is None, extract_info returns empty entries.
    """
    ydl = MagicMock()
    ydl.__enter__ = MagicMock(return_value=ydl)
    ydl.__exit__ = MagicMock(return_value=False)
    if video_id:
        ydl.extract_info.return_value = {"entries": [{"id": video_id}]}
    else:
        ydl.extract_info.return_value = {"entries": []}
    return ydl


def _make_transcript_mock(text: str) -> MagicMock:
    """Return a mock YouTubeTranscriptApi instance that returns one snippet."""
    snippet = MagicMock()
    snippet.text = text
    api_instance = MagicMock()
    api_instance.fetch.return_value = [snippet]
    api_class = MagicMock(return_value=api_instance)
    return api_class


def _make_guardian_response(
    article_text: str, url: str = "https://example.com/article"
) -> MagicMock:
    """Return a mock httpx response with one Guardian article result."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "response": {
            "results": [
                {
                    "webUrl": url,
                    "fields": {"body": _ARTICLE_HTML},
                }
            ]
        }
    }
    return response


# ===========================================================================
# Rung 1 — YouTube
# ===========================================================================


@pytest.mark.asyncio
async def test_youtube_success() -> None:
    """yt-dlp finds a video and transcript is available — return modality='video'."""
    with (
        patch(_YDL, return_value=_make_ydl_mock("vid123")),
        patch(_TRANSCRIPT_API, _make_transcript_mock(_TRANSCRIPT_TEXT)),
    ):
        result = await acquire_content("regulations")

    assert result.modality == "video"
    assert result.source == "YouTube"
    assert _TRANSCRIPT_TEXT in result.text
    assert result.url == "https://www.youtube.com/watch?v=vid123"


# ===========================================================================
# Rung 1 → Rung 2 fallthrough
# ===========================================================================


@pytest.mark.asyncio
async def test_youtube_transcript_unavailable_falls_to_guardian() -> None:
    """When the transcript API raises, acquire_content falls through to Guardian."""
    transcript_api = MagicMock()
    transcript_api.return_value.fetch.side_effect = Exception("no transcript")

    mock_settings = MagicMock()
    mock_settings.GUARDIAN_API_KEY = "test-key"

    with (
        patch(_YDL, return_value=_make_ydl_mock("vid123")),
        patch(_TRANSCRIPT_API, transcript_api),
        patch(_HTTPX_GET, return_value=_make_guardian_response(_ARTICLE_TEXT)),
        patch(_TRAFILATURA, return_value=_ARTICLE_TEXT),
        patch(_SETTINGS, mock_settings),
    ):
        result = await acquire_content("regulations")

    assert result.modality == "article"
    assert result.source == "Guardian"


@pytest.mark.asyncio
async def test_youtube_no_video_falls_to_guardian() -> None:
    """When yt-dlp returns no entries, acquire_content falls through to Guardian."""
    mock_settings = MagicMock()
    mock_settings.GUARDIAN_API_KEY = "test-key"

    with (
        patch(_YDL, return_value=_make_ydl_mock(None)),
        patch(_HTTPX_GET, return_value=_make_guardian_response(_ARTICLE_TEXT)),
        patch(_TRAFILATURA, return_value=_ARTICLE_TEXT),
        patch(_SETTINGS, mock_settings),
    ):
        result = await acquire_content("regulations")

    assert result.modality == "article"
    assert result.source == "Guardian"


# ===========================================================================
# Rung 2 → Rung 3 fallthrough
# ===========================================================================


@pytest.mark.asyncio
async def test_guardian_unavailable_falls_to_topic_only() -> None:
    """When yt-dlp fails and GUARDIAN_API_KEY is empty, return modality='topic-only'."""
    mock_settings = MagicMock()
    mock_settings.GUARDIAN_API_KEY = ""

    with (
        patch(_YDL, side_effect=Exception("ydl error")),
        patch(_SETTINGS, mock_settings),
    ):
        result = await acquire_content("regulations")

    assert result.modality == "topic-only"
    assert result.text == ""
    assert result.url is None


# ===========================================================================
# Rung 3 — topic-only direct call
# ===========================================================================


def test_topic_only_always_succeeds() -> None:
    """_try_topic_only must always return a topic-only ContentResult and never raise."""
    result = _try_topic_only()

    assert isinstance(result, ContentResult)
    assert result.modality == "topic-only"
    assert result.source == "topic-only"
    assert result.text == ""
    assert result.url is None
