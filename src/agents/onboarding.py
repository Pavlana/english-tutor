"""Onboarding agent — runs once per user to establish their learning profile.

Two-state flow driven by session.turn_count:
  turn_count == 0 → ask the level-selection question
  turn_count == 1 → process the user's answer and branch to novice or experienced path
"""

from sqlmodel import Session as DBSession

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


async def _novice_path(session: TutorSession, db_session: DBSession) -> AgentResult:
    """Placeholder for the novice onboarding path (task 3.2).

    Args:
        session: The active onboarding TutorSession.
        db_session: Active database session.

    Returns:
        Canned AgentResult with task_status="in_progress".
    """
    return AgentResult(
        message="NOVICE path — not yet implemented.",
        agent="onboarding",
        task_status="in_progress",
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
