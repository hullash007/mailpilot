"""Opt-in synthetic Action Inbox. No startup schema creation or live installation."""
from fastapi import Depends,Request,HTTPException,Form
from fastapi.responses import RedirectResponse
from fastapi.exception_handlers import http_exception_handler
from sqlalchemy import select
from .google_main import create_app as google_app,own_account,require_same_origin,page,esc
from .database import get_db
from .models import Email
from .action_models import ActionDecision
from .services import action_review as service

def shell(body):
    return page('<style>.decision-controls{display:flex;flex-wrap:wrap;gap:8px}.card blockquote{margin:16px 0;padding:8px 12px;border-left:3px solid #17223b}a:focus-visible,button:focus-visible{outline:3px solid #17223b;outline-offset:3px}</style><nav><a href="/actions">Potential action review</a> · <a href="/actions/history">Decision history</a></nav><p class="notice">Synthetic local prototype only. No Gmail or AI. A local decision never sends or changes email.</p>'+body)
def create_app():
    app=google_app()
    @app.exception_handler(HTTPException)
    async def error(request,exc):
        if not request.url.path.startswith('/actions'):return await http_exception_handler(request,exc)
        r=shell('<h1>Review could not continue</h1><p class="notice">'+esc(exc.detail)+'</p><p><a href="/actions">Reload potential actions</a></p>');r.status_code=exc.status_code;return r
    @app.get('/actions')
    def inbox(account=Depends(own_account),db=Depends(get_db)):
        body='<h1>Potential actions - unverified</h1><p>Explicit language patterns only, not a complete task list. A question, date or commitment does not establish an obligation for you. False positives and missed implied requests are expected.</p>'
        rows=db.scalars(select(Email).where(Email.account_id==account.id,Email.is_sample.is_(True)).order_by(Email.id).limit(50)).all();shown=0
        for row in rows:
            data=service.review(db,account.id,row.id);obs=data['observation']
            if not obs and not data['history']:continue
            shown+=1;body+='<article class="card"><h2>'+esc(row.subject or '(no subject)')+'</h2>'
            if obs:
                current=data['current'];revision=current.revision if current else 0
                body+='<p>Potential action - unverified</p><p>'+esc(service.REASONS[obs.reason])+'</p><blockquote>'+esc(data['excerpt'])+'</blockquote><p>Local status: '+esc(data['status'].replace('_',' '))+'</p>'
                body+='<form class="decision-controls" method="post" action="/actions/'+str(row.id)+'"><input type="hidden" name="fingerprint" value="'+obs.fingerprint+'"><input type="hidden" name="revision" value="'+str(revision)+'">'
                for status,label in [('reviewed','Mark reviewed'),('confirmed','Confirm as action'),('dismissed','Dismiss'),('completed','Mark completed')]:body+='<button name="status" value="'+status+'">'+label+'</button> '
                body+='</form>'
            else:body+='<p>No current candidate. Previous local decisions remain historical.</p>'
            body+='<p><a href="/actions/source/'+str(row.id)+'">Open synthetic source</a></p>'
            if data['history']:
                body+='<details><summary>Local decision history</summary>'
                for d in data['history']:body+='<p>'+esc(d.status)+' · revision '+str(d.revision)+' · '+('Current evidence' if obs and d.evidence_fingerprint==obs.fingerprint and d.detector_version==obs.version else 'Historical evidence')+'</p>'
                body+='</details>'
            body+='</article>'
        if not shown:body+='<p>No explicit language-pattern candidates in the first 50 stored messages. This is not proof that nothing needs attention.</p>'
        body+='<p>Prototype scans at most 50 messages and 16,000 characters per message, showing one candidate per message. No deadline tracking or urgency inference.</p>'
        return shell(body)
    @app.get('/actions/history')
    def history(before:int|None=None,account=Depends(own_account),db=Depends(get_db)):
        if before is not None and not 1<=before<=9223372036854775807:raise HTTPException(422,'Invalid history page.')
        query=select(ActionDecision,Email).outerjoin(Email,(Email.id==ActionDecision.source_email_id)&(Email.account_id==ActionDecision.account_id)&(Email.is_sample.is_(True))).where(ActionDecision.account_id==account.id)
        if before is not None:query=query.where(ActionDecision.id<before)
        rows=db.execute(query.order_by(ActionDecision.id.desc()).limit(50)).all()
        body='<h1>Decision history</h1><p>Local decisions only. Confirmation or completion records what you marked, not proof an external action happened. This is the last status per exact evidence, not a log of every click.</p><p>Up to 50 decisions on this page, newest local record first. Historical records cannot be changed here.</p>'
        for decision,row in rows:
            obs=service.observation_for(row) if row else None
            current=bool(obs and obs.fingerprint==decision.evidence_fingerprint and obs.version==decision.detector_version)
            label='Source unavailable' if row is None else ('Current evidence matches' if current else 'Historical evidence - source changed or no current candidate')
            body+='<article class="card"><h2>Local decision '+str(decision.id)+'</h2><p>Status: '+esc(decision.status)+' · revision '+str(decision.revision)+'</p><p>'+label+'</p>'
            if row:body+='<p><a href="/actions/source/'+str(row.id)+'">Open current synthetic source</a></p><p>The current source may differ from the evidence reviewed. No old excerpt is stored here.</p>'
            else:body+='<p>No source content is available for this record. No content has been inferred or copied.</p>'
            body+='</article>'
        if not rows:body+='<p>No local decisions on this page.</p>'
        if len(rows)==50:body+='<p><a href="/actions/history?before='+str(rows[-1][0].id)+'">Older decisions</a></p>'
        if before is not None:body+='<p><a href="/actions/history">Newest decisions</a></p>'
        return shell(body)
    @app.get('/actions/source/{email_id}')
    def source(email_id:int,account=Depends(own_account),db=Depends(get_db)):
        row=service.owned_source(db,account.id,email_id)
        return shell('<h1>Synthetic stored source</h1><pre>'+esc((row.body_text or '')[:16000])+'</pre>')
    @app.post('/actions/{email_id}')
    def decision(email_id:int,request:Request,fingerprint:str=Form(max_length=64),revision:int=Form(ge=0),status:str=Form(max_length=16),account=Depends(own_account),db=Depends(get_db)):
        require_same_origin(request)
        service.decide(db,account.id,email_id,fingerprint,revision,status)
        return RedirectResponse('/actions',status_code=303)
    return app
