"""Explicit fictional local-only UI fixture. No OAuth, DB or provider network."""
import base64
from copy import deepcopy
from types import SimpleNamespace
from fastapi import FastAPI,HTTPException,Request
from fastapi.responses import RedirectResponse,PlainTextResponse
from .gmail_reader_candidate import install_reader
from .google_main import own_account
from .database import get_db
from .services.gmail_reader import PageSigner,ReadPolicy

class FakeMailbox:
    def __init__(self,email='alex@harbor.example',prefix='H'):
        self.email=email;self.calls=[];self.rows={};self.prefix=prefix
        subjects=['Design review for the spring collection','Your train tickets are ready','Quick follow-up on the studio proposal','Saturday plans at the coast','Invoice for the November workshop','A few thoughts on the new website','The reading list you asked for','Welcome to the neighborhood','Next steps for the product launch','Notes from our planning call','Your monthly account summary','Dinner next Thursday?','The 2024 lease renewal','Project Northstar archive']
        for i,subject in enumerate(subjects):
            tid=prefix+str(i+1);label=['INBOX'] if i<12 else ['Label_archive'];labels=label+(['UNREAD'] if i%3==0 else [])+(['STARRED'] if i%4==0 else [])
            sender=['Maya Chen <maya@studio.example>','Oliver at Rail <tickets@rail.example>','Jordan Lee <jordan@design.example>'][i%3]
            body='Hi Alex,\n\n'+('Here is the older lease renewal from 2024. The reference is LEASE-2024-082.' if i==12 else 'I have shared the latest details for '+subject.lower()+'.\n\nCould you take a look before Thursday? I would love to hear what you think.')+'\n\nThanks,\n'+sender.split(' <')[0]
            msg=dict(id=tid+'a',threadId=tid,labelIds=labels,snippet=body.replace('\n',' ')[:170],payload=dict(mimeType='text/plain',headers=[dict(name=k,value=v) for k,v in [('Subject',subject),('From',sender),('To',email),('Date','Wed, 18 Nov 2026 09:30:00 +0000')]],body=dict(data=base64.urlsafe_b64encode(body.encode()).decode())))
            messages=[msg]
            if i==0:
                reply=deepcopy(msg);reply['id']=tid+'b';reply['labelIds']=['SENT','INBOX'];reply['payload']['headers'][1]['value']=email;reply['payload']['headers'][2]['value']=sender;reply['payload']['body']['data']=base64.urlsafe_b64encode(b'Hi Maya,\n\nThe new direction looks good. I will add my notes to the deck this afternoon.\n\nAlex').decode();reply['snippet']='The new direction looks good. I will add my notes this afternoon.';messages.append(reply)
            self.rows[tid]=dict(id=tid,messages=messages)
    def profile(self,*,timeout):self.calls.append(('profile',timeout));return dict(emailAddress=self.email)
    def labels(self,*,timeout):self.calls.append(('labels',timeout));return dict(labels=[dict(id='Label_archive',name='Projects',type='user')])
    def threads(self,*,label,query,page_token,page_size,timeout):
        self.calls.append(('threads',label,query,page_token,timeout));items=list(self.rows.values())
        if label:items=[t for t in items if any(label in m['labelIds'] for m in t['messages'])]
        if query:items=[t for t in items if query.casefold() in str(t).casefold() or (query=='lease' and t['id']==self.prefix+'13')]
        offset=int(page_token or '0');out=items[offset:offset+page_size];v=dict(threads=[dict(id=t['id']) for t in out])
        if offset+page_size<len(items):v['nextPageToken']=str(offset+page_size)
        return v
    def thread(self,tid,*,metadata_only,timeout):
        self.calls.append(('thread',tid,metadata_only,timeout))
        if tid not in self.rows:raise ValueError('missing')
        return deepcopy(self.rows[tid])
    def new_message(self):
        row=deepcopy(self.rows[self.prefix+'1']);row['id']=self.prefix+'99';row['messages']=[row['messages'][0]];m=row['messages'][0];m['id']=self.prefix+'99a';m['threadId']=row['id'];m['payload']['headers'][0]['value']='A new message just arrived';m['snippet']='Your mailbox is up to date.';self.rows={row['id']:row,**self.rows}

def create_fixture_app():
    app=FastAPI();boxes={'harbor':FakeMailbox(),'cedar':FakeMailbox('sam@cedar.example','C')}
    @app.middleware('http')
    async def loopback(request,call_next):
        if request.url.hostname not in ('127.0.0.1','localhost','testserver'):return PlainTextResponse('Local fixture only',status_code=403)
        return await call_next(request)
    def principal(request:Request):
        key=request.cookies.get('fixture_person','harbor')
        if key not in boxes:raise HTTPException(401,'Choose fixture identity')
        return SimpleNamespace(id=1 if key=='harbor' else 2,google_user_id='fictional-'+key,email_address=boxes[key].email)
    app.dependency_overrides[own_account]=principal;app.dependency_overrides[get_db]=lambda:None
    def provider(account,db):return boxes['harbor' if account.id==1 else 'cedar']
    install_reader(app,provider_for=provider,page_signer=PageSigner(b'fictional-only-page-key-not-a-real-secret-32'),policy=ReadPolicy(8,11,15),fixture=True)
    @app.get('/preview/as/{person}')
    def choose(person:str):
        if person not in boxes:raise HTTPException(404)
        r=RedirectResponse('/mail',303);r.set_cookie('fixture_person',person,httponly=True,samesite='strict');return r
    app.state.boxes=boxes
    return app
