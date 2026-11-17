"""Token encryption at rest: roundtrip, fail-closed without key, sample banner."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.models import Email, GmailAccount, Thread
from app.services import crypto

TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_token_roundtrip():
    payload = {"token": "abc", "refresh_token": "xyz", "scopes": ["readonly"]}
    blob = crypto.encrypt_tokens(payload)
    assert "abc" not in blob and "xyz" not in blob
    assert crypto.decrypt_tokens(blob) == payload


def test_storage_fails_closed_without_key(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(crypto.TokenEncryptionError):
        crypto.encrypt_tokens({"token": "abc"})


def test_wrong_key_rejected():
    blob = crypto.encrypt_tokens({"token": "abc"})
    other = Fernet(Fernet.generate_key())
    with pytest.raises(crypto.InvalidToken):
        other.decrypt(blob.encode())


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    from app.main import create_app as _create

    app = _create()
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app), session
    session.close()


def _seed(session, is_sample):
    account = GmailAccount(email_address="bob@example.com")
    session.add(account)
    session.flush()
    thread = Thread(account_id=account.id, gmail_thread_id="t1", subject="Hi", snippet="s")
    session.add(thread)
    session.flush()
    session.add(Email(thread_id=thread.id, account_id=account.id, gmail_message_id="m1", subject="Hi",
                      sender="a@example.com", is_sample=is_sample))
    session.commit()


def test_dashboard_labels_sample_data(client):
    http, session = client
    _seed(session, True)
    html = http.get("/dashboard").text
    assert "sample data" in html.lower()
    assert ">SAMPLE<" in html


def test_dashboard_no_sample_banner_for_real_data(client):
    http, session = client
    _seed(session, False)
    html = http.get("/dashboard").text
    assert "Showing sample data" not in html
    assert ">SAMPLE<" not in html
