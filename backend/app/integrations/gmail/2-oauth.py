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


def authorization_url() -> tuple[str, str, str]:
    """Return (url, state, code_verifier).

    The library generates a PKCE code_verifier per flow instance and sends its
    challenge in the URL. The verifier must survive until the callback (a new
    flow instance there cannot regenerate it), so the caller stores it
    server-side with the state and passes it to exchange_code.
    """
    flow = build_flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def exchange_code(code: str, state: str | None = None, code_verifier: str | None = None) -> dict:
    """Trade an authorization code for tokens. Returns a plain credential dict."""
    flow = build_flow(state=state)
    flow.code_verifier = code_verifier
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
