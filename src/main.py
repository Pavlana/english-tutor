"""FastAPI application entry point."""

from dotenv import load_dotenv
from fastapi import FastAPI

from src.routers.chat import router as chat_router

load_dotenv()

app = FastAPI(title="English Tutor", version="0.1.0")

app.include_router(chat_router)


@app.get("/health")
async def health() -> dict:
    """Return service liveness status."""
    return {"status": "ok"}
