"""Inbox ingestion with deduplication.

Design rules (project principles): failures are reported, never hidden; an
email is never stored twice; Phase 1 performs no writes to Gmail.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..integrations.gmail.client import GmailClient
from ..models import Email, GmailAccount, Thread
from .classify import classify_email


def ingest_account(db: Session, account: GmailAccount, max_results: int = 20) -> dict:
    """Fetch recent messages for an account and persist new ones.

    Returns counts: {"fetched": n, "stored": n, "skipped_duplicates": n}.
    Raises on Gmail/API errors - the caller surfaces the failure.
    """
    client = GmailClient(account.credentials)
    message_ids = client.list_message_ids(max_results=max_results)

    existing = set(
        db.scalars(
            select(Email.gmail_message_id).where(
                Email.account_id == account.id,
                Email.gmail_message_id.in_(message_ids),
            )
        ).all()
    ) if message_ids else set()

    stored = 0
    for message_id in message_ids:
        if message_id in existing:
            continue
        parsed = client.get_message(message_id)
        thread = db.scalar(
            select(Thread).where(
                Thread.account_id == account.id,
                Thread.gmail_thread_id == parsed["gmail_thread_id"],
            )
        )
        if thread is None:
            thread = Thread(
                account_id=account.id,
                gmail_thread_id=parsed["gmail_thread_id"],
                subject=parsed["subject"],
                snippet=parsed["snippet"],
                last_message_at=parsed["internal_date"],
            )
            db.add(thread)
            db.flush()
        elif parsed["internal_date"] and (
            thread.last_message_at is None or parsed["internal_date"] > thread.last_message_at
        ):
            thread.last_message_at = parsed["internal_date"]
            thread.subject = parsed["subject"] or thread.subject
            thread.snippet = parsed["snippet"] or thread.snippet

        category, priority = classify_email(
            headers=parsed["headers"],
            label_ids=parsed["label_ids"],
            sender=parsed["sender"],
            subject=parsed["subject"],
        )
        db.add(
            Email(
                thread_id=thread.id,
                account_id=account.id,
                gmail_message_id=parsed["gmail_message_id"],
                subject=parsed["subject"],
                sender=parsed["sender"],
                recipients=parsed["recipients"],
                snippet=parsed["snippet"],
                body_text=parsed["body_text"],
                headers=parsed["headers"],
                label_ids=parsed["label_ids"],
                internal_date=parsed["internal_date"],
                category=category,
                priority=priority,
                classified_at=datetime.utcnow(),
            )
        )
        stored += 1

    db.commit()
    return {
        "fetched": len(message_ids),
        "stored": stored,
        "skipped_duplicates": len(message_ids) - stored,
    }
