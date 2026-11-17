"""Read-only browsing of already stored mail. No provider/network/model calls."""
from fastapi import HTTPException
from sqlalchemy import select,or_
from ..models import Email,GmailAccount

CATEGORIES={'receipt','newsletter','notification','promotions','social','updates','forums','personal'}
PRIORITIES={'high','normal','low'}

def account_list(db):
    rows=db.scalars(select(GmailAccount).order_by(GmailAccount.id).limit(101)).all()
    return {'accounts':[{'id':a.id,'address':a.email_address} for a in rows[:100]],'more_accounts_omitted':len(rows)>100}

def inbox(db,account_id,query='',category='',priority='',before=None,limit=25):
    if db.get(GmailAccount,account_id) is None:raise HTTPException(404,'Unknown account.')
    if len(query)>200:raise HTTPException(422,'Search is limited to 200 characters.')
    if category and category not in CATEGORIES:raise HTTPException(422,'Unknown category.')
    if priority and priority not in PRIORITIES:raise HTTPException(422,'Unknown priority.')
    if type(limit) is not int or not 1<=limit<=50:raise HTTPException(422,'Limit must be 1-50.')
    if before is not None and (type(before) is not int or before<1):raise HTTPException(422,'Invalid page cursor.')
    statement=select(Email).where(Email.account_id==account_id)
    if query:
        # Bound parameter plus escaped LIKE metacharacters: % and _ are literal.
        term='%'+query.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
        statement=statement.where(or_(Email.subject.ilike(term,escape='!'),Email.sender.ilike(term,escape='!'),Email.snippet.ilike(term,escape='!')))
    if category:statement=statement.where(Email.category==category)
    if priority:statement=statement.where(Email.priority==priority)
    if before is not None:statement=statement.where(Email.id<before)
    rows=db.scalars(statement.order_by(Email.id.desc()).limit(limit+1)).all();page=rows[:limit]
    return dict(account_id=account_id,query=query,category=category,priority=priority,limit=limit,
                ordering='Stored record ID descending, not sent/received time.',
                search_scope='Literal case-insensitive subject, sender and snippet search. Case behavior depends on database collation; bodies and attachments excluded.',
                messages=[dict(id=m.id,thread_id=m.thread_id,subject=(m.subject or '')[:998],sender=(m.sender or '')[:320],snippet=(m.snippet or '')[:1000],snippet_truncated=len(m.snippet or '')>1000,category=m.category,priority=m.priority,source=m.classification_source,is_sample=m.is_sample,date=m.internal_date.isoformat() if m.internal_date else None) for m in page],
                next_before=page[-1].id if len(rows)>limit and page else None)

def stored_message(db,account_id,message_id):
    m=db.scalar(select(Email).where(Email.id==message_id,Email.account_id==account_id))
    if m is None:raise HTTPException(404,'Stored message not found.')
    text=m.body_text or m.snippet or ''
    return dict(id=m.id,account_id=account_id,thread_id=m.thread_id,subject=(m.subject or '')[:998],sender=(m.sender or '')[:320],recipients=(m.recipients or '')[:2000],recipients_truncated=len(m.recipients or '')>2000,is_sample=m.is_sample,date=m.internal_date.isoformat() if m.internal_date else None,text=text[:32000],text_truncated=len(text)>32000,category=m.category,priority=m.priority,classification_source=m.classification_source)
