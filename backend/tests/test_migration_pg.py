"""Postgres execution evidence for the schema upgrade path.

Skips unless MAILPILOT_TEST_PG_URL points at a scratch Postgres database
(e.g. postgresql+psycopg2://user:pass@localhost:5432/mailpilot_test).
Verifies the same guarantees as test_migration.py against real Postgres DDL.
"""
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import ensure_schema
from app.models import GmailAccount, Thread
from app.services.classify import classify_unclassified

TEST_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()

_OLD_EMAILS_DDL_PG = """
CREATE TABLE emails (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    account_id INTEGER NOT NULL REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    gmail_message_id VARCHAR(64) NOT NULL,
    subject VARCHAR(500) NOT NULL,
    sender VARCHAR(320) NOT NULL,
    recipients TEXT NOT NULL,
    snippet TEXT NOT NULL,
    body_text TEXT NOT NULL,
    headers JSON NOT NULL,
    label_ids JSON NOT NULL,
    internal_date TIMESTAMP,
    ingested_at TIMESTAMP NOT NULL
)
"""

PG_URL = os.environ.get("MAILPILOT_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(not PG_URL, reason="MAILPILOT_TEST_PG_URL not set")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def old_pg():
    engine = create_engine(PG_URL)
    with engine.begin() as conn:
        for table in ("emails", "threads", "gmail_accounts"):
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    GmailAccount.__table__.create(engine)
    Thread.__table__.create(engine)
    with engine.begin() as conn:
        conn.execute(text(_OLD_EMAILS_DDL_PG))
    session = sessionmaker(bind=engine)()
    account = GmailAccount(email_address="pgbob@example.com")
    account.credentials = {"token": "pgtok", "refresh_token": "pgref"}
    session.add(account)
    session.commit()
    thread = Thread(account_id=account.id, gmail_thread_id="pgt1", subject="Hi", snippet="s")
    session.add(thread)
    session.commit()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO emails (id, thread_id, account_id, gmail_message_id, subject, sender,"
                " recipients, snippet, body_text, headers, label_ids, internal_date, ingested_at)"
                " VALUES (1, :tid, :aid, 'pgm1', 'Your invoice', 'vendor@example.com', 'pgbob@example.com',"
                " 's', 'b', '{}', '[]', NULL, '2026-11-17 00:00:00')"
            ),
            {"tid": thread.id, "aid": account.id},
        )
    yield engine, session, account
    session.close()
    with engine.begin() as conn:
        for table in ("emails", "threads", "gmail_accounts"):
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))
    engine.dispose()


def test_pg_upgrade_preserves_data_and_adds_columns(old_pg):
    from app.models import Email

    engine, session, account = old_pg

    ensure_schema(engine)
    ensure_schema(engine)  # idempotent

    fresh = session.get(GmailAccount, account.id)
    assert fresh.credentials == {"token": "pgtok", "refresh_token": "pgref"}

    row = session.query(Email).filter_by(gmail_message_id="pgm1").one()
    assert row.subject == "Your invoice"
    assert row.category == ""
    assert row.is_sample is False
    assert row.classified_at is None
    assert row.ai_category == ""
    assert row.classification_source == "rules"

    assert classify_unclassified(session, account.id)["classified"] == 1
    assert session.get(Email, row.id).category == "receipt"
