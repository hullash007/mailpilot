"""Opt-in request-scoped Gmail reader. No credential discovery, storage or writes.

The supplied provider is trusted and MUST belong to the verified principal.
A profile-email check adds mismatch detection; it does not replace that binding.
Transport deadlines/refresh custody remain a separately qualified adapter boundary.
"""
import base64
from email.message import Message
from .mail_html_text import html_to_text, HTMLTextLimit
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

class MailUnavailable(RuntimeError): pass
class ReconnectRequired(MailUnavailable): pass
class InvalidPage(ValueError): pass

def provider_id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        raise MailUnavailable('Invalid provider identifier')
    return value

def text(value, limit=2000):
    if type(value) is not str: raise MailUnavailable('Invalid provider text')
    return value[:limit]

@dataclass(frozen=True)
class ReadPolicy:
    page_size: int
    max_requests: int
    seconds: float
    def __post_init__(self):
        if type(self.page_size) is not int or not 1<=self.page_size<=25 or type(self.max_requests) is not int or not self.page_size+3<=self.max_requests<=100 or not 0<self.seconds<=60:
            raise ValueError('Explicit bounded read policy required')

class PageSigner:
    def __init__(self,key,*,clock=time.time):
        if type(key) is not bytes or len(key)<32: raise ValueError('Separate page-signing key required')
        self.key,self.clock=key,clock
    def issue(self,principal,folder,query,token):
        if type(token) is not str or not 1<=len(token)<=4096: raise MailUnavailable('Invalid continuation')
        binding=hmac.new(self.key,b'principal:'+principal.encode(),hashlib.sha256).hexdigest()
        payload=json.dumps([binding,folder,query,token,int(self.clock())],separators=(',',':')).encode()
        raw=base64.urlsafe_b64encode(payload).rstrip(b'=')
        return (raw+b'.'+hmac.new(self.key,raw,hashlib.sha256).hexdigest().encode()).decode()
    def open(self,value,principal,folder,query):
        try:
            if type(value) is not str or len(value)>9000: raise ValueError()
            raw,mac=value.encode().split(b'.')
            if not hmac.compare_digest(mac,hmac.new(self.key,raw,hashlib.sha256).hexdigest().encode()): raise ValueError()
            p=json.loads(base64.urlsafe_b64decode(raw+b'='*(-len(raw)%4)))
            if type(p) is not list or len(p)!=5 or p[:3]!=[hmac.new(self.key,b'principal:'+principal.encode(),hashlib.sha256).hexdigest(),folder,query] or type(p[4]) is not int or not 0<=self.clock()-p[4]<=1800:raise ValueError()
            if type(p[3]) is not str or not 1<=len(p[3])<=4096:raise ValueError()
            return p[3]
        except Exception:raise InvalidPage('This page has expired. Start from the first page.') from None

class GmailReadService:
    def __init__(self,provider,*,email,policy,clock=time.monotonic):
        if not isinstance(policy,ReadPolicy) or type(email) is not str or not email:raise ValueError('Explicit bound reader required')
        self.provider,self.email,self.policy,self.clock=provider,email,policy,clock
        self.deadline=clock()+policy.seconds;self.calls=0
    def call(self,name,*args,**kwargs):
        remaining=self.deadline-self.clock()
        if remaining<=0 or self.calls>=self.policy.max_requests:raise MailUnavailable('Mailbox read budget exhausted')
        self.calls+=1
        try:
            result=getattr(self.provider,name)(*args,timeout=remaining,**kwargs)
            if self.clock()>=self.deadline:raise MailUnavailable('Mailbox read budget exhausted')
            if type(result) is not dict:raise MailUnavailable('Invalid mailbox response')
            return result
        except ReconnectRequired:raise
        except Exception:raise MailUnavailable('Mailbox could not be refreshed') from None
    def verify(self):
        p=self.call('profile')
        if type(p.get('emailAddress')) is not str or p['emailAddress'].casefold()!=self.email.casefold():
            raise MailUnavailable('Connected mailbox does not match signed-in account')
    def labels(self):
        value=self.call('labels').get('labels',[])
        if type(value) is not list or len(value)>10000:raise MailUnavailable('Invalid labels')
        return [dict(id=provider_id(x.get('id')),name=text(x.get('name'),200)) for x in value if type(x) is dict and x.get('type')=='user'][:100]
    def listing(self,folder,query,token):
        page=self.call('threads',label=folder,query=query,page_token=token,page_size=self.policy.page_size)
        rows=page.get('threads',[])
        if type(rows) is not list or len(rows)>self.policy.page_size:raise MailUnavailable('Invalid mailbox page')
        seen=set();result=[]
        for row in rows:
            tid=provider_id(row.get('id') if type(row) is dict else None)
            if tid in seen:raise MailUnavailable('Duplicate conversation')
            seen.add(tid)
            raw=self.call('thread',tid,metadata_only=True)
            result.append(thread(raw,expected=tid,metadata_only=True))
        return dict(threads=result,next_token=page.get('nextPageToken'),checked=datetime.now(timezone.utc).strftime('%H:%M UTC'))
    def conversation(self,tid):
        return thread(self.call('thread',provider_id(tid),metadata_only=False),expected=tid,metadata_only=False)

def message_body(payload, depth=0, *, budget=None):
    """Select inline MIME content; prefer plain within each alternative subtree."""
    budget = budget if budget is not None else {'nodes': 2000, 'bytes': 2000000}
    attachments = []

    def walk(p, d):
        budget['nodes'] -= 1
        if budget['nodes'] < 0 or d > 20 or type(p) is not dict:
            raise MailUnavailable('Invalid or oversized message structure')
        hs = p.get('headers', [])
        if type(hs) is not list or len(hs) > 1000:
            raise MailUnavailable('Invalid message headers')
        headers = {}
        for h in hs:
            if type(h) is dict and type(h.get('name')) is str:
                name = h['name'].lower()
                if name in ('content-type', 'content-disposition'):
                    if name in headers: raise MailUnavailable('Duplicate MIME headers')
                    headers[name] = text(h.get('value', ''), 4000)
        disposition = Message()
        disposition['Content-Disposition'] = headers.get('content-disposition', '')
        filename = p.get('filename', '')
        if filename or disposition.get_content_disposition() == 'attachment':
            attachments.append(text(filename or 'Unnamed attachment', 255))
            return '', False, 'none'
        body = p.get('body', {})
        parts = p.get('parts', [])
        if type(body) is not dict or type(parts) is not list or len(parts) > 2000:
            raise MailUnavailable('Invalid message body or parts')
        mime = p.get('mimeType', '').lower() if type(p.get('mimeType', '')) is str else ''
        own = ('', False, 'none')
        if mime in ('text/plain', 'text/html') and body.get('data'):
            data = body['data']
            if type(data) is not str or len(data) > 1000000:
                raise MailUnavailable('Message text too large')
            budget['bytes'] -= len(data)
            if budget['bytes'] < 0: raise MailUnavailable('Conversation text too large')
            try:
                raw = base64.b64decode(data + '=' * (-len(data) % 4), altchars=b'-_', validate=True)
            except Exception:
                raise MailUnavailable('Invalid message encoding') from None
            content_type = Message()
            content_type['Content-Type'] = headers.get('content-type', mime)
            charset = content_type.get_content_charset() or 'utf-8'
            # Restrict to text encodings, not arbitrary Python codec transforms.
            charset = charset.lower().replace('_', '-')
            allowed = {'utf-8', 'utf8', 'us-ascii', 'ascii', 'iso-8859-1',
                       'latin-1', 'windows-1252', 'cp1252', 'utf-16',
                       'utf-16le', 'utf-16be', 'shift-jis', 'gb18030', 'big5',
                       'iso-8859-15', 'windows-1251', 'cp1251', 'koi8-r'}
            value = raw.decode(charset if charset in allowed else 'utf-8', errors='replace')
            if mime == 'text/html':
                try: value, clipped = html_to_text(value)
                except (HTMLTextLimit, ValueError):
                    raise MailUnavailable('HTML text could not be safely converted') from None
                own = value, clipped, 'html_text'
            else:
                own = value[:128000], len(value) > 128000, 'plain'
        children = [walk(child, d + 1) for child in parts]
        values = ([own] if own[0] else []) + [x for x in children if x[0]]
        if mime == 'multipart/alternative' and values:
            values = [next((x for x in values if x[2] == 'plain'), values[-1])]
        content = '\n\n'.join(x[0] for x in values)
        kind = ('html_text' if any(x[2] == 'html_text' for x in values) else
                'plain' if values else 'none')
        return content[:128000], len(content) > 128000 or any(x[1] for x in values), kind

    content, truncated, kind = walk(payload, depth)
    return content, truncated, attachments, kind


def plain_parts(payload, depth=0, *, budget=None):
    # Preserve the existing three-value source interface.
    return message_body(payload, depth, budget=budget)[:3]

def thread(raw,*,expected,metadata_only):
    if raw.get('id')!=expected:raise MailUnavailable('Conversation identity mismatch')
    messages=raw.get('messages',[])
    if type(messages) is not list or not messages or len(messages)>1000:raise MailUnavailable('Invalid conversation')
    output=[];budget={'nodes':2000,'bytes':2000000}
    for m in messages:
        if type(m) is not dict or m.get('threadId')!=expected:raise MailUnavailable('Message identity mismatch')
        mid=provider_id(m.get('id'));p=m.get('payload',{})
        if type(p) is not dict:raise MailUnavailable('Invalid message structure')
        hs=p.get('headers',[])
        if type(hs) is not list or len(hs)>1000:raise MailUnavailable('Invalid message headers')
        headers={}
        for h in hs:
            if type(h) is dict and type(h.get('name')) is str and h['name'].casefold() in ('subject','from','to','date'):
                headers[h['name'].casefold()]=text(h.get('value',''),4000)
        labels=m.get('labelIds',[])
        if type(labels) is not list or any(type(x) is not str for x in labels):raise MailUnavailable('Invalid message labels')
        body,truncated,attachments,body_format=('',False,[],'none') if metadata_only else message_body(p,budget=budget)
        output.append(dict(id=mid,subject=headers.get('subject','(No subject)'),sender=headers.get('from',''),to=headers.get('to',''),date=headers.get('date',''),snippet=text(m.get('snippet',''),1000),unread='UNREAD' in labels,starred='STARRED' in labels,body=body,body_format=body_format,truncated=truncated,attachments=attachments))
    return dict(id=expected,subject=output[0]['subject'],messages=output,latest=output[-1],unread=any(m['unread'] for m in output))
