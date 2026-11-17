"""Access boundary: loopback open by default, remote refused, token mode."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db


@pytest.fixture(autouse=True)
def _clear():
    from app.api.access_routes import _attempts
    _attempts.clear()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    from app.main import create_app as _create
    app = _create()
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app)
    session.close()


def test_loopback_open_without_token(client):
    assert client.get("/emails").status_code == 200
    assert client.get("/dashboard").status_code == 200


def test_token_mode_requires_token(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "sekret")
    get_settings.cache_clear()
    assert client.get("/emails").status_code == 401
    assert client.get("/dashboard").status_code == 401
    assert client.post("/classify/1").status_code == 401  # backfill route is behind the same boundary
    assert client.post("/refine/1").status_code == 401  # LLM second-opinion route too
    assert client.get("/emails", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/emails", headers={"Authorization": "Bearer sekret"}).status_code == 200


def test_access_form_sets_cookie(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "sekret")
    get_settings.cache_clear()
    resp = client.post("/access", data={"token": "sekret"}, follow_redirects=False)
    assert resp.status_code == 303
    assert client.get("/emails").status_code == 200  # cookie now present


def test_access_form_rejects_wrong_token(client, monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "sekret")
    get_settings.cache_clear()
    resp = client.post("/access", data={"token": "nope"}, follow_redirects=False)
    assert resp.status_code == 403
