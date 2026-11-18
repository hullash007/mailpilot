"""Candidate-only synthetic sync, not live Gmail qualification."""
from datetime import datetime
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app.models import Email, Thread, GmailAccount
from app.sync_models import MailboxSync, SyncRun, MessageRemoteState, ThreadSnapshot
from app.services import mailbox_sync as sync


def message(mid='m1', tid='t1', labels=None, body='hello'):
    return dict(gmail_message_id=mid, gmail_thread_id=tid, subject='Example', sender='a@example.test',
                recipients='b@example.test', snippet=body, body_text=body, headers={},
                label_ids=labels if labels is not None else ['INBOX'], internal_date=datetime(2026, 11, 17, 10))


class ProviderError(Exception):
    def __init__(self, code):
        self.resp = SimpleNamespace(status=code)


class Client:
    def __init__(self):
        self.calls = []
        self.pages = {None: dict(messages=[dict(id='m1', threadId='t1')])}
        self.histories = {None: dict(history=[], historyId='120')}
        self.threads = {'t1': [message()]}
        self.thread_history = '200'
    def get_profile(self):
        return {'historyId': '100'}
    def list_message_page(self, page_token=None, page_size=25):
        self.calls.append(('messages', page_token))
        value = self.pages[page_token]
        if isinstance(value, Exception):
            raise value
        return value
    def list_history_page(self, start_history_id, page_token=None):
        self.calls.append(('history', start_history_id, page_token))
        value = self.histories[page_token]
        if isinstance(value, Exception):
            raise value
        return value
    def get_thread_snapshot(self, tid):
        value = self.threads[tid]
        if isinstance(value, Exception):
            raise value
        return dict(messages=value, history_id=self.thread_history)


@pytest.fixture
def db():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(GmailAccount(email_address='owner@example.test'))
    session.commit()
    sync.start_sync(session, 1)
    yield session
    session.close()


def state(db):
    db.expire_all()
    return db.get(MailboxSync, 1)


def ready(db, client):
    assert sync.sync_tick(db, 1, client)['phase'] == 'catchup'
    assert sync.sync_tick(db, 1, client)['phase'] == 'ready'


def test_initial_resumes_and_catches_up_before_current(db):
    c = Client()
    c.pages[None]['nextPageToken'] = 'next'
    c.pages['next'] = dict(messages=[dict(id='m2', threadId='t2')])
    c.threads['t2'] = [message('m2', 't2')]
    sync.sync_tick(db, 1, c)
    assert state(db).last_success is None
    assert state(db).page_token == 'next'
    db.close()  # restart session, not a cached Python state
    sync.sync_tick(db, 1, c)
    assert state(db).phase == 'catchup'
    sync.sync_tick(db, 1, c)
    assert state(db).history_id == '120'
    assert state(db).last_success
    assert db.query(Email).count() == 2
    assert ('history', '100', None) in c.calls


def test_replay_updates_labels_body_and_preserves_user_decision(db):
    c = Client(); ready(db, c)
    row = db.scalar(select(Email))
    row.category = 'personal'; row.priority = 'high'; row.classification_source = 'user_confirmed'
    db.commit()
    c.histories[None] = dict(history=[dict(labelsRemoved=[dict(message=dict(id='m1', threadId='t1'))])], historyId='130')
    c.thread_history='210'
    c.threads['t1'] = [message(labels=['SENT'], body='edited')]
    result = sync.sync_tick(db, 1, c)
    assert result['updated'] == 1
    row = db.scalar(select(Email))
    assert row.label_ids == ['SENT'] and row.body_text == 'edited'
    assert (row.category, row.priority) == ('personal', 'high')
    assert row.ai_status == 'none'
    sync.sync_tick(db, 1, c)
    assert db.query(Email).count() == 1


def test_history_continuation_keeps_anchor_until_final_page(db):
    c = Client(); ready(db, c)
    c.histories = {None: dict(history=[], historyId='200', nextPageToken='p2'),
                   'p2': dict(history=[], historyId='210')}
    sync.sync_tick(db, 1, c)
    assert state(db).history_id == '120'
    assert state(db).page_token == 'p2'
    sync.sync_tick(db, 1, c)
    assert ('history', '120', 'p2') in c.calls
    assert state(db).history_id == '210'


def test_thread_404_is_not_expired_history(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(messagesAdded=[dict(message=dict(id='m2', threadId='t2'))])], historyId='130')
    c.threads['t2'] = ProviderError(404)
    assert sync.sync_tick(db, 1, c)['outcome'] == 'page_committed'
    assert state(db).phase == 'ready' and state(db).history_id == '130'
    assert not state(db).error_code


def test_expired_history_reconciles_without_content_loss(db):
    c = Client(); ready(db, c)
    c.histories[None] = ProviderError(404)
    assert sync.sync_tick(db, 1, c)['outcome'] == 'reconcile_required'
    assert state(db).phase == 'initial' and state(db).history_id == '120'
    assert db.query(Email).count() == 1


def test_remote_deletion_does_not_silently_purge(db):
    c = Client(); ready(db, c)
    c.threads['t1'] = ProviderError(404)
    c.histories[None] = dict(history=[dict(messagesDeleted=[dict(message=dict(id='m1'))])], historyId='130')
    sync.sync_tick(db, 1, c)
    assert db.query(Email).count() == 1
    assert db.scalar(select(MessageRemoteState)).deleted_at


@pytest.mark.parametrize('code', [401, 403, 429, 500, 503])
def test_failures_preserve_page_and_cursor_without_sensitive_errors(db, code):
    c = Client(); ready(db, c)
    c.histories[None] = ProviderError(code)
    result = sync.sync_tick(db, 1, c)
    transient = code in (429, 500, 503)
    assert result['outcome'] == ('retry_scheduled' if transient else 'failed')
    assert state(db).history_id == '120' and state(db).phase == 'ready'
    assert state(db).error_code == ('retry_scheduled' if transient else f'provider_{code}')


def test_atomic_rollback_if_persistence_fails(db, monkeypatch):
    c = Client()
    original = sync._persist_threads
    def fail(*args):
        original(*args)
        raise RuntimeError('injected crash after message write')
    monkeypatch.setattr(sync, '_persist_threads', fail)
    with pytest.raises(RuntimeError):
        sync.sync_tick(db, 1, c)
    assert db.query(Email).count() == 0
    assert state(db).revision == 0 and state(db).anchor is None
    monkeypatch.setattr(sync, '_persist_threads', original)
    assert sync.sync_tick(db, 1, c)['inserted'] == 1


def test_stale_worker_cannot_commit(db):
    c = Client()
    original = c.get_thread_snapshot
    def concurrent(tid):
        # Another completed worker changes the revision during the network gap.
        db.query(MailboxSync).update({'revision': 1}); db.commit()
        return original(tid)
    c.get_thread_snapshot = concurrent
    with pytest.raises(sync.SyncConflict):
        sync.sync_tick(db, 1, c)
    assert db.query(Email).count() == 0
    assert state(db).revision == 1


def test_account_scoped_identity(db):
    c = Client(); ready(db, c)
    db.add(GmailAccount(email_address='other@example.test')); db.commit()
    sync.start_sync(db, 2)
    sync.sync_tick(db, 2, c)
    assert db.query(Email).count() == 2
    assert db.query(Thread).count() == 2
    assert db.query(ThreadSnapshot).count() == 2


@pytest.mark.parametrize('mutation', ['wrong_thread', 'duplicate', 'repeated_token', 'invalid_cursor'])
def test_invalid_provider_data_never_advances(db, mutation):
    c = Client()
    if mutation == 'wrong_thread': c.threads['t1'][0]['gmail_thread_id'] = 'wrong'
    if mutation == 'duplicate': c.threads['t1'] *= 2
    if mutation == 'repeated_token':
        c.pages[None]['nextPageToken'] = 'p'
        sync.sync_tick(db, 1, c)
        c.pages['p'] = dict(messages=[], nextPageToken='p')
    if mutation == 'invalid_cursor': c.get_profile = lambda: {'historyId': 'bad'}
    before = state(db).revision
    assert sync.sync_tick(db, 1, c)['outcome'] == 'failed'
    assert state(db).revision == before + 1  # failure receipt, not data/cursor advance
    assert state(db).history_id is None


def test_full_thread_captured_not_only_listed_message(db):
    c = Client(); c.threads['t1'] = [message('m0'), message('m1')]
    assert sync.sync_tick(db, 1, c)['inserted'] == 2
    assert db.scalar(select(ThreadSnapshot)).message_count == 2


def test_explicit_enrollment_and_dedicated_session(db):
    c = Client()
    with pytest.raises(ValueError): sync.sync_tick(db, 99, c)
    db.add(GmailAccount(email_address='pending@example.test'))
    with pytest.raises(ValueError): sync.sync_tick(db, 1, c)
    assert not c.calls


def test_backward_cursor_refused(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[], historyId='119')
    assert sync.sync_tick(db, 1, c)['outcome'] == 'failed'
    assert state(db).history_id == '120'


def test_candidate_schema_additive_idempotent_and_snapshot_restore(tmp_path):
    import sqlite3
    from sqlalchemy import inspect, text
    from app.database import ensure_schema
    path = tmp_path / 'old.sqlite'
    engine = create_engine('sqlite:///' + str(path))
    # Actual prior-model schema simulated independently of the candidate tables.
    original_tables = [GmailAccount.__table__, Thread.__table__, Email.__table__]
    Base.metadata.create_all(engine, tables=original_tables)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO gmail_accounts (id,email_address,credentials_blob,created_at) VALUES (1,'old@example.test','opaque-encrypted-placeholder',CURRENT_TIMESTAMP)"))
    with sqlite3.connect(path) as conn:
        before = conn.execute('SELECT * FROM gmail_accounts').fetchall()
        with sqlite3.connect(tmp_path / 'restore.sqlite') as backup:
            conn.backup(backup)
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    Base.metadata.create_all(engine)
    ensure_schema(engine)
    assert 'mailbox_sync' in inspect(engine).get_table_names()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert conn.execute('SELECT * FROM gmail_accounts').fetchall() == before
    with sqlite3.connect(tmp_path / 'restore.sqlite') as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert conn.execute('SELECT * FROM gmail_accounts').fetchall() == before
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='mailbox_sync'").fetchall()


def test_message_arriving_during_scan_is_caught_by_start_anchor(db):
    c = Client()
    c.pages[None]['nextPageToken'] = 'older'
    c.pages['older'] = dict(messages=[])
    sync.sync_tick(db, 1, c)
    # New mail after first enumeration page, never included in the list scan.
    c.threads['t2'] = [message('new', 't2')]
    c.histories[None] = dict(history=[dict(messagesAdded=[dict(message=dict(id='new', threadId='t2'))])], historyId='150')
    sync.sync_tick(db, 1, c)
    assert not state(db).last_success
    sync.sync_tick(db, 1, c)
    assert ('history', '100', None) in c.calls
    assert set(db.scalars(select(Email.gmail_message_id))) == {'m1', 'new'}
    assert state(db).history_id == '150'


def test_incremental_crash_replays_without_skipping(db, monkeypatch):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(messagesAdded=[dict(message=dict(id='new', threadId='t2'))])], historyId='150')
    c.threads['t2'] = [message('new', 't2')]
    original = sync._persist_threads
    def fail(*args):
        original(*args)
        raise RuntimeError('injected')
    monkeypatch.setattr(sync, '_persist_threads', fail)
    with pytest.raises(RuntimeError): sync.sync_tick(db, 1, c)
    assert state(db).history_id == '120' and db.query(Email).count() == 1
    monkeypatch.setattr(sync, '_persist_threads', original)
    sync.sync_tick(db, 1, c)
    assert state(db).history_id == '150' and db.query(Email).count() == 2


def test_excluded_thread_messages_never_import_content(db):
    c = Client()
    c.threads['t1'] = [message(), message('spam', labels=['SPAM']), message('trash', labels=['TRASH'])]
    sync.sync_tick(db, 1, c)
    assert list(db.scalars(select(Email.gmail_message_id))) == ['m1']
    snap = db.scalar(select(ThreadSnapshot))
    assert snap.message_count == 1 and snap.excluded_count == 2
    assert db.query(MessageRemoteState).filter_by(excluded=True).count() == 2


def test_existing_message_moved_to_trash_marked_not_purged(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(labelsAdded=[dict(message=dict(id='m1', threadId='t1'))])], historyId='130')
    c.thread_history='210'
    c.threads['t1'] = [message(labels=['TRASH'], body='must not overwrite retained content')]
    sync.sync_tick(db, 1, c)
    assert db.scalar(select(Email)).body_text == 'hello'
    assert db.scalar(select(MessageRemoteState)).excluded
    assert db.scalar(select(ThreadSnapshot)).message_count == 0


def test_deletion_only_refresh_updates_metadata_not_retention(db):
    c = Client()
    older = message('old'); older['internal_date'] = datetime(2026, 11, 16)
    newer = message('m1'); newer['subject'] = 'Newer subject'
    c.threads['t1'] = [older, newer]
    ready(db, c)
    assert db.scalar(select(Thread)).subject == 'Newer subject'
    c.histories[None] = dict(history=[dict(messagesDeleted=[dict(message=dict(id='m1', threadId='t1'))])], historyId='230')
    c.threads['t1'] = [older]; c.thread_history = '230'
    sync.sync_tick(db, 1, c)
    assert db.scalar(select(Thread)).subject == 'Example'
    assert db.scalar(select(ThreadSnapshot)).message_count == 1
    assert db.query(Email).count() == 2  # retained content is not erased
    assert db.scalar(select(MessageRemoteState).where(MessageRemoteState.gmail_message_id == 'm1')).deleted_at


def test_last_message_deletion_404_marks_unavailable_not_empty_complete(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(messagesDeleted=[dict(message=dict(id='m1'))])], historyId='240')
    c.threads['t1'] = ProviderError(404)
    sync.sync_tick(db, 1, c)
    snapshot = db.scalar(select(ThreadSnapshot))
    assert snapshot.availability == 'unavailable'
    assert snapshot.provider_history_id == '200'  #404 is not a versioned response
    assert db.query(Email).count() == 1
    assert db.scalar(select(MessageRemoteState)).deleted_at
    assert state(db).phase == 'ready'  #history cursor valid; per-thread status separate


def test_older_provider_snapshot_cannot_overwrite_newer_version(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(labelsAdded=[dict(message=dict(id='m1', threadId='t1'))])], historyId='260')
    c.thread_history = '260'; c.threads['t1'] = [message(body='new snapshot')]
    sync.sync_tick(db, 1, c)
    before = state(db).revision
    c.thread_history = '250'; c.threads['t1'] = [message(body='slow stale response')]
    with pytest.raises(sync.SyncConflict): sync.sync_tick(db, 1, c)
    assert db.scalar(select(Email)).body_text == 'new snapshot'
    assert db.scalar(select(ThreadSnapshot)).provider_history_id == '260'
    assert state(db).revision == before


def test_provider_present_supersedes_older_delete_history(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[dict(messagesDeleted=[dict(message=dict(id='m1', threadId='t1'))])], historyId='220')
    c.thread_history = '230'  #current fullthread explicitly still includes the message
    sync.sync_tick(db, 1, c)
    assert db.scalar(select(MessageRemoteState)).deleted_at is None


def token_error(location='pageToken', code=400):
    import json
    error = ProviderError(code)
    error.content = json.dumps({'error': {'code': code, 'errors': [dict(reason='badRequest', location=location, locationType='parameter')]}}).encode()
    return error


def test_initial_token_restart_durable_anchor_and_duplicates(db):
    c = Client(); c.pages[None]['nextPageToken'] = 'p2'
    c.pages['p2'] = token_error()
    sync.sync_tick(db, 1, c)
    assert sync.sync_tick(db, 1, c)['outcome'] == 'restarting_page_sequence'
    db.close()
    assert state(db).anchor == '100' and state(db).token_restarts == 1 and state(db).page_token is None
    # Profile changed while we restart; original100 must survive.
    c.get_profile = lambda: {'historyId': '999'}
    c.pages[None].pop('nextPageToken')
    c.threads['t2'] = [message('new', 't2')]
    c.histories[None] = dict(history=[dict(messagesAdded=[dict(message=dict(id='new', threadId='t2'))])], historyId='250')
    sync.sync_tick(db, 1, c); sync.sync_tick(db, 1, c)
    assert ('history', '100', None) in c.calls
    assert db.query(Email).count() == 2
    assert state(db).phase == 'ready' and state(db).history_id == '250'


def test_history_page_token_restarts_original_cursor(db):
    c = Client(); ready(db, c)
    c.histories[None] = dict(history=[], historyId='300', nextPageToken='p')
    c.histories['p'] = token_error()
    sync.sync_tick(db, 1, c)
    assert sync.sync_tick(db, 1, c)['outcome'] == 'restarting_page_sequence'
    assert state(db).history_id == '120' and state(db).token_restarts == 1
    c.histories[None] = dict(history=[], historyId='310')
    sync.sync_tick(db, 1, c)
    assert c.calls[-1] == ('history', '120', None)
    assert state(db).history_id == '310'


@pytest.mark.parametrize('error', [token_error('other'), ProviderError(400), token_error(code=401),
                                   token_error(code=403), token_error(code=429), token_error(code=503)])
def test_non_token_errors_preserve_sequence(db, error):
    c = Client(); c.pages[None]['nextPageToken'] = 'p'
    c.pages['p'] = error
    sync.sync_tick(db, 1, c)
    assert sync.sync_tick(db, 1, c)['outcome'] == ('retry_scheduled' if error.resp.status in (429,503) else 'failed')
    assert state(db).page_token == 'p' and state(db).anchor == '100'
    assert state(db).token_restarts == 0


def test_token_restart_limit_survives_process_and_never_ready(db):
    c = Client(); c.pages[None]['nextPageToken'] = 'p'
    c.pages['p'] = token_error()
    for attempt in range(3):
        sync.sync_tick(db, 1, c)
        outcome = sync.sync_tick(db, 1, c)['outcome']
        db.close()
    assert outcome == 'blocked' and state(db).blocked
    assert state(db).token_restarts == 2 and state(db).history_id is None
    assert state(db).last_success is None
    calls = len(c.calls)
    assert sync.sync_tick(db, 1, c)['outcome'] == 'blocked'
    assert len(c.calls) == calls


def test_request_budget_pending_page_progress_survives_restart(db):
    c = Client()
    c.pages[None] = dict(messages=[dict(id=f'm{i}', threadId=f't{i}') for i in range(5)])
    c.threads={f't{i}':[message(f'm{i}',f't{i}')] for i in range(5)}
    result=sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    assert result['outcome']=='budget_exhausted' and result['requests']==2
    assert state(db).pending_page and state(db).anchor=='100'
    assert db.query(Email).count()==0 and state(db).history_id is None
    db.close()
    for count in (2,4):
        result=sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
        assert result['outcome']=='budget_exhausted'
        assert db.query(Email).count()==count
    result=sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    assert result['phase']=='catchup' and db.query(Email).count()==5
    assert c.calls.count(('messages',None))==1  #not replaying doomed wholepage
    sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    assert state(db).phase=='ready'


def test_time_budget_stops_new_requests_and_resumes(db):
    now=[0.0]; calls=[]
    c=Client();c.pages[None]=dict(messages=[dict(id=f'm{i}',threadId=f't{i}') for i in range(3)])
    c.threads={f't{i}':[message(f'm{i}',f't{i}')] for i in range(3)}
    original=c.get_thread_snapshot
    def slow(tid):
        calls.append((tid,now[0]));result=original(tid);now[0]+=4;return result
    c.get_thread_snapshot=slow
    c.set_request_timeout=lambda seconds: None
    result=sync.sync_tick(db,1,c,budget=sync.TickBudget(seconds=5,clock=lambda:now[0]))
    assert result['outcome']=='budget_exhausted'
    assert [t for _,t in calls]==[0,4]  #none starts at8, afterdeadline
    assert db.query(Email).count()==2 and state(db).history_id is None
    sync.sync_tick(db,1,c,budget=sync.TickBudget(seconds=5,clock=lambda:now[0]))
    assert db.query(Email).count()==3 and state(db).phase=='catchup'


def test_budget_boundary_commit_crash_rolls_back_pending_and_data(db,monkeypatch):
    c=Client()
    sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    before=state(db).revision
    original=sync._persist_threads
    def fail(*args):original(*args);raise RuntimeError('crash at stagedcommit')
    monkeypatch.setattr(sync,'_persist_threads',fail)
    with pytest.raises(RuntimeError):sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    assert state(db).revision==before and state(db).pending_page['tids']==['t1']
    assert db.query(Email).count()==0
    monkeypatch.setattr(sync,'_persist_threads',original)
    sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    assert db.query(Email).count()==1


def test_dynamic_request_timeout_capped_by_remaining_and_twenty(db):
    now=[0.0];timeouts=[];c=Client();c.set_request_timeout=timeouts.append
    original=c.get_profile
    def profile():result=original();now[0]=58;return result
    c.get_profile=profile
    sync.sync_tick(db,1,c,budget=sync.TickBudget(clock=lambda:now[0]))
    assert timeouts[0]==20 and all(t==2 for t in timeouts[1:])


class RetryError(ProviderError):
    def __init__(self,code,header=None):
        self.resp=type('Response',(dict,),{})(status=code)
        self.resp.status=code
        if header is not None:self.resp['retry-after']=header


@pytest.mark.parametrize('code',[429,500,502,503,504])
def test_transient_retry_durable_no_early_network(db,code):
    from datetime import timedelta
    c=Client();ready(db,c);now=datetime(2026,11,17,20)
    c.histories[None]=RetryError(code)
    policy=sync.RetryPolicy(random_source=lambda:0)
    assert sync.sync_tick(db,1,c,retry_policy=policy,now=now)['outcome']=='retry_scheduled'
    assert state(db).retry_at==now+timedelta(seconds=15)
    calls=len(c.calls);db.close()
    assert sync.sync_tick(db,1,c,retry_policy=policy,now=now+timedelta(seconds=14))['outcome']=='retry_wait'
    assert len(c.calls)==calls and state(db).history_id=='120'
    c.histories[None]=dict(history=[],historyId='200')
    sync.sync_tick(db,1,c,retry_policy=policy,now=now+timedelta(seconds=15))
    assert state(db).history_id=='200' and state(db).retry_attempts==0 and state(db).retry_at is None


@pytest.mark.parametrize('header,expected',[('120',120),('Tue, 17 Nov 2026 20:03:00 GMT',180),('bad',30)])
def test_retry_after_header_or_date(db,header,expected):
    from datetime import timedelta
    c=Client();c.pages[None]=RetryError(429,header);now=datetime(2026,11,17,20)
    sync.sync_tick(db,1,c,retry_policy=sync.RetryPolicy(random_source=lambda:1),now=now)
    assert state(db).retry_at==now+timedelta(seconds=expected)


def test_retry_limit_and_overcap_retry_after_are_blocked(db):
    from datetime import timedelta
    c=Client();c.pages[None]=RetryError(503);now=datetime(2026,11,17,20)
    policy=sync.RetryPolicy(max_attempts=2,base_seconds=10,max_seconds=10,random_source=lambda:1)
    sync.sync_tick(db,1,c,retry_policy=policy,now=now)
    assert sync.sync_tick(db,1,c,retry_policy=policy,now=now+timedelta(seconds=10))['outcome']=='retry_exhausted'
    calls=len(c.calls)
    assert sync.sync_tick(db,1,c,now=now+timedelta(days=1))['outcome']=='retry_exhausted'
    assert len(c.calls)==calls and state(db).history_id is None
    #A fresh account with provider wait longer than maximum never retries early.
    db.add(GmailAccount(email_address='second@example.test'));db.commit();sync.start_sync(db,2)
    c.pages[None]=RetryError(429,'7200')
    assert sync.sync_tick(db,2,c,now=now)['outcome']=='retry_exhausted'


def test_pending_thread_retry_preserves_work_and_budget(db):
    from datetime import timedelta
    c=Client();sync.sync_tick(db,1,c,budget=sync.TickBudget(max_requests=2))
    c.threads['t1']=RetryError(503);now=datetime(2026,11,17,20)
    sync.sync_tick(db,1,c,now=now,retry_policy=sync.RetryPolicy(random_source=lambda:0))
    assert state(db).pending_page['tids']==['t1'] and state(db).history_id is None
    c.threads['t1']=[message()]
    clock=[0.0];budget=sync.TickBudget(seconds=1,clock=lambda:clock[0]);clock[0]=2
    assert sync.sync_tick(db,1,c,now=now+timedelta(seconds=15),budget=budget)['outcome']=='budget_exhausted'
    assert state(db).pending_page['tids']==['t1']
    sync.sync_tick(db,1,c,now=now+timedelta(seconds=15))
    assert db.query(Email).count()==1 and state(db).phase=='catchup'


@pytest.mark.parametrize('change',['body','membership'])
def test_same_version_conflict_preserves_committed_snapshot_and_restart(db,change):
    c=Client();ready(db,c)
    c.histories[None]=dict(history=[dict(labelsAdded=[dict(message=dict(id='m1',threadId='t1'))])],historyId='250')
    c.threads['t1']=[message(body='contradiction')] if change=='body' else [message(),message('extra')]
    assert sync.sync_tick(db,1,c)['outcome']=='same_version_conflict'
    assert state(db).history_id=='120' and db.scalar(select(Email)).body_text=='hello'
    assert db.query(Email).count()==1
    db.close()
    assert state(db).error_code=='same_version_conflict'
    #Later controlled tick with genuinely newer provider version can resolve.
    c.thread_history='260'
    assert sync.sync_tick(db,1,c)['outcome']=='page_committed'
    assert state(db).history_id=='250' and not state(db).error_code


def test_same_version_normalization_order_is_idempotent(db):
    c=Client();c.threads['t1']=[message('m1',labels=['INBOX','UNREAD']),message('m2')];ready(db,c)
    c.histories[None]=dict(history=[dict(labelsAdded=[dict(message=dict(id='m1',threadId='t1'))])],historyId='250')
    c.threads['t1']=[message('m2'),message('m1',labels=['UNREAD','INBOX'])]
    assert sync.sync_tick(db,1,c)['outcome']=='page_committed'
    assert db.query(Email).count()==2


def test_conflict_pending_page_does_not_advance_or_clear_pending(db):
    c=Client();ready(db,c)
    c.histories[None]=dict(history=[dict(labelsAdded=[dict(message=dict(id='m1',threadId='t1'))])],historyId='250')
    #Spendbudgetonhistory itself, queue thread, then conflictingresponse later.
    now=[0.0];original=c.list_history_page
    def slow(**kwargs):result=original(**kwargs);now[0]=2;return result
    c.list_history_page=slow
    sync.sync_tick(db,1,c,budget=sync.TickBudget(seconds=1,clock=lambda:now[0]))
    assert state(db).pending_page['tids']==['t1']
    c.threads['t1']=[message(body='conflict')]
    assert sync.sync_tick(db,1,c)['outcome']=='same_version_conflict'
    assert state(db).pending_page['tids']==['t1'] and state(db).history_id=='120'


def test_generation_absence_only_after_completed_scan_and_history(db):
    c=Client();ready(db,c)
    assert state(db).completed_generation==1
    c.histories[None]=ProviderError(404);sync.sync_tick(db,1,c)
    assert state(db).generation==2 and state(db).completed_generation==1
    snapshot=db.scalar(select(ThreadSnapshot));assert snapshot.observed_generation==1
    c.pages[None]=dict(messages=[],nextPageToken='p2');c.pages['p2']=dict(messages=[])
    sync.sync_tick(db,1,c);db.close()
    assert state(db).generation==2 and state(db).completed_generation==1
    sync.sync_tick(db,1,c)
    c.histories[None]=ProviderError(503);sync.sync_tick(db,1,c)
    assert state(db).completed_generation==1  #failedreplay neverfinalizes
    c.histories[None]=dict(history=[],historyId='300')
    from datetime import timedelta
    when=state(db).retry_at+timedelta(seconds=1)
    sync.sync_tick(db,1,c,now=when)
    assert state(db).completed_generation==2
    assert db.scalar(select(ThreadSnapshot)).observed_generation==1
    assert db.query(Email).count()==1 and db.scalar(select(MessageRemoteState)).deleted_at is None


def test_generation_observation_and_replay_changes_atomic(db):
    c=Client();ready(db,c)
    c.histories[None]=ProviderError(404);sync.sync_tick(db,1,c)
    sync.sync_tick(db,1,c)
    assert db.scalar(select(ThreadSnapshot)).observed_generation==2
    assert state(db).completed_generation==1
    c.thread_history='300';c.threads['t1']=[message(body='changed during replay')]
    c.histories[None]=dict(history=[dict(labelsAdded=[dict(message=dict(id='m1',threadId='t1'))])],historyId='310')
    sync.sync_tick(db,1,c)
    assert state(db).completed_generation==2
    assert db.scalar(select(Email)).body_text=='changed during replay'


def test_generation_conflict_does_not_finalize(db):
    c=Client();ready(db,c)
    c.histories[None]=ProviderError(404);sync.sync_tick(db,1,c)
    c.threads['t1']=[message(body='sameversionconflict')]
    assert sync.sync_tick(db,1,c)['outcome']=='same_version_conflict'
    assert state(db).completed_generation==1
    c.thread_history='300'
    sync.sync_tick(db,1,c)
    c.histories[None]=dict(history=[],historyId='310');sync.sync_tick(db,1,c)
    assert state(db).completed_generation==2
