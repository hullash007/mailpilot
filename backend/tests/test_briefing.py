"""Sample briefing uses same ephemeral fixture as accepted thread review."""
from datetime import datetime
from sqlalchemy import event
from app.models import Email,Thread
from .test_thread_review import client


def url(a):return f'/accounts/{a}/sample-briefing'


def test_briefing_source_offsets_and_no_writes(client):
    tc,s,a,b,t=client;statements=[]
    event.listen(s.bind,'before_cursor_execute',lambda conn,cursor,statement,*args:statements.append(statement))
    d=tc.get(url(a)).json()
    assert d['sample_only'] and d['included_threads']==1 and d['total_sample_messages']==2
    assert all(q.lstrip().upper().startswith('SELECT') for q in statements)
    item=d['threads'][0];assert item['thread_id']==t
    assert item['stored_rules_high_priority_message_count']==0
    review=tc.get(item['review_path']).json();messages={m['id']:m for m in review['messages']}
    for evidence in item['extractive_highlights']+item['unconfirmed_candidates']:
        assert messages[evidence['message_id']]['text'][evidence['start']:evidence['end']]==evidence['quote']
    assert 'No time-window or daily-completeness claim.' in d['limitations']


def test_no_cross_account_data_and_empty(client):
    tc,s,a,b,t=client
    d=tc.get(url(b)).json()
    assert d['total_sample_messages']==0 and d['threads']==[]
    assert tc.get(url(999999)).status_code==404


def test_real_sample_mix_fails_closed_even_beyond_output(client):
    tc,s,a,b,t=client
    for i in range(21):
        th=Thread(account_id=a,gmail_thread_id=f'extra{i}');s.add(th);s.flush()
        s.add(Email(account_id=a,thread_id=th.id,gmail_message_id=f'extra{i}',is_sample=i!=20))
    s.commit();assert tc.get(url(a)).status_code==404


def test_foreign_thread_or_message_fails_closed(client):
    tc,s,a,b,t=client
    row=s.query(Email).first();row.account_id=b;s.commit()
    assert tc.get(url(a)).status_code==404
    assert tc.get(url(b)).status_code==404


def test_orphan_message_fails_closed(client):
    tc,s,a,b,t=client
    s.add(Email(account_id=a,thread_id=999999,gmail_message_id='orphan',is_sample=True));s.commit()
    assert tc.get(url(a)).status_code==404


def test_actual_message_activity_and_high_rules_counts(client):
    tc,s,a,b,t=client
    th=Thread(account_id=a,gmail_thread_id='newer',last_message_at=datetime(1900,1,1));s.add(th);s.flush()
    s.add_all([Email(account_id=a,thread_id=th.id,gmail_message_id='newer',is_sample=True,internal_date=datetime(2026,12,1),priority='high',classification_source='rules'),
               Email(account_id=a,thread_id=th.id,gmail_message_id='notrules',is_sample=True,priority='high',classification_source='user_confirmed')])
    s.commit();d=tc.get(url(a)).json();assert d['threads'][0]['thread_id']==th.id
    assert d['threads'][0]['stored_rules_high_priority_message_count']==1
    assert '2026-12-01' in d['threads'][0]['latest_stored_message_at']


def test_bounded_output_and_disclosed_omissions(client):
    tc,s,a,b,t=client
    row=s.query(Email).first();row.body_text='Please review by tomorrow.\n'*400;s.commit()
    for i in range(25):
        th=Thread(account_id=a,gmail_thread_id=f'bounded{i}');s.add(th);s.flush()
        s.add(Email(account_id=a,thread_id=th.id,gmail_message_id=f'bounded{i}',is_sample=True))
    s.commit();d=tc.get(url(a)).json()
    assert d['included_threads']==20 and d['omitted_threads']==6
    item=next(i for i in d['threads'] if i['thread_id']==t)
    assert len(item['unconfirmed_candidates'])==5 and item['candidates_omitted_from_scanned_text']>0
    assert item['source_text_truncated'] and len(item['extractive_highlights'])<=2


def test_global_access_still_required(client,monkeypatch):
    from app.config import get_settings
    tc,s,a,b,t=client
    monkeypatch.setenv('APP_ACCESS_TOKEN','briefing-test');get_settings.cache_clear()
    try:
        assert tc.get(url(a)).status_code==401
        assert tc.get(url(a),headers={'Authorization':'Bearer briefing-test'}).status_code==200
    finally:get_settings.cache_clear()
