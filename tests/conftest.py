"""Pytest configuration and shared fixtures."""

import os

import pytest


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
