"""Pytest configuration and shared fixtures."""

import os
from collections.abc import Generator

import pytest
from sqlmodel import Session, SQLModel, create_engine

# noqa: F401 — side-effect imports: registers tables with SQLModel metadata
# so that SQLModel.metadata.create_all() creates every table in the fixture engine.
from src.db.schemas import (  # noqa: F401
    EvalResult,
    LearningLog,
    LlmCall,
    TutorSession,
    User,
    UserProfile,
    VocabularyItem,
)


def pytest_configure(config: pytest.Config) -> None:
    """Set required env vars before collection triggers module-level Settings().

    pydantic-settings instantiates Settings() at import time, so the API key
    must be present before test modules are collected and src/ is imported.
    """
    os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-real")


@pytest.fixture(autouse=True)
def patch_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure ANTHROPIC_API_KEY is set to a safe placeholder for every test."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")


@pytest.fixture(name="db_session")
def db_session_fixture() -> Generator[Session, None, None]:
    """Yield a fresh in-memory SQLite session; each test gets its own DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
