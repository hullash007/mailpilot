"""LLM provider abstraction (Phase 2 prep).

Config-driven OpenAI-compatible endpoint: an Ollama/llama.cpp server on a
private VM today, a hosted API later - callers never change. The endpoint
must stay private (localhost / tunnel); MailPilot never exposes it. Privacy
comes from deployment (loopback bind, SSH tunnel), not from any URL check -
get_provider only warns when the configured host does not look private.
"""
import json
import ipaddress
import logging
from urllib.parse import urlparse

import requests

from ..config import get_settings

log = logging.getLogger(__name__)


def _looks_private(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False  # DNS name: cannot verify privacy here
    return addr.is_private or addr.is_loopback or addr.is_link_local


class LLMNotConfigured(RuntimeError):
    pass


class OpenAICompatibleProvider:
    """Minimal client for OpenAI-compatible chat completions."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 60,
                 max_tokens: int = 128):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.max_tokens = max_tokens

    def complete(self, prompt: str, system: str = "") -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        if sum(len(m["content"].encode("utf-8")) for m in messages) > 1400:
            raise ValueError("private gateway message budget exceeded")
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": 0,
                  "max_tokens": self.max_tokens},
            headers=headers,
            timeout=(min(5, self.timeout), self.timeout),
            stream=True,
            allow_redirects=False,
        )
        try:
            resp.raise_for_status()
            if 300 <= resp.status_code < 400:
                raise ValueError("inference redirects are not allowed")
            raw = bytearray()
            for chunk in resp.iter_content(chunk_size=4096):
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise ValueError("inference response exceeds64KiB")
            data = json.loads(raw)
            if data.get("model") != self.model:
                raise ValueError("inference model mismatch")
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete inference output")
            message = choice["message"]
            content = message["content"]
            if message.get("role") != "assistant" or message.get("tool_calls") or not isinstance(content, str) or len(content) > 4000:
                raise ValueError("invalid or oversized inference content")
            return content
        finally:
            resp.close()


def get_provider() -> OpenAICompatibleProvider:
    """Build the configured provider; fails closed when LLM_BASE_URL is unset."""
    settings = get_settings()
    if not settings.llm_base_url or not settings.llm_model:
        raise LLMNotConfigured("Set LLM_BASE_URL and LLM_MODEL to enable LLM features.")
    if not _looks_private(settings.llm_base_url):
        log.warning(
            "LLM_BASE_URL host does not look private (loopback/RFC1918): %s. "
            "Model traffic should stay on localhost or a private tunnel.",
            urlparse(settings.llm_base_url).hostname,
        )
    return OpenAICompatibleProvider(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
