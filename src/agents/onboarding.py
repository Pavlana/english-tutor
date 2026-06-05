"""Onboarding agent — runs once per user to establish their learning profile.

Turn-count-driven state machine (all state in DB, no in-memory variables):
  turn_count == 0 → send level-selection question
  turn_count == 1 → process level reply; branch to novice or experienced
  turn_count >= 2 → experienced path: continue conversation or evaluate
"""

import json

from sqlmodel import Session as DBSession

from src.api_client import call_anthropic
from src.db.repo import (
    set_task_status,
    update_task,
    write_learning_log,
    write_user_profile,
)
from src.db.schemas import TutorSession
from src.models import AgentResult

NOVICE_KEYWORD: str = "beginner"
EXPERIENCED_KEYWORD: str = "experienced"

_LEVEL_QUESTION: str = (
    "Hi! Are you a beginner, or experienced learner?\n"
    "Reply with: BEGINNER or EXPERIENCED"
)
_RE_ASK: str = (
    "Sorry, I didn't catch that. Please reply with just: BEGINNER or EXPERIENCED"
)
_FIRST_QUESTION: str = (
    "Great! Tell me a bit about yourself — what's your name, "
    "where are you from, and what brings you to learning English?"
)

# TODO(task-3.6): replace with importlib.resources read of
# src/prompts/onboarding_conversation.txt
_CONVERSATION_SYSTEM_PROMPT: str = (
    "You are a warm, encouraging English tutor conducting a short "
    "getting-to-know-you conversation. Ask one natural follow-up question at a "
    "time about the learner's background, interests, and reasons for learning "
    "English. Keep responses concise and friendly."
)

# Max experienced-path turns before the evaluator is triggered.
_MAX_CONVERSATION_TURNS: int = 5

# Default A1 profile values — architecture §2, NOVICE path.
_A1_CEFR_LEVEL: str = "A1"
_A1_VOCABULARY_RANGE: str = "very limited, foundational words only"
_A1_GRAMMAR_GAPS: list[str] = ["present simple", "basic sentence structure"]
_A1_GRAMMAR_STRENGTHS: list[str] = []
_A1_CONFIDENCE_LEVEL: str = "low"
_A1_INTERESTS: list[str] = []
_A1_ONBOARDING_TRANSCRIPT: str = ""
_A1_ASSESSMENT_METHOD: str = "self_declared_novice"

_NOVICE_WELCOME: str = (
    "Welcome! I've set you up at A1 level — we'll start from the very basics. "
    "Your first session is ready."
)


def _read_transcript(session: TutorSession) -> list[dict]:
    """Return the onboarding conversation transcript stored in session.tasks.

    Args:
        session: The active onboarding TutorSession.

    Returns:
        List of turn dicts with "role" and "content" keys.
    """
    tasks = json.loads(session.tasks)
    return tasks.get("onboarding", {}).get("transcript", [])


def _save_transcript(
    session_id: str, transcript: list[dict], db_session: DBSession
) -> None:
    """Persist the updated transcript via repo.update_task().

    Args:
        session_id: ID of the active onboarding TutorSession.
        transcript: Updated list of turn dicts to persist.
        db_session: Active database session.
    """
    update_task(session_id, "onboarding", {"transcript": transcript}, db_session)


def _generate_first_log(user_id: str, db_session: DBSession) -> dict:
    """Stub: generate the first learning log from a new profile (task 3.5).

    Args:
        user_id: The user whose first log to generate.
        db_session: Active database session.

    Returns:
        A minimal log dict placeholder until task 3.5 is implemented.
    """
    log = write_learning_log(user_id, None, {}, db_session)
    return {"log_id": log.log_id}


_EVALUATOR_WELCOME: str = (
    "Great conversation! I've built your learning profile. "
    "Your first session is ready — let's get started."
)

# TODO(task-3.6): replace with importlib.resources read of
# src/prompts/onboarding_evaluator.txt
_EVALUATOR_SYSTEM_PROMPT: str = (
    "You are a language assessment expert. Based on the conversation transcript "
    "provided, produce a JSON object that accurately reflects the learner's "
    "English proficiency. Return ONLY valid JSON — no prose, no markdown fences. "
    "The JSON must have these exact keys: cefr_level (string, e.g. 'B1'), "
    "vocabulary_range (string description), grammar_gaps (array of strings), "
    "grammar_strengths (array of strings), confidence_level (one of: 'low', "
    "'medium', 'high'), interests (array of strings inferred from conversation), "
    "onboarding_transcript (string, the full transcript as plain text)."
)

_FALLBACK_PROFILE: dict = {
    "cefr_level": _A1_CEFR_LEVEL,
    "vocabulary_range": _A1_VOCABULARY_RANGE,
    "grammar_gaps": _A1_GRAMMAR_GAPS,
    "grammar_strengths": _A1_GRAMMAR_STRENGTHS,
    "confidence_level": _A1_CONFIDENCE_LEVEL,
    "interests": _A1_INTERESTS,
    "onboarding_transcript": _A1_ONBOARDING_TRANSCRIPT,
}


async def _evaluate_transcript(
    session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Run the silent Opus evaluator over the onboarding transcript.

    Reads the full conversation from tasks["onboarding"]["transcript"], calls
    Opus to produce a structured UserProfile JSON, and persists the result.
    If JSON parsing fails the fallback A1 profile is written instead so the
    user is never blocked by a bad model response.

    Also closes the onboarding TutorSession so the orchestrator's next call
    starts the first real learning session.

    Args:
        session: The active onboarding TutorSession containing the transcript.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="complete" and a welcome message.
    """
    transcript = _read_transcript(session)
    transcript_text = "\n".join(
        f"{turn['role'].capitalize()}: {turn['content']}" for turn in transcript
    )

    raw, usage = await call_anthropic(
        messages=[{"role": "user", "content": transcript_text}],
        tier="opus",
        agent="onboarding_evaluator",
        system=_EVALUATOR_SYSTEM_PROMPT,
        session_id=session.session_id,
    )

    try:
        profile_data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        profile_data = dict(_FALLBACK_PROFILE)

    profile_data["assessment_method"] = "conversation_assessed"
    profile_data.setdefault("onboarding_transcript", transcript_text)

    write_user_profile(session.user_id, profile_data, db_session)
    _generate_first_log(session.user_id, db_session)
    set_task_status(session.session_id, "onboarding", "complete", db_session)

    session.status = "complete"
    db_session.add(session)
    db_session.commit()

    return AgentResult(
        message=_EVALUATOR_WELCOME,
        agent="onboarding",
        task_status="complete",
        usage=usage,
    )


async def _novice_path(session: TutorSession, db_session: DBSession) -> AgentResult:
    """Write the default A1 profile and seed the first learning log.

    Makes no LLM calls — the novice path is entirely deterministic.

    Args:
        session: The active onboarding TutorSession.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="complete" and a welcome message.
    """
    profile = {
        "cefr_level": _A1_CEFR_LEVEL,
        "vocabulary_range": _A1_VOCABULARY_RANGE,
        "grammar_gaps": _A1_GRAMMAR_GAPS,
        "grammar_strengths": _A1_GRAMMAR_STRENGTHS,
        "confidence_level": _A1_CONFIDENCE_LEVEL,
        "interests": _A1_INTERESTS,
        "onboarding_transcript": _A1_ONBOARDING_TRANSCRIPT,
        "assessment_method": _A1_ASSESSMENT_METHOD,
    }
    write_user_profile(session.user_id, profile, db_session)
    _generate_first_log(session.user_id, db_session)
    set_task_status(session.session_id, "onboarding", "complete", db_session)

    # Close the onboarding session so the orchestrator's next call falls through
    # to branch 2 (learning log found) and starts the first real session.
    session.status = "complete"
    db_session.add(session)
    db_session.commit()

    return AgentResult(
        message=_NOVICE_WELCOME,
        agent="onboarding",
        task_status="complete",
        usage=None,
    )


async def _experienced_path(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Drive the experienced-path conversation and trigger evaluation at turn 5.

    At turn_count == 1 (user just selected "experienced"): sends the first
    hardcoded question without an API call and stores it in the transcript.

    At turn_count 2–4: appends the user's message to the transcript, calls
    Sonnet to generate the next question, and stores the reply.

    At turn_count == 5: calls _evaluate_transcript() to close the conversation
    and produce the user profile.

    Turn state is read from and written to the DB — no in-memory turn variables.

    Args:
        message: Raw text sent by the user for this turn.
        session: The active onboarding TutorSession.
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress" while conversation continues,
        or the result of _evaluate_transcript() on the final turn.
    """
    if session.turn_count == _MAX_CONVERSATION_TURNS:
        return await _evaluate_transcript(session, db_session)

    transcript = _read_transcript(session)

    if session.turn_count == 1:
        # First experienced turn: send hardcoded opening question.
        transcript.append({"role": "assistant", "content": _FIRST_QUESTION})
        session.turn_count = 2
        db_session.add(session)
        db_session.commit()
        set_task_status(session.session_id, "onboarding", "in_progress", db_session)
        _save_transcript(session.session_id, transcript, db_session)
        return AgentResult(
            message=_FIRST_QUESTION,
            agent="onboarding",
            task_status="in_progress",
            usage=None,
        )

    # Turns 2–4: append user message, call Sonnet, append reply.
    transcript.append({"role": "user", "content": message})

    reply, usage = await call_anthropic(
        messages=transcript,
        tier="sonnet",
        agent="onboarding_conversation",
        system=_CONVERSATION_SYSTEM_PROMPT,
        session_id=session.session_id,
    )

    transcript.append({"role": "assistant", "content": reply})
    session.turn_count += 1
    db_session.add(session)
    db_session.commit()
    _save_transcript(session.session_id, transcript, db_session)

    if session.turn_count == _MAX_CONVERSATION_TURNS:
        return await _evaluate_transcript(session, db_session)

    return AgentResult(
        message=reply,
        agent="onboarding",
        task_status="in_progress",
        usage=usage,
    )


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Drive the onboarding flow based on session.turn_count.

    State machine:
      turn_count == 0 → send the level-selection question, advance to 1.
      turn_count == 1 → process "BEGINNER" / "EXPERIENCED"; re-ask on anything
                        else.
      turn_count >= 2 → experienced path continuation (conversation or
                        evaluation).

    Args:
        message: Raw text sent by the user.
        session: The active onboarding TutorSession (topic="onboarding").
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress" until a path completes.
    """
    if session.turn_count == 0:
        session.turn_count = 1
        db_session.add(session)
        db_session.commit()
        set_task_status(session.session_id, "onboarding", "in_progress", db_session)
        return AgentResult(
            message=_LEVEL_QUESTION,
            agent="onboarding",
            task_status="in_progress",
            usage=None,
        )

    if session.turn_count == 1:
        keyword = message.strip().lower()
        if keyword == NOVICE_KEYWORD:
            return await _novice_path(session, db_session)
        if keyword == EXPERIENCED_KEYWORD:
            return await _experienced_path(message, session, db_session)
        return AgentResult(
            message=_RE_ASK,
            agent="onboarding",
            task_status="in_progress",
            usage=None,
        )

    # turn_count >= 2: must be experienced path continuation.
    return await _experienced_path(message, session, db_session)
