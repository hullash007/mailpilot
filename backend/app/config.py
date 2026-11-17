"""Environment-driven configuration. No secrets are hardcoded."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    database_url: str = "postgresql+psycopg2://mailpilot:mailpilot@localhost:5432/mailpilot"
    # Signs the cookie that binds OAuth state to the initiating browser.
    # Empty = random per process (in-flight logins fail closed on restart).
    oauth_state_secret: str = ""
    # Fernet key encrypting stored OAuth tokens. Required before connecting
    # any Gmail account; empty = token storage fails closed.
    token_encryption_key: str = ""
    # OpenAI-compatible LLM endpoint (private VM / tunnel only) - Phase 2.
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    # Off by default: when on, /classify backfill asks the private endpoint to
    # refine each rule-based suggestion; failures fall back to the rule result.
    llm_refine: bool = False
    # Access boundary: empty = loopback-only (fail closed for remote).
    # Set for any remote/VM exposure; then serve over HTTPS with cookie_secure.
    app_access_token: str = ""
    cookie_secure: bool = False

    # Phase 1 is read-only by design.
    gmail_scopes: list[str] = ["https://www.googleapis.com/auth/gmail.readonly"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
