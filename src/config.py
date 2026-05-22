from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ANTHROPIC_API_KEY: str
    DATABASE_URL: str = "sqlite:///./tutor.db"

    # Model tier constants — not from env
    model_opus: str = "claude-opus-4-7"
    model_sonnet: str = "claude-sonnet-4-6"
    model_haiku: str = "claude-haiku-4-5-20251001"

    # Per-tier max_tokens
    max_tokens_opus: int = 4096
    max_tokens_sonnet: int = 2048
    max_tokens_haiku: int = 1024

    # Per-tier timeout_seconds
    timeout_opus: int = 60
    timeout_sonnet: int = 30
    timeout_haiku: int = 15


settings = Settings()
