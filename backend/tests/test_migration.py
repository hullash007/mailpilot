"""Schema upgrade path: existing Phase 1 databases gain the new columns
without losing accounts, encrypted tokens, emails or OAuth state."""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, ensure_schema
from app.models import Email, GmailAccount, Thread
from app.services.classify import classify_unclassified

TEST_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()

# The Phase 1 emails table, before is_sample/category/priority/classified_at.
_OLD_EMAILS_DDL = """
CREATE TABLE emails (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id),
    account_id INTEGER NOT NULL REFERENCES gmail_accounts(id),
    gmail_message_id VARCHAR(64) NOT NULL,
    subject VARCHAR(998) NOT NULL,
    sender VARCHAR(320) NOT NULL,
    recipients TEXT NOT NULL,
    snippet TEXT NOT NULL,
    body_text TEXT NOT NULL,
    headers JSON NOT NULL,
    label_ids JSON NOT NULL,
    internal_date DATETIME,
    ingested_at DATETIME NOT NULL
)
"""


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def old_db():
    """A database created at the Phase 1 schema with real rows in it."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Unchanged tables come from the models; emails is created with the OLD DDL.
    GmailAccount.__table__.create(engine)
    Thread.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text(_OLD_EMAILS_DDL))
    session = sessionmaker(bind=engine)()

    account = GmailAccount(email_address="bob@example.com")
    account.credentials = {"token": "tok", "refresh_token": "ref"}  # Fernet-encrypted at rest
    session.add(account)
    session.commit()
    thread = Thread(account_id=account.id, gmail_thread_id="t1", subject="Hello", snippet="s")
    session.add(thread)
    session.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO emails (thread_id, account_id, gmail_message_id, subject, sender,"
                " recipients, snippet, body_text, headers, label_ids, internal_date, ingested_at)"
                " VALUES (:tid, :aid, 'm1', 'Your invoice', 'vendor@example.com', 'bob@example.com',"
                " 's', 'b', '{}', '[]', NULL, '2026-11-17 00:00:00')"
            ),
            {"tid": thread.id, "aid": account.id},
        )
    yield engine, session, account
    session.close()


def test_ensure_schema_preserves_data_and_adds_columns(old_db):
    engine, session, account = old_db

    ensure_schema(engine)
    ensure_schema(engine)  # idempotent: second run is a no-op

    # Encrypted credentials still decrypt - the upgrade touched nothing else.
    fresh = session.get(GmailAccount, account.id)
    assert fresh.credentials == {"token": "tok", "refresh_token": "ref"}

    # The old row survived and gained defaulted classification columns.
    row = session.query(Email).filter_by(gmail_message_id="m1").one()
    assert row.subject == "Your invoice"
    assert row.category == ""
    assert row.is_sample is False
    assert row.classified_at is None
    assert row.ai_category == ""
    assert row.ai_priority == ""
    assert row.ai_classified_at is None
    assert row.classification_source == "rules"

    # Backfill now works on the upgraded table.
    assert classify_unclassified(session, account.id)["classified"] == 1
    assert session.get(Email, row.id).category == "receipt"


def test_ensure_schema_noop_without_emails_table():
    engine = create_engine("sqlite:///:memory:")
    ensure_schema(engine)  # nothing to upgrade, must not raise
