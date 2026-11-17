"""Optional LLM refinement of the rule-based baseline classification.

Fail-closed by design: any problem (endpoint unreachable, auth failure,
unparseable or out-of-vocabulary output) returns None and the rule-based
result stands. Refinement never triggers mail actions - it only adjusts the
category/priority suggestion columns, exactly like the baseline rules.
Synthetic fixtures only until real-inbox inference is explicitly approved.
"""
import json
import re

from .classify import CATEGORIES, PRIORITIES
from .llm import LLMNotConfigured, get_provider

_SYSTEM = (
    "You refine email classifications. Reply with ONLY a JSON object, no prose: "
    '{"category": "<one of ' + ", ".join(sorted(CATEGORIES)) + '>", '
    '"priority": "<one of ' + ", ".join(sorted(PRIORITIES)) + '>"}. '
    "Keep the suggested category/priority unless the email text clearly says otherwise."
)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def parse_refinement(text: str):
    """Extract and validate a {"category","priority"} object; None on any deviation."""
    match = _JSON_RE.search(text or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if set(data) != {"category", "priority"}:
        return None
    if data["category"] not in CATEGORIES or data["priority"] not in PRIORITIES:
        return None
    return data["category"], data["priority"]


def refine_one(provider, *, subject: str, sender: str, labels: list, snippet: str,
               rule_category: str, rule_priority: str):
    """Ask the endpoint to refine one email's rule-based suggestion.

    Returns (category, priority) on validated agreement-grade output, else None.
    Raises nothing: transport/HTTP errors are the caller's signal to fall back,
    so they propagate as requests exceptions handled by the caller.
    """
    prompt = (
        f"Subject: {subject}\nFrom: {sender}\nGmail labels: {', '.join(labels) or '(none)'}\n"
        f"Snippet: {snippet[:500]}\n"
        f"Rule-based suggestion: category={rule_category}, priority={rule_priority}"
    )
    return parse_refinement(provider.complete(prompt, system=_SYSTEM))


def maybe_refine(email):
    """Refine one stored Email row in place when refinement is enabled.

    Returns "refined", "kept" (endpoint said nothing valid) or "off"/"failed".
    Never raises: any endpoint/auth/parse problem leaves rule values untouched.
    """
    from ..config import get_settings

    settings = get_settings()
    if not settings.llm_refine:
        return "off"
    try:
        provider = get_provider()
    except LLMNotConfigured:
        return "off"
    try:
        result = refine_one(
            provider,
            subject=email.subject, sender=email.sender,
            labels=email.label_ids or [], snippet=email.snippet,
            rule_category=email.category, rule_priority=email.priority,
        )
    except Exception:
        return "failed"
    if result is None:
        return "kept"
    email.category, email.priority = result
    return "refined"
