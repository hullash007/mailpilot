"""Explicit candidate sync ticks, disconnected from live app/AI/worker.

At most one provider page per tick, possibly spanning ticks. Network reads precede writes. A revision CAS guards
commit: replay is safe and stale concurrent results cannot advance the cursor.
No local mail content is removed; remote deletions are separate facts, pending
owner retention policy. No exception text/provider payload reaches diagnostics.
"""
from datetime import datetime
import hashlib
import json
import time
import random
from email.utils import parsedate_to_datetime
from datetime import timezone
from dataclasses import dataclass
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from ..models import Email, GmailAccount, Thread
from ..sync_models import MailboxSync, SyncRun, MessageRemoteState, ThreadSnapshot
from .classify import classify_email


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class TickBudget:
    max_requests: int = 25
    seconds: float = 60
    clock: object = time.monotonic
    requests: int = 0

    def __post_init__(self):
        if type(self.max_requests) is not int or not 2 <= self.max_requests <= 100 or not 0 < self.seconds <= 300:
            raise ValueError('Invalid tick budget')
        self.deadline = self.clock() + self.seconds

    def before(self):
        remaining = self.deadline - self.clock()
        if remaining <= 0 or self.requests >= self.max_requests:
            raise BudgetExhausted('Aggregate sync budget exhausted')
        self.requests += 1
        return min(20, remaining)


@dataclass
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 30
    max_seconds: float = 3600
    random_source: object = random.random

    def __post_init__(self):
        if (type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10 or
                not 0 < self.base_seconds <= self.max_seconds <= 86400):
            raise ValueError('Invalid retry policy')

    def delay(self, exc, attempt, now):
        # Full jitter in [0.5*backoff, backoff], never an immediate loop.
        backoff = min(self.max_seconds, self.base_seconds * 2 ** (attempt - 1))
        jitter = min(1, max(0, self.random_source()))
        delay = max(1, backoff * (0.5 + 0.5 * jitter))
        header = None
        try:
            header = exc.resp.get('retry-after') or exc.resp.get('Retry-After')
        except (AttributeError, TypeError):
            pass
        if isinstance(header, str):
            try:
                seconds = int(header) if header.isdecimal() else (parsedate_to_datetime(header).astimezone(timezone.utc).replace(tzinfo=None) - now).total_seconds()
                if seconds > 0:
                    # Provider says wait longer than our cap: do NOT retry early.
                    # Return None so operator must decide, rather than violate it.
                    if seconds > self.max_seconds:
                        return None
                    delay = max(delay, seconds)
            except (TypeError, ValueError, OverflowError):
                pass
        return min(self.max_seconds, delay)


class BudgetClient:
    def __init__(self, client, budget):
        self.client, self.budget = client, budget

    def __getattr__(self, method):
        if method not in ('get_profile', 'list_message_page', 'list_history_page', 'get_thread_snapshot'):
            raise AttributeError(method)
        def request(*args, **kwargs):
            timeout = self.budget.before()
            if hasattr(self.client, 'set_request_timeout'):
                self.client.set_request_timeout(timeout)
            return getattr(self.client, method)(*args, **kwargs)
        return request


class SyncConflict(RuntimeError):
    pass


class SameVersionConflict(RuntimeError):
    pass


class InvalidProviderData(ValueError):
    pass


def _id(value):
    if not isinstance(value, str) or not value or len(value) > 64 or not value.isalnum():
        raise InvalidProviderData('Invalid provider identity')
    return value


def _cursor(value):
    if not isinstance(value, str) or not value.isdecimal() or len(value) > 64:
        raise InvalidProviderData('Invalid history cursor')
    return value


def _token(value):
    if value is not None and (not isinstance(value, str) or not value or len(value) > 4096):
        raise InvalidProviderData('Invalid page token')
    return value


def _status(exc):
    return getattr(getattr(exc, 'resp', None), 'status', None)


def _page_token_error(exc):
    """Defensive classifier, not a promised Gmail error contract.

    Never log provider content. Only explicitly parameter-located 400 is safe to
    recover automatically; vague badRequest text is deliberately insufficient.
    """
    if _status(exc) != 400:
        return False
    try:
        body = json.loads(exc.content)
        errors = body['error']['errors']
        return bool(errors) and all(e.get('location') == 'pageToken' and
               e.get('locationType') == 'parameter' for e in errors)
    except (AttributeError, ValueError, KeyError, TypeError):
        return False


def start_sync(db: Session, account_id: int):
    """Explicitly enroll an account; no network and no startup auto-enrollment."""
    if db.get(GmailAccount, account_id) is None:
        raise ValueError('Unknown account')
    if db.get(MailboxSync, account_id) is None:
        db.add(MailboxSync(account_id=account_id))
    db.commit()


def _lookup(db, model, account_id, field, value):
    return db.scalar(select(model).where(model.account_id == account_id, field == value))


def _persist_threads(db, account_id, fetched, deleted, now, generation):
    inserted = updated = 0
    for tid, payload in fetched.items():
        messages, provider_cursor = payload['messages'], payload['history_id']
        snap = _lookup(db, ThreadSnapshot, account_id, ThreadSnapshot.gmail_thread_id, tid)
        if not payload.get('unavailable') and snap is not None and int(provider_cursor) < int(snap.provider_history_id):
            raise SyncConflict('Older provider thread snapshot')
        normalized = sorted((dict(msg, label_ids=sorted(msg['label_ids'])) for msg in messages), key=lambda m: m['gmail_message_id'])
        snapshot_fingerprint = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
        if (not payload.get('unavailable') and snap is not None and snap.normalized_fingerprint and
                provider_cursor == snap.provider_history_id and snapshot_fingerprint != snap.normalized_fingerprint):
            raise SameVersionConflict('Provider snapshot conflicts at same version')
        if payload.get('unavailable'):
            if snap is None:
                snap = ThreadSnapshot(account_id=account_id, gmail_thread_id=tid, message_count=0,
                                      excluded_count=0, provider_history_id='0')
                db.add(snap)
            snap.observed_generation = generation
            snap.availability = 'unavailable'
            snap.fetched_at = now
            continue
        thread = _lookup(db, Thread, account_id, Thread.gmail_thread_id, tid)
        if thread is None:
            thread = Thread(account_id=account_id, gmail_thread_id=tid)
            db.add(thread)
            db.flush()
        eligible = []
        for msg in messages:
            mid = msg['gmail_message_id']
            row = _lookup(db, Email, account_id, Email.gmail_message_id, mid)
            fingerprint = hashlib.sha256(json.dumps(msg, sort_keys=True, default=str).encode()).hexdigest()
            remote = _lookup(db, MessageRemoteState, account_id, MessageRemoteState.gmail_message_id, mid)
            if remote is None:
                remote = MessageRemoteState(account_id=account_id, gmail_message_id=mid)
                db.add(remote)
            remote.excluded = bool({'SPAM', 'TRASH'} & set(msg['label_ids']))
            if remote.excluded:
                # Never import excluded content, but preserve already-stored mail
                # pending an owner retention policy. Status is separate.
                remote.deleted_at = None
                continue
            eligible.append(msg)
            if row is None:
                row = Email(account_id=account_id, gmail_message_id=mid, thread_id=thread.id)
                db.add(row)
                inserted += 1
            elif remote.fingerprint != fingerprint:
                updated += 1
            row.thread_id = thread.id
            # A provider-origin update must never retain fixture Action eligibility.
            row.is_sample = False
            for field in ('subject', 'sender', 'recipients', 'snippet', 'body_text', 'headers', 'label_ids', 'internal_date'):
                setattr(row, field, msg[field])
            if row.classification_source != 'user_confirmed':
                row.category, row.priority = classify_email(headers=msg['headers'], label_ids=msg['label_ids'],
                                                           sender=msg['sender'], subject=msg['subject'])
                row.classified_at = now
            remote.fingerprint = fingerprint
            remote.deleted_at = None
        latest = max(eligible, key=lambda m: (m['internal_date'] or datetime.min, m['gmail_message_id'])) if eligible else None
        if latest is not None:
            thread.subject, thread.snippet = latest['subject'], latest['snippet']
            thread.last_message_at = latest['internal_date']
        if snap is None:
            snap = ThreadSnapshot(account_id=account_id, gmail_thread_id=tid)
            db.add(snap)
        snap.fetched_at, snap.message_count = now, len(eligible)
        snap.excluded_count = len(messages) - len(eligible)
        snap.observed_generation = generation
        snap.normalized_fingerprint = snapshot_fingerprint
        snap.provider_history_id = provider_cursor
        snap.availability = 'available'
        snap.message_ids = [msg['gmail_message_id'] for msg in eligible]
    for mid in deleted:
        remote = _lookup(db, MessageRemoteState, account_id, MessageRemoteState.gmail_message_id, mid)
        if remote is None:
            remote = MessageRemoteState(account_id=account_id, gmail_message_id=mid)
            db.add(remote)
        remote.deleted_at = now
    return inserted, updated


def _fetch(client, phase, page_token, anchor, resolve_threads, pending):
    if pending is None:
        deleted, tids, unresolved = set(), set(), set()
        if phase == 'initial':
            anchor = anchor or _cursor(client.get_profile()['historyId'])
            try:
                page = client.list_message_page(page_token=page_token, page_size=25)
            except Exception as exc:
                exc._mailpilot_page_token_error = bool(page_token) and _page_token_error(exc)
                raise
            for m in page.get('messages', []):
                _id(m['id'])
                tids.add(_id(m['threadId']))
        else:
            try:
                page = client.list_history_page(start_history_id=anchor, page_token=page_token)
            except Exception as exc:
                if _status(exc) == 404:
                    exc._mailpilot_history_expired = True
                exc._mailpilot_page_token_error = bool(page_token) and _page_token_error(exc)
                raise
            for event in page.get('history', []):
                for key in ('messagesAdded', 'labelsAdded', 'labelsRemoved'):
                    for change in event.get(key, []):
                        msg = change['message']
                        _id(msg['id'])
                        tids.add(_id(msg['threadId']))
                for change in event.get('messagesDeleted', []):
                    msg = change['message']
                    mid = _id(msg['id'])
                    deleted.add(mid)
                    tid = msg.get('threadId')
                    if tid:
                        tids.add(_id(tid))
                    else:
                        unresolved.add(mid)
        if len(page.get('messages', [])) > 100 or len(page.get('history', [])) > 100:
            raise InvalidProviderData('Provider page exceeds safety budget')
        next_token = _token(page.get('nextPageToken'))
        if next_token is not None and next_token == page_token:
            raise InvalidProviderData('Repeated page token')
        if len(tids) > 100 or len(deleted) > 2000:
            raise InvalidProviderData('Provider page exceeds safety budget')
        tids.update(resolve_threads(unresolved))
        if len(tids) > 100:
            raise InvalidProviderData('Provider page exceeds thread budget')
        final_cursor = _cursor(page['historyId']) if phase != 'initial' and not next_token else None
        if final_cursor is not None and int(final_cursor) < int(anchor):
            raise InvalidProviderData('History cursor moved backwards')
        pending = dict(tids=sorted(tids), deleted=sorted(deleted), next_token=next_token,
                       anchor=anchor, final_cursor=final_cursor)
    else:
        anchor = pending['anchor']
    deleted = set(pending['deleted'])
    fetched, count, chars = {}, 0, 0
    remaining = list(pending['tids'])
    for tid in list(remaining):
        try:
            payload = client.get_thread_snapshot(tid)
            provider_cursor = _cursor(payload['history_id'])
            messages = payload['messages']
        except BudgetExhausted:
            break
        except Exception as exc:
            if _status(exc) == 404:
                # Record unavailability, never infer all retained messages deleted.
                fetched[tid] = dict(messages=[], history_id=anchor, unavailable=True)
                remaining.remove(tid)
                continue
            raise
        if not messages:
            raise InvalidProviderData('Empty thread response')
        ids = set()
        for msg in messages:
            mid = _id(msg['gmail_message_id'])
            if msg['gmail_thread_id'] != tid or mid in ids:
                raise InvalidProviderData('Invalid thread membership')
            ids.add(mid)
            count += 1
            chars += len(json.dumps(msg, default=str))
        if count > 2000 or chars > 8_000_000:
            raise InvalidProviderData('Thread page exceeds safety budget')
        fetched[tid] = dict(messages=messages, history_id=provider_cursor)
        remaining.remove(tid)
    # Current full-thread response supersedes historical delete events on this page.
    present = {m['gmail_message_id'] for payload in fetched.values() for m in payload['messages']}
    deleted -= present
    next_pending = dict(pending, tids=remaining, deleted=sorted(deleted)) if remaining else None
    return fetched, (set() if remaining else deleted), pending['next_token'], anchor, pending['final_cursor'], next_pending


def sync_tick(db: Session, account_id: int, client, budget=None, retry_policy=None, now=None):
    """Caller owns authentication and serialization. No hidden long-running job.

    Do not call with a session carrying unrelated writes: rollback intentionally
    ends the snapshot before doing provider network reads.
    """
    from datetime import timedelta
    now = now or datetime.utcnow()
    retry_policy = retry_policy or RetryPolicy()
    budget = budget or TickBudget()
    client = BudgetClient(client, budget)
    if db.new or db.dirty or db.deleted:
        raise ValueError('Sync requires a clean dedicated session')
    state = db.get(MailboxSync, account_id)
    if state is None:
        raise ValueError('Account has not explicitly enrolled')
    if state.scope != 'non_spam_trash' or state.phase not in ('initial', 'catchup', 'ready'):
        db.rollback()
        raise ValueError('Unsupported sync state or scope')
    if state.blocked:
        db.rollback()
        return {'outcome': 'blocked', 'error_code': 'page_token_restart_limit'}
    if state.retry_exhausted:
        db.rollback()
        return {'outcome': 'retry_exhausted', 'error_code': 'retry_exhausted'}
    if state.retry_at and now < state.retry_at:
        db.rollback()
        return {'outcome': 'retry_wait', 'error_code': 'retry_scheduled'}
    retry_attempts = state.retry_attempts
    revision, phase, page_token, token_restarts = state.revision, state.phase, state.page_token, state.token_restarts
    generation = state.generation
    pending = state.pending_page
    anchor = state.anchor if phase in ('initial', 'catchup') else state.history_id
    db.rollback()
    def resolve_threads(message_ids):
        if not message_ids:
            return set()
        try:
            return set(db.scalars(select(Thread.gmail_thread_id).join(Email, Email.thread_id == Thread.id).where(
                Email.account_id == account_id, Thread.account_id == account_id,
                Email.gmail_message_id.in_(message_ids))).all())
        finally:
            db.rollback()
    started = now
    try:
        result = _fetch(client, phase, page_token, anchor, resolve_threads, pending)
    except Exception as exc:
        db.rollback()
        expired = phase != 'initial' and _status(exc) == 404
        # Only history-list404 expires the cursor; get-thread404 is a replayable
        # fetch failure (classified below by the wrapper in _fetch).
        expired = expired and getattr(exc, '_mailpilot_history_expired', False)
        token_fault = getattr(exc, '_mailpilot_page_token_error', False)
        exhausted = isinstance(exc, BudgetExhausted)
        restart = token_fault and token_restarts < 2
        code = ('page_token_restart' if restart else 'page_token_restart_limit') if token_fault else 'history_expired' if expired else ('provider_' + str(_status(exc)) if _status(exc) else 'invalid_or_failed_read')
        transient = _status(exc) in (429, 500, 502, 503, 504)
        retry_delay = retry_policy.delay(exc, retry_attempts + 1, now) if transient else None
        retry_done = transient and (retry_attempts + 1 >= retry_policy.max_attempts or retry_delay is None)
        if transient:
            code = 'retry_exhausted' if retry_done else 'retry_scheduled'
        if exhausted:
            code = 'budget_exhausted'
        values = dict(revision=revision + 1, last_attempt=started, error_code=code)
        if transient:
            values.update(retry_attempts=retry_attempts + 1, retry_at=None if retry_done else now + timedelta(seconds=retry_delay),
                          retry_exhausted=retry_done)
        if expired:
            values.update(phase='initial', page_token=None, anchor=None, token_restarts=0, pending_page=None, generation=generation+1)
        if token_fault:
            values.update(token_restarts=token_restarts + 1 if restart else token_restarts, blocked=not restart)
            if restart:
                values.update(page_token=None)
        changed = db.execute(update(MailboxSync).where(MailboxSync.account_id == account_id,
                            MailboxSync.revision == revision).values(**values)).rowcount
        if changed != 1:
            db.rollback()
            raise SyncConflict('Stale sync result') from None
        outcome = ('retry_exhausted' if retry_done else 'retry_scheduled') if transient else 'budget_exhausted' if exhausted else 'reconcile_required' if expired else 'restarting_page_sequence' if restart else 'blocked' if token_fault else 'failed'
        db.add(SyncRun(account_id=account_id, phase=phase, started_at=started, finished_at=datetime.utcnow(),
                       outcome=outcome, error_code=code))
        db.commit()
        return {'outcome': outcome, 'error_code': code}
    fetched, deleted, token, anchor, final_cursor, next_pending = result
    values = dict(revision=revision + 1, last_attempt=started, error_code='budget_exhausted' if next_pending else '',
                  page_token=page_token if next_pending else token, anchor=anchor, pending_page=next_pending,
                  retry_attempts=0, retry_at=None, retry_exhausted=False)
    if next_pending is None and phase == 'initial' and token is None:
        values.update(phase='catchup')
    elif next_pending is None and phase != 'initial' and token is None:
        values.update(phase='ready', history_id=final_cursor, anchor=None, last_success=now, token_restarts=0, completed_generation=generation)
    # CAS is the first write. Any subsequent persistence error rolls all of it back.
    try:
        changed = db.execute(update(MailboxSync).where(MailboxSync.account_id == account_id,
                            MailboxSync.revision == revision).values(**values)).rowcount
        if changed != 1:
            raise SyncConflict('Stale sync result')
        inserted, updated = _persist_threads(db, account_id, fetched, deleted, now, generation)
        db.add(SyncRun(account_id=account_id, phase=phase, started_at=started, finished_at=now,
                       outcome='budget_exhausted' if next_pending else 'page_committed', inserted=inserted, updated=updated, remote_deleted=len(deleted)))
        db.commit()
    except SameVersionConflict:
        db.rollback()
        changed = db.execute(update(MailboxSync).where(MailboxSync.account_id == account_id,
                             MailboxSync.revision == revision).values(
                             revision=revision+1, last_attempt=started, error_code='same_version_conflict')).rowcount
        if changed != 1:
            db.rollback()
            raise SyncConflict('Stale conflict result') from None
        db.add(SyncRun(account_id=account_id, phase=phase, started_at=started, finished_at=datetime.utcnow(),
                       outcome='same_version_conflict', error_code='same_version_conflict'))
        db.commit()
        return {'outcome': 'same_version_conflict', 'error_code': 'same_version_conflict'}
    except Exception:
        db.rollback()
        raise
    return {'outcome': 'budget_exhausted' if next_pending else 'page_committed', 'requests': budget.requests, 'inserted': inserted, 'updated': updated,
            'remote_deleted': len(deleted), 'has_more': token is not None, 'phase': values.get('phase', phase)}
