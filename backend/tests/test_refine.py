"""LLM refinement: fail-closed behavior and strict output validation.

All tests use stub providers or synthetic fixtures - no real inbox content.
"""
import pytest

from app.config import get_settings
from app.services import refine as refine_mod
from app.services.refine import maybe_refine, parse_refinement, refine_one

TEST_KEY = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()


class StubProvider:
    def __init__(self, output=None, exc=None):
        self.output = output
        self.exc = exc
        self.calls = 0

    def complete(self, prompt, system=""):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.output


class FakeEmail:
    def __init__(self):
        self.subject = "Your receipt"
        self.sender = "vendor@example.com"
        self.label_ids = ["INBOX"]
        self.snippet = "thanks for your order"
        self.category = "receipt"
        self.priority = "normal"
        self.classification_source = "rules"
        self.ai_category = ""
        self.ai_priority = ""
        self.ai_classified_at = None


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    monkeypatch.setenv("LLM_REFINE", "1")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens
    monkeypatch.setenv("LLM_MODEL", "stub")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_parse_valid():
    assert parse_refinement('{"category": "newsletter", "priority": "low"}') == ("newsletter", "low")
    assert parse_refinement('  {"category": "social", "priority": "low"}\n') == ("social", "low")


@pytest.mark.parametrize("bad", [
    "",
    "no json here",
    'Sure! {"category": "social", "priority": "low"} hope this helps',  # prose wrapper
    '```json\n{"category": "social", "priority": "low"}\n```',          # code fence
    '{"category": "invoice", "priority": "low"}',          # out-of-vocabulary category
    '{"category": "receipt", "priority": "critical"}',      # out-of-vocabulary priority
    '{"category": "receipt"}',                              # missing key
    '{"category": "receipt", "priority": "low", "x": 1}',   # extra key
    '{"category": "receipt", "priority": "low"} {"category": "social", "priority": "low"}',  # ambiguous second object
    '{"category": "receipt", "category": "social", "priority": "low"}',   # duplicate key
    '{"category": {"x": "receipt"}, "priority": "low"}',                  # nested object value
    '[{"category": "receipt", "priority": "low"}]',                       # array wrapper
    '{"category": ["receipt"], "priority": "low"}',                       # array value
    '{"category": "receipt", "priority": "low"},',                        # trailing junk
    '{"category": "receipt", "priority": null}',                           # non-string value
])
def test_parse_rejects_deviations(bad):
    assert parse_refinement(bad) is None


def test_injected_email_text_cannot_change_vocabulary():
    # Untrusted subject tries prompt injection; the stub "model" obeys it and
    # emits an out-of-vocabulary value - parse still rejects it.
    out = parse_refinement('{"category": "ignore_all_rules", "priority": "high"}')
    assert out is None


def test_refine_one_returns_pair_on_valid_output():
    p = StubProvider('{"category": "newsletter", "priority": "low"}')
    assert refine_one(p, subject="s", sender="x@y.z", labels=[], snippet="t",
                      rule_category="personal", rule_priority="normal") == ("newsletter", "low")


def test_maybe_refine_applies_valid_to_separate_columns(monkeypatch):
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider('{"category": "notification", "priority": "high"}'))
    e = FakeEmail()
    assert maybe_refine(e) == "refined"
    # suggestion stored separately; authoritative rule columns untouched
    assert (e.ai_category, e.ai_priority) == ("notification", "high")
    assert e.ai_classified_at is not None
    assert (e.category, e.priority, e.classification_source) == ("receipt", "normal", "rules")


def test_maybe_refine_keeps_rules_on_garbage(monkeypatch):
    monkeypatch.setattr(refine_mod, "get_provider", lambda: StubProvider("I think it is a receipt"))
    e = FakeEmail()
    assert maybe_refine(e) == "invalid"
    assert (e.category, e.priority) == ("receipt", "normal")
    assert e.ai_classified_at is None  # retryable


def test_maybe_refine_fails_closed_on_transport_error(monkeypatch):
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider(exc=ConnectionError("endpoint down")))
    e = FakeEmail()
    assert maybe_refine(e) == "failed"
    assert (e.category, e.priority) == ("receipt", "normal")


def test_maybe_refine_off_without_flag(monkeypatch):
    monkeypatch.setenv("LLM_REFINE", "0")
    get_settings.cache_clear()
    assert maybe_refine(FakeEmail()) == "off"


def test_maybe_refine_off_without_endpoint(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    get_settings.cache_clear()
    assert maybe_refine(FakeEmail()) == "off"


def test_prompt_bounds_untrusted_text():
    p = StubProvider('{"category": "receipt", "priority": "normal"}')
    refine_one(p, subject="s", sender="x", labels=[], snippet="A" * 5000,
               rule_category="receipt", rule_priority="normal")
    assert p.calls == 1


def _seed_classified(db, account_id):
    from app.models import Email, GmailAccount, Thread
    from app.services.classify import classify_unclassified

    account = GmailAccount(email_address="refine@example.com")
    account.credentials = {"token": "t"}
    db.add(account)
    db.commit()
    thread = Thread(account_id=account.id, gmail_thread_id="rt1", subject="s", snippet="s")
    db.add(thread)
    db.commit()
    db.add(Email(thread_id=thread.id, account_id=account.id, gmail_message_id="rm1",
                 subject="Weekend trek plan", sender="rahul@friend.example", recipients="me@example.com",
                 snippet="are we still on?", body_text="b", headers={"From": "rahul@friend.example"},
                 label_ids=["INBOX"]))
    db.commit()
    assert classify_unclassified(db, account.id)["classified"] == 1
    return account


def test_refine_pending_reaches_already_classified_rows(tmp_path, monkeypatch):
    """Rows backfilled BEFORE refinement was enabled must still get a second
    opinion: selection keys on ai_classified_at, not classified_at."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Email
    from app.services.refine import refine_pending

    engine = create_engine(f"sqlite:///{tmp_path}/r.db")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    account = _seed_classified(db, 1)

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider('{"category": "personal", "priority": "normal"}'))
    out = refine_pending(db, account.id, limit=10)
    assert out == {"processed": 1, "refined": 1, "invalid": 0, "failed": 0, "off": 0}

    row = db.query(Email).filter_by(gmail_message_id="rm1").one()
    assert row.ai_category == "personal" and row.ai_classified_at is not None
    assert (row.category, row.classification_source) == ("personal", "rules")

    # second pass: nothing pending
    assert refine_pending(db, account.id, limit=10)["processed"] == 0


def test_refine_pending_counts_failures(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import Email
    from app.services.refine import refine_pending

    engine = create_engine(f"sqlite:///{tmp_path}/r2.db")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    account = _seed_classified(db, 1)

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", TEST_KEY)
    get_settings.cache_clear()
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider(exc=ConnectionError("down")))
    out = refine_pending(db, account.id, limit=10)
    assert out == {"processed": 1, "refined": 0, "invalid": 0, "failed": 1, "off": 0}
    row = db.query(Email).filter_by(gmail_message_id="rm1").one()
    assert row.ai_category == "" and row.ai_classified_at is None
    # failed rows are retried on the next pass (ai_classified_at still NULL)
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider('{"category": "personal", "priority": "normal"}'))
    row.ai_next_attempt_at = None  # simulate the backoff period elapsing
    db.commit()
    assert refine_pending(db, account.id, limit=10)["refined"] == 1


def test_endpoint_quality_synthetic_only():
    """Real-endpoint quality probe; runs only when MAILPILOT_TEST_LLM_URL is set.

    Sends three SYNTHETIC emails through the full refine path and prints the
    validated outcomes. Never sends real inbox content.
    """
    import os

    url = os.environ.get("MAILPILOT_TEST_LLM_URL", "")
    if not url:
        pytest.skip("MAILPILOT_TEST_LLM_URL not set")
    from app.services.llm import OpenAICompatibleProvider

    provider = OpenAICompatibleProvider(url, os.environ.get("MAILPILOT_TEST_LLM_MODEL", "qwen2.5:14b-instruct-q4_K_M"), timeout=120)
    cases = [
        ("Your receipt from Swiggy #48291", "orders@food.example", [], "Thanks for your order. Total paid: Rs 486", "receipt", "normal"),
        ("Your daily digest", "digest@news.example", ["List-Unsubscribe"], "Top products today", "newsletter", "low"),
        ("Security alert: new sign-in", "no-reply@accounts.example", [], "A new sign-in was detected", "notification", "high"),
    ]
    outcomes = []
    for subject, sender, label_hints, snippet, want_cat, want_prio in cases:
        out = refine_one(provider, subject=subject, sender=sender, labels=label_hints,
                         snippet=snippet, rule_category=want_cat, rule_priority=want_prio)
        outcomes.append((subject[:25], (want_cat, want_prio), out))
    for o in outcomes:
        print(o)
    # small-n accuracy assertion, not a smoke check: every case must return a
    # contract-valid suggestion AND agree with the expected (category, priority)
    for subject, want, got in outcomes:
        assert got == want, f"{subject}: expected {want}, got {got}"
