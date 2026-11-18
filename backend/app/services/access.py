"""Owner access: bearer clients or short-lived signed browser sessions.

Hosted deployment must set PUBLIC_ORIGIN, COOKIE_SECURE and a strong
APP_ACCESS_TOKEN, and bind the application to loopback behind HTTPS.
"""
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from ..config import get_settings

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
COOKIE_NAME = "mailpilot_access"
SESSION_SECONDS = 12 * 3600


def _signature(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), ("mailpilot-session-v1:" + payload).encode(), hashlib.sha256).hexdigest()


def issue_session(secret: str) -> str:
    payload = f"{int(time.time())}.{secrets.token_hex(24)}"
    return payload + "." + _signature(payload, secret)


def valid_session(value: str, secret: str) -> bool:
    if not secret or len(value) > 160:
        return False
    try:
        issued, nonce, signature = value.split(".")
        timestamp = int(issued)
    except (ValueError, TypeError):
        return False
    if len(nonce) != 48 or len(signature) != 64:
        return False
    age = time.time() - timestamp
    return 0 <= age <= SESSION_SECONDS and hmac.compare_digest(signature, _signature(issued + "." + nonce, secret))


def require_same_origin(request: Request) -> None:
    """Cookie-authenticated writes and login require an exact origin in hosted mode.

    Bearer clients do not call this check: credentials are explicit, not ambient.
    Missing/null Origin is refused in hosted mode rather than trusting Referer.
    """
    origin = getattr(request.app.state, "explicit_origin", None) or get_settings().public_origin
    if origin and request.headers.get("origin") != origin:
        raise HTTPException(status_code=403, detail="Same-origin request required.")


def require_access(request: Request) -> bool:
    settings = get_settings()
    token = settings.app_access_token
    host = request.client.host if request.client else ""
    if not token:
        if host in LOOPBACK_HOSTS and not settings.public_origin:
            return True
        raise HTTPException(status_code=403, detail="Owner access is not configured.")
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), token):
        return True
    if valid_session(request.cookies.get(COOKIE_NAME, ""), token):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_same_origin(request)
        return True
    raise HTTPException(status_code=401, detail="Owner login required.")
