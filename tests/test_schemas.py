"""Tests for SQLModel table definitions in src/db/schemas.py."""

import json

from sqlmodel import Session, SQLModel, create_engine, text

# noqa: F401 on this block — EvalResult, LearningLog, LlmCall, User, UserProfile,
# VocabularyItem are side-effect imports that register table classes with
# SQLModel.metadata before create_all() runs. TutorSession is used in tests.
from src.db.schemas import (  # noqa: F401
    EvalResult,
    LearningLog,
    LlmCall,
    TutorSession,
    User,
    UserProfile,
    VocabularyItem,
)


def _in_memory_engine():
    """Return a fresh in-memory SQLite engine with all tables created."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def test_all_tables_exist_after_create_all():
    engine = _in_memory_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        ).fetchall()
    table_names = {row[0] for row in rows}
    expected = {
        "llmcall",
        "user",
        "userprofile",
        "tutorsession",
        "vocabularyitem",
        "learninglog",
        "evalresult",
    }
    assert expected.issubset(table_names)


def test_tutorsession_default_tasks_has_all_four_keys_not_started():
    engine = _in_memory_engine()
    row = TutorSession(
        session_id="sess-1",
        user_id="user-1",
        topic="climate change",
    )
    with Session(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)

    tasks = json.loads(row.tasks)
    assert set(tasks.keys()) == {"listening", "writing", "speaking", "grammar"}
    for task_data in tasks.values():
        assert task_data["status"] == "not_started"
