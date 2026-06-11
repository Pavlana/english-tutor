"""Dictionary lookup tool — dictionaryapi.dev (Free Dictionary API).

lookup() queries the Free Dictionary API for a single English word and
returns its first definition, part of speech, and usage example.

No LLM calls and no API key are required. Returns None gracefully on any
failure: 404 (word not found), timeout, network error, or parse error.
"""

from typing import TypedDict

import httpx

# Base URL for the Free Dictionary API.
_DICT_API_BASE: str = "https://api.dictionaryapi.dev/api/v2/entries/en"

# HTTP request timeout (seconds).
_HTTP_TIMEOUT: int = 5


class DictionaryEntry(TypedDict):
    """A parsed dictionary entry from the Free Dictionary API.

    Attributes:
        word: The looked-up word (normalised by the API).
        definition: The first definition found for the word.
        example: The first usage example, or None if absent.
        part_of_speech: Grammatical category (e.g. "noun", "verb"), or None
            if absent.
    """

    word: str
    definition: str
    example: str | None
    part_of_speech: str | None


async def _fetch(word: str) -> list[dict] | None:
    """Fetch raw JSON from the Free Dictionary API for a single word.

    Args:
        word: The English word to look up.

    Returns:
        The parsed JSON response (list of entry dicts) on HTTP 200,
        or None on any error (404, timeout, network failure, non-JSON body).
    """
    url = f"{_DICT_API_BASE}/{word}"
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(url)
    except (httpx.TimeoutException, httpx.RequestError):
        return None

    if response.status_code != 200:
        return None

    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


async def lookup(word: str) -> DictionaryEntry | None:
    """Return a parsed dictionary entry for an English word, or None.

    Queries the Free Dictionary API (dictionaryapi.dev) and extracts the
    first definition, part of speech, and usage example from the response.
    Returns None if the word is not found, the request fails, or the
    response cannot be parsed.

    Args:
        word: The English word to look up.

    Returns:
        A DictionaryEntry with the word, definition, example, and
        part_of_speech fields, or None if the lookup fails for any reason.
    """
    data = await _fetch(word)
    if not data or not isinstance(data, list):
        return None

    try:
        entry = data[0]
        meanings = entry.get("meanings", [])
        if not meanings:
            return None

        first_meaning = meanings[0]
        part_of_speech: str | None = first_meaning.get("partOfSpeech") or None

        definitions = first_meaning.get("definitions", [])
        if not definitions:
            return None

        first_def = definitions[0]
        definition: str = first_def.get("definition", "")
        if not definition:
            return None

        example: str | None = first_def.get("example") or None

        return DictionaryEntry(
            word=entry.get("word", word),
            definition=definition,
            example=example,
            part_of_speech=part_of_speech,
        )
    except (KeyError, IndexError, TypeError):
        return None
