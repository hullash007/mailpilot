"""Owner login for HTTPS browser access; no raw access secret in cookies."""
from collections import deque
import hmac
import threading
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import get_settings
from ..services.access import COOKIE_NAME, SESSION_SECONDS, issue_session, require_same_origin

router = APIRouter()
# Single-process owner application: globally bounded login attempts, not an
# unbounded per-IP dictionary. Reverse proxy must not run multiple workers.
_attempts: deque[float] = deque()
_lock = threading.Lock()

_FORM = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MailPilot - Access</title></head>
<body style="font-family:sans-serif;max-width:360px;margin:80px auto">
<h1 style="font-size:18px">MailPilot access</h1>
<form method="post" action="/access">
<label for="token">Owner access key</label>
<input type="password" id="token" name="token" autocomplete="current-password"
 required maxlength="256" style="width:100%;padding:8px;margin-bottom:8px">
<button type="submit" style="padding:8px 16px">Continue</button>
</form></body></html>"""


@router.get("/access", response_class=HTMLResponse)
def access_form() -> HTMLResponse:
    return HTMLResponse(_FORM)


@router.post("/access")
def access_login(request: Request, token: str = Form(..., max_length=256)):
    require_same_origin(request)
    with _lock:
        now = time.monotonic()
        while _attempts and _attempts[0] <= now - 60:
            _attempts.popleft()
        if len(_attempts) >= 10:
            raise HTTPException(status_code=429, detail="Try again in one minute.", headers={"Retry-After": "60"})
        _attempts.append(now)
    settings = get_settings()
    expected = settings.app_access_token
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid access key.")
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie(COOKIE_NAME, issue_session(expected), httponly=True,
                        samesite="lax", secure=settings.cookie_secure, max_age=SESSION_SECONDS)
    return response


@router.post("/logout")
def access_logout(request: Request):
    require_same_origin(request)
    response = RedirectResponse("/access", status_code=303)
    response.delete_cookie(COOKIE_NAME, secure=get_settings().cookie_secure,
                           httponly=True, samesite="lax")
    return response
