"""Access boundary for every endpoint, including the dashboard.

Two modes, fail closed:
- No APP_ACCESS_TOKEN: loopback only (local dev default). Any remote client
  is refused - bind uvicorn to 127.0.0.1 and reach remote machines via an
  SSH tunnel.
- APP_ACCESS_TOKEN set: every request needs the token (Bearer header or the
  mailpilot_access cookie set via POST /access). Serve over HTTPS and set
  COOKIE_SECURE=1 when remote.
"""
import hmac

from fastapi import HTTPException, Request

from ..config import get_settings

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
COOKIE_NAME = "mailpilot_access"


def require_access(request: Request) -> bool:
    settings = get_settings()
    token = settings.app_access_token
    host = request.client.host if request.client else ""
    if not token:
        if host in LOOPBACK_HOSTS:
            return True
        raise HTTPException(
            status_code=403,
            detail="Remote access refused: set APP_ACCESS_TOKEN and serve over HTTPS.",
        )
    provided = ""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        provided = auth[7:].strip()
    if not provided:
        provided = request.cookies.get(COOKIE_NAME, "")
    if provided and hmac.compare_digest(provided, token):
        return True
    raise HTTPException(status_code=401, detail="Valid access token required.")
