"""Round-trip tests for all db/repo.py functions."""

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from src.db.repo import (
    create_session,
    create_user,
    get_grammar_history,
    get_latest_learning_log,
    get_open_session,
    get_session,
    get_user,
    get_user_profile,
    get_vocabulary_for_review,
    set_task_status,
    update_task,
    upsert_vocabulary,
    write_learning_log,
    write_user_profile,
)

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_PROFILE = {
    "cefr_level": "B1",
    "vocabulary_range": "adequate",
    "grammar_gaps": ["passive voice", "reported speech"],
    "grammar_strengths": ["present perfect"],
    "confidence_level": "medium",
    "interests": ["technology", "travel"],
    "onboarding_transcript": "some transcript",
}

_LOG_DATA = {
    "vocabulary_to_review": ["ubiquitous", "mitigate"],
    "grammar_focus": ["passive voice"],
    "grammar_gap_summary": "Needs work on passive.",
    "session_notes": "Good session.",
    "recommended_topic_tags": ["technology"],
}


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------


def test_create_user_and_get_user(db_session):
    user = create_user("user-1", db_session)
    assert user.user_id == "user-1"

    fetched = get_user("user-1", db_session)
    assert fetched is not None
    assert fetched.user_id == "user-1"


def test_create_user_duplicate_raises_integrity_error(db_session):
    create_user("user-dup", db_session)
    with pytest.raises(IntegrityError):
        create_user("user-dup", db_session)


def test_get_user_returns_none_for_unknown(db_session):
    assert get_user("no-such-user", db_session) is None


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------


def test_write_and_get_user_profile_list_fields_are_deserialised(db_session):
    write_user_profile("user-1", _PROFILE, db_session)
    result = get_user_profile("user-1", db_session)

    assert result is not None
    assert isinstance(result["grammar_gaps"], list)
    assert isinstance(result["grammar_strengths"], list)
    assert isinstance(result["interests"], list)
    assert result["grammar_gaps"] == ["passive voice", "reported speech"]
    assert result["cefr_level"] == "B1"


def test_write_user_profile_upserts_on_second_write(db_session):
    write_user_profile("user-1", _PROFILE, db_session)
    write_user_profile("user-1", {**_PROFILE, "cefr_level": "B2"}, db_session)

    result = get_user_profile("user-1", db_session)
    assert result["cefr_level"] == "B2"


def test_get_user_profile_returns_none_for_user_without_profile(db_session):
    assert get_user_profile("ghost-user", db_session) is None


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_create_session_defaults(db_session):
    ts = create_session("user-1", "climate change", db_session)

    assert ts.status == "in_progress"
    tasks = json.loads(ts.tasks)
    assert set(tasks.keys()) == {"listening", "writing", "speaking", "grammar"}
    for task_data in tasks.values():
        assert task_data["status"] == "not_started"


def test_get_open_session_returns_none_for_different_user(db_session):
    create_session("user-1", "topic", db_session)
    assert get_open_session("user-2", db_session) is None


def test_get_open_session_returns_most_recent_of_two(db_session):
    ts1 = create_session("user-1", "old topic", db_session)
    ts1.date_started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db_session.add(ts1)
    db_session.commit()

    ts2 = create_session("user-1", "new topic", db_session)

    result = get_open_session("user-1", db_session)
    assert result is not None
    assert result.session_id == ts2.session_id


def test_get_open_session_ignores_complete_sessions(db_session):
    ts = create_session("user-1", "topic", db_session)
    ts.status = "complete"
    db_session.add(ts)
    db_session.commit()

    assert get_open_session("user-1", db_session) is None


def test_get_session_returns_correct_row(db_session):
    ts = create_session("user-1", "topic", db_session)
    fetched = get_session(ts.session_id, db_session)
    assert fetched is not None
    assert fetched.session_id == ts.session_id


def test_get_session_returns_none_for_unknown_id(db_session):
    assert get_session("no-such-session", db_session) is None


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


def test_update_task_merges_into_target_task(db_session):
    ts = create_session("user-1", "topic", db_session)
    update_task(
        ts.session_id,
        "listening",
        {"status": "complete", "summary": "good"},
        db_session,
    )

    updated = get_session(ts.session_id, db_session)
    tasks = json.loads(updated.tasks)
    assert tasks["listening"]["status"] == "complete"
    assert tasks["listening"]["summary"] == "good"


def test_update_task_sibling_keys_survive(db_session):
    ts = create_session("user-1", "topic", db_session)
    update_task(
        ts.session_id,
        "listening",
        {"status": "complete", "summary": "good"},
        db_session,
    )

    updated = get_session(ts.session_id, db_session)
    tasks = json.loads(updated.tasks)
    assert tasks["writing"]["status"] == "not_started"
    assert tasks["speaking"]["status"] == "not_started"
    assert tasks["grammar"]["status"] == "not_started"


def test_update_task_raises_for_unknown_session(db_session):
    with pytest.raises(ValueError, match="not found"):
        update_task("no-such-session", "listening", {"status": "complete"}, db_session)


# ---------------------------------------------------------------------------
# set_task_status
# ---------------------------------------------------------------------------


def test_set_task_status_updates_correctly(db_session):
    ts = create_session("user-1", "topic", db_session)
    set_task_status(ts.session_id, "writing", "in_progress", db_session)

    updated = get_session(ts.session_id, db_session)
    tasks = json.loads(updated.tasks)
    assert tasks["writing"]["status"] == "in_progress"


def test_set_task_status_raises_for_invalid_status(db_session):
    ts = create_session("user-1", "topic", db_session)
    with pytest.raises(ValueError, match="done"):
        set_task_status(ts.session_id, "writing", "done", db_session)


# ---------------------------------------------------------------------------
# write_learning_log / get_latest_learning_log
# ---------------------------------------------------------------------------


def test_write_and_read_learning_log_list_fields_are_deserialised(db_session):
    write_learning_log("user-1", None, _LOG_DATA, db_session)
    result = get_latest_learning_log("user-1", db_session)

    assert result is not None
    assert isinstance(result["vocabulary_to_review"], list)
    assert isinstance(result["grammar_focus"], list)
    assert isinstance(result["recommended_topic_tags"], list)
    assert result["vocabulary_to_review"] == ["ubiquitous", "mitigate"]


def test_get_latest_learning_log_returns_more_recent(db_session):
    log1 = write_learning_log("user-1", None, _LOG_DATA, db_session)
    log1.date = "2025-01-01"
    db_session.add(log1)
    db_session.commit()

    log2 = write_learning_log("user-1", None, _LOG_DATA, db_session)
    # log2.date is today — sorts after "2025-01-01" lexicographically

    result = get_latest_learning_log("user-1", db_session)
    assert result["log_id"] == log2.log_id


def test_get_latest_learning_log_returns_none_when_no_logs(db_session):
    assert get_latest_learning_log("user-1", db_session) is None


# ---------------------------------------------------------------------------
# get_grammar_history
# ---------------------------------------------------------------------------


def test_get_grammar_history_returns_last_n_most_recent_first(db_session):
    for date_str in ["2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01"]:
        log = write_learning_log("user-1", None, {}, db_session)
        log.date = date_str
        db_session.add(log)
        db_session.commit()

    result = get_grammar_history("user-1", db_session, last_n=3)

    assert len(result) == 3
    assert result[0]["date"] == "2025-04-01"
    assert result[1]["date"] == "2025-03-01"
    assert result[2]["date"] == "2025-02-01"


def test_get_grammar_history_returns_empty_list_when_no_logs(db_session):
    assert get_grammar_history("user-1", db_session) == []


# ---------------------------------------------------------------------------
# upsert_vocabulary
# ---------------------------------------------------------------------------


def test_upsert_vocabulary_first_call(db_session):
    item = upsert_vocabulary("user-1", "ubiquitous", "sess-1", db_session)

    assert item.times_seen == 1
    assert item.status == "new"
    assert item.last_seen_session == "sess-1"


def test_upsert_vocabulary_second_call_increments_times_seen_merges_tags(db_session):
    upsert_vocabulary("user-1", "ubiquitous", "sess-1", db_session, topic_tags=["tech"])
    item = upsert_vocabulary(
        "user-1", "ubiquitous", "sess-2", db_session, topic_tags=["climate"]
    )

    assert item.times_seen == 2
    assert item.last_seen_session == "sess-2"
    tags = json.loads(item.topic_tags)
    assert "tech" in tags
    assert "climate" in tags


def test_upsert_vocabulary_deduplicates_topic_tags(db_session):
    upsert_vocabulary("user-1", "mitigate", "sess-1", db_session, topic_tags=["tech"])
    item = upsert_vocabulary(
        "user-1", "mitigate", "sess-2", db_session, topic_tags=["tech"]
    )

    tags = json.loads(item.topic_tags)
    assert tags.count("tech") == 1


def test_upsert_vocabulary_different_users_are_independent(db_session):
    upsert_vocabulary("user-1", "ubiquitous", "sess-1", db_session)
    upsert_vocabulary("user-2", "ubiquitous", "sess-1", db_session)
    upsert_vocabulary("user-1", "ubiquitous", "sess-2", db_session)

    item1 = upsert_vocabulary("user-1", "ubiquitous", "sess-3", db_session)
    item2 = upsert_vocabulary("user-2", "ubiquitous", "sess-3", db_session)

    assert item1.times_seen == 3
    assert item2.times_seen == 2


# ---------------------------------------------------------------------------
# get_vocabulary_for_review
# ---------------------------------------------------------------------------


def test_get_vocabulary_for_review_returns_new_and_learning_not_mastered(db_session):
    new_item = upsert_vocabulary("user-1", "ubiquitous", "sess-1", db_session)
    new_item.status = "new"
    db_session.add(new_item)

    learning_item = upsert_vocabulary("user-1", "mitigate", "sess-1", db_session)
    learning_item.status = "learning"
    db_session.add(learning_item)

    mastered_item = upsert_vocabulary("user-1", "climate", "sess-1", db_session)
    mastered_item.status = "mastered"
    db_session.add(mastered_item)
    db_session.commit()

    result = get_vocabulary_for_review("user-1", db_session)
    words = {r.word for r in result}

    assert "ubiquitous" in words
    assert "mitigate" in words
    assert "climate" not in words


def test_get_vocabulary_for_review_excludes_mastered(db_session):
    item = upsert_vocabulary("user-1", "ubiquitous", "sess-1", db_session)
    item.status = "mastered"
    db_session.add(item)
    db_session.commit()

    assert get_vocabulary_for_review("user-1", db_session) == []


def test_get_vocabulary_for_review_returns_empty_for_new_user(db_session):
    assert get_vocabulary_for_review("user-with-no-vocab", db_session) == []
