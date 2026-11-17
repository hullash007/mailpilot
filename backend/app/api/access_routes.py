"""Token login for remote/browser access (sets the access cookie)."""
from fastapi import APIRouter, Form, HTTPException, Response
from fastapi.responses import HTMLResponse
import hmac

from ..config import get_settings
from ..services.access import COOKIE_NAME

router = APIRouter()

_FORM = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MailPilot - Access</title></head>
<body style="font-family:sans-serif;max-width:360px;margin:80px auto">
<h1 style="font-size:18px">MailPilot access</h1>
<form method="post" action="/access">
<input type="password" name="token" placeholder="Access token" autofocus
 style="width:100%;padding:8px;margin-bottom:8px">
<button type="submit" style="padding:8px 16px">Continue</button>
</form></body></html>"""


@router.get("/access", response_class=HTMLResponse)
def access_form() -> HTMLResponse:
    return HTMLResponse(_FORM)


@router.post("/access")
def access_login(response: Response, token: str = Form(...)):
    expected = get_settings().app_access_token
    if not expected or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="Invalid access token.")
    response = HTMLResponse('<meta http-equiv="refresh" content="0; url=/dashboard">')
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        secure=get_settings().cookie_secure,
        max_age=7 * 24 * 3600,
    )
    return response
