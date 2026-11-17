"""OAuth state validation: browser binding, expiry, single-use."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes
from app.database import Base, get_db
from app.models import GmailAccount, OAuthState
from app.config import get_settings
from app.services import oauth_state

TEST_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    from app.main import create_app as _create

    app = _create()
    app.dependency_overrides[get_db] = lambda: session

    monkeypatch.setattr(
        routes.oauth,
        "authorization_url",
        lambda: ("https://accounts.example/auth", "state-abc", "verifier-123"),
    )
    captured: dict = {}

    def _fake_exchange(code, state=None, code_verifier=None):
        captured["code_verifier"] = code_verifier
        return {"token": "tok", "refresh_token": "ref", "scopes": ["readonly"]}

    monkeypatch.setattr(routes.oauth, "exchange_code", _fake_exchange)
    monkeypatch.setattr(
        routes.GmailClient,
        "get_profile",
        lambda self: {"emailAddress": "bob@example.com", "historyId": "1"},
    )
    http = TestClient(app)
    http.captured_exchange = captured
    yield http, session
    session.close()


def test_login_issues_state_and_binding_cookie(client):
    http, session = client
    resp = http.get("/auth/google/login")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "state-abc"
    row = session.get(OAuthState, "state-abc")
    assert row is not None and row.expires_at > datetime.utcnow()
    assert row.code_verifier == "verifier-123"
    cookie = resp.cookies.get(oauth_state.COOKIE_NAME)
    assert cookie == oauth_state.sign_state("state-abc")
    # Lax (not Strict): the Google callback is a cross-site top-level GET and
    # Strict would withhold the cookie, failing every real browser login.
    assert "samesite=lax" in resp.headers["set-cookie"].lower()


def test_callback_passes_stored_pkce_verifier(client):
    http, session = client
    http.get("/auth/google/login")
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert resp.status_code == 200
    assert http.captured_exchange["code_verifier"] == "verifier-123"


def test_callback_success_consumes_state(client):
    http, session = client
    http.get("/auth/google/login")
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert resp.status_code == 200
    assert resp.json()["connected"] == "bob@example.com"
    assert session.get(OAuthState, "state-abc") is None
    assert session.query(GmailAccount).one().email_address == "bob@example.com"


def test_callback_rejects_replayed_state(client):
    http, _ = client
    http.get("/auth/google/login")
    first = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert first.status_code == 200
    replay = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert replay.status_code == 403


def test_callback_rejects_unknown_state(client):
    http, _ = client
    http.get("/auth/google/login")
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "attacker-state"})
    assert resp.status_code == 403


def test_callback_rejects_missing_cookie(client):
    http, _ = client
    http.get("/auth/google/login")
    http.cookies.clear()
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert resp.status_code == 403


def test_callback_rejects_wrong_cookie(client):
    http, _ = client
    http.get("/auth/google/login")
    http.cookies.clear()
    http.cookies.set(oauth_state.COOKIE_NAME, oauth_state.sign_state("someone-elses-state"))
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert resp.status_code == 403


def test_callback_rejects_expired_state(client):
    http, session = client
    http.get("/auth/google/login")
    row = session.get(OAuthState, "state-abc")
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    session.commit()
    resp = http.get("/auth/google/callback", params={"code": "c1", "state": "state-abc"})
    assert resp.status_code == 403
