"""Bounded synthetic inbox evidence inventory, not a daily delivery/decision.

No LLM, sends, scheduling, inferred obligation or state change. All output is
literal source evidence or explicitly named stored rule metadata.
"""
from fastapi import HTTPException
from sqlalchemy import select, func, or_
from ..models import Email, Thread, GmailAccount
from .thread_review import review_thread

MAX_THREADS=20
HIGHLIGHTS_PER_THREAD=2
CANDIDATES_PER_THREAD=5


def sample_briefing(db,account_id):
    if db.get(GmailAccount,account_id) is None:
        raise HTTPException(status_code=404,detail='Sample account not found.')
    # Validate entire account and linked-message ownership before limiting.
    # A corrupt thread/account relation must not reveal another account.
    unsafe=db.scalar(select(func.count(Email.id)).outerjoin(Thread,Email.thread_id==Thread.id).where(
        or_(Email.account_id==account_id,Thread.account_id==account_id),
        or_(Email.is_sample.is_not(True),Email.account_id!=account_id,
            Thread.account_id!=account_id,Thread.id.is_(None)))) or 0
    if unsafe:
        raise HTTPException(status_code=404,detail='Sample account not found.')
    total_messages=db.scalar(select(func.count(Email.id)).where(Email.account_id==account_id)) or 0
    # Order from actual stored messages, never stale Thread.last_message_at.
    activity=(select(Email.thread_id,func.max(Email.internal_date).label('latest'),
                     func.count(Email.id).label('messages'))
              .where(Email.account_id==account_id,Email.is_sample.is_(True))
              .group_by(Email.thread_id).subquery())
    total_threads=db.scalar(select(func.count()).select_from(activity)) or 0
    rows=db.execute(select(Thread,activity.c.latest,activity.c.messages)
                    .join(activity,activity.c.thread_id==Thread.id)
                    .where(Thread.account_id==account_id)
                    .order_by(activity.c.latest.desc().nulls_last(),Thread.id)
                    .limit(MAX_THREADS)).all()
    inventory=[]
    for thread,latest,count in rows:
        review=review_thread(db,account_id,thread.id)
        # These counts cover the full thread, unlike the bounded phrase scan.
        high=db.scalar(select(func.count(Email.id)).where(Email.thread_id==thread.id,
                       Email.account_id==account_id,Email.priority=='high',
                       Email.classification_source=='rules')) or 0
        candidates=review['candidates'][:CANDIDATES_PER_THREAD]
        inventory.append(dict(thread_id=thread.id,subject=review['subject'],
            review_path=f'/accounts/{account_id}/threads/{thread.id}/review',
            review_ui_path=f'/dashboard/accounts/{account_id}/threads/{thread.id}',
            message_count=count,latest_stored_message_at=latest.isoformat() if latest else None,
            time_basis='Stored timestamp; timezone unspecified. Not a deadline or freshness promise.',
            stored_rules_high_priority_message_count=high,
            extractive_highlights=review['extractive_highlights'][:HIGHLIGHTS_PER_THREAD],
            unconfirmed_candidates=candidates,
            candidates_omitted_from_scanned_text=(len(review['candidates'])-len(candidates)+review['omitted_candidates']),
            overview_highlights_omitted=len(review['extractive_highlights'])-min(HIGHLIGHTS_PER_THREAD,len(review['extractive_highlights'])),
            source_messages_scanned=len(review['messages']),
            source_messages_omitted=count-len(review['messages']),
            source_text_truncated=any(m['text_truncated'] for m in review['messages'])))
    return dict(account_id=account_id,sample_only=True,version='sample-briefing-v1',
        notice='Synthetic evidence inventory, not a daily notification, confirmed fact, task list or recommendation. Literal text can be false, quoted or malicious. No inferred obligations or scheduled actions.',
        ordering='Latest stored message timestamp, descending; unknown timestamps last.',
        total_sample_messages=total_messages,total_nonempty_sample_threads=total_threads,
        included_threads=len(inventory),omitted_threads=total_threads-len(inventory),
        threads=inventory,
        limits=dict(threads=MAX_THREADS,source_messages_per_thread=50,
                    source_characters_per_message=8000,highlights_per_thread=HIGHLIGHTS_PER_THREAD,
                    candidates_per_thread=CANDIDATES_PER_THREAD),
        limitations=['No time-window or daily-completeness claim.',
                     'No match does not mean no request or deadline exists.',
                     'No contextual summary, replies-needed inference or follow-up tracking.',
                     'No LLM, network, persistence, mail mutation or delivery.'])
