"""Deterministic baseline classification - Phase 2 foundation.

Transparent rules over parsed headers and Gmail labels, no LLM required. The
LLM provider (services/llm.py) refines this in a later step; the baseline
keeps every stored email classified even with no model configured, and every
rule here is auditable and testable.

Categories: receipt, newsletter, notification, promotions, social, updates,
forums, personal. Priorities: high, normal, low.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

CATEGORY_LABELS = {
    "CATEGORY_PROMOTIONS": "promotions",
    "CATEGORY_SOCIAL": "social",
    "CATEGORY_UPDATES": "updates",
    "CATEGORY_FORUMS": "forums",
}

RECEIPT_SUBJECT_HINTS = (
    "receipt",
    "invoice",
    "order confirmation",
    "order shipped",
    "payment confirmation",
    "payment successful",
    "your order",
)

URGENCY_HINTS = (
    "urgent",
    "action required",
    "payment due",
    "security alert",
    "suspicious",
    "expires",
    "suspended",
    "verify your",
)

AUTOMATED_SENDER_HINTS = ("no-reply@", "noreply@", "donotreply@", "notifications@", "notify@")


def _headers_lower(headers: dict) -> dict:
    return {str(k).lower(): str(v) for k, v in (headers or {}).items()}


def classify_email(
    *,
    headers: dict,
    label_ids: list,
    sender: str,
    subject: str,
) -> tuple[str, str]:
    """Return (category, priority) for one parsed message."""
    h = _headers_lower(headers)
    labels = set(label_ids or [])
    sender_l = (sender or "").lower()
    subject_l = (subject or "").lower()

    # Category: specific signals first, then Gmail's own category labels,
    # then the personal default.
    if any(hint in subject_l for hint in RECEIPT_SUBJECT_HINTS):
        category = "receipt"
    elif "list-unsubscribe" in h or h.get("precedence", "").lower() in ("bulk", "list", "junk"):
        category = "newsletter"
    elif any(hint in sender_l for hint in AUTOMATED_SENDER_HINTS) or h.get("auto-submitted", "").lower() not in ("", "no"):
        category = "notification"
    else:
        category = next((c for label, c in CATEGORY_LABELS.items() if label in labels), "personal")

    # Priority: urgency beats everything; bulk-ish categories sink to low.
    if any(hint in subject_l for hint in URGENCY_HINTS):
        priority = "high"
    elif category in ("promotions", "social", "newsletter", "forums"):
        priority = "low"
    else:
        priority = "normal"

    return category, priority


def classify_unclassified(db, account_id: int) -> int:
    """Backfill classification for stored emails that lack it. Returns count."""
    from ..models import Email

    rows = db.scalars(
        select(Email).where(Email.account_id == account_id, Email.classified_at.is_(None))
    ).all()

    for row in rows:
        row.category, row.priority = classify_email(
            headers=row.headers, label_ids=row.label_ids, sender=row.sender, subject=row.subject
        )
        row.classified_at = datetime.utcnow()
    db.commit()
    return len(rows)
