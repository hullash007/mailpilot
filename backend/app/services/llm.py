"""LLM provider abstraction (Phase 2 prep).

Config-driven OpenAI-compatible endpoint: an Ollama/llama.cpp server on a
private VM today, a hosted API later - callers never change. The endpoint
must stay private (localhost / tunnel); MailPilot never exposes it.
"""
import requests

from ..config import get_settings


class LLMNotConfigured(RuntimeError):
    pass


class OpenAICompatibleProvider:
    """Minimal client for OpenAI-compatible chat completions."""

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def complete(self, prompt: str, system: str = "") -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages, "temperature": 0},
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def get_provider() -> OpenAICompatibleProvider:
    """Build the configured provider; fails closed when LLM_BASE_URL is unset."""
    settings = get_settings()
    if not settings.llm_base_url or not settings.llm_model:
        raise LLMNotConfigured("Set LLM_BASE_URL and LLM_MODEL to enable LLM features.")
    return OpenAICompatibleProvider(settings.llm_base_url, settings.llm_model, settings.llm_api_key)
