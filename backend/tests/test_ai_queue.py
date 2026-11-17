"""Synthetic-only persistent AI job and resource-bound regressions."""
import json
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.database import Base, get_db
from app.models import Email, GmailAccount, Thread
from app.services import refine
from app.services.llm import OpenAICompatibleProvider


@pytest.fixture()
def work(monkeypatch):
    monkeypatch.setenv('LLM_REFINE', '1')
    monkeypatch.setenv('LLM_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('LLM_MODEL', 'synthetic-model')
    monkeypatch.delenv('APP_ACCESS_TOKEN', raising=False)
    get_settings.cache_clear()
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    a = GmailAccount(email_address='synthetic@example.invalid')
    db.add(a); db.flush()
    t = Thread(account_id=a.id, gmail_thread_id='queue-thread')
    db.add(t); db.flush()
    for i in range(3):
        db.add(Email(account_id=a.id, thread_id=t.id, gmail_message_id=str(i),
                     subject='Security alert', sender='no-reply@example.invalid',
                     category='notification', priority='high', classified_at=datetime.utcnow(),
                     is_sample=i != 2))
    db.commit()
    yield db, a.id
    db.close(); engine.dispose(); get_settings.cache_clear()


def test_http_enqueues_only_samples_and_never_calls_provider(work, monkeypatch):
    from app.main import create_app
    db, account = work
    app = create_app(); app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(refine, 'get_provider', lambda: pytest.fail('HTTP must not call inference'))
    c = TestClient(app)
    assert c.post(f'/refine/{account}').json() == {'queued': 2, 'off': False}
    assert c.post(f'/refine/{account}').json()['queued'] == 0
    assert db.query(Email).filter_by(is_sample=False).one().ai_status == 'none'
    assert c.post(f'/classify/{account}').status_code == 200


def test_worker_only_queued_samples_and_provenance(work, monkeypatch):
    db, account = work
    refine.enqueue_samples(db, account)
    class Provider:
        def complete(self, *_args, **_kw):
            return '{"category":"personal","priority":"low"}'
    monkeypatch.setattr(refine, 'get_provider', Provider)
    result = refine.refine_pending(db, account, queued_only=True, sample_only=True)
    assert result['refined'] == 2
    for row in db.query(Email).filter_by(is_sample=True):
        assert (row.category, row.priority, row.classification_source) == ('notification', 'high', 'rules')
        assert (row.ai_category, row.ai_priority, row.ai_status) == ('personal', 'low', 'suggested')
        assert row.ai_model == 'synthetic-model' and row.ai_prompt_version == refine.PROMPT_VERSION
    assert db.query(Email).filter_by(is_sample=False).one().ai_attempts == 0


def test_failed_newest_does_not_starve_older_and_backoff(work, monkeypatch):
    db, account = work
    refine.enqueue_samples(db, account)
    seen = []
    def invalid(row):
        seen.append(row.id)
        return 'invalid'
    monkeypatch.setattr(refine, 'maybe_refine', invalid)
    for _ in range(2):
        refine.refine_pending(db, account, limit=1, queued_only=True, sample_only=True)
    assert len(set(seen)) == 2
    assert refine.refine_pending(db, account, queued_only=True, sample_only=True)['processed'] == 0
    row = db.get(Email, seen[0]); row.ai_next_attempt_at = datetime.utcnow()-timedelta(seconds=1)
    row.ai_attempts = 3; db.commit()
    assert refine.refine_pending(db, account, queued_only=True, sample_only=True)['processed'] == 0


def test_hard_wall_deadline_preserves_rules_and_commits_failure(work, monkeypatch):
    db, account = work
    refine.enqueue_samples(db, account)
    def slow(row):
        time.sleep(3)
        return 'refined'
    monkeypatch.setattr(refine, 'maybe_refine', slow)
    started = time.monotonic()
    result = refine.refine_pending(db, account, queued_only=True, sample_only=True, wall_seconds=1)
    assert time.monotonic()-started < 2
    assert result['failed'] == 1 and result['processed'] == 1
    row = db.query(Email).filter_by(ai_status='failed').one()
    assert row.ai_attempts == 1 and row.ai_next_attempt_at is not None
    assert (row.category, row.priority) == ('notification', 'high')


def test_stale_running_lease_is_recovered(work, monkeypatch):
    db, account = work
    row = db.query(Email).filter_by(is_sample=True).first()
    row.ai_status = 'running'; row.ai_attempts = 1
    row.ai_attempted_at = datetime.utcnow()-timedelta(minutes=3); db.commit()
    monkeypatch.setattr(refine, 'maybe_refine', lambda _: 'invalid')
    result = refine.refine_pending(db, account, queued_only=True, sample_only=True)
    assert result['invalid'] == 1
    assert row.ai_attempts == 2 and row.ai_status == 'invalid'


@pytest.mark.parametrize('chunks', [
    [b'x'*65537],
    [json.dumps({'choices':[{'message':{'content':'{}'},'finish_reason':'length'}]}).encode()],
    [json.dumps({'choices':[{'message':{'content':'{} trailing '*500}}]}).encode()],
])
def test_provider_rejects_oversize_and_truncation(chunks):
    fake = MagicMock(); fake.status_code = 200; fake.iter_content.return_value = chunks
    with patch('app.services.llm.requests.post', return_value=fake):
        with pytest.raises((ValueError, KeyError)):
            OpenAICompatibleProvider('http://127.0.0.1:9/v1', 'test').complete('test')
    fake.close.assert_called_once()


def test_final_attempt_crash_becomes_exhausted_without_inference(work, monkeypatch):
    db, account = work
    row = db.query(Email).filter_by(is_sample=True).first()
    row.ai_status = 'running'; row.ai_attempts = 3
    row.ai_attempted_at = datetime.utcnow()-timedelta(minutes=3); db.commit()
    monkeypatch.setattr(refine, 'maybe_refine', lambda _: pytest.fail('exhausted work must not call a model'))
    assert refine.refine_pending(db, account, queued_only=True, sample_only=True)['processed'] == 0
    db.refresh(row)
    assert row.ai_status == 'exhausted' and row.ai_attempts == 3


def test_failure_provenance_is_latest_attempt_not_only_success(work, monkeypatch):
    db, account = work
    refine.enqueue_samples(db, account, limit=1)
    monkeypatch.setattr(refine, 'maybe_refine', lambda _: 'failed')
    refine.refine_pending(db, account, queued_only=True, sample_only=True)
    row = db.query(Email).filter_by(ai_status='failed').one()
    assert row.ai_model == 'synthetic-model' and row.ai_prompt_version == refine.PROMPT_VERSION
