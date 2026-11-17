"""Google OAuth for Gmail. Phase 1 requests the read-only scope only."""
from google_auth_oauthlib.flow import Flow

from ...config import get_settings


def build_flow(state: str | None = None) -> Flow:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError(
            "Google OAuth is not configured: set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in the environment."
        )
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_redirect_uri],
            }
        },
        scopes=settings.gmail_scopes,
        state=state,
    )
    flow.redirect_uri = settings.google_redirect_uri
    return flow


def authorization_url() -> tuple[str, str]:
    """Return (url, state). The caller must keep `state` to verify the callback."""
    flow = build_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state


def exchange_code(code: str, state: str | None = None) -> dict:
    """Trade an authorization code for tokens. Returns a plain credential dict."""
    flow = build_flow(state=state)
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes or []),
    }
