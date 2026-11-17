"""OAuth state issuance and validation.

A login request issues a state that is:
- stored server-side with a 10 minute expiry,
- bound to the initiating browser via an HMAC-signed HttpOnly cookie,
- consumed on first callback use (single-use: replayed or unknown states fail).
"""
import hmac
import secrets
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import OAuthState

STATE_TTL_SECONDS = 600
COOKIE_NAME = "mailpilot_oauth_state"

# Fallback signing key when OAUTH_STATE_SECRET is not configured: random per
# process, so in-flight logins simply fail closed after a restart.
_process_key = secrets.token_bytes(32)


def _signing_key() -> bytes:
    configured = get_settings().oauth_state_secret
    return configured.encode() if configured else _process_key


def sign_state(state: str) -> str:
    """HMAC signature binding a state value to the browser cookie."""
    return hmac.new(_signing_key(), state.encode(), sha256).hexdigest()


def issue_state(db: Session, state: str, code_verifier: str) -> None:
    """Persist a freshly generated state + PKCE verifier with expiry; drop expired rows."""
    db.execute(delete(OAuthState).where(OAuthState.expires_at <= datetime.utcnow()))
    db.add(
        OAuthState(
            state=state,
            code_verifier=code_verifier,
            expires_at=datetime.utcnow() + timedelta(seconds=STATE_TTL_SECONDS),
        )
    )
    db.commit()


def consume_state(db: Session, state: str) -> str | None:
    """Validate and consume a state atomically (single DELETE ... RETURNING).

    Returns the stored PKCE code verifier on success, None on failure.
    Single-use: the row is deleted on first presentation, so replays, unknown
    states and expired states all fail, including under concurrent callbacks."""
    row = db.execute(
        delete(OAuthState)
        .where(OAuthState.state == state)
        .returning(OAuthState.code_verifier, OAuthState.expires_at)
    ).first()
    db.commit()
    if row is None or row.expires_at <= datetime.utcnow():
        return None
    return row.code_verifier


def cookie_matches(cookie_value: str | None, state: str) -> bool:
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, sign_state(state))
