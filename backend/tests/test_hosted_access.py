"""Hosted owner-session boundary; all synthetic, no Google calls."""
import time
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from cryptography.fernet import Fernet

from app.config import get_settings
from app.database import Base, get_db
from app.services.access import COOKIE_NAME, issue_session, valid_session, SESSION_SECONDS
from app.api.access_routes import _attempts

ORIGIN = "https://mailpilot.example"
KEY = "a" * 48


@pytest.fixture()
def hosted(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", KEY)
    monkeypatch.setenv("PUBLIC_ORIGIN", ORIGIN)
    monkeypatch.setenv("COOKIE_SECURE", "1")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    _attempts.clear()
    from app.main import create_app
    from app.api import routes
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(routes.oauth, "authorization_url", lambda: ("https://accounts.example/auth", "state", "verifier"))
    monkeypatch.setattr(routes.oauth, "exchange_code", lambda *a, **k: {"token": "synthetic"})
    monkeypatch.setattr(routes.GmailClient, "get_profile", lambda self: {"emailAddress": "fixture@example.com"})
    client = TestClient(app, base_url=ORIGIN)
    yield client
    client.close()
    session.close()
    get_settings.cache_clear()
    _attempts.clear()


def login(client):
    return client.post("/access", data={"token": KEY}, headers={"Origin": ORIGIN}, follow_redirects=False)


def test_session_not_raw_secret_and_cookie_attributes(hosted):
    response = login(hosted)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert KEY not in cookie
    assert hosted.get("/inbox").status_code == 200


@pytest.mark.parametrize("origin", [None, "null", "https://evil.example", ORIGIN + ".evil.example"])
def test_login_requires_exact_origin(hosted, origin):
    headers = {} if origin is None else {"Origin": origin}
    assert hosted.post("/access", data={"token": KEY}, headers=headers).status_code == 403


def test_raw_cookie_rejected(hosted):
    hosted.cookies.set(COOKIE_NAME, KEY)
    assert hosted.get("/inbox").status_code == 401


def test_expiry_tamper_rotation_and_future(monkeypatch):
    now = time.time()
    monkeypatch.setattr("app.services.access.time.time", lambda: now)
    value = issue_session(KEY)
    assert valid_session(value, KEY)
    assert not valid_session(value + "x", KEY)
    assert not valid_session(value, "b" * 48)
    monkeypatch.setattr("app.services.access.time.time", lambda: now + SESSION_SECONDS + 1)
    assert not valid_session(value, KEY)
    monkeypatch.setattr("app.services.access.time.time", lambda: now - 5)
    assert not valid_session(value, KEY)


def test_cookie_post_origin_gate_bearer_unaffected(hosted):
    login(hosted)
    assert hosted.post("/classify/123").status_code == 403
    assert hosted.post("/classify/123", headers={"Origin": "https://evil.example"}).status_code == 403
    assert hosted.post("/classify/123", headers={"Origin": ORIGIN}).status_code == 404
    hosted.cookies.clear()
    assert hosted.post("/classify/123", headers={"Authorization": "Bearer " + KEY}).status_code == 404


def test_both_callback_cookies_lax_and_secure(hosted):
    login(hosted)
    response = hosted.get("/auth/google/login")
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()
    response = hosted.get("/auth/google/callback?code=fixture&state=state")
    assert response.status_code == 200
    assert response.json()["connected"] == "fixture@example.com"


def test_callback_owner_boundary_still_required(hosted):
    login(hosted)
    hosted.get("/auth/google/login")
    hosted.cookies.delete(COOKIE_NAME)
    assert hosted.get("/auth/google/callback?code=fixture&state=state").status_code == 401


def test_rate_limit_and_large_body(hosted):
    for _ in range(10):
        assert hosted.post("/access", data={"token": "wrong"}, headers={"Origin": ORIGIN}).status_code == 403
    assert login(hosted).status_code == 429
    assert hosted.post("/access", content=b"x" * 4097).status_code == 413


def test_host_and_security_headers(hosted):
    response = hosted.get("/access")
    for name in ["cache-control", "content-security-policy", "strict-transport-security", "x-frame-options", "referrer-policy"]:
        assert name in response.headers
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "same-origin"
    assert hosted.get("/access", headers={"host": "evil.example"}).status_code == 400


def test_logout_clears_session(hosted):
    login(hosted)
    assert hosted.post("/logout", headers={"Origin": ORIGIN}, follow_redirects=False).status_code == 303
    assert hosted.get("/inbox").status_code == 401


@pytest.mark.parametrize("origin,secure,key", [("http://example.com", "1", KEY), (ORIGIN, "0", KEY), (ORIGIN, "1", "short"), (ORIGIN + "/", "1", KEY)])
def test_hosted_misconfiguration_refused(monkeypatch, origin, secure, key):
    monkeypatch.setenv("PUBLIC_ORIGIN", origin)
    monkeypatch.setenv("COOKIE_SECURE", secure)
    monkeypatch.setenv("APP_ACCESS_TOKEN", key)
    get_settings.cache_clear()
    from app.main import create_app
    with pytest.raises(RuntimeError):
        create_app()
    get_settings.cache_clear()


def test_google_identity_restriction(hosted, monkeypatch):
    monkeypatch.setenv("GOOGLE_ALLOWED_EMAIL", "owner@example.com")
    get_settings.cache_clear()
    login(hosted)
    hosted.get("/auth/google/login")
    response = hosted.get("/auth/google/callback?code=fixture&state=state")
    assert response.status_code == 403
    assert hosted.get("/accounts").json()["accounts"] == []


@pytest.mark.parametrize("kind", ["host", "body"])
def test_early_rejections_keep_security_headers(hosted, kind):
    if kind == "host":
        response = hosted.get("/access", headers={"host": "evil.example"})
        assert response.status_code == 400
    else:
        response = hosted.post("/access", content=b"x" * 4097)
        assert response.status_code == 413
    for name in ["cache-control", "content-security-policy", "strict-transport-security", "x-frame-options", "referrer-policy", "x-content-type-options"]:
        assert name in response.headers
    assert response.headers["cache-control"] == "no-store"
