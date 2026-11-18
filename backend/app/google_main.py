"""Google-only workspace candidate. No legacy owner routes or access-key login.

Every mailbox lookup is derived from a verified immutable Google subject and
its explicit stored binding. Email matching never claims legacy mailbox data.
"""
import base64
from collections import deque
from datetime import datetime
import hashlib
import hmac
import html
import json
import os
import secrets
import threading
import time
from urllib.parse import urlsplit, urlencode
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI, HTTPException, Request, Query, Path as PathParameter
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import ForeignKey, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.exc import IntegrityError

from .config import get_settings
from .database import Base, default_engine, ensure_schema, get_db
from .models import GmailAccount, Email, Thread
from .services import oauth_state
from .services import inbox as stored_inbox
from .services.browser_boundary import BrowserBoundary
from .services.access import require_same_origin
from .services.ingestion import ingest_account
from .integrations.gmail.client import GmailClient
from .integrations.gmail.oauth import build_flow

COOKIE = 'mailpilot_google_session'
TTL = 12 * 3600
SCOPES = ['openid', 'https://www.googleapis.com/auth/userinfo.email',
          'https://www.googleapis.com/auth/gmail.readonly']
_attempts = deque()
_attempt_lock = threading.Lock()
_sync_lock = threading.Lock()


class GooglePrincipal(Base):
    __tablename__ = 'google_principals'
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey('gmail_accounts.id'), unique=True)


def _key():
    key = get_settings().oauth_state_secret
    if len(key) < 32:
        raise RuntimeError('Persistent Google session signing is not configured.')
    return key.encode()


def _sign(value):
    return hmac.new(_key(), b'mailpilot-google-session-v1:' + value.encode(), hashlib.sha256).hexdigest()


def issue_session(subject):
    raw = json.dumps({'sub': subject, 'iat': int(time.time()), 'nonce': secrets.token_hex(24)}, separators=(',', ':'))
    value = base64.urlsafe_b64encode(raw.encode()).decode().rstrip('=')
    return value + '.' + _sign(value)


def session_subject(cookie):
    try:
        if not cookie or len(cookie) > 1024:
            return None
        value, signature = cookie.split('.')
        if not hmac.compare_digest(signature, _sign(value)):
            return None
        claims = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
        if set(claims) != {'sub', 'iat', 'nonce'}:
            return None
        if (type(claims['sub']) is not str or not 0 < len(claims['sub']) <= 255
                or type(claims['iat']) is not int or not 0 <= time.time() - claims['iat'] <= TTL
                or type(claims['nonce']) is not str or len(claims['nonce']) != 48):
            return None
        return claims['sub']
    except Exception:
        return None


def own_account(request: Request, db: Session = Depends(get_db)):
    subject = session_subject(request.cookies.get(COOKIE))
    if not subject:
        raise HTTPException(401, 'Sign in with Google.')
    binding = db.get(GooglePrincipal, subject)
    if binding is None:
        raise HTTPException(401, 'Google account is not linked.')
    account = db.get(GmailAccount, binding.account_id)
    if account is None or account.google_user_id != subject:
        raise HTTPException(401, 'Google account is not linked.')
    return account


def _flow(state=None):
    flow = build_flow(state)
    flow.oauth2session.scope = SCOPES
    return flow


def _authorize():
    state = secrets.token_urlsafe(32)
    flow = _flow(state)
    url, returned = flow.authorization_url(access_type='offline', prompt='consent', nonce=oauth_state.sign_state(state))
    if returned != state:
        raise RuntimeError('OAuth state mismatch.')
    return url, state, flow.code_verifier


def _exchange_identity(code, state, verifier):
    from google.oauth2 import id_token
    from google.auth.transport.requests import Request as GoogleRequest
    flow = _flow(state)
    flow.code_verifier = verifier
    flow.fetch_token(code=code, timeout=10)
    creds = flow.credentials
    token = creds.id_token
    if not token:
        raise ValueError('Missing identity token.')
    class BoundedRequest(GoogleRequest):
        def __call__(self, *args, **kwargs):
            kwargs['timeout'] = 10
            return super().__call__(*args, **kwargs)
    claims = id_token.verify_oauth2_token(token, BoundedRequest(), get_settings().google_client_id)
    _validate_identity(claims, state)
    scopes = set(creds.granted_scopes or creds.scopes or [])
    if 'https://www.googleapis.com/auth/gmail.readonly' not in scopes:
        raise ValueError('Read-only Gmail consent is required.')
    credentials = {'token': creds.token, 'refresh_token': creds.refresh_token,
                   'token_uri': creds.token_uri, 'client_id': creds.client_id,
                   'client_secret': creds.client_secret, 'scopes': list(scopes)}
    profile = GmailClient(credentials).get_profile()
    if profile.get('emailAddress', '').casefold() != claims['email'].casefold():
        raise ValueError('Gmail and Google identity do not match.')
    return credentials, claims


def _validate_identity(claims, state):
    if (claims.get('iss') not in {'accounts.google.com', 'https://accounts.google.com'}
            or claims.get('aud') != get_settings().google_client_id
            or type(claims.get('exp')) is not int or claims['exp'] <= time.time()
            or claims.get('email_verified') is not True
            or type(claims.get('sub')) is not str or not 0 < len(claims['sub']) <= 255
            or type(claims.get('email')) is not str or not claims['email']
            or claims.get('nonce') != oauth_state.sign_state(state)):
        raise ValueError('Unverified Google identity.')
    allowed = get_settings().google_allowed_email
    if allowed and claims['email'].casefold() != allowed.casefold():
        raise ValueError('This Google account is not enabled for the pilot.')


def link_verified(db, claims, credentials):
    """Never attach legacy data by an email lookup. Explicit bindings only."""
    subject, address = claims['sub'], claims['email']
    binding = db.get(GooglePrincipal, subject)
    if binding is None:
        legacy_id = db.scalar(select(GmailAccount.id).where(GmailAccount.email_address == address))
        if legacy_id is not None:
            raise HTTPException(409, 'Existing mailbox needs a verified migration before Google login can access it.')
        account = GmailAccount(email_address=address, google_user_id=subject, credentials=credentials)
        db.add(account)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            existing = db.get(GooglePrincipal, subject)
            current = db.get(GmailAccount, existing.account_id) if existing is not None else None
            if current is None or current.google_user_id != subject or current.email_address.casefold() != address.casefold():
                raise HTTPException(409, 'Google account linking conflicted; restart sign-in.') from None
            return current
        db.add(GooglePrincipal(subject=subject, account_id=account.id))
    else:
        account = db.get(GmailAccount, binding.account_id)
        if account is None or account.google_user_id != subject:
            raise HTTPException(409, 'Stored Google binding is inconsistent.')
        if account.email_address.casefold() != address.casefold():
            raise HTTPException(409, 'Google email changed; explicit account update is required.')
        # Google sometimes omits a refresh token after a previous grant.
        if not credentials.get('refresh_token'):
            credentials = {**credentials, 'refresh_token': account.credentials.get('refresh_token')}
        account.credentials = credentials
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(GooglePrincipal, subject)
        current = db.get(GmailAccount, existing.account_id) if existing is not None else None
        if current is None or current.google_user_id != subject or current.email_address.casefold() != address.casefold():
            raise HTTPException(409, 'Google account linking conflicted; restart sign-in.') from None
        return current
    return account


def _mail_snapshot(db, account_id):
    rows = db.execute(select(Email, Thread.gmail_thread_id).join(Thread, Email.thread_id == Thread.id)
                      .where(Email.account_id == account_id, Thread.account_id == account_id)
                      .order_by(Email.id)).all()
    return tuple((row.id, row.gmail_message_id, thread_id, row.subject, row.sender,
                  row.recipients, row.snippet, row.body_text, row.internal_date) for row, thread_id in rows)


def migrate_verified_owner(db, claims, credentials, state, expected_account_id, expected_email, expected_count, dry_run=False):
    """One explicit maintenance migration after fresh identity and mailbox proof.

    Current credentials and mail must stay unchanged until an atomic compare-and-
    set. The caller must choose this exact existing owner's account, never a
    generic match-by-email policy. No source/destination data is returned.
    """
    _validate_identity(claims, state)
    if (type(expected_account_id) is not int or expected_account_id < 1
            or type(expected_count) is not int or not 1 <= expected_count <= 5
            or not expected_email or claims['email'].casefold() != expected_email.casefold()):
        raise HTTPException(409, 'Legacy migration identity mismatch.')
    account = db.get(GmailAccount, expected_account_id)
    if (account is None or account.email_address.casefold() != expected_email.casefold()
            or account.google_user_id is not None
            or db.get(GooglePrincipal, claims['sub']) is not None
            or db.scalar(select(GooglePrincipal.subject).where(GooglePrincipal.account_id == expected_account_id)) is not None):
        raise HTTPException(409, 'Legacy account cannot be bound.')
    original_blob = account.credentials_blob
    snapshot = _mail_snapshot(db, expected_account_id)
    if len(snapshot) != expected_count:
        raise HTTPException(409, 'Legacy message set changed.')
    db.rollback()
    fresh_client = GmailClient(credentials)
    profile = fresh_client.get_profile()
    if profile.get('emailAddress', '').casefold() != expected_email.casefold():
        raise HTTPException(409, 'Fresh Gmail mailbox does not match.')
    for row in snapshot:
        parsed = fresh_client.get_message(row[1])
        actual = (parsed.get('gmail_message_id'), parsed.get('gmail_thread_id'), parsed.get('subject'),
                  parsed.get('sender'), parsed.get('recipients'), parsed.get('snippet'), parsed.get('body_text'), parsed.get('internal_date'))
        if actual != row[1:]:
            raise HTTPException(409, 'Fresh Gmail message proof does not match legacy data.')
    proof = {'matched_messages': len(snapshot), 'mail_sha256': hashlib.sha256(json.dumps(snapshot, default=str, ensure_ascii=False).encode()).hexdigest(), 'credentials_sha256': hashlib.sha256(original_blob.encode()).hexdigest()}
    if dry_run:
        db.rollback()
        return proof
    try:
        # Atomic row claim protects SQLite too, where SELECT FOR UPDATE is not a lock.
        claimed = db.execute(update(GmailAccount).where(GmailAccount.id == expected_account_id,
                     GmailAccount.google_user_id.is_(None), GmailAccount.credentials_blob == original_blob)
                     .values(google_user_id=claims['sub']))
        if claimed.rowcount != 1 or _mail_snapshot(db, expected_account_id) != snapshot:
            raise HTTPException(409, 'Legacy account changed during verification.')
        account = db.get(GmailAccount, expected_account_id, populate_existing=True)
        # Preserve the original encrypted credentials byte-for-byte during this binding.
        db.add(GooglePrincipal(subject=claims['sub'], account_id=expected_account_id))
        db.commit()
        return account
    except Exception:
        db.rollback()
        raise


def _maybe_migrate_owner(db, claims, credentials, state):
    """Disabled by default, exact operator-configured owner migration only."""
    selected = os.environ.get('MAILPILOT_LEGACY_BIND_ACCOUNT_ID', '')
    if not selected:
        return False
    expected_email = get_settings().google_allowed_email
    count = os.environ.get('MAILPILOT_LEGACY_BIND_MESSAGE_COUNT', '')
    if not expected_email or not selected.isdecimal() or not count.isdecimal():
        raise HTTPException(409, 'Legacy migration configuration is incomplete.')
    if db.get(GooglePrincipal, claims['sub']) is not None:
        return False
    if os.environ.get('MAILPILOT_LEGACY_BIND_DRY_RUN', '') == '1':
        return migrate_verified_owner(db, claims, credentials, state, int(selected), expected_email, int(count), dry_run=True)
    migrate_verified_owner(db, claims, credentials, state, int(selected), expected_email, int(count))
    return True


def esc(value):
    return html.escape(str(value or ''), quote=True)


def page(body):
    return HTMLResponse('''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>MailPilot</title><style>
*{box-sizing:border-box}body{font:16px/1.5 system-ui,sans-serif;margin:0;background:#f4f6f9;color:#17223b}header{background:#163f85;color:white;padding:18px 6%}main{max-width:900px;margin:32px auto;padding:0 20px}a{color:#163f85}.button,button{font:inherit;display:inline-block;background:#163f85;color:white;padding:11px 17px;border:0;border-radius:7px;text-decoration:none;cursor:pointer}.notice{background:#fff8df;border:1px solid #eadb99;padding:14px;border-radius:8px}.card{padding:18px;margin:16px 0;background:white;border:1px solid #dce2eb;border-radius:10px;overflow-wrap:anywhere}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}nav{display:flex;gap:18px;align-items:center;flex-wrap:wrap;margin:20px 0}small{color:#53617a}h1{font-size:24px}form{display:inline}.filters{display:flex;flex-wrap:wrap;gap:14px;align-items:end;background:white;border:1px solid #dce2eb;padding:18px;border-radius:10px}.filters label{display:flex;flex-direction:column;gap:5px;font-size:14px}.filters input:not([type=hidden]),.filters select{font:inherit;max-width:100%;box-sizing:border-box;padding:9px;border:1px solid #a8b5c9;border-radius:5px}.filters label:first-child{flex:1;min-width:220px}.wordmark{color:white;text-decoration:none}.welcome{max-width:780px;margin:45px auto}.welcome h1{font-size:clamp(32px,5vw,50px);line-height:1.15;letter-spacing:-1.5px;margin:16px 0}.eyebrow{font-size:11px;letter-spacing:1.7px;font-weight:700;color:#53617a}.lead{font-size:19px;color:#53617a;max-width:580px}.features{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:46px 0}.features h2{font-size:17px}.features p{font-size:14px;color:#53617a}.account-head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}.account-card h2{font-size:21px;margin-top:0}.account-card form{display:block}.confirmation summary{cursor:pointer;min-height:44px;padding:10px 0;color:#163f85}.account-card:last-of-type .confirmation button{background:#9d3439}.account-card:last-of-type .confirmation summary{color:#9d3439}.account-card details{font-size:14px;color:#53617a;margin:16px 0}.account-card label{display:block;margin:18px 0;font-size:14px}.account-card input[type=checkbox]{margin-right:8px}.status-pill{font-size:12px;background:#e5f2eb;color:#23563d;border-radius:20px;padding:5px 10px}@media(max-width:600px){main{margin:24px auto;padding:0 20px}.welcome{margin:30px auto}.features{grid-template-columns:1fr;gap:6px;margin:32px 0}.features section{border-top:1px solid #dce2eb;padding-top:8px}.welcome h1{font-size:36px}.lead{font-size:17px}.account-card{padding:20px}} </style></head><body><header><a class="wordmark" href="/mail"><strong>MailPilot</strong></a></header><main>''' + body + '</main></body></html>')


@asynccontextmanager
async def lifespan(app):
    settings = get_settings()
    origin = urlsplit(settings.public_origin)
    if (origin.scheme != 'https' or not origin.hostname or origin.path or origin.query or origin.fragment
            or origin.username or origin.password or not settings.cookie_secure
            or settings.google_redirect_uri != settings.public_origin + '/auth/google/callback'
            or not settings.google_client_id or not settings.google_client_secret
            or settings.llm_refine):
        raise RuntimeError('Google-only hosted configuration is incomplete or unsafe.')
    _key()
    Fernet(settings.token_encryption_key.encode())
    engine = default_engine()
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    yield


def create_app():
    app = FastAPI(title='MailPilot', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BrowserBoundary)

    @app.get('/')
    @app.get('/access')
    def home():
        return page('<h1>Your mail. Your workspace.</h1><p>Sign in with Google to link that Gmail account to MailPilot. No separate access key.</p><p><a class="button" href="/login">Sign in with Google</a></p><p class="notice">Gmail permission is read-only. MailPilot cannot send, delete or relabel mail. Connecting stores encrypted OAuth tokens on this server. Imported message text is not application-encrypted. AI is off. This pilot is not a production service and has no automatic deletion or backup guarantee.</p><p>Google may show a testing-app notice. Access is limited by the current Google pilot configuration.</p>')

    @app.get('/login')
    def login(db: Session = Depends(get_db)):
        with _attempt_lock:
            now = time.monotonic()
            while _attempts and _attempts[0] <= now - 60:
                _attempts.popleft()
            if len(_attempts) >= 10:
                raise HTTPException(429, 'Try again in one minute.', headers={'Retry-After': '60'})
            _attempts.append(now)
        url, state, verifier = _authorize()
        oauth_state.issue_state(db, state, verifier)
        response = RedirectResponse(url, status_code=303)
        response.set_cookie(oauth_state.COOKIE_NAME, oauth_state.sign_state(state), max_age=600, httponly=True, secure=True, samesite='lax')
        return response

    @app.get('/auth/google/callback')
    def callback(request: Request, code: str, state: str, db: Session = Depends(get_db)):
        if not oauth_state.cookie_matches(request.cookies.get(oauth_state.COOKIE_NAME), state):
            raise HTTPException(403, 'OAuth state does not match this browser.')
        verifier = oauth_state.consume_state(db, state)
        if verifier is None:
            raise HTTPException(403, 'OAuth state is expired, used or unknown.')
        try:
            credentials, claims = _exchange_identity(code, state, verifier)
            _validate_identity(claims, state)
        except Exception:
            raise HTTPException(403, 'Google identity or Gmail consent could not be verified. Restart sign-in.') from None
        migration = _maybe_migrate_owner(db, claims, credentials, state)
        if type(migration) is dict:
            return page('<h1>Google mailbox proof passed</h1><p>Qualification dry run only. Existing mail, credentials and account binding have not changed. Google-only access is being finished.</p><pre>' + esc(json.dumps(migration, sort_keys=True)) + '</pre><a href="/">Return to MailPilot</a>')
        if not migration:
            link_verified(db, claims, credentials)
        response = RedirectResponse('/app', status_code=303)
        response.set_cookie(COOKIE, issue_session(claims['sub']), max_age=TTL, httponly=True, secure=True, samesite='lax')
        response.delete_cookie(oauth_state.COOKIE_NAME, httponly=True, secure=True, samesite='lax')
        return response

    @app.get('/app')
    def inbox(request: Request, q: str = Query(default='', max_length=200),
              category: str = '', priority: str = '',
              before: int | None = Query(default=None, ge=1, le=9223372036854775807),
              limit: int = Query(default=25, ge=1, le=50),
              account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        data = stored_inbox.inbox(db, account.id, query=q, category=category,
                                  priority=priority, before=before, limit=limit)
        body = '<nav><a href="/workspace">Search stored conversations and message text</a></nav><h1>Your stored inbox</h1><p>Signed in as ' + esc(account.email_address) + '</p><p class="notice">Stored mail only, not a live or complete inbox. Sync is manual and bounded to five recent messages. Real-mail AI is off. No mail changes.</p><nav><form method="post" action="/app/sync"><button>Import up to 5 recent messages</button></form><form method="post" action="/logout"><button>Sign out</button></form></nav>'
        body += '<form class="filters" method="get" action="/app"><label>Search stored subject, sender or snippet<input name="q" maxlength="200" value="' + esc(q) + '" placeholder="Literal search"></label>'
        for name, current, values in [('category', category, sorted(stored_inbox.CATEGORIES)), ('priority', priority, sorted(stored_inbox.PRIORITIES))]:
            body += '<label>' + name.capitalize() + '<select name="' + name + '"><option value="">All</option>'
            for value in values:
                body += '<option value="' + value + '"' + (' selected' if value == current else '') + '>' + value.capitalize() + '</option>'
            body += '</select></label>'
        body += '<input type="hidden" name="limit" value="' + str(limit) + '"><button>Apply filters</button><a href="/app">Reset</a></form>'
        body += '<p><small>Newest additions to stored mail come first, not the newest email dates. Search matches the exact text you type in stored subjects, senders and previews, not message bodies or attachments.</small></p><p><small>Changing filters starts at the first page. After importing mail, return to the first page to see new records. Pages can change as stored mail changes.</small></p>'
        if before is not None:
            first = '/app?' + urlencode(dict(q=q, category=category, priority=priority, limit=limit))
            body += '<p><a href="' + esc(first) + '">Return to first page</a></p>'
        if not data['messages']:
            body += '<p>No stored messages match this page and these filters.</p>'
        for row in data['messages']:
            body += '<article class="card"><h2><a href="/app/messages/' + str(row['id']) + '">' + esc(row['subject'] or '(no subject)') + '</a></h2><p>' + esc(row['sender']) + '</p><p>' + esc(html.unescape(row['snippet'])) + '</p><small>Stored classification: ' + esc(row['category']) + ' / ' + esc(row['priority']) + ' (rules)</small>'
            if row['snippet_truncated']:
                body += '<p><small>Snippet preview truncated at 1000 characters; search covers the full stored snippet.</small></p>'
            body += '</article>'
        if data['next_before'] is not None:
            older = '/app?' + urlencode(dict(q=q, category=category, priority=priority, limit=limit, before=data['next_before']))
            body += '<nav><a class="button" href="' + esc(older) + '">Older stored records</a></nav>'
        return page(body)

    @app.get('/app/messages/{message_id}')
    def detail(message_id: int = PathParameter(ge=1, le=9223372036854775807), account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        row = db.scalar(select(Email).where(Email.id == message_id, Email.account_id == account.id))
        if row is None:
            raise HTTPException(404, 'Message not found.')
        return page('<nav><a href="/app">Your stored inbox</a></nav><p class="notice">Stored untrusted email text. No links, scripts or attachments are loaded.</p><h1>' + esc(row.subject or '(no subject)') + '</h1><p>From: ' + esc(row.sender) + '</p><pre>' + esc((row.body_text or '')[:32000]) + '</pre>' + ('<p>Message preview truncated at 32000 characters.</p>' if len(row.body_text or '') > 32000 else ''))

    @app.post('/app/sync')
    def sync(request: Request, account: GmailAccount = Depends(own_account), db: Session = Depends(get_db)):
        require_same_origin(request)
        if not _sync_lock.acquire(blocking=False):
            raise HTTPException(429, 'Another import is running. Try again shortly.')
        try:
            ingest_account(db, account, max_results=5)
        finally:
            _sync_lock.release()
        return RedirectResponse('/app', status_code=303)

    @app.post('/logout')
    def logout(request: Request):
        require_same_origin(request)
        response = RedirectResponse('/', status_code=303)
        response.delete_cookie(COOKIE, httponly=True, secure=True, samesite='lax')
        return response

    from .stored_conversations import install_routes
    return install_routes(app)


app = create_app()
