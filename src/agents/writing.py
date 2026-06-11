"""Writing agent — task generation, response evaluation, vocab-usage assessment.

Flow per turn:
  turn_count == 0  → generate writing task from topic + vocabulary,
                     return task text as in_progress.
  turn_count >= 1  → evaluate user response:
                       not acceptable and turns < _MAX_EVAL_TURNS → feedback,
                         in_progress, increment turn_count.
                       acceptable or max turns reached → vocab assessment,
                         summary, complete.

All state (task text, accumulated answers, vocab signals, summary) is
persisted in tasks["writing"] so every turn is stateless between HTTP requests.
"""

import json
from pathlib import Path
from typing import Any

from sqlmodel import Session as DBSession

from src.agents.base import extract_json
from src.api_client import call_anthropic
from src.db.repo import (
    get_latest_learning_log,
    get_vocabulary_for_review,
    set_task_status,
    update_task,
)
from src.db.schemas import TutorSession
from src.models import AgentResult

# ---------------------------------------------------------------------------
# Prompt files — loaded once at import time from src/prompts/.
# ---------------------------------------------------------------------------

_PROMPTS_DIR: Path = Path(__file__).parent.parent / "prompts"

_WRITING_TASK_GENERATION_PROMPT: str = (
    _PROMPTS_DIR / "writing_task_generation.txt"
).read_text()

_WRITING_EVALUATION_PROMPT: str = (
    _PROMPTS_DIR / "writing_evaluation.txt"
).read_text()

_VOCAB_USAGE_ASSESSMENT_PROMPT: str = (
    _PROMPTS_DIR / "vocab_usage_assessment.txt"
).read_text()

_WRITING_SUMMARY_PROMPT: str = (
    _PROMPTS_DIR / "writing_summary.txt"
).read_text()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum number of evaluation turns before forcing completion.
_MAX_EVAL_TURNS: int = 3

# Fallback eval dict when the evaluator response cannot be parsed.
_EVAL_FALLBACK: dict[str, Any] = {
    "acceptable": False,
    "feedback": "Good effort — moving on.",
    "summary": None,
}


# ---------------------------------------------------------------------------
# Private helpers — LLM calls
# ---------------------------------------------------------------------------


async def _generate_task(
    topic: str,
    vocab_list: list[str],
    listening_summary: str,
    recommendations: str,
    session_id: str,
) -> tuple[str, Any]:
    """Generate a short essay writing task for the user.

    Calls Sonnet with the writing-task-generation prompt. Returns the plain-text
    task description to send to the user.

    Args:
        topic: The session topic string.
        vocab_list: Vocabulary words the user should incorporate.
        listening_summary: Summary of the listening task, or empty string.
        recommendations: Grammar/vocabulary recommendations from the learning
            log, or empty string.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (task_text, usage).
    """
    user_content = (
        f"Topic: {topic}\n\n"
        f"Vocabulary: {', '.join(vocab_list) if vocab_list else 'none'}\n\n"
        f"Listening summary: {listening_summary or 'not available'}\n\n"
        f"Learning recommendations: {recommendations or 'none'}"
    )
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="writing_task_generator",
        system=_WRITING_TASK_GENERATION_PROMPT,
        session_id=session_id,
    )
    return raw, usage


async def _evaluate_response(
    writing_task: str,
    accumulated_answers: str,
    vocab_list: list[str],
    session_id: str,
) -> tuple[dict[str, Any], Any]:
    """Evaluate the user's accumulated writing response.

    Calls Sonnet with the writing-evaluation prompt. Falls back to
    _EVAL_FALLBACK on parse failure.

    Args:
        writing_task: The original task text shown to the user.
        accumulated_answers: All answer attempts joined as "Attempt N: …".
        vocab_list: Vocabulary words the task required.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (eval_dict, usage). eval_dict keys: acceptable (bool),
        feedback (str), summary (str | None).
    """
    user_content = (
        f"Writing task:\n{writing_task}\n\n"
        f"Accumulated answers:\n{accumulated_answers}\n\n"
        f"Vocabulary list: {', '.join(vocab_list) if vocab_list else 'none'}"
    )
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="writing_evaluator",
        system=_WRITING_EVALUATION_PROMPT,
        session_id=session_id,
    )
    try:
        result: dict[str, Any] = json.loads(extract_json(raw))
    except (json.JSONDecodeError, ValueError):
        result = dict(_EVAL_FALLBACK)
    return result, usage


async def _assess_vocab_usage(
    combined_response: str,
    vocab_list: list[str],
    session_id: str,
) -> tuple[dict[str, str], Any]:
    """Assess whether each vocabulary word was used correctly in the response.

    Calls Haiku with the vocab-usage-assessment prompt. Returns a dict mapping
    each word to one of "used_correctly", "not_used", or "used_incorrectly".
    Falls back to an empty dict on parse failure.

    Args:
        combined_response: The user's full combined writing response.
        vocab_list: Vocabulary words to assess.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (signals_dict, usage).
    """
    if not vocab_list:
        return {}, None

    user_content = (
        f"User response:\n{combined_response}\n\n"
        f"Vocabulary list: {', '.join(vocab_list)}"
    )
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="haiku",
        agent="vocab_usage_assessor",
        system=_VOCAB_USAGE_ASSESSMENT_PROMPT,
        session_id=session_id,
    )
    try:
        signals: dict[str, str] = json.loads(extract_json(raw))
        if not isinstance(signals, dict):
            signals = {}
    except (json.JSONDecodeError, ValueError):
        signals = {}
    return signals, usage


async def _generate_summary(
    writing_task: str,
    combined_response: str,
    vocab_signals: dict[str, str],
    session_id: str,
) -> tuple[str, Any]:
    """Generate a plain-text summary of the writing task and user performance.

    Calls Sonnet with the writing-summary prompt.

    Args:
        writing_task: The original task text.
        combined_response: The user's full combined writing response.
        vocab_signals: Vocab-usage assessment dict mapping word → signal.
        session_id: Session identifier for observability.

    Returns:
        Tuple of (summary_text, usage).
    """
    user_content = (
        f"Writing task:\n{writing_task}\n\n"
        f"User response:\n{combined_response}\n\n"
        f"Vocabulary signals:\n{json.dumps(vocab_signals)}"
    )
    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": user_content}],
        tier="sonnet",
        agent="writing_summary",
        system=_WRITING_SUMMARY_PROMPT,
        session_id=session_id,
    )
    return raw, usage


# ---------------------------------------------------------------------------
# Private helper — task completion
# ---------------------------------------------------------------------------


async def _complete_task(
    feedback: str,
    writing_task: str,
    combined_response: str,
    vocab_list: list[str],
    session: TutorSession,
    db_session: DBSession,
) -> AgentResult:
    """Finalise the writing task: assess vocab, generate summary, close status.

    Args:
        feedback: Evaluator feedback to show the user (may be empty string).
        writing_task: The original task text.
        combined_response: The user's full combined writing response.
        vocab_list: Vocabulary words from the session.
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="complete".
    """
    signals, _ = await _assess_vocab_usage(
        combined_response, vocab_list, session.session_id
    )
    summary_text, summary_usage = await _generate_summary(
        writing_task, combined_response, signals, session.session_id
    )

    update_task(
        session.session_id,
        "writing",
        {
            "summary": summary_text,
            "vocab_signals": json.dumps(signals),
        },
        db_session,
    )
    set_task_status(session.session_id, "writing", "complete", db_session)

    # Reset turn_count so the next task's loop starts fresh.
    session.turn_count = 0
    db_session.add(session)
    db_session.commit()

    return AgentResult(
        message=feedback or "Good work — writing task complete.",
        agent="writing",
        task_status="complete",
        usage=summary_usage,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Run one turn of the writing agent.

    Turn 0: reads vocabulary and context from DB, calls Sonnet to generate
    a short essay task, persists it, and returns it to the user.

    Turns 1+: evaluates the user's response with Sonnet. If not acceptable
    and under the turn cap, returns targeted feedback and stays in_progress.
    On acceptance or at the turn cap, runs vocab-usage assessment (Haiku),
    generates a summary (Sonnet), persists both, and completes the task.

    All intermediate state is persisted in tasks["writing"] so each call
    is fully stateless between HTTP requests.

    Args:
        message: Raw text sent by the user for this turn.
        session: The active TutorSession row.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress" or "complete".
    """
    db_session.refresh(session)
    tasks_all: dict[str, Any] = json.loads(session.tasks)
    t: dict[str, Any] = tasks_all.get("writing", {})

    # Idempotency guard: if already complete, return immediately.
    if t.get("status") == "complete":
        return AgentResult(
            message=t.get("summary", "Writing task already complete."),
            agent="writing",
            task_status="complete",
            usage=None,
        )

    # ── Turn 0: generate writing task ────────────────────────────────────────
    if session.turn_count == 0:
        # Collect vocabulary words from the DB (status "new" or "learning").
        vocab_items = get_vocabulary_for_review(session.user_id, db_session)
        vocab_list: list[str] = [item.word for item in vocab_items]

        # Listening summary for context — absent on a topic-only session.
        listening_t: dict[str, Any] = tasks_all.get("listening", {})
        listening_summary: str = listening_t.get("summary", "")

        # Learning log recommendations for personalisation.
        learning_log = get_latest_learning_log(session.user_id, db_session)
        recommendations: str = (
            learning_log.get("grammar_gap_summary", "") if learning_log else ""
        )

        task_text, task_usage = await _generate_task(
            session.topic,
            vocab_list,
            listening_summary,
            recommendations,
            session.session_id,
        )

        update_task(
            session.session_id,
            "writing",
            {
                "task": task_text,
                "vocab": json.dumps(vocab_list),
                "answers": json.dumps([]),
            },
            db_session,
        )

        session.turn_count = 1
        db_session.add(session)
        db_session.commit()

        return AgentResult(
            message=task_text,
            agent="writing",
            task_status="in_progress",
            usage=task_usage,
        )

    # ── Turns 1+: evaluate user response ─────────────────────────────────────
    writing_task: str = t.get("task", "")

    try:
        vocab_list = json.loads(t.get("vocab", "[]"))
    except (json.JSONDecodeError, ValueError):
        vocab_list = []

    try:
        answers: list[str] = json.loads(t.get("answers", "[]"))
    except (json.JSONDecodeError, ValueError):
        answers = []

    answers.append(message)
    update_task(
        session.session_id,
        "writing",
        {"answers": json.dumps(answers)},
        db_session,
    )

    combined_answers: str = "\n\n".join(
        f"Attempt {i + 1}: {a}" for i, a in enumerate(answers)
    )

    eval_result, _ = await _evaluate_response(
        writing_task, combined_answers, vocab_list, session.session_id
    )

    force_complete: bool = session.turn_count >= _MAX_EVAL_TURNS
    acceptable: bool = bool(eval_result.get("acceptable", False))
    feedback: str = eval_result.get("feedback", "").strip()

    if acceptable or force_complete:
        return await _complete_task(
            feedback,
            writing_task,
            combined_answers,
            vocab_list,
            session,
            db_session,
        )

    # Not yet acceptable — give feedback and stay in_progress.
    session.turn_count += 1
    db_session.add(session)
    db_session.commit()

    return AgentResult(
        message=feedback or "Please try again.",
        agent="writing",
        task_status="in_progress",
        usage=None,
    )
