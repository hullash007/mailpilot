"""HTTP API for Phase 1: OAuth, ingestion, and read access to stored mail."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..integrations.gmail import oauth
from ..integrations.gmail.client import GmailClient
from ..models import Email, GmailAccount, Thread
from ..services.ingestion import ingest_account

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


class LoginResponse(BaseModel):
    authorization_url: str
    state: str


@router.get("/auth/google/login", response_model=LoginResponse)
def google_login() -> LoginResponse:
    try:
        url, state = oauth.authorization_url()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return LoginResponse(authorization_url=url, state=state)


@router.get("/auth/google/callback")
def google_callback(code: str, state: str | None = None, db: Session = Depends(get_db)) -> dict:
    credentials = oauth.exchange_code(code, state=state)
    profile = GmailClient(credentials).get_profile()
    address = profile.get("emailAddress")
    if not address:
        raise HTTPException(status_code=502, detail="Google did not return an account email address.")

    account = db.scalar(select(GmailAccount).where(GmailAccount.email_address == address))
    if account is None:
        account = GmailAccount(email_address=address)
        db.add(account)
    account.credentials_json = credentials
    account.history_id = str(profile.get("historyId", "")) or None
    db.commit()
    return {"connected": address, "account_id": account.id}


@router.post("/ingest/{account_id}")
def ingest(account_id: int, max_results: int = 20, db: Session = Depends(get_db)) -> dict:
    account = db.get(GmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown account.")
    if not account.credentials_json:
        raise HTTPException(status_code=409, detail="Account has no stored OAuth credentials.")
    return ingest_account(db, account, max_results=max_results)


@router.get("/emails")
def list_emails(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Email).order_by(Email.internal_date.desc().nulls_last()).limit(limit).offset(offset)
    ).all()
    return [
        {
            "id": e.id,
            "gmail_message_id": e.gmail_message_id,
            "thread_id": e.thread_id,
            "subject": e.subject,
            "sender": e.sender,
            "recipients": e.recipients,
            "snippet": e.snippet,
            "internal_date": e.internal_date.isoformat() if e.internal_date else None,
            "label_ids": e.label_ids,
        }
        for e in rows
    ]


@router.get("/emails/{email_id}")
def get_email(email_id: int, db: Session = Depends(get_db)) -> dict:
    email = db.get(Email, email_id)
    if email is None:
        raise HTTPException(status_code=404, detail="Unknown email.")
    return {
        "id": email.id,
        "gmail_message_id": email.gmail_message_id,
        "thread_id": email.thread_id,
        "subject": email.subject,
        "sender": email.sender,
        "recipients": email.recipients,
        "snippet": email.snippet,
        "body_text": email.body_text,
        "headers": email.headers,
        "internal_date": email.internal_date.isoformat() if email.internal_date else None,
        "label_ids": email.label_ids,
    }


@router.get("/threads")
def list_threads(limit: int = 50, offset: int = 0, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(Thread).order_by(Thread.last_message_at.desc().nulls_last()).limit(limit).offset(offset)
    ).all()
    return [
        {
            "id": t.id,
            "gmail_thread_id": t.gmail_thread_id,
            "subject": t.subject,
            "snippet": t.snippet,
            "last_message_at": t.last_message_at.isoformat() if t.last_message_at else None,
            "message_count": len(t.emails),
        }
        for t in rows
    ]
