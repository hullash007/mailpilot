"""Existing-schema stored reads. No provider clients, sync models or writes."""
import re
from sqlalchemy import select,or_,func
from fastapi import HTTPException
from ..models import Email,Thread
from .inbox import CATEGORIES,PRIORITIES

def storage_status(db,account_id):
 count=db.scalar(select(func.count(Email.id)).where(Email.account_id==account_id))
 return dict(label=str(count)+' stored messages',detail='Locally stored content only. Conversations may be incomplete. Gmail membership and completeness are not established.')

def _query(account_id):
 return select(Email).join(Thread,Email.thread_id==Thread.id).where(Email.account_id==account_id,Thread.account_id==account_id)

def _message(row):
 text=row.body_text or row.snippet or ''
 return dict(id=row.id,thread_id=row.thread_id,subject=row.subject or '(no subject)',sender=row.sender,recipients=row.recipients,date=row.internal_date,sent='SENT' in (row.label_ids or []),status='Stored only; current Gmail membership unverified',text=text[:32000],truncated=len(text)>32000,category=row.category,priority=row.priority,is_sample=row.is_sample)

def search(db,account_id,term='',category='',priority='',before=None):
 if len(term)>200 or (category and category not in CATEGORIES) or (priority and priority not in PRIORITIES):raise HTTPException(422,'Choose a valid search or filter.')
 query=_query(account_id)
 if term:
  pattern='%'+term.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
  query=query.where(or_(*[col.ilike(pattern,escape='!') for col in (Email.subject,Email.sender,Email.body_text,Email.snippet)]))
 if category:query=query.where(Email.category==category)
 if priority:query=query.where(Email.priority==priority)
 if before is not None:query=query.where(Email.id<before)
 rows=db.scalars(query.order_by(Email.id.desc()).limit(51)).all();grouped={};results=[]
 for row in rows[:50]:
  if row.thread_id in grouped:grouped[row.thread_id]['matched_messages']+=1;continue
  item=_message(row);item['matched_messages']=1;grouped[row.thread_id]=item
  text=row.body_text or row.snippet or '';match=text.casefold().find(term.casefold()) if term else 0;start=max(0,match-60)
  item['excerpt']=text[start:start+240];results.append(item)
 return dict(messages=results,next_before=rows[49].id if len(rows)>50 else None,withheld=0)

def conversation(db,account_id,thread_id):
 thread=db.scalar(select(Thread).where(Thread.id==thread_id,Thread.account_id==account_id))
 if thread is None:raise HTTPException(404,'Conversation not found in your stored mail.')
 rows=db.scalars(_query(account_id).where(Email.thread_id==thread_id).order_by(Email.internal_date.asc().nullslast(),Email.id.asc()).limit(501)).all()
 return dict(subject=thread.subject or '(no subject)',messages=[_message(row) for row in rows[:500]],omitted=len(rows)>500)
