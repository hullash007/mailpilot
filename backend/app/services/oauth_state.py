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


def issue_state(db: Session, state: str) -> None:
    """Persist a freshly generated state with its expiry."""
    db.add(OAuthState(state=state, expires_at=datetime.utcnow() + timedelta(seconds=STATE_TTL_SECONDS)))
    db.commit()


def consume_state(db: Session, state: str) -> bool:
    """Validate and consume a state. Single-use: the row is deleted on first
    presentation, so replays, unknown states and expired states all fail."""
    row = db.get(OAuthState, state)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return row.expires_at > datetime.utcnow()


def cookie_matches(cookie_value: str | None, state: str) -> bool:
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, sign_state(state))
