"""Candidate-only language signals and exact-evidence human decisions. No network."""
from dataclasses import dataclass
from datetime import datetime
import hashlib,json,re
from fastapi import HTTPException
from sqlalchemy import select,update,insert,literal,exists
from sqlalchemy.exc import IntegrityError,OperationalError
from ..models import Email
from ..action_models import ActionDecision
VERSION='explicit-v1'
REASONS={'request':'Contains explicit request wording','question':'Contains a direct question/request','commitment':'Contains a stated commitment, not necessarily yours','followup':'Contains follow-up wording'}
RULES=[('request',r'\bplease\s+(?:review|send|reply|respond|confirm|check|provide|share|approve)\b'),('question',r'\b(?:could|can|would|will)\s+you\b[^\n?]{0,160}\?'),('commitment',r'\bI\s+(?:will|shall)\s+(?:send|review|reply|confirm|provide|share)\b'),('followup',r'\bfollowing\s+up\b')]
@dataclass(frozen=True)
class Observation:
    version:str
    fingerprint:str
    source_fingerprint:str
    reason:str
    start:int
    end:int

def detect(body,headers=None,version=VERSION):
    headers={str(k).lower():str(v).lower() for k,v in (headers or {}).items()}
    if 'list-unsubscribe' in headers or headers.get('precedence','') in ('bulk','list') or headers.get('auto-submitted','no')!='no':return None
    if not isinstance(body,str):return None
    source=hashlib.sha256(json.dumps([body,headers],sort_keys=True,separators=(',',':')).encode()).hexdigest();offset=0
    for line in body[:16000].splitlines(keepends=True):
        text=line.strip()
        if text=='--' or re.match(r'^On .+wrote:$',text,re.I):break
        if text.startswith('>'):offset+=len(line);continue
        for reason,pattern in RULES:
            if re.search(pattern,line,re.I):
                start=offset;end=min(offset+len(line.rstrip()),start+320)
                identity=json.dumps([version,source,reason,start,end],separators=(',',':'))
                return Observation(version,hashlib.sha256(identity.encode()).hexdigest(),source,reason,start,end)
        offset+=len(line)
    return None

def owned_source(db,account_id,email_id):
    row=db.scalar(select(Email).where(Email.id==email_id,Email.account_id==account_id,Email.is_sample.is_(True)))
    if row is None:raise HTTPException(404,'Stored source not found.')
    return row

def observation_for(row,version=VERSION):return detect(row.body_text or '',row.headers,version)
def evidence(row,obs):
    body=row.body_text or ''
    actual=observation_for(row,obs.version)
    if actual is None or actual.fingerprint!=obs.fingerprint or not 0<=obs.start<obs.end<=len(body):raise HTTPException(409,'Source evidence changed. Review again.')
    return body[obs.start:obs.end]

def decision_query(account_id,email_id,obs):
    return select(ActionDecision).where(ActionDecision.account_id==account_id,ActionDecision.source_email_id==email_id,ActionDecision.detector_version==obs.version,ActionDecision.evidence_fingerprint==obs.fingerprint)

def review(db,account_id,email_id,version=VERSION):
    row=owned_source(db,account_id,email_id);obs=observation_for(row,version)
    history=db.scalars(select(ActionDecision).where(ActionDecision.account_id==account_id,ActionDecision.source_email_id==email_id).order_by(ActionDecision.id.desc())).all()
    current=db.scalar(decision_query(account_id,email_id,obs)) if obs else None
    status=current.status if current else ('needs_review' if history and obs else 'unreviewed')
    return dict(source=row,observation=obs,excerpt=evidence(row,obs) if obs else None,current=current,status=status,history=history)

def decide(db,account_id,email_id,fingerprint,revision,status,version=VERSION):
    if status not in {'reviewed','confirmed','dismissed','completed'} or type(revision) is not int or revision<0:raise HTTPException(422,'Choose a valid local decision.')
    row=owned_source(db,account_id,email_id);obs=observation_for(row,version)
    if obs is None or obs.fingerprint!=fingerprint:raise HTTPException(409,'Candidate evidence changed. Review again.')
    current=db.scalar(decision_query(account_id,email_id,obs))
    source_matches=exists(select(Email.id).where(Email.id==email_id,Email.account_id==account_id,Email.body_text==row.body_text,Email.headers==row.headers,Email.is_sample.is_(True)))
    try:
        if current is None:
            if revision!=0:raise HTTPException(409,'Decision changed in another page. Reload.')
            now=datetime.utcnow()
            values=select(literal(account_id),literal(email_id),literal(obs.version),literal(obs.fingerprint),literal(status),literal(1),literal(now),literal(now)).where(source_matches)
            changed=db.execute(insert(ActionDecision).from_select(['account_id','source_email_id','detector_version','evidence_fingerprint','status','revision','created_at','updated_at'],values)).rowcount
            if changed!=1:raise HTTPException(409,'Source evidence changed. Review again.')
        else:
            changed=db.execute(update(ActionDecision).where(ActionDecision.id==current.id,ActionDecision.account_id==account_id,ActionDecision.revision==revision,source_matches).values(status=status,revision=ActionDecision.revision+1,updated_at=datetime.utcnow()),execution_options={'synchronize_session':False}).rowcount
            if changed!=1:raise HTTPException(409,'Decision changed in another page. Reload.')
        db.commit();db.expire_all()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'Decision changed in another page. Reload.') from None
    except OperationalError as exc:
        db.rollback()
        # SQLite lock contention is retryable; do not expose raw database errors.
        code=getattr(exc.orig,'sqlite_errorcode',None)
        if isinstance(code,int) and code & 0xff in (5,6):
            raise HTTPException(503,'Local decisions are busy. Reload and retry.') from None
        raise
    except HTTPException:
        db.rollback();raise
