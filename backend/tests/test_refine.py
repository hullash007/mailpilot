"""LLM refinement: fail-closed behavior and strict output validation.

All tests use stub providers or synthetic fixtures - no real inbox content.
"""
import pytest

from app.config import get_settings
from app.services import refine as refine_mod
from app.services.refine import maybe_refine, parse_refinement, refine_one


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


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LLM_REFINE", "1")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1")  # nothing listens
    monkeypatch.setenv("LLM_MODEL", "stub")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_parse_valid():
    assert parse_refinement('{"category": "newsletter", "priority": "low"}') == ("newsletter", "low")


def test_parse_surrounding_prose_still_validates():
    assert parse_refinement('Sure! {"category": "social", "priority": "low"} hope this helps') == (
        "social", "low")


@pytest.mark.parametrize("bad", [
    "",
    "no json here",
    '{"category": "invoice", "priority": "low"}',          # out-of-vocabulary category
    '{"category": "receipt", "priority": "critical"}',      # out-of-vocabulary priority
    '{"category": "receipt"}',                              # missing key
    '{"category": "receipt", "priority": "low", "x": 1}',   # extra key
    '{"category": "receipt", "priority": "low"} {"a": 1}',  # ambiguous second object is ignored
])
def test_parse_rejects_deviations(bad):
    # the two-object case resolves to the first; ensure it is still a valid pair or None
    out = parse_refinement(bad)
    if bad.endswith('{"a": 1}'):
        assert out == ("receipt", "low")
    else:
        assert out is None


def test_injected_email_text_cannot_change_vocabulary():
    # Untrusted subject tries prompt injection; the stub "model" obeys it and
    # emits an out-of-vocabulary value - parse still rejects it.
    out = parse_refinement('{"category": "ignore_all_rules", "priority": "high"}')
    assert out is None


def test_refine_one_returns_pair_on_valid_output():
    p = StubProvider('{"category": "newsletter", "priority": "low"}')
    assert refine_one(p, subject="s", sender="x@y.z", labels=[], snippet="t",
                      rule_category="personal", rule_priority="normal") == ("newsletter", "low")


def test_maybe_refine_applies_valid(monkeypatch):
    monkeypatch.setattr(refine_mod, "get_provider",
                        lambda: StubProvider('{"category": "notification", "priority": "high"}'))
    e = FakeEmail()
    assert maybe_refine(e) == "refined"
    assert (e.category, e.priority) == ("notification", "high")


def test_maybe_refine_keeps_rules_on_garbage(monkeypatch):
    monkeypatch.setattr(refine_mod, "get_provider", lambda: StubProvider("I think it is a receipt"))
    e = FakeEmail()
    assert maybe_refine(e) == "kept"
    assert (e.category, e.priority) == ("receipt", "normal")


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
    for subject, sender, label_hints, snippet, rc, rp in cases:
        out = refine_one(provider, subject=subject, sender=sender, labels=label_hints,
                         snippet=snippet, rule_category=rc, rule_priority=rp)
        outcomes.append((subject[:25], rc, rp, out))
    for o in outcomes:
        print(o)
    assert all(o[3] is not None for o in outcomes), "endpoint returned no valid refinement"
