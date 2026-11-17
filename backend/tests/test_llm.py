"""LLM provider abstraction: config gating and request shape."""
import json

from unittest.mock import patch, MagicMock

import pytest

from app.config import get_settings
from app.services import llm


@pytest.fixture(autouse=True)
def _clear():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_unconfigured_fails_closed(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "")
    monkeypatch.setenv("LLM_MODEL", "")
    with pytest.raises(llm.LLMNotConfigured):
        llm.get_provider()


def test_request_shape(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://10.0.0.5:11434/v1")
    monkeypatch.setenv("LLM_MODEL", "llama3.1:8b")
    provider = llm.get_provider()
    fake = MagicMock()
    fake.status_code = 200
    fake.iter_content.return_value = [json.dumps({"model":"llama3.1:8b", "choices": [{"message": {"role":"assistant", "content": "ok"}, "finish_reason":"stop"}]}).encode()]
    fake.raise_for_status = lambda: None
    with patch("app.services.llm.requests.post", return_value=fake) as post:
        out = provider.complete("summarize", system="be terse")
    assert out == "ok"
    url, = post.call_args.args
    assert url == "http://10.0.0.5:11434/v1/chat/completions"
    body = post.call_args.kwargs["json"]
    assert body["model"] == "llama3.1:8b"
    assert body["messages"][0] == {"role": "system", "content": "be terse"}
