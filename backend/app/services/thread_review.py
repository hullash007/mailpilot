"""Synthetic-only extractive thread review. No LLM, writes or action sink.

A candidate means only that literal text matched a narrow pattern. Every item
carries an exact body/snippet span; no claim of truth, intent or commitment.
Relative dates are never resolved. Quoted replies/signatures are untrusted too.
"""
import re
from datetime import date
from fastapi import HTTPException
from sqlalchemy import select, func
from ..models import Email, Thread

VERSION = 'literal-thread-v1'
MAX_MESSAGES = 50
MAX_TEXT = 8000
MAX_ITEMS = 30
REQUEST = re.compile(r'\b(?:please\s+(?:send|review|confirm|reply|submit|approve)|could you\s+(?:send|review|confirm|reply|submit)|can you\s+(?:send|review|confirm|reply|submit))\b', re.I)
DEADLINE = re.compile(r'\b(?:by|before|deadline(?:\s+is|:)?|due(?:\s+on|:)?)[ \t]+(?P<literal>\d{4}-\d{2}-\d{2}|tomorrow|today|next\s+\w+|\d{1,2}/\d{1,2}(?:/\d{2,4})?|(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b', re.I)


def evidence_spans(text, field):
    """Offsets index the returned source text's Unicode code points."""
    items = []
    # Work line-by-line so displayed text and offsets remain exact, bounded.
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        left = len(line)-len(line.lstrip())
        if stripped:
            start = offset+left
            end = start+len(stripped)
            if REQUEST.search(stripped):
                items.append(dict(kind='request_phrase', field=field, start=start, end=end,
                                  quote=text[start:end], status='unconfirmed'))
            for match in DEADLINE.finditer(line):
                literal = match.group('literal')
                interpretation = 'unresolved'
                if re.fullmatch(r'\d{4}-\d{2}-\d{2}', literal):
                    try:
                        date.fromisoformat(literal)
                        interpretation = 'valid_date_literal_not_a_scheduled_deadline'
                    except ValueError:
                        interpretation = 'invalid_date_literal'
                items.append(dict(kind='deadline_phrase', field=field,
                                  start=offset+match.start(), end=offset+match.end(),
                                  quote=match.group(), literal=literal,
                                  interpretation=interpretation, status='unconfirmed'))
        offset += len(line)
    return items


def review_thread(db, account_id, thread_id):
    thread = db.scalar(select(Thread).where(Thread.id==thread_id, Thread.account_id==account_id))
    if thread is None:
        raise HTTPException(status_code=404, detail='Sample thread not found.')
    # Check ALL records before limiting output. Mixed/corrupted ownership or
    # real-message threads fail closed, not merely filtered from presentation.
    total = db.scalar(select(func.count(Email.id)).where(Email.thread_id==thread_id)) or 0
    unsafe = db.scalar(select(func.count(Email.id)).where(Email.thread_id==thread_id,
                      (Email.account_id!=account_id) | Email.is_sample.is_not(True))) or 0
    if unsafe or not total:
        raise HTTPException(status_code=404, detail='Sample thread not found.')
    rows = db.scalars(select(Email).where(Email.thread_id==thread_id, Email.account_id==account_id,
                      Email.is_sample.is_(True)).order_by(Email.internal_date.asc().nulls_last(), Email.id).limit(MAX_MESSAGES)).all()
    messages=[];candidates=[];highlights=[];omitted_candidates=0
    for row in rows:
        full=row.body_text or row.snippet or ''
        field='body_text' if row.body_text else 'snippet'
        source=full[:MAX_TEXT]
        message=dict(id=row.id, sender=(row.sender or '')[:320],
                     date=row.internal_date.isoformat() if row.internal_date else None,
                     source_field=field, text=source, text_truncated=len(full)>len(source))
        messages.append(message)
        # Extractive overview: first nonempty line, not an inferred summary.
        first=re.search(r'\S[^\n\r]*',source)
        if first:
            end=min(first.end(), first.start()+240)
            highlights.append(dict(message_id=row.id,field=field,start=first.start(),end=end,
                                   quote=source[first.start():end]))
        for item in evidence_spans(source,field):
            if len(candidates)<MAX_ITEMS:
                candidates.append(dict(message_id=row.id,**item))
            else: omitted_candidates+=1
    return dict(account_id=account_id,thread_id=thread_id,subject=(thread.subject or '')[:998],
                sample_only=True,version=VERSION,notice='Literal matches in untrusted sample text only. Not confirmed requests, commitments, facts or scheduled deadlines. Quoted text may match. No actions created.',
                total_messages=total,messages_truncated=total>len(messages),
                messages=messages,extractive_highlights=highlights,candidates=candidates,
                omitted_candidates=omitted_candidates)
