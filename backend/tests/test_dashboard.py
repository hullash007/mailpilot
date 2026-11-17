"""Dashboard tests: renders stored emails, escapes HTML, handles empty inbox."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.dashboard import _esc
from app.database import Base, get_db
from app.models import Email, GmailAccount, Thread


@pytest.fixture()
def client():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # create_app() runs create_all on the default engine; the dependency
    # override below points the app at the in-memory database instead.
    from app.main import create_app as _create

    app = _create()
    app.dependency_overrides[get_db] = lambda: session
    yield TestClient(app), session
    session.close()


def _seed(session):
    account = GmailAccount(email_address="bob@example.com")
    session.add(account)
    session.flush()
    thread = Thread(account_id=account.id, gmail_thread_id="t1", subject="Hello", snippet="snip")
    session.add(thread)
    session.flush()
    session.add(
        Email(
            thread_id=thread.id,
            gmail_message_id="m1",
            subject="Interview <script>alert(1)</script>",
            sender="Recruiter <jobs@example.com>",
            recipients="bob@example.com",
            snippet="We would like to schedule...",
            body_text="body",
            headers={},
            label_ids=["INBOX", "IMPORTANT"],
            internal_date=datetime(2026, 11, 17, 9, 30),
        )
    )
    session.commit()


def test_dashboard_empty(client):
    tc, _ = client
    resp = tc.get("/dashboard")
    assert resp.status_code == 200
    assert "No emails stored yet" in resp.text


def test_dashboard_lists_and_escapes(client):
    tc, session = client
    _seed(session)
    resp = tc.get("/dashboard")
    assert resp.status_code == 200
    assert "Recruiter" in resp.text
    assert "IMPORTANT" in resp.text
    assert "<script>alert(1)</script>" not in resp.text
    assert "&lt;script&gt;" in resp.text


def test_esc():
    assert _esc('<a href="x">&') == '&lt;a href=&quot;x&quot;&gt;&amp;'
