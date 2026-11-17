"""HTTP API for Phase 1: OAuth, ingestion, and read access to stored mail."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..integrations.gmail import oauth
from ..integrations.gmail.client import GmailClient
from ..models import Email, GmailAccount, Thread
from ..services.ingestion import ingest_account
from ..services import oauth_state
from ..services.access import require_access
from ..services.classify import classify_unclassified
from ..services.crypto import TokenEncryptionError

router = APIRouter(dependencies=[Depends(require_access)])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


class LoginResponse(BaseModel):
    authorization_url: str
    state: str


@router.get("/auth/google/login", response_model=LoginResponse)
def google_login(db: Session = Depends(get_db)) -> JSONResponse:
    try:
        url, state, code_verifier = oauth.authorization_url()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    oauth_state.issue_state(db, state, code_verifier)
    response = JSONResponse(content=LoginResponse(authorization_url=url, state=state).model_dump())
    # Binds this state to the initiating browser; the callback requires it.
    response.set_cookie(
        oauth_state.COOKIE_NAME,
        oauth_state.sign_state(state),
        max_age=oauth_state.STATE_TTL_SECONDS,
        httponly=True,
        # Lax, not Strict: Google returns the user with a cross-site top-level
        # GET, and Strict would withhold this cookie from that navigation and
        # fail every real browser callback. Lax still blocks cross-site
        # subrequests and POSTs.
        samesite="lax",
    )
    return response


@router.get("/auth/google/callback")
def google_callback(
    request: Request, code: str, state: str | None = None, db: Session = Depends(get_db)
) -> dict:
    if not state:
        raise HTTPException(status_code=400, detail="Missing OAuth state.")
    if not oauth_state.cookie_matches(request.cookies.get(oauth_state.COOKIE_NAME), state):
        raise HTTPException(status_code=403, detail="OAuth state does not match this browser.")
    code_verifier = oauth_state.consume_state(db, state)
    if code_verifier is None:
        raise HTTPException(status_code=403, detail="OAuth state is unknown, used or expired.")
    try:
        credentials = oauth.exchange_code(code, state=state, code_verifier=code_verifier)
    except Exception:
        # State is already consumed; the user must restart login.
        raise HTTPException(status_code=502, detail="Google token exchange failed; please restart login.")
    profile = GmailClient(credentials).get_profile()
    address = profile.get("emailAddress")
    if not address:
        raise HTTPException(status_code=502, detail="Google did not return an account email address.")

    account = db.scalar(select(GmailAccount).where(GmailAccount.email_address == address))
    if account is None:
        account = GmailAccount(email_address=address)
        db.add(account)
    account.credentials = credentials
    account.history_id = str(profile.get("historyId", "")) or None
    db.commit()
    return {"connected": address, "account_id": account.id}


@router.post("/ingest/{account_id}")
def ingest(account_id: int, max_results: int = 20, db: Session = Depends(get_db)) -> dict:
    account = db.get(GmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown account.")
    if not account.credentials_blob:
        raise HTTPException(status_code=409, detail="Account has no stored OAuth credentials.")
    try:
        return ingest_account(db, account, max_results=max_results)
    except TokenEncryptionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/classify/{account_id}")
def classify(account_id: int, db: Session = Depends(get_db)) -> dict:
    """Backfill baseline classification for an account's stored emails."""
    account = db.get(GmailAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Unknown account.")
    return classify_unclassified(db, account_id)


@router.post("/refine/{account_id}")
def refine(account_id: int, limit: int = 25, db: Session = Depends(get_db)) -> dict:
    """Enqueue a bounded sample-only AI pass; NEVER call inference in HTTP.

    Same fail-closed access as every route on this router. Suggestions land in
    the separate ai_* columns; rule classifications stay authoritative.
    """
    from ..services.refine import enqueue_samples

    if db.get(GmailAccount, account_id) is None:
        raise HTTPException(status_code=404, detail="account not found")
    return enqueue_samples(db, account_id, limit=limit)


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
            "category": e.category,
            "priority": e.priority,
            "classification_source": e.classification_source,
            "ai_category": e.ai_category,
            "ai_priority": e.ai_priority,
            "ai_status": e.ai_status,
            "ai_model": e.ai_model,
            "ai_prompt_version": e.ai_prompt_version,
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


@router.get('/accounts/{account_id}/threads/{thread_id}/review')
def sample_thread_review(account_id: int, thread_id: int, db: Session = Depends(get_db)) -> dict:
    """Derived literal evidence only; no persistence, LLM or mailbox effects."""
    from ..services.thread_review import review_thread
    return review_thread(db, account_id, thread_id)
