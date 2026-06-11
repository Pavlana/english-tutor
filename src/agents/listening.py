"""Listening agent — content acquisition, vocabulary extraction, comprehension Q&A.

Flow per turn:
  turn_count == 0  → acquire content, extract vocab, generate questions,
                     hand off to run_turn_loop (returns "in_progress").
  turn_count >= 1  → gate logic:
                       empty message   → re-display task card, no turn increment.
                       help request    → explain, re-display task card, no increment.
                       answer attempt  → evaluate; reject up to _MAX_ANSWER_ATTEMPTS,
                                         then force complete.
  topic-only       → skip immediately, return task_status="skipped".

All state (questions, content text, vocab) is persisted in
tasks["listening"] so every turn is stateless between HTTP requests.
"""

import json
import re
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

_INTENT_CLASSIFICATION_PROMPT: str = (
    _PROMPTS_DIR / "intent_classification.txt"
).read_text()

_HELP_RESPONSE_PROMPT: str = (_PROMPTS_DIR / "help_response.txt").read_text()

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

# Maximum number of evaluated answer attempts before the task is forced complete.
# Help requests and empty messages do not count toward this cap.
_MAX_ANSWER_ATTEMPTS: int = 2

# Shown when the user sends an empty message during the Q&A phase.
_EMPTY_NUDGE: str = (
    "Take your time — answer the questions above, "
    "or type 'help' if something is unclear."
)

# Appended to evaluator feedback when the user has one retry remaining.
_RETRY_NUDGE: str = "Try once more if you'd like:"


# ---------------------------------------------------------------------------
# Private helpers — JSON extraction
# ---------------------------------------------------------------------------


def _extract_json(raw: str) -> str:
    """Extract the first JSON array or object from a string.

    Models sometimes prepend prose before the JSON. This strips everything
    outside the outermost ``[…]`` or ``{…}`` so ``json.loads`` can parse it.

    Args:
        raw: Raw model output that should contain JSON.

    Returns:
        The extracted JSON substring, or the original string if no bracket
        pair is found (letting the caller's json.loads raise naturally).
    """
    match = re.search(r"(\[.*\]|\{.*\})", raw, re.DOTALL)
    return match.group(0) if match else raw


# ---------------------------------------------------------------------------
# Private helpers — LLM calls
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
        words: list[str] = json.loads(_extract_json(raw))
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

    Calls Sonnet with the comprehension-questions prompt. Returns the JSON
    array string (prose stripped) to be stored and later parsed for display.

    Args:
        text: Source text to base questions on.
        vocab_list: Vocabulary words extracted from the text.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (questions_json_str, usage).
    """
    user_content = f"Text:\n{text}\n\nVocabulary: {', '.join(vocab_list)}"
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="question_generator",
        system=_COMPREHENSION_QUESTIONS_PROMPT,
        session_id=session_id,
    )
    return _extract_json(raw), usage


async def _classify_intent(
    message: str,
    questions: str,
    content_text: str,
    session_id: str | None,
) -> str:
    """Classify the user's message as an answer attempt or a help request.

    Calls Haiku with the intent-classification prompt. Falls back to "answer"
    on parse failure so the gate always has a safe default.

    Args:
        message: The user's raw message for this turn.
        questions: The comprehension questions that were asked.
        content_text: Source text used as classification context.
        session_id: Session identifier for observability.

    Returns:
        One of: "answer", "vocabulary", "question_clarification",
        "content_confusion", "hint_request".
    """
    user_content = (
        f"Questions:\n{questions}\n\n"
        f"Source text (excerpt):\n{content_text[:1000]}\n\n"
        f"User message:\n{message}"
    )
    raw, _ = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="haiku",
        agent="intent_classifier",
        system=_INTENT_CLASSIFICATION_PROMPT,
        session_id=session_id,
    )
    try:
        data: dict[str, Any] = json.loads(_extract_json(raw))
        intent: str = data.get("intent", "answer")
    except (json.JSONDecodeError, ValueError):
        intent = "answer"
    return intent


async def _handle_help(
    message: str,
    intent: str,
    questions: str,
    vocab_json: str,
    content_text: str,
    session_id: str | None,
) -> str:
    """Generate a contextual help response for the given intent type.

    Uses Sonnet for content_confusion (needs deeper reasoning) and Haiku for
    all other help types.

    Args:
        message: The user's original help message.
        intent: One of "vocabulary", "question_clarification",
            "content_confusion", "hint_request".
        questions: The comprehension questions that were asked.
        vocab_json: JSON string of the vocabulary list.
        content_text: Source text for context.
        session_id: Session identifier for observability.

    Returns:
        Plain-text help response to show the user.
    """
    try:
        vocab: list[str] = json.loads(vocab_json)
    except (json.JSONDecodeError, ValueError):
        vocab = []

    user_content = (
        f"Help type: {intent}\n\n"
        f"Learner's message: {message}\n\n"
        f"Comprehension questions:\n{questions}\n\n"
        f"Vocabulary list: {', '.join(vocab)}\n\n"
        f"Source text:\n{content_text}"
    )
    tier = "sonnet" if intent == "content_confusion" else "haiku"
    raw, _ = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier=tier,
        agent="help_responder",
        system=_HELP_RESPONSE_PROMPT,
        session_id=session_id,
    )
    return raw


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
        result: dict[str, Any] = json.loads(_extract_json(raw))
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
# Private helper — task card builder
# ---------------------------------------------------------------------------


def _build_task_card(t: dict[str, Any]) -> str:
    """Build the formatted task card shown to the user.

    Reads url, title, modality, vocab, and questions from the tasks dict.
    Returns a multi-line string with the content link, vocabulary list,
    instructions, and numbered questions.

    Args:
        t: The parsed tasks["listening"] dict.

    Returns:
        Formatted task card string.
    """
    url: str | None = t.get("url")
    title: str | None = t.get("title")
    modality: str = t.get("modality", "article")

    try:
        vocab: list[str] = json.loads(t.get("vocab", "[]"))
    except (json.JSONDecodeError, ValueError):
        vocab = []
    try:
        questions: list[str] = json.loads(t.get("questions", "[]"))
    except (json.JSONDecodeError, ValueError):
        questions = []

    lines: list[str] = []

    if url:
        verb = "Watch" if modality == "video" else "Read"
        if title:
            lines.append(f"{verb}: {title}")
            lines.append(url)
        else:
            lines.append(f"{verb}: {url}")
        lines.append("")

    if vocab:
        lines.append("Vocabulary to focus on:")
        lines.extend(f"  • {w}" for w in vocab)
        lines.append("")

    lines.append("Answer each question in 1–3 sentences using your own words.")
    lines.append("")

    if questions:
        for i, q in enumerate(questions, 1):
            lines.append(f"{i}. {q}")
    else:
        lines.append(t.get("questions", ""))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private helper — task completion
# ---------------------------------------------------------------------------


async def _complete_task(
    eval_result: dict[str, Any],
    session: TutorSession,
    db_session: DBSession,
) -> AgentResult:
    """Finalise the listening task: persist summary, close status, return result.

    Args:
        eval_result: The evaluator dict (acceptable, feedback, summary).
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="complete".
    """
    set_task_status(session.session_id, "listening", "complete", db_session)
    session.turn_count = 0
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    t: dict[str, Any] = json.loads(session.tasks)["listening"]
    summary_text, summary_usage = await _generate_summary(
        t.get("questions", ""),
        t.get("content_text", ""),
        session.session_id,
    )
    update_task(session.session_id, "listening", {"summary": summary_text}, db_session)

    feedback = eval_result.get("feedback", "").strip()
    message = f"{feedback}\n\n{summary_text}" if feedback else summary_text
    return AgentResult(
        message=message,
        agent="listening",
        task_status="complete",
        usage=summary_usage,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Run one turn of the listening agent.

    Turn 0: acquires content, extracts vocabulary, generates comprehension
    questions, hands off to run_turn_loop which shows the task card.

    Turns 1+: gate logic —
      - Empty message: re-display task card with a nudge; no turn increment.
      - Help request (vocab/clarification/confusion/hint): call help responder,
        re-display task card; no turn increment.
      - Answer attempt: evaluate with Haiku; reject up to _MAX_ANSWER_ATTEMPTS
        then force complete.

    All intermediate state is persisted in tasks["listening"] so each call
    is fully stateless between HTTP requests.

    Args:
        message: Raw text sent by the user for this turn.
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress", "complete", or "skipped".
    """
    # ── Turn 0: content acquisition and question generation ──────────────────
    if session.turn_count == 0:
        content = await acquire_content(session.topic)
        update_task(
            session.session_id,
            "listening",
            {
                "url": content.url,
                "title": content.title,
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
            session.session_id,
            "listening",
            {
                "vocab": json.dumps(vocab_list),
                "questions": questions_text,
            },
            db_session,
        )
        db_session.refresh(session)

        # Use run_turn_loop only for the generate step (turn_count=0 path).
        # evaluate_fn is never called on turn 0 so a placeholder is safe.
        async def _generate_fn() -> str:
            db_session.refresh(session)
            return _build_task_card(json.loads(session.tasks)["listening"])

        async def _placeholder_eval(_msg: str) -> dict[str, Any]:
            return dict(_EVAL_FALLBACK)  # never reached on turn 0

        return await run_turn_loop(
            _generate_fn, _placeholder_eval, session, "listening", message, db_session
        )

    # ── Turns 1+: gate logic ─────────────────────────────────────────────────
    db_session.refresh(session)
    t: dict[str, Any] = json.loads(session.tasks)["listening"]

    # Gate 1: empty message — nudge without consuming a turn.
    if not message.strip():
        task_card = _build_task_card(t)
        return AgentResult(
            message=f"{_EMPTY_NUDGE}\n\n{task_card}",
            agent="listening",
            task_status="in_progress",
            usage=None,
        )

    # Gate 2: intent classification.
    intent = await _classify_intent(
        message,
        t.get("questions", ""),
        t.get("content_text", ""),
        session.session_id,
    )

    # Gate 3: help request — respond and re-display task card; no turn increment.
    if intent != "answer":
        help_text = await _handle_help(
            message,
            intent,
            t.get("questions", ""),
            t.get("vocab", "[]"),
            t.get("content_text", ""),
            session.session_id,
        )
        task_card = _build_task_card(t)
        return AgentResult(
            message=f"{help_text}\n\n---\n\n{task_card}",
            agent="listening",
            task_status="in_progress",
            usage=None,
        )

    # Gate 4: answer attempt — evaluate, enforce 2-attempt cap.
    eval_result, _ = await _evaluate_response(
        message,
        t.get("questions", ""),
        t.get("content_text", ""),
        session.session_id,
    )
    force_complete = session.turn_count >= _MAX_ANSWER_ATTEMPTS

    if eval_result.get("acceptable") or force_complete:
        return await _complete_task(eval_result, session, db_session)

    # Rejected — offer one retry; increment turn counter.
    session.turn_count += 1
    db_session.add(session)
    db_session.commit()

    feedback = eval_result.get("feedback", "").strip()
    task_card = _build_task_card(t)
    return AgentResult(
        message=f"{feedback}\n\n{_RETRY_NUDGE}\n\n{task_card}",
        agent="listening",
        task_status="in_progress",
        usage=None,
    )
