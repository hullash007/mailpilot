"""Candidate-only stored conversation/search reads; no network or mutations."""
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, or_, func
from ..models import Email, Thread
from ..sync_models import MailboxSync, MessageRemoteState, ThreadSnapshot


def sync_status(db, account_id):
    row = db.get(MailboxSync, account_id)
    if row is None:
        return dict(label='Limited stored sample', detail='Manual five-message import only. Full mailbox sync is not enabled.', last_success=None)
    if row.retry_exhausted:
        label, detail = 'Sync paused for review', 'Automatic retry limit reached. Stored mail is preserved.'
    elif row.retry_at:
        label, detail = 'Sync paused, retry scheduled', 'Stored progress is saved. Another attempt is scheduled; mail may be out of date.'
    elif row.blocked:
        label, detail = 'Sync blocked', 'Page recovery stopped safely. Stored mail remains available; operator review needed.'
    elif row.error_code == 'budget_exhausted':
        label, detail = 'Sync paused safely', 'This batch reached its work limit. Stored progress is saved; another controlled batch can continue.'
    elif row.error_code:
        label, detail = 'Sync needs attention', 'The latest sync did not finish. Results may be out of date.'
    elif row.phase != 'ready':
        label, detail = 'Initial sync incomplete', 'Import or catch-up is unfinished. Results are partial.'
    elif not row.last_success or (datetime.utcnow() - row.last_success).total_seconds() > 3600:
        label, detail = 'Stored mail may be stale', 'No successful sync within the last hour. Nothing here is live.'
    else:
        label, detail = 'Last sync completed', 'History replay completed at the time shown. This is stored mail, not a live Gmail view.'
    return dict(label=label, detail=detail, last_success=row.last_success)


def _membership(row, remote, snap, completed_generation=0):
    if remote and remote.deleted_at:
        return 'retained after remote deletion'
    if remote and remote.excluded:
        return 'retained outside selected scope'
    if snap and snap.availability == 'unavailable':
        return 'provider conversation unavailable'
    if snap and completed_generation and snap.observed_generation < completed_generation:
        return 'not observed in latest completed sync'
    if snap and row.gmail_message_id not in snap.message_ids:
        return 'retained outside last snapshot'
    if snap:
        return 'in last provider snapshot'
    return 'stored only, provider membership unverified'


def _message(row, remote, snap, completed_generation=0):
    return dict(id=row.id, thread_id=row.thread_id, subject=row.subject or '(no subject)', sender=row.sender,
                recipients=row.recipients, date=row.internal_date, sent='SENT' in (row.label_ids or []),
                status=_membership(row, remote, snap, completed_generation), text=(row.body_text or row.snippet or '')[:32000],
                truncated=len(row.body_text or row.snippet or '') > 32000, category=row.category, priority=row.priority,
                is_sample=row.is_sample)


def _query(account_id):
    return select(Email, MessageRemoteState, ThreadSnapshot).join(Thread, Email.thread_id == Thread.id).outerjoin(
        MessageRemoteState, (MessageRemoteState.account_id == Email.account_id) &
        (MessageRemoteState.gmail_message_id == Email.gmail_message_id)).outerjoin(
        ThreadSnapshot, (ThreadSnapshot.account_id == Thread.account_id) &
        (ThreadSnapshot.gmail_thread_id == Thread.gmail_thread_id)).where(
        Email.account_id == account_id, Thread.account_id == account_id)


def conversation(db, account_id, thread_id):
    thread = db.scalar(select(Thread).where(Thread.id == thread_id, Thread.account_id == account_id))
    if thread is None:
        raise HTTPException(404, 'Conversation not found in your stored mail.')
    rows = db.execute(_query(account_id).where(Email.thread_id == thread_id).order_by(
        Email.internal_date.asc().nullslast(), Email.id.asc()).limit(501)).all()
    snap = db.scalar(select(ThreadSnapshot).where(ThreadSnapshot.account_id == account_id,
                                                ThreadSnapshot.gmail_thread_id == thread.gmail_thread_id))
    state = db.get(MailboxSync, account_id)
    completed = state.completed_generation if state else 0
    return dict(subject=thread.subject or '(no subject)', messages=[_message(*row, completed) for row in rows[:500]],
                unobserved=bool(snap and completed and snap.observed_generation < completed),
                omitted=len(rows) > 500, snapshot=snap)


def search(db, account_id, term='', category='', priority='', before=None):
    from .inbox import CATEGORIES, PRIORITIES
    if len(term) > 200 or (category and category not in CATEGORIES) or (priority and priority not in PRIORITIES):
        raise HTTPException(422, 'Choose a valid search or filter.')
    if before is not None and (type(before) is not int or not 1 <= before <= 9223372036854775807):
        raise HTTPException(422, 'Invalid stored page.')
    query = _query(account_id).where(or_(MessageRemoteState.deleted_at.is_(None), MessageRemoteState.id.is_(None)),
               or_(MessageRemoteState.excluded.is_(False), MessageRemoteState.id.is_(None)))
    if term:
        pattern = '%' + term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
        query = query.where(or_(*[col.ilike(pattern, escape='!') for col in
                       (Email.subject, Email.sender, Email.body_text, Email.snippet)]))
    if category: query = query.where(Email.category == category)
    if priority: query = query.where(Email.priority == priority)
    if before: query = query.where(Email.id < before)
    rows = db.execute(query.order_by(Email.id.desc()).limit(51)).all()
    # Bound work. Skipped stale memberships are explicit, never a silent empty result.
    state = db.get(MailboxSync, account_id)
    completed = state.completed_generation if state else 0
    results, grouped = [], {}
    visible_matches = 0
    for row, remote, snap in rows[:50]:
        if snap and (snap.availability != 'available' or row.gmail_message_id not in snap.message_ids or
                     (completed and snap.observed_generation < completed)):
            continue
        if {'SPAM', 'TRASH'} & set(row.label_ids or []):
            continue
        visible_matches += 1
        if row.thread_id in grouped:
            grouped[row.thread_id]['matched_messages'] += 1
            continue
        item = _message(row, remote, snap, completed)
        item['matched_messages'] = 1
        grouped[row.thread_id] = item
        text = row.body_text or row.snippet or ''
        match = text.casefold().find(term.casefold()) if term else 0
        start = max(0, match - 60) if match >= 0 else 0
        item['excerpt'] = text[start:start+240]
        results.append(item)
    return dict(messages=results, next_before=rows[49][0].id if len(rows) > 50 else None,
                withheld=len(rows[:50])-visible_matches)
