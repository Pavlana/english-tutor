"""Content acquisition tool — YouTube → Guardian → topic-only fallback chain.

acquire_content() tries each rung in order and returns on the first success.
The topic-only rung always succeeds, so the function never raises.

No LLM calls are made anywhere in this module.
"""

from dataclasses import dataclass

import httpx
import trafilatura
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from src.config import settings

# Guardian content-search API endpoint.
_GUARDIAN_API_URL: str = "https://content.guardianapis.com/search"

# Request timeout for external HTTP calls (seconds).
_HTTP_TIMEOUT: int = 10


@dataclass
class ContentResult:
    """Result from a content acquisition attempt.

    Attributes:
        source: Display name of the source (e.g. ``"YouTube"``, ``"Guardian"``,
            ``"topic-only"``).
        text: Full text content — transcript for video, article body for article,
            or empty string for topic-only.
        url: Link to the source; ``None`` for topic-only.
        modality: One of ``"video"``, ``"article"``, ``"topic-only"``.
        title: Human-readable title of the content; ``None`` if unavailable.
    """

    source: str
    text: str
    url: str | None
    modality: str
    title: str | None = None


def _try_youtube(topic: str) -> ContentResult | None:
    """Search YouTube for the topic and fetch an English transcript.

    Uses yt-dlp for search (top result only) and youtube-transcript-api
    to fetch the transcript. Falls through on any failure.

    Args:
        topic: The session topic string to search for.

    Returns:
        ContentResult with modality="video" on success, or None on any failure.
    """
    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"ytsearch1:{topic}", download=False)

        if not info or not info.get("entries"):
            return None

        entry = info["entries"][0]
        video_id: str | None = entry.get("id")
        if not video_id:
            return None

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        video_title: str | None = entry.get("title")
    except Exception:  # noqa: BLE001 — yt-dlp raises many undocumented exceptions
        return None

    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(video_id, languages=["en"])
        text = " ".join(snippet.text for snippet in transcript)
    except Exception:  # noqa: BLE001 — transcript may be disabled or unavailable
        return None

    return ContentResult(
        source="YouTube",
        text=text,
        url=video_url,
        modality="video",
        title=video_title,
    )


def _try_guardian(topic: str) -> ContentResult | None:
    """Query the Guardian API for the topic and extract the article body.

    Requires ``settings.GUARDIAN_API_KEY`` to be non-empty. Falls through
    if the key is absent, the request fails, or no results are returned.
    Uses trafilatura to extract clean text from the article HTML.

    Args:
        topic: The session topic string to query.

    Returns:
        ContentResult with modality="article" on success, or None on any failure.
    """
    if not settings.GUARDIAN_API_KEY:
        return None

    try:
        response = httpx.get(
            _GUARDIAN_API_URL,
            params={
                "q": topic,
                "api-key": settings.GUARDIAN_API_KEY,
                "show-fields": "body",
                "page-size": "1",
            },
            timeout=_HTTP_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:  # noqa: BLE001 — network errors, bad JSON, non-2xx status
        return None

    results = data.get("response", {}).get("results", [])
    if not results:
        return None

    article = results[0]
    article_url: str = article.get("webUrl", "")
    article_title: str | None = article.get("webTitle") or None
    html_body: str = article.get("fields", {}).get("body", "")

    if not html_body:
        return None

    text = trafilatura.extract(html_body) or ""
    if not text:
        return None

    return ContentResult(
        source="Guardian",
        text=text,
        url=article_url or None,
        modality="article",
        title=article_title,
    )


def _try_topic_only() -> ContentResult:
    """Return a topic-only ContentResult. Always succeeds; never raises.

    Returns:
        ContentResult with modality="topic-only", empty text, and no URL.
        Downstream agents generate content from internal knowledge when
        text is empty.
    """
    return ContentResult(
        source="topic-only",
        text="",
        url=None,
        modality="topic-only",
    )


async def acquire_content(topic: str) -> ContentResult:
    """Acquire content for the given topic via a three-rung fallback chain.

    Tries rungs in order: YouTube → Guardian → topic-only. Returns on the
    first success. The topic-only rung always succeeds, so this function
    never raises.

    Single-user MVP note: yt-dlp and youtube-transcript-api are synchronous
    libraries called directly here, consistent with the pattern used for
    sync DB writes elsewhere in the codebase.

    Args:
        topic: The session topic string (e.g. ``"climate change"``).

    Returns:
        ContentResult from the first successful rung.
    """
    result = _try_youtube(topic)
    if result is not None:
        return result

    result = _try_guardian(topic)
    if result is not None:
        return result

    return _try_topic_only()
