"""SQLModel engine, session factory, and table initialisation."""

from collections.abc import Generator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from src.config import settings

engine = create_engine(settings.DATABASE_URL)


def create_db_and_tables() -> None:
    """Create all SQLModel tables that have not yet been created."""
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Yield a database session and close it when the block exits."""
    with Session(engine) as session:
        yield session
