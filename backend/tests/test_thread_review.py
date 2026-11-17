from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base,get_db
from app.models import Email,Thread,GmailAccount
from app.services.thread_review import evidence_spans

@pytest.fixture
def client():
    from app.main import create_app
    e=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(e);s=sessionmaker(bind=e)()
    a=GmailAccount(email_address='sample@example.invalid');b=GmailAccount(email_address='other@example.invalid');s.add_all([a,b]);s.flush()
    t=Thread(account_id=a.id,gmail_thread_id='test-thread',subject='<script>topic</script>');s.add(t);s.flush()
    for i,text in enumerate(['Please review the proposal by 2026-11-30.\nDue tomorrow.','Thanks.\nCould you confirm by 03/04/26?\n<script>alert(1)</script>']):
        s.add(Email(account_id=a.id,thread_id=t.id,gmail_message_id='m'+str(i),is_sample=True,body_text=text,sender='test@example.invalid',internal_date=datetime(2026,11,17,9+i),category='personal',priority='normal'))
    s.commit();app=create_app();app.dependency_overrides[get_db]=lambda:s
    yield TestClient(app),s,a.id,b.id,t.id
    s.close()

def path(a,t):return f'/accounts/{a}/threads/{t}/review'

def test_evidence_exact_unicode_offsets():
    text='नमस्ते\n  Please send this by 2026-02-30.\nDue tomorrow.\n'
    spans=evidence_spans(text,'body_text')
    assert len(spans)==3
    for item in spans:assert text[item['start']:item['end']]==item['quote']
    dates=[s for s in spans if s['kind']=='deadline_phrase']
    assert dates[0]['interpretation']=='invalid_date_literal'
    assert dates[1]['interpretation']=='unresolved'
    assert all(x['status']=='unconfirmed' for x in spans)

def test_api_no_writes_and_source_links(client):
    tc,s,a,b,t=client;before=[(r.id,r.category,r.priority,r.ai_attempts) for r in s.query(Email).all()]
    d=tc.get(path(a,t)).json()
    assert d['sample_only'] and d['version']=='literal-thread-v1'
    assert len(d['messages'])==2 and len(d['candidates'])==5
    sources={m['id']:m for m in d['messages']}
    for item in d['candidates']+d['extractive_highlights']:
        assert sources[item['message_id']]['text'][item['start']:item['end']]==item['quote']
    assert before==[(r.id,r.category,r.priority,r.ai_attempts) for r in s.query(Email).all()]
    assert d['candidates'][1]['literal']=='2026-11-30'

def test_wrong_account_and_real_thread_fail_closed(client):
    tc,s,a,b,t=client
    assert tc.get(path(b,t)).status_code==404
    row=s.query(Email).first();row.is_sample=False;s.commit()
    assert tc.get(path(a,t)).status_code==404
    assert tc.get(f'/dashboard/accounts/{a}/threads/{t}').status_code==404

def test_corrupted_message_account_fails_closed(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.account_id=b;s.commit()
    assert tc.get(path(a,t)).status_code==404

def test_real_message_beyond_limit_fails_closed(client):
    tc,s,a,b,t=client
    for i in range(55):s.add(Email(account_id=a,thread_id=t,gmail_message_id='late'+str(i),is_sample=i<54,body_text='no literal request'))
    s.commit();assert tc.get(path(a,t)).status_code==404

def test_preview_bounds_and_candidate_cap(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.body_text=('Please review by 2026-11-30.\n'*500);s.commit()
    d=tc.get(path(a,t)).json()
    assert len(d['messages'][0]['text'])==8000 and d['messages'][0]['text_truncated']
    assert len(d['candidates'])==30 and d['omitted_candidates']>0

def test_html_escaped_and_caveated(client):
    tc,s,a,b,t=client;html=tc.get(f'/dashboard/accounts/{a}/threads/{t}').text
    assert '&lt;script&gt;' in html and '<script>' not in html
    assert 'UNCONFIRMED' in html and 'NO ACTIONS CREATED' in html
    assert '#message-' in html and 'not an AI summary' in html

def test_unmatched_does_not_claim_absence():
    assert evidence_spans('We might want to consider April next year.','body_text')==[]

def test_api_access_remote_denied(client):
    from app.config import get_settings
    get_settings.cache_clear()
    tc,s,a,b,t=client
    from app.main import create_app
    app=create_app();app.dependency_overrides[get_db]=lambda:s
    assert TestClient(app,client=('203.0.113.4',5000)).get(path(a,t)).status_code==403


def test_unknown_empty_and_snippet_source(client):
    tc,s,a,b,t=client
    assert tc.get(path(a,99999)).status_code==404
    empty=Thread(account_id=a,gmail_thread_id='empty');s.add(empty);s.commit()
    assert tc.get(path(a,empty.id)).status_code==404
    row=s.query(Email).first();row.body_text='';row.snippet='नमस्ते\nPlease reply by next Friday.';s.commit()
    d=tc.get(path(a,t)).json();m=d['messages'][0]
    assert m['source_field']=='snippet'
    for item in [i for i in d['candidates'] if i['message_id']==row.id]:
        assert item['field']=='snippet' and m['text'][item['start']:item['end']]==item['quote']
    assert any(i.get('literal')=='next Friday' and i['interpretation']=='unresolved' for i in d['candidates'])


def test_message_cap_and_unknown_date_order(client):
    tc,s,a,b,t=client
    for i in range(55):s.add(Email(account_id=a,thread_id=t,gmail_message_id='many'+str(i),is_sample=True,body_text='sample'))
    s.commit();d=tc.get(path(a,t)).json()
    assert d['total_messages']==57 and len(d['messages'])==50 and d['messages_truncated']
    assert d['messages'][0]['date'] is not None and d['messages'][-1]['date'] is None
