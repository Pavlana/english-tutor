"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from src.db.engine import create_db_and_tables
from src.routers.chat import router as chat_router

load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create database tables on startup."""
    create_db_and_tables()
    yield


app = FastAPI(title="English Tutor", version="0.1.0", lifespan=lifespan)

app.include_router(chat_router)


@app.get("/health")
async def health() -> dict:
    """Return service liveness status."""
    return {"status": "ok"}
