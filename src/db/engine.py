"""SQLModel engine, session factory, and table initialisation."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine

from src.config import settings

# Side-effect imports: registering all table classes with SQLModel.metadata
# before create_db_and_tables() calls SQLModel.metadata.create_all().
from src.db.schemas import (  # noqa: F401
    EvalResult,
    LearningLog,
    LlmCall,
    TutorSession,
    User,
    UserProfile,
    VocabularyItem,
)

engine = create_engine(settings.DATABASE_URL)


def _run_migrations() -> None:
    """Apply additive schema changes that create_all() cannot handle.

    SQLModel's create_all() creates missing tables but does not ALTER existing
    ones. Each migration here is idempotent — it checks whether a column exists
    before issuing ALTER TABLE, so it is safe to run on every startup.
    """
    with engine.connect() as conn:
        # Phase 5: add usage_signal to vocabularyitem.
        result = conn.execute(text("PRAGMA table_info(vocabularyitem)"))
        col_names = [row[1] for row in result]
        if "usage_signal" not in col_names:
            conn.execute(
                text("ALTER TABLE vocabularyitem ADD COLUMN usage_signal TEXT")
            )
            conn.commit()


def create_db_and_tables() -> None:
    """Create all SQLModel tables that have not yet been created, then migrate."""
    SQLModel.metadata.create_all(engine)
    _run_migrations()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a database session and close it when the block exits."""
    with Session(engine) as session:
        yield session
