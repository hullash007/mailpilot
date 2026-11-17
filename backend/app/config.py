"""Environment-driven configuration. No secrets are hardcoded."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    database_url: str = "postgresql+psycopg2://mailpilot:mailpilot@localhost:5432/mailpilot"

    # Phase 1 is read-only by design.
    gmail_scopes: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
