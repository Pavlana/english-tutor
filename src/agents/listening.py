"""Listening agent — content acquisition, vocabulary extraction, comprehension Q&A.

Flow per turn:
  turn_count == 0  → acquire content, extract vocab, generate questions,
                     hand off to run_turn_loop (which returns "in_progress").
  turn_count >= 1  → run_turn_loop evaluates the user's answers; on
                     "complete", generate and store the final summary.
  topic-only       → skip immediately, return task_status="skipped".

All state (questions, content text, vocab) is persisted in
tasks["listening"] so every turn is stateless between HTTP requests.
"""

import json
from pathlib import Path
from typing import Any

from sqlmodel import Session as DBSession

from src.agents.base import run_turn_loop
from src.api_client import call_anthropic
from src.db.repo import set_task_status, update_task, upsert_vocabulary
from src.db.schemas import TutorSession
from src.models import AgentResult
from src.tools.content import acquire_content

# ---------------------------------------------------------------------------
# Prompt files — loaded once at import time from src/prompts/.
# ---------------------------------------------------------------------------

_PROMPTS_DIR: Path = Path(__file__).parent.parent / "prompts"

_VOCAB_EXTRACTION_PROMPT: str = (_PROMPTS_DIR / "vocab_extraction.txt").read_text()

_COMPREHENSION_QUESTIONS_PROMPT: str = (
    _PROMPTS_DIR / "comprehension_questions.txt"
).read_text()

_ANSWER_EVALUATION_PROMPT: str = (_PROMPTS_DIR / "answer_evaluation.txt").read_text()

_LISTENING_SUMMARY_PROMPT: str = (_PROMPTS_DIR / "listening_summary.txt").read_text()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOPIC_ONLY_MESSAGE: str = (
    "I couldn't find a video or article for this topic. "
    "We'll work directly from the topic — moving on to writing."
)

# Fallback when the evaluator returns unparseable JSON.
_EVAL_FALLBACK: dict[str, Any] = {
    "acceptable": True,
    "feedback": "Good effort — moving on.",
    "summary": "Comprehension task completed.",
}

# Characters of source text stored in the DB for evaluation context.
# Keeps tasks JSON manageable while giving the evaluator useful context.
_MAX_STORED_TEXT_CHARS: int = 4000


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _extract_vocabulary(
    text: str,
    session: TutorSession,
    db_session: DBSession,
) -> tuple[list[str], Any]:
    """Extract 5 vocabulary items from text and persist them to the DB.

    Calls Haiku with the vocab-extraction prompt. Parses the JSON array
    response. Persists each word via repo.upsert_vocabulary. Falls back to
    an empty list if JSON parsing fails so the agent is never blocked.

    Args:
        text: Source text to extract vocabulary from.
        session: Active TutorSession (provides user_id, session_id, topic).
        db_session: Active database session.

    Returns:
        Tuple of (word_list, usage) where word_list may be empty on parse failure.
    """
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": text}],
        tier="haiku",
        agent="vocab_extractor",
        system=_VOCAB_EXTRACTION_PROMPT,
        session_id=session.session_id,
    )
    try:
        words: list[str] = json.loads(raw)
        if not isinstance(words, list):
            words = []
    except (json.JSONDecodeError, ValueError):
        words = []

    for word in words:
        upsert_vocabulary(
            user_id=session.user_id,
            word=str(word),
            session_id=session.session_id,
            session=db_session,
            topic_tags=[session.topic],
        )

    return words, usage


async def _generate_questions(
    text: str,
    vocab_list: list[str],
    session_id: str | None,
) -> tuple[str, Any]:
    """Generate 3–5 comprehension questions from the source text.

    Calls Sonnet with the comprehension-questions prompt. Returns the raw
    model response (question text) to be shown directly to the user.

    Args:
        text: Source text to base questions on.
        vocab_list: Vocabulary words extracted from the text.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (questions_text, usage).
    """
    user_content = f"Text:\n{text}\n\nVocabulary: {', '.join(vocab_list)}"
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="question_generator",
        system=_COMPREHENSION_QUESTIONS_PROMPT,
        session_id=session_id,
    )
    return raw, usage


async def _evaluate_response(
    user_message: str,
    questions: str,
    content_text: str,
    session_id: str | None,
) -> tuple[dict[str, Any], Any]:
    """Evaluate the user's answers using Haiku as a judge.

    Calls Haiku with the answer-evaluation prompt. Parses the JSON response.
    Falls back to _EVAL_FALLBACK on parse failure so the loop always completes.

    Args:
        user_message: The user's answer text for this turn.
        questions: The comprehension questions that were asked.
        content_text: Source text used as grading context.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (eval_dict, usage). eval_dict has keys: acceptable (bool),
        feedback (str), summary (str | None).
    """
    user_content = (
        f"Questions:\n{questions}\n\n"
        f"Source text:\n{content_text}\n\n"
        f"User answers:\n{user_message}"
    )
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="haiku",
        agent="answer_evaluator",
        system=_ANSWER_EVALUATION_PROMPT,
        session_id=session_id,
    )
    try:
        result: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = dict(_EVAL_FALLBACK)
    return result, usage


async def _generate_summary(
    questions: str,
    content_text: str,
    session_id: str | None,
) -> tuple[str, Any]:
    """Generate a plain-text summary of the listening task.

    Calls Sonnet with the listening-summary prompt.

    Args:
        questions: The comprehension questions that were asked.
        content_text: Source text for context.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (summary_text, usage).
    """
    user_content = f"Questions:\n{questions}\n\nContent:\n{content_text}"
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="listening_summary",
        system=_LISTENING_SUMMARY_PROMPT,
        session_id=session_id,
    )
    return raw, usage


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Run one turn of the listening agent.

    On turn_count == 0: acquires content, extracts vocabulary, generates
    comprehension questions, and hands off to run_turn_loop.
    On subsequent turns: delegates directly to run_turn_loop.
    On run_turn_loop completion: generates and stores the final summary.

    All intermediate state (questions, content text) is persisted in
    tasks["listening"] so each call is fully stateless.

    Args:
        message: Raw text sent by the user for this turn.
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress", "complete", or "skipped".
    """
    if session.turn_count == 0:
        content = await acquire_content(session.topic)
        update_task(
            session.session_id,
            "listening",
            {
                "url": content.url,
                "modality": content.modality,
                "content_text": content.text[:_MAX_STORED_TEXT_CHARS],
            },
            db_session,
        )
        db_session.refresh(session)

        if content.modality == "topic-only":
            set_task_status(session.session_id, "listening", "skipped", db_session)
            return AgentResult(
                message=_TOPIC_ONLY_MESSAGE,
                agent="listening",
                task_status="skipped",
                usage=None,
            )

        vocab_list, _ = await _extract_vocabulary(content.text, session, db_session)
        questions_text, _ = await _generate_questions(
            content.text, vocab_list, session.session_id
        )
        update_task(
            session.session_id, "listening", {"questions": questions_text}, db_session
        )
        db_session.refresh(session)

    # Closures read from DB so they are valid on every turn, not just the first.
    async def generate_fn() -> str:
        """Return the questions and optional content URL for the user."""
        db_session.refresh(session)
        t: dict[str, Any] = json.loads(session.tasks)["listening"]
        questions = t.get("questions", "")
        url: str | None = t.get("url")
        return f"Watch: {url}\n\n{questions}" if url else questions

    async def evaluate_fn(user_response: str) -> dict[str, Any]:
        """Evaluate user answers; return acceptable/feedback/summary dict."""
        db_session.refresh(session)
        t: dict[str, Any] = json.loads(session.tasks)["listening"]
        result, _ = await _evaluate_response(
            user_response,
            t.get("questions", ""),
            t.get("content_text", ""),
            session.session_id,
        )
        return result

    loop_result = await run_turn_loop(
        generate_fn, evaluate_fn, session, "listening", message, db_session
    )

    if loop_result.task_status == "complete":
        db_session.refresh(session)
        t = json.loads(session.tasks)["listening"]
        summary_text, summary_usage = await _generate_summary(
            t.get("questions", ""),
            t.get("content_text", ""),
            session.session_id,
        )
        update_task(
            session.session_id, "listening", {"summary": summary_text}, db_session
        )
        return AgentResult(
            message=summary_text,
            agent="listening",
            task_status="complete",
            usage=summary_usage,
        )

    return loop_result
