"""Baseline classification: rule units, ingest-time classification, backfill API."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Email, GmailAccount, Thread
from app.services import ingestion
from app.services.classify import classify_email

TEST_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_receipt_from_subject():
    category, priority = classify_email(headers={}, label_ids=["INBOX"], sender="shop@example.com", subject="Your receipt from Store #123")
    assert category == "receipt"
    assert priority == "normal"


def test_newsletter_from_list_unsubscribe():
    category, priority = classify_email(
        headers={"List-Unsubscribe": "<mailto:unsub@example.com>"},
        label_ids=["INBOX"],
        sender="news@example.com",
        subject="Weekly digest",
    )
    assert category == "newsletter"
    assert priority == "low"


def test_promotions_from_gmail_label():
    category, priority = classify_email(headers={}, label_ids=["INBOX", "CATEGORY_PROMOTIONS"], sender="deals@example.com", subject="50% off")
    assert category == "promotions"
    assert priority == "low"


def test_notification_from_automated_sender():
    category, _ = classify_email(headers={}, label_ids=["INBOX"], sender="no-reply@service.example.com", subject="Sign-in attempt")
    assert category == "notification"


def test_notification_from_auto_submitted_header():
    category, _ = classify_email(headers={"Auto-Submitted": "auto-generated"}, label_ids=[], sender="alerts@example.com", subject="Report")
    assert category == "notification"


def test_personal_default_and_urgency_priority():
    category, priority = classify_email(headers={}, label_ids=["INBOX"], sender="alice@example.com", subject="Action required: confirm travel")
    assert category == "personal"
    assert priority == "high"


def test_header_names_are_case_insensitive():
    category, _ = classify_email(headers={"list-unsubscribe": "<mailto:x@y.z>"}, label_ids=[], sender="a@b.c", subject="Hi")
    assert category == "newsletter"


class StubGmailClient:
    def __init__(self, credentials_dict):
        pass

    def list_message_ids(self, max_results=20, query=None):
        return ["m1", "m2"]

    def get_message(self, message_id):
        if message_id == "m1":
            return {
                "gmail_message_id": "m1",
                "gmail_thread_id": "t1",
                "subject": "Your receipt from Store",
                "sender": "shop@example.com",
                "recipients": "bob@example.com",
                "snippet": "s",
                "body_text": "b",
                "headers": {},
                "label_ids": ["INBOX"],
                "internal_date": None,
            }
        return {
            "gmail_message_id": "m2",
            "gmail_thread_id": "t2",
            "subject": "Weekly digest",
            "sender": "news@example.com",
            "recipients": "bob@example.com",
            "snippet": "s",
            "body_text": "b",
            "headers": {"List-Unsubscribe": "<mailto:unsub@example.com>"},
            "label_ids": ["INBOX"],
            "internal_date": None,
        }


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    from app.main import create_app as _create

    app = _create()
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(ingestion, "GmailClient", StubGmailClient)
    yield TestClient(app), session
    session.close()


def test_ingest_classifies_new_mail(client):
    http, session = client
    account = GmailAccount(email_address="bob@example.com")
    account.credentials = {"token": "t"}
    session.add(account)
    session.commit()

    resp = http.post(f"/ingest/{account.id}?max_results=2")
    assert resp.status_code == 200
    rows = {e.gmail_message_id: e for e in session.scalars(select(Email)).all()}
    assert rows["m1"].category == "receipt"
    assert rows["m2"].category == "newsletter"
    assert rows["m2"].priority == "low"
    assert all(e.classified_at is not None for e in rows.values())


def test_classify_endpoint_backfills_and_is_idempotent(client):
    http, session = client
    account = GmailAccount(email_address="bob@example.com")
    session.add(account)
    session.commit()
    thread = Thread(account_id=account.id, gmail_thread_id="t1")
    session.add(thread)
    session.commit()
    session.add(
        Email(
            thread_id=thread.id,
            account_id=account.id,
            gmail_message_id="old1",
            subject="Your invoice",
            sender="vendor@example.com",
        )
    )
    session.commit()

    first = http.post(f"/classify/{account.id}")
    assert first.status_code == 200
    assert first.json() == {"classified": 1}
    row = session.scalar(select(Email).where(Email.gmail_message_id == "old1"))
    assert row.category == "receipt"

    second = http.post(f"/classify/{account.id}")
    assert second.json() == {"classified": 0}


def test_classify_endpoint_unknown_account(client):
    http, _ = client
    assert http.post("/classify/999").status_code == 404


def test_emails_response_includes_classification(client):
    http, session = client
    account = GmailAccount(email_address="bob@example.com")
    account.credentials = {"token": "t"}
    session.add(account)
    session.commit()
    http.post(f"/ingest/{account.id}?max_results=1")

    resp = http.get("/emails?limit=1")
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["category"] == "receipt"
    assert row["priority"] == "normal"
