"""Ingestion tests with an in-memory database and a stubbed Gmail client.

No live Gmail access: these tests verify deduplication and thread linkage,
not the Google API itself.
"""
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Email, GmailAccount, Thread
from app.services import ingestion


class StubGmailClient:
    def __init__(self, credentials_dict):
        pass

    def list_message_ids(self, max_results=20, query=None):
        return ["m1", "m2", "m3"]

    def get_message(self, message_id):
        return {
            "gmail_message_id": message_id,
            "gmail_thread_id": "t1" if message_id in ("m1", "m2") else "t2",
            "subject": f"Subject {message_id}",
            "sender": "alice@example.com",
            "recipients": "bob@example.com",
            "snippet": "snip",
            "body_text": "body",
            "headers": {},
            "label_ids": ["INBOX"],
            "internal_date": None,
        }


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(ingestion, "GmailClient", StubGmailClient)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_ingest_stores_and_dedupes(db):
    account = GmailAccount(email_address="bob@example.com")
    db.add(account)
    db.commit()

    first = ingestion.ingest_account(db, account)
    assert first == {"fetched": 3, "stored": 3, "skipped_duplicates": 0}

    second = ingestion.ingest_account(db, account)
    assert second == {"fetched": 3, "stored": 0, "skipped_duplicates": 3}

    assert len(db.scalars(select(Email)).all()) == 3
    assert len(db.scalars(select(Thread)).all()) == 2
