"""Google OAuth for Gmail. Phase 1 requests the read-only scope only."""
from dataclasses import dataclass, field

from google_auth_oauthlib.flow import Flow

from ...config import get_settings


@dataclass(frozen=True)
class OAuthFlowConfig:
    """Explicit application configuration, never an account identity."""
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    scopes: tuple[str, ...]

    def __post_init__(self):
        if (any(type(value) is not str or not value.strip()
                for value in (self.client_id, self.client_secret, self.redirect_uri))
                or type(self.scopes) is not tuple or not self.scopes
                or any(type(scope) is not str or not scope.strip() for scope in self.scopes)):
            raise ValueError("Explicit OAuth flow configuration is incomplete.")


def build_explicit_flow(config: OAuthFlowConfig, state: str | None = None) -> Flow:
    if not isinstance(config, OAuthFlowConfig):
        raise TypeError("Explicit OAuth flow configuration is required.")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [config.redirect_uri],
            }
        },
        scopes=list(config.scopes),
        state=state,
    )
    flow.redirect_uri = config.redirect_uri
    return flow


def build_flow(state: str | None = None) -> Flow:
    """Legacy settings adapter. Explicit composition uses build_explicit_flow."""
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError(
            "Google OAuth is not configured: set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in the environment."
        )
    return build_explicit_flow(OAuthFlowConfig(
        settings.google_client_id, settings.google_client_secret,
        settings.google_redirect_uri, tuple(settings.gmail_scopes)), state)


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
