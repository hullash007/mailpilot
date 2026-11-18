"""Real disposable file and two-connection synthetic tests. Never read a host DB."""
import sqlite3, threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine,event
from sqlalchemy.orm import Session
from app.database import Base
from app.google_main import GooglePrincipal
from app.models import GmailAccount,Thread,Email
from app.action_models import ActionDecision
from app import action_migration_proof as m
from app.services import action_review as s

@pytest.fixture
def sample(tmp_path):
    path=str(tmp_path/'synthetic.db')
    engine=create_engine('sqlite:///'+path,connect_args={'timeout':0.08,'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for i in (1,2):
            db.add(GmailAccount(id=i,email_address=f'sample{i}@example.test',google_user_id=f'sub{i}',credentials_blob='SYNTHETIC-CIPHERTEXT'))
            db.add(GooglePrincipal(subject=f'sub{i}',account_id=i))
            db.add(Thread(id=i,account_id=i,gmail_thread_id=f't{i}'))
            db.add(Email(id=i,thread_id=i,account_id=i,gmail_message_id=f'm{i}',body_text='Please review the synthetic draft.',headers={},is_sample=True))
        db.commit()
    c=sqlite3.connect(path,timeout=0.08)
    assert c.execute('PRAGMA foreign_keys').fetchone()[0]==0
    yield path,engine,c
    c.close();engine.dispose()


def snapshot(c):
    return {t:c.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in ('emails','gmail_accounts','google_principals','threads','oauth_states')}

def insert(c,account=1,source=1,fp='a'*64,status='confirmed',revision=1):
    c.execute('INSERT INTO action_decisions_candidate(account_id,source_email_id,detector_version,evidence_fingerprint,status,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',(account,source,'explicit-v1',fp,status,revision,'2026-11-17','2026-11-17'))
    c.commit()


def test_additive_empty_rollback_originals_and_collision(sample):
    _,_,c=sample;before=snapshot(c)
    m.install_disposable(c)
    assert snapshot(c)==before
    with pytest.raises(ValueError):m.install_disposable(c)
    assert not c.in_transaction
    m.rollback_empty_disposable(c)
    assert snapshot(c)==before
    assert not c.execute("SELECT name FROM sqlite_master WHERE name GLOB 'action_*'").fetchall()


def test_explicit_migration_transaction_failure_undoes_table(sample):
    _,_,c=sample;before=snapshot(c)
    def authorize(action,arg1,arg2,db,source):
        return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_CREATE_TRIGGER else sqlite3.SQLITE_OK
    c.set_authorizer(authorize)
    with pytest.raises(sqlite3.DatabaseError):m.install_disposable(c)
    c.set_authorizer(None)
    assert not c.in_transaction and snapshot(c)==before
    assert not c.execute("SELECT name FROM sqlite_master WHERE name GLOB 'action_*'").fetchall()

@pytest.mark.parametrize('sql',[
 'DELETE FROM emails WHERE id=1', 'UPDATE emails SET id=8 WHERE id=1',
 'UPDATE emails SET account_id=2 WHERE id=1', 'DELETE FROM gmail_accounts WHERE id=1',
 'UPDATE gmail_accounts SET id=8 WHERE id=1', 'UPDATE gmail_accounts SET google_user_id=\'new\' WHERE id=1',
 'DELETE FROM google_principals WHERE account_id=1',
 'UPDATE google_principals SET subject=\'new\' WHERE account_id=1',
 'UPDATE google_principals SET account_id=8 WHERE account_id=1',
 'UPDATE action_decisions_candidate SET source_email_id=2',
 'UPDATE action_decisions_candidate SET evidence_fingerprint=\'b\''])
def test_fkoff_guards_reject_source_or_binding_reuse(sample,sql):
    _,_,c=sample;m.install_disposable(c);insert(c);before=snapshot(c)
    with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    c.rollback();assert snapshot(c)==before
    assert c.execute('SELECT count(*) FROM action_decisions_candidate').fetchone()[0]==1

@pytest.mark.parametrize('account,source,status,revision',[(1,2,'confirmed',1),(3,1,'confirmed',1),(1,88,'confirmed',1),(1,1,'sent',1),(1,1,'confirmed',0)])
def test_direct_insert_guards(sample,account,source,status,revision):
    _,_,c=sample;m.install_disposable(c)
    with pytest.raises(sqlite3.IntegrityError):insert(c,account,source,status=status,revision=revision)
    c.rollback()


def test_non_sample_and_missing_principal_cannot_insert(sample):
    _,_,c=sample;m.install_disposable(c)
    c.execute('UPDATE emails SET is_sample=0 WHERE id=1');c.commit()
    with pytest.raises(sqlite3.IntegrityError):insert(c)
    c.rollback();c.execute('UPDATE emails SET is_sample=1 WHERE id=1');c.execute('DELETE FROM google_principals WHERE account_id=1');c.commit()
    with pytest.raises(sqlite3.IntegrityError):insert(c)
    c.rollback()


def test_populated_rollback_refused_and_content_revision(sample):
    _,engine,c=sample;m.install_disposable(c)
    with Session(engine) as db:
        obs=s.review(db,1,1)['observation'];s.decide(db,1,1,obs.fingerprint,0,'confirmed')
        before=snapshot(c)
        with pytest.raises(ValueError):m.rollback_empty_disposable(c)
        assert snapshot(c)==before
        c.execute("UPDATE emails SET body_text='Please review the new synthetic draft.' WHERE id=1");c.commit();db.expire_all()
        assert s.review(db,1,1)['status']=='needs_review'
        c.execute("UPDATE emails SET body_text='No action wording.' WHERE id=1");c.commit();db.expire_all()
        assert len(s.review(db,1,1)['history'])==1
        c.execute("UPDATE emails SET body_text='Please review the synthetic draft.' WHERE id=1");c.commit();db.expire_all()
        assert s.review(db,1,1)['status']=='confirmed' # disclosed content reversion


def test_history_is_account_first_and_safe_for_legacy_orphans(sample):
    _,_,c=sample;m.install_disposable(c);insert(c);insert(c,2,2)
    # Deliberate malformed legacy fixture: removing one protection is not normal API.
    c.execute('DROP TRIGGER action_candidate_guard_email_delete');c.execute('DELETE FROM emails WHERE id=1');c.commit()
    rows=m.history_page(c,1)
    assert len(rows)==1 and rows[0][1]==1 and rows[0][-1] is None
    assert len(m.history_page(c,2))==1 and m.history_page(c,2)[0][-1]==2
    assert m.history_page(c,99)==[]
    # A foreign-owned replacement source must not become an available source.
    c.execute('INSERT INTO emails SELECT 1,thread_id,account_id,\'legacy-other\',subject,sender,recipients,snippet,body_text,headers,label_ids,internal_date,is_sample,category,priority,classified_at,ai_category,ai_priority,ai_classified_at,classification_source,ai_status,ai_attempts,ai_attempted_at,ai_next_attempt_at,ai_model,ai_prompt_version,ingested_at FROM emails WHERE id=2');c.commit()
    assert m.history_page(c,1)[0][-1] is None
    assert len(m.history_page(c,1,before_id=rows[0][0]))==0


def concurrent_decide(engine,fp,rev,status,barrier):
    # Dedicated engine per connection, barrier immediately before actual mutation.
    local=create_engine(str(engine.url),connect_args={'timeout':0.08,'check_same_thread':False})
    def pause(conn,cursor,statement,params,context,many):
        if statement.lstrip().upper().startswith(('INSERT INTO ACTION_DECISIONS_CANDIDATE','UPDATE ACTION_DECISIONS_CANDIDATE')):
            barrier.wait(timeout=3)
    event.listen(local,'before_cursor_execute',pause)
    try:
        with Session(local) as db:
            try:s.decide(db,1,1,fp,rev,status);return 200
            except HTTPException as exc:return exc.status_code
    finally:local.dispose()

@pytest.mark.parametrize('initial', [False,True])
def test_two_connections_first_insert_and_revision_cas(sample,initial):
    _,engine,c=sample;m.install_disposable(c)
    with Session(engine) as db:
        fp=s.review(db,1,1)['observation'].fingerprint
        if initial:s.decide(db,1,1,fp,0,'reviewed')
    barrier=threading.Barrier(2)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(concurrent_decide,engine,fp,int(initial),status,barrier) for status in ('confirmed','dismissed')]
        results=sorted(f.result(timeout=5) for f in futures)
    assert results==[200,409]
    rows=c.execute('SELECT status,revision FROM action_decisions_candidate').fetchall()
    assert len(rows)==1 and rows[0][1]==int(initial)+1

@pytest.mark.parametrize('initial',[False,True])
def test_two_connections_source_changes_between_validation_and_write(sample,initial):
    _,engine,c=sample;m.install_disposable(c)
    with Session(engine) as db:
        fp=s.review(db,1,1)['observation'].fingerprint
        if initial:s.decide(db,1,1,fp,0,'reviewed')
    def change(conn,cursor,statement,params,context,many):
        if statement.lstrip().upper().startswith(('INSERT INTO ACTION_DECISIONS_CANDIDATE','UPDATE ACTION_DECISIONS_CANDIDATE')):
            c.execute("UPDATE emails SET body_text='Please review different evidence.' WHERE id=1");c.commit()
    event.listen(engine,'before_cursor_execute',change)
    try:
        with Session(engine) as db:
            with pytest.raises(HTTPException) as exc:s.decide(db,1,1,fp,int(initial),'confirmed')
            assert exc.value.status_code==409
    finally:event.remove(engine,'before_cursor_execute',change)
    rows=c.execute('SELECT status,revision FROM action_decisions_candidate').fetchall()
    assert rows==([('reviewed',1)] if initial else [])


def test_actual_second_connection_write_lock_returns_safe_503(sample):
    _,engine,c=sample;m.install_disposable(c)
    with Session(engine) as db:
        fp=s.review(db,1,1)['observation'].fingerprint
        c.execute('BEGIN IMMEDIATE')
        try:
            with pytest.raises(HTTPException) as exc:s.decide(db,1,1,fp,0,'confirmed')
            assert exc.value.status_code==503 and not db.in_transaction()
        finally:c.rollback()
        s.decide(db,1,1,fp,0,'confirmed')
        assert s.review(db,1,1)['status']=='confirmed'

@pytest.mark.parametrize('sql',[
 "INSERT OR REPLACE INTO google_principals VALUES('replacement',1)",
 "INSERT OR REPLACE INTO google_principals VALUES('sub1',8)",
 "INSERT OR REPLACE INTO gmail_accounts SELECT id,email_address,'new',credentials_blob,history_id,created_at FROM gmail_accounts WHERE id=1",
 "INSERT OR REPLACE INTO gmail_accounts SELECT 8,email_address,'new',credentials_blob,history_id,created_at FROM gmail_accounts WHERE id=1",
 "INSERT OR REPLACE INTO emails SELECT * FROM emails WHERE id=1",
 "INSERT OR REPLACE INTO emails SELECT 8,thread_id,account_id,gmail_message_id,subject,sender,recipients,snippet,body_text,headers,label_ids,internal_date,is_sample,category,priority,classified_at,ai_category,ai_priority,ai_classified_at,classification_source,ai_status,ai_attempts,ai_attempted_at,ai_next_attempt_at,ai_model,ai_prompt_version,ingested_at FROM emails WHERE id=1",
])
def test_replace_cannot_bypass_delete_guards_with_recursive_triggers_off(sample,sql):
    _,_,c=sample;m.install_disposable(c);insert(c);before=snapshot(c)
    assert c.execute('PRAGMA recursive_triggers').fetchone()[0]==0
    with pytest.raises(sqlite3.IntegrityError):c.execute(sql)
    c.rollback();assert snapshot(c)==before


def test_non_contention_operational_error_is_not_a_503(sample):
    from sqlalchemy.exc import OperationalError
    _,engine,c=sample;m.install_disposable(c)
    def malformed(conn,cursor,statement,params,context,many):
        if statement.lstrip().upper().startswith('INSERT INTO ACTION_DECISIONS_CANDIDATE'):
            # Real SQLite error code1 includes misleading words in table name.
            cursor.execute('SELECT * FROM busy_locked_missing_table')
    event.listen(engine,'before_cursor_execute',malformed)
    try:
        with Session(engine) as db:
            fp=s.review(db,1,1)['observation'].fingerprint
            with pytest.raises(OperationalError):s.decide(db,1,1,fp,0,'confirmed')
            assert not db.in_transaction()
    finally:event.remove(engine,'before_cursor_execute',malformed)
