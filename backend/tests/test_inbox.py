from .test_thread_review import client
from app.models import Email,Thread
from app.config import get_settings
from sqlalchemy import event


def test_account_scope_and_detail_no_sideeffects(client):
    tc,s,a,b,t=client;queries=[]
    event.listen(s.bind,'before_cursor_execute',lambda conn,cursor,statement,*args:queries.append(statement))
    d=tc.get(f'/accounts/{a}/inbox').json();assert len(d['messages'])==2
    assert tc.get(f'/accounts/{b}/inbox').json()['messages']==[]
    mid=d['messages'][0]['id']
    assert tc.get(f'/accounts/{b}/messages/{mid}').status_code==404
    assert tc.get(f'/inbox/{b}/messages/{mid}').status_code==404
    assert tc.get(f'/accounts/{a}/messages/{mid}').status_code==200
    assert all(q.lstrip().upper().startswith('SELECT') for q in queries)


def test_literal_wildcards_and_injection(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.subject='100%_ complete!';s.commit()
    for q,count in [('%_',1),('!',1),("' OR 1=1 --",0),('missing',0)]:
        r=tc.get(f'/accounts/{a}/inbox',params={'q':q});assert len(r.json()['messages'])==count
    assert len(tc.get(f'/accounts/{a}/inbox',params={'q':'COMPLETE'}).json()['messages'])==1


def test_filter_cursor_validation(client):
    tc,s,a,b,t=client
    for params in [{'limit':0},{'limit':51},{'before':-1},{'before':0},{'category':'bad'},{'priority':'bad'},{'q':'x'*201}]:
        assert tc.get(f'/accounts/{a}/inbox',params=params).status_code==422
    assert tc.get('/accounts/999999/inbox').status_code==404
    assert tc.get(f'/accounts/{a}/inbox',params={'priority':'high'}).json()['messages']==[]


def test_stable_descending_cursor(client):
    tc,s,a,b,t=client
    for i in range(9):s.add(Email(account_id=a,thread_id=t,gmail_message_id='page'+str(i),is_sample=True,category='notification',priority='high'))
    s.commit();ids=[];cursor=None
    for _ in range(4):
        params={'limit':3,'priority':'high','category':'notification'}
        if cursor:params['before']=cursor
        d=tc.get(f'/accounts/{a}/inbox',params=params).json();ids.extend(m['id'] for m in d['messages']);cursor=d['next_before']
        if cursor is None:break
    assert len(ids)==9 and ids==sorted(set(ids),reverse=True)


def test_pages_escape_and_label_real_vs_sample(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.subject='<img src=x onerror=bad()>';row.body_text='<script>bad()</script>';row.is_sample=False;s.commit()
    for path in [f'/inbox/{a}',f'/inbox/{a}/messages/{row.id}']:
        text=tc.get(path).text
        assert '<script>' not in text and '<img src=x' not in text
        assert 'STORED MAIL' in text and '&lt;' in text
    text=tc.get(f'/inbox/{a}',params={'q':'" onfocus="bad'}).text
    assert '&quot; onfocus=&quot;bad' in text
    assert 'value="" onfocus=' not in text


def test_body_and_snippet_bounded(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.body_text='x'*33000;row.snippet='y'*2000;s.commit()
    d=tc.get(f'/accounts/{a}/messages/{row.id}').json();assert len(d['text'])==32000 and d['text_truncated']
    d=tc.get(f'/accounts/{a}/inbox').json();r=next(m for m in d['messages'] if m['id']==row.id)
    assert len(r['snippet'])==1000 and r['snippet_truncated']


def test_workspace_access_required(client,monkeypatch):
    tc,s,a,b,t=client;monkeypatch.setenv('APP_ACCESS_TOKEN','inbox-access');get_settings.cache_clear()
    try:
        for path in ['/accounts','/inbox',f'/inbox/{a}',f'/accounts/{a}/inbox']:
            assert tc.get(path).status_code==401
            assert tc.get(path,headers={'Authorization':'Bearer inbox-access'}).status_code==200
    finally:get_settings.cache_clear()


def test_recipient_truncation_disclosed_and_real_badge(client):
    tc,s,a,b,t=client;row=s.query(Email).first();row.recipients='r'*2001;row.is_sample=False;s.commit()
    d=tc.get(f'/accounts/{a}/messages/{row.id}').json()
    assert d['recipients_truncated'] and len(d['recipients'])==2000
    text=tc.get(f'/inbox/{a}/messages/{row.id}').text
    assert 'recipient preview truncated at 2000 characters' in text
    assert '<span class="label stored-source">STORED MAIL - UNTRUSTED TEXT</span>' in text
    assert '<span class="label sample">STORED MAIL' not in text
    assert 'Case matching follows database collation' in tc.get(f'/inbox/{a}').text
    row.recipients='short@example.invalid';s.commit()
    assert not tc.get(f'/accounts/{a}/messages/{row.id}').json()['recipients_truncated']
    assert 'recipient preview truncated' not in tc.get(f'/inbox/{a}/messages/{row.id}').text
