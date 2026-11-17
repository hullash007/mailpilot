"""Seed SAMPLE data for dashboard demos.

Every row is marked is_sample=True; the dashboard shows a "sample data"
banner and a SAMPLE badge for these. Never use against a real inbox.
Run: python scripts/seed_sample_data.py  (from the repo root, DATABASE_URL set)
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "backend")

from app.database import SessionLocal, Base, engine
from app.models import Email, GmailAccount, Thread

SAMPLES = [
    ("Priya Sharma <priya@recruiter.example>", "Interview loop - senior backend role",
     "Hi, following up on your application - the panel would like to schedule..."),
    ("HDFC Bank Alerts <alerts@bank.example>", "Your October statement is ready",
     "Your credit card statement for October is now available..."),
    ("Rahul Verma <rahul@friend.example>", "Weekend trek plan",
     "Are we still on for Saturday? I found a shorter route..."),
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        account = GmailAccount(email_address="demo@example.com")
        db.add(account)
        db.flush()
        for i, (sender, subject, snippet) in enumerate(SAMPLES):
            thread = Thread(
                account_id=account.id, gmail_thread_id=f"sample-t{i}",
                subject=subject, snippet=snippet,
                last_message_at=datetime.utcnow() - timedelta(hours=i * 5),
            )
            db.add(thread)
            db.flush()
            db.add(Email(
                thread_id=thread.id,
                account_id=account.id, gmail_message_id=f"sample-m{i}",
                subject=subject, sender=sender, recipients="you@example.com",
                snippet=snippet, body_text=snippet, headers={"From": sender},
                label_ids=["INBOX"], internal_date=thread.last_message_at,
                is_sample=True,
            ))
        db.commit()
        print("Seeded 3 sample emails (is_sample=True).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
