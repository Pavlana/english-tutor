"""Database read/write functions — the only path to persistent state.

Architecture layering rule: nothing outside this module may import SQLModel
Session objects or execute queries directly. All DB access goes through here.
"""

import json
import uuid
from datetime import date

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from src.db.schemas import (
    TASK_STATUSES,
    LearningLog,
    TutorSession,
    User,
    UserProfile,
    VocabularyItem,
)

_VALID_TASK_NAMES: frozenset[str] = frozenset(
    {"listening", "writing", "speaking", "grammar"}
)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _get_session_or_raise(session_id: str, session: DBSession) -> TutorSession:
    ts = session.get(TutorSession, session_id)
    if ts is None:
        raise ValueError(f"Session {session_id!r} not found.")
    return ts


def _learning_log_to_dict(log: LearningLog) -> dict:
    return {
        "log_id": log.log_id,
        "user_id": log.user_id,
        "generated_after_session": log.generated_after_session,
        "date": log.date,
        "status": log.status,
        "vocabulary_to_review": json.loads(log.vocabulary_to_review),
        "grammar_focus": json.loads(log.grammar_focus),
        "grammar_gap_summary": log.grammar_gap_summary,
        "session_notes": log.session_notes,
        "recommended_topic_tags": json.loads(log.recommended_topic_tags),
    }


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


def get_user(user_id: str, session: DBSession) -> User | None:
    """Return the User row for user_id, or None if not found.

    Args:
        user_id: The user to look up.
        session: Active database session.

    Returns:
        The matching User row, or None.
    """
    return session.get(User, user_id)


def create_user(user_id: str, session: DBSession) -> User:
    """Insert and return a new User row.

    Args:
        user_id: Caller-supplied UUID string.
        session: Active database session.

    Returns:
        The newly created User row.

    Raises:
        sqlalchemy.exc.IntegrityError: If user_id already exists.
    """
    user = User(user_id=user_id)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------


def get_user_profile(user_id: str, session: DBSession) -> dict | None:
    """Return the profile for user_id as a plain dict, or None.

    List fields (grammar_gaps, grammar_strengths, interests) are deserialised
    from JSON strings to Python lists.

    Args:
        user_id: The user whose profile to fetch.
        session: Active database session.

    Returns:
        A dict matching architecture §9 UserProfile shape, or None.
    """
    profile = session.get(UserProfile, user_id)
    if profile is None:
        return None
    return {
        "user_id": profile.user_id,
        "cefr_level": profile.cefr_level,
        "vocabulary_range": profile.vocabulary_range,
        "grammar_gaps": json.loads(profile.grammar_gaps),
        "grammar_strengths": json.loads(profile.grammar_strengths),
        "confidence_level": profile.confidence_level,
        "interests": json.loads(profile.interests),
        "onboarding_transcript": profile.onboarding_transcript,
    }


def write_user_profile(
    user_id: str, profile_data: dict, session: DBSession
) -> UserProfile:
    """Upsert the profile for user_id (delete existing row, insert new).

    List fields (grammar_gaps, grammar_strengths, interests) are serialised
    to JSON strings before writing.

    Args:
        user_id: The user whose profile to write.
        profile_data: Dict matching architecture §9 UserProfile shape.
        session: Active database session.

    Returns:
        The newly written UserProfile row.
    """
    existing = session.get(UserProfile, user_id)
    if existing:
        session.delete(existing)
        session.commit()

    profile = UserProfile(
        user_id=user_id,
        cefr_level=profile_data["cefr_level"],
        vocabulary_range=profile_data["vocabulary_range"],
        grammar_gaps=json.dumps(profile_data["grammar_gaps"]),
        grammar_strengths=json.dumps(profile_data["grammar_strengths"]),
        confidence_level=profile_data["confidence_level"],
        interests=json.dumps(profile_data["interests"]),
        onboarding_transcript=profile_data["onboarding_transcript"],
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def get_open_session(user_id: str, session: DBSession) -> TutorSession | None:
    """Return the most recent in-progress TutorSession for user_id, or None.

    Args:
        user_id: The user to look up.
        session: Active database session.

    Returns:
        The most recent TutorSession with status="in_progress", or None.
    """
    stmt = (
        select(TutorSession)
        .where(
            TutorSession.user_id == user_id,
            TutorSession.status == "in_progress",
        )
        .order_by(col(TutorSession.date_started).desc())
        .limit(1)
    )
    return session.exec(stmt).first()


def create_session(user_id: str, topic: str, session: DBSession) -> TutorSession:
    """Create and return a new TutorSession with a generated session_id.

    The tasks column is initialised to the default not_started JSON from
    schemas.py. Status defaults to "in_progress".

    Args:
        user_id: The owner of the session.
        topic: The topic string for this session.
        session: Active database session.

    Returns:
        The newly created TutorSession row.
    """
    ts = TutorSession(
        session_id=str(uuid.uuid4()),
        user_id=user_id,
        topic=topic,
    )
    session.add(ts)
    session.commit()
    session.refresh(ts)
    return ts


def get_session(session_id: str, session: DBSession) -> TutorSession | None:
    """Return the TutorSession for session_id, or None if not found.

    Args:
        session_id: The session to look up.
        session: Active database session.

    Returns:
        The matching TutorSession, or None.
    """
    return session.get(TutorSession, session_id)


def update_task(
    session_id: str, task_name: str, task_data: dict, session: DBSession
) -> None:
    """Deep-merge task_data into tasks[task_name] and persist.

    Keys not present in task_data survive unchanged. Sibling tasks are
    never touched.

    Args:
        session_id: The session to update.
        task_name: One of "listening", "writing", "speaking", "grammar".
        task_data: Partial task dict to merge in.
        session: Active database session.

    Raises:
        ValueError: If task_name is not a valid task name or session not found.
    """
    if task_name not in _VALID_TASK_NAMES:
        raise ValueError(f"Invalid task name {task_name!r}.")
    ts = _get_session_or_raise(session_id, session)
    tasks = json.loads(ts.tasks)
    tasks[task_name].update(task_data)
    ts.tasks = json.dumps(tasks)
    session.add(ts)
    session.commit()


def set_task_status(
    session_id: str, task_name: str, status: str, session: DBSession
) -> None:
    """Set tasks[task_name]["status"] to status.

    Args:
        session_id: The session to update.
        task_name: One of "listening", "writing", "speaking", "grammar".
        status: Must be a value in TASK_STATUSES.
        session: Active database session.

    Raises:
        ValueError: If status is not in TASK_STATUSES, or session/task is invalid.
    """
    if status not in TASK_STATUSES:
        raise ValueError(f"Invalid task status {status!r}.")
    update_task(session_id, task_name, {"status": status}, session)


# ---------------------------------------------------------------------------
# Learning Log
# ---------------------------------------------------------------------------


def get_latest_learning_log(user_id: str, session: DBSession) -> dict | None:
    """Return the most recent LearningLog for user_id as a dict, or None.

    "Most recent" is determined by the date field (ISO lexicographic sort).
    List fields are deserialised from JSON strings to Python lists.

    Args:
        user_id: The user to look up.
        session: Active database session.

    Returns:
        A dict matching architecture §9 LearningLog shape, or None.
    """
    stmt = (
        select(LearningLog)
        .where(LearningLog.user_id == user_id)
        .order_by(col(LearningLog.date).desc())
        .limit(1)
    )
    log = session.exec(stmt).first()
    return None if log is None else _learning_log_to_dict(log)


def write_learning_log(
    user_id: str,
    generated_after_session: str | None,
    log_data: dict,
    session: DBSession,
) -> LearningLog:
    """Insert and return a new LearningLog row.

    Args:
        user_id: The user the log belongs to.
        generated_after_session: The session_id that triggered this log,
            or None for the first log generated during onboarding.
        log_data: Dict with keys: vocabulary_to_review, grammar_focus,
            grammar_gap_summary, session_notes, recommended_topic_tags.
        session: Active database session.

    Returns:
        The newly written LearningLog row.
    """
    log = LearningLog(
        log_id=str(uuid.uuid4()),
        user_id=user_id,
        generated_after_session=generated_after_session,
        date=date.today().isoformat(),
        vocabulary_to_review=json.dumps(log_data.get("vocabulary_to_review", [])),
        grammar_focus=json.dumps(log_data.get("grammar_focus", [])),
        grammar_gap_summary=log_data.get("grammar_gap_summary", ""),
        session_notes=log_data.get("session_notes", ""),
        recommended_topic_tags=json.dumps(log_data.get("recommended_topic_tags", [])),
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def get_grammar_history(
    user_id: str, session: DBSession, last_n: int = 3
) -> list[dict]:
    """Return the last_n LearningLogs for user_id, most recent first.

    Args:
        user_id: The user to look up.
        session: Active database session.
        last_n: Maximum number of logs to return. Defaults to 3.

    Returns:
        A list of dicts (list fields deserialised), most recent first.
        Returns an empty list if no logs exist.
    """
    stmt = (
        select(LearningLog)
        .where(LearningLog.user_id == user_id)
        .order_by(col(LearningLog.date).desc())
        .limit(last_n)
    )
    return [_learning_log_to_dict(log) for log in session.exec(stmt).all()]


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------


def upsert_vocabulary(
    user_id: str,
    word: str,
    session_id: str,
    session: DBSession,
    status: str = "new",
    topic_tags: list[str] | None = None,
) -> VocabularyItem:
    """Insert or update a vocabulary item for user_id.

    If (user_id, word) already exists: increments times_seen, updates
    last_seen_session, and appends any new topic_tags (deduplicating,
    preserving order, new tags appended at end).
    If not: inserts with times_seen=1, times_used_correctly=0.

    Args:
        user_id: The user who encountered this word.
        word: The vocabulary item (may be a phrase, phrasal verb, or collocation).
        session_id: The session in which the word was encountered.
        session: Active database session.
        status: Initial status for new items. Defaults to "new".
        topic_tags: Topic tags to associate with this word. Defaults to [].

    Returns:
        The updated or newly created VocabularyItem row.
    """
    stmt = select(VocabularyItem).where(
        VocabularyItem.user_id == user_id,
        VocabularyItem.word == word,
    )
    item = session.exec(stmt).first()

    if item:
        item.times_seen += 1
        item.last_seen_session = session_id
        if topic_tags:
            existing_tags: list[str] = json.loads(item.topic_tags)
            for tag in topic_tags:
                if tag not in existing_tags:
                    existing_tags.append(tag)
            item.topic_tags = json.dumps(existing_tags)
        session.add(item)
        session.commit()
        session.refresh(item)
        return item

    new_item = VocabularyItem(
        word=word,
        user_id=user_id,
        status=status,
        times_seen=1,
        times_used_correctly=0,
        last_seen_session=session_id,
        topic_tags=json.dumps(topic_tags or []),
    )
    session.add(new_item)
    session.commit()
    session.refresh(new_item)
    return new_item


def get_vocabulary_for_review(user_id: str, session: DBSession) -> list[VocabularyItem]:
    """Return all VocabularyItems for user_id with status "new" or "learning".

    Args:
        user_id: The user whose vocabulary to fetch.
        session: Active database session.

    Returns:
        A list of VocabularyItem rows, or an empty list.
    """
    stmt = select(VocabularyItem).where(
        VocabularyItem.user_id == user_id,
        col(VocabularyItem.status).in_(["new", "learning"]),
    )
    return list(session.exec(stmt).all())
