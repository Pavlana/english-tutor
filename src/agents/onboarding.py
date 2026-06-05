"""Onboarding agent — runs once per user to establish their learning profile.

Two-state flow driven by session.turn_count:
  turn_count == 0 → ask the level-selection question
  turn_count == 1 → process the user's answer and branch to novice or experienced path
"""

from sqlmodel import Session as DBSession

from src.db.repo import write_learning_log, write_user_profile
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


def _generate_first_log(user_id: str, db_session: DBSession) -> dict:
    """Stub: generate the first learning log from a new profile (task 3.5).

    Args:
        user_id: The user whose first log to generate.
        db_session: Active database session.

    Returns:
        An empty log dict placeholder until task 3.5 is implemented.
    """
    log = write_learning_log(user_id, None, {}, db_session)
    return {"log_id": log.log_id}


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

    return AgentResult(
        message=_NOVICE_WELCOME,
        agent="onboarding",
        task_status="complete",
        usage=None,
    )


async def _experienced_path(
    session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Placeholder for the experienced onboarding path (task 3.3).

    Args:
        session: The active onboarding TutorSession.
        db_session: Active database session.

    Returns:
        Canned AgentResult with task_status="in_progress".
    """
    return AgentResult(
        message="EXPERIENCED path — not yet implemented.",
        agent="onboarding",
        task_status="in_progress",
        usage=None,
    )


async def run(
    message: str, session: TutorSession, db_session: DBSession
) -> AgentResult:
    """Drive the onboarding flow based on session.turn_count.

    State 1 (turn_count == 0): send the level-selection question and advance
    the turn counter to 1.

    State 2 (turn_count == 1): accept "BEGINNER" or "EXPERIENCED"
    (case-insensitive) and branch to the corresponding path. Any other input
    re-asks the question without incrementing the counter.

    Args:
        message: Raw text sent by the user.
        session: The active onboarding TutorSession (topic="onboarding").
        db_session: Active database session.

    Returns:
        AgentResult with task_status="in_progress" until the path completes.
    """
    if session.turn_count == 0:
        session.turn_count = 1
        db_session.add(session)
        db_session.commit()
        return AgentResult(
            message=_LEVEL_QUESTION,
            agent="onboarding",
            task_status="in_progress",
            usage=None,
        )

    # turn_count == 1: process level selection
    keyword = message.strip().lower()
    if keyword == NOVICE_KEYWORD:
        return await _novice_path(session, db_session)
    if keyword == EXPERIENCED_KEYWORD:
        return await _experienced_path(session, db_session)

    return AgentResult(
        message=_RE_ASK,
        agent="onboarding",
        task_status="in_progress",
        usage=None,
    )
