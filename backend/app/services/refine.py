"""Optional LLM refinement of the rule-based baseline classification.

Fail-closed by design: any problem (endpoint unreachable, auth failure,
unparseable or out-of-vocabulary output) returns None and the rule-based
result stands. A validated suggestion is stored in the SEPARATE ai_category/
ai_priority columns: the authoritative category/priority columns always keep
the deterministic rule result (classification_source stays "rules" until a
user explicitly accepts a suggestion, a future UI action). Suggestions never
trigger mail actions. Untrusted email text is only prompt data, but prompt
boundaries alone are not a guarantee - the strict output validation below is
what limits a manipulated response to these two suggestion columns.
Synthetic fixtures only until real-inbox inference is explicitly approved.
"""
import json
import signal
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import select, update, or_, and_

from .classify import CATEGORIES, PRIORITIES
from .llm import LLMNotConfigured, get_provider

PROMPT_VERSION = "classification-v2"
MAX_ATTEMPTS = 3

_SYSTEM = (
    "You refine email classifications. Reply with ONLY a JSON object, no prose, "
    "no code fences: "
    '{"category": "<one of ' + ", ".join(sorted(CATEGORIES)) + '>", '
    '"priority": "<one of ' + ", ".join(sorted(PRIORITIES)) + '>"}. '
    "Keep the suggested category/priority unless the email text clearly says otherwise."
)


def _object_no_dupes(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate keys")
    return dict(pairs)


def parse_refinement(text: str):
    """Validate the exact output contract: the ENTIRE stripped response must
    decode as one JSON object with exactly the string keys "category" and
    "priority" (no duplicates, no nesting, no arrays, no prose, no trailing
    content), both values strings from the allowed vocabularies. Any deviation
    -> None. Vocabulary limits the blast radius of a manipulated response; it
    does not prevent semantic manipulation, which is why suggestions are
    stored separately and never become authoritative.
    """
    if not isinstance(text, str) or len(text) > 4000:
        return None
    raw = text.strip()
    if not raw.startswith("{") or not raw.endswith("}"):
        return None
    try:
        data = json.loads(raw, object_pairs_hook=_object_no_dupes)
    except ValueError:
        return None
    if not isinstance(data, dict) or set(data) != {"category", "priority"}:
        return None
    if not all(isinstance(v, str) for v in data.values()):
        return None
    if data["category"] not in CATEGORIES or data["priority"] not in PRIORITIES:
        return None
    return data["category"], data["priority"]


def refine_one(provider, *, subject: str, sender: str, labels: list, snippet: str,
               rule_category: str, rule_priority: str):
    """Ask the endpoint to refine one email's rule-based suggestion.

    Every untrusted input field is length-bounded before entering the prompt.
    Returns (category, priority) on contract-valid output, else None.
    Transport/HTTP errors propagate for the caller to count as "failed".
    """
    bounded_labels = [str(l)[:50] for l in (labels or [])[:10]]
    prompt = (
        f"Subject: {(subject or '')[:300]}\nFrom: {(sender or '')[:200]}\n"
        f"Gmail labels: {', '.join(bounded_labels) or '(none)'}\n"
        f"Snippet: {(snippet or '')[:500]}\n"
        f"Rule-based suggestion: category={rule_category}, priority={rule_priority}"
    )
    if len(prompt.encode("utf-8")) + len(_SYSTEM.encode("utf-8")) > 1400:
        return None  # reject rather than silently remove source context
    return parse_refinement(provider.complete(prompt, system=_SYSTEM))


def maybe_refine(email):
    """Store an LLM second opinion for one classified Email row, in place.

    Writes ai_category/ai_priority/ai_classified_at only; the authoritative
    rule columns (category/priority/classification_source) are never touched.
    Returns "refined" (valid suggestion stored), "invalid" (endpoint answered
    but broke the output contract), "failed" (transport/HTTP error) or "off"
    (refinement disabled/unconfigured). Never raises: any endpoint/auth/parse
    problem leaves the row's authoritative rule columns untouched.
    """
    from datetime import datetime

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
        return "invalid"
    email.ai_category, email.ai_priority = result
    email.ai_classified_at = datetime.utcnow()
    email.ai_model = settings.llm_model[:128]
    email.ai_prompt_version = PROMPT_VERSION
    return "refined"



def enqueue_samples(db, account_id: int, limit: int = 25) -> dict:
    """Persist sample-only jobs, without inference or network activity.

    Already-complete/running/exhausted jobs are never reset by this route.
    Failed attempts use backoff and a hard three-attempt ceiling.
    """
    from ..models import Email
    from ..config import get_settings
    if not get_settings().llm_refine:
        return {"queued": 0, "off": True}
    rows = db.scalars(select(Email).where(
        Email.account_id == account_id, Email.is_sample.is_(True),
        Email.classified_at.is_not(None), Email.ai_classified_at.is_(None),
        Email.ai_status == "none", Email.ai_attempts < MAX_ATTEMPTS,
    ).order_by(Email.id).limit(max(1, min(int(limit), 100)))).all()
    for row in rows:
        row.ai_status = "pending"
    db.commit()
    return {"queued": len(rows), "off": False}


class InferenceDeadline(TimeoutError):
    pass


def _deadline_signal(*_):
    raise InferenceDeadline("bounded inference deadline")


def refine_pending(db, account_id: int, limit: int = 25, *, queued_only=False,
                   sample_only=False, wall_seconds=90, refine_fn=None) -> dict:
    """Resumable sequential worker; supported CLI uses queued_only/sample_only.

    Independent AI attempt timestamps drive oldest-attempt-first fairness.
    Invalid/errors back off 60/120/180s, stop after3 attempts; off is not done.
    CAS status claim prevents duplicate processing of the same row. Each job
    commits independently. Stale running leases expire after120s on a later
    worker pass. A Unix main-thread alarm bounds all network/decoding work.
    No production caller runs this from an HTTP request. Unqueued mode is
    retained for offline regression probes only.
    """
    from ..models import Email
    from ..config import get_settings
    counts = {"processed": 0, "refined": 0, "invalid": 0, "failed": 0, "off": 0}
    if not get_settings().llm_refine:
        counts["off"] = 1
        return counts
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("worker must run in its own main process")
    now = datetime.utcnow()
    settings = get_settings()
    exhausted = [Email.account_id == account_id, Email.ai_status == "running",
                 Email.ai_attempts >= MAX_ATTEMPTS,
                 Email.ai_attempted_at < now - timedelta(seconds=120)]
    if sample_only:
        exhausted.append(Email.is_sample.is_(True))
    db.execute(update(Email).where(*exhausted).values(ai_status="exhausted", ai_next_attempt_at=None))
    db.commit()  # terminalise final-attempt crashes without another model call
    filters = [Email.account_id == account_id, Email.classified_at.is_not(None),
               Email.ai_classified_at.is_(None), Email.ai_attempts < MAX_ATTEMPTS,
               or_(Email.ai_next_attempt_at.is_(None), Email.ai_next_attempt_at <= now),
               or_(Email.ai_status != "running", Email.ai_attempted_at < now - timedelta(seconds=120))]
    if queued_only:
        filters.append(Email.ai_status.in_(["pending", "invalid", "failed", "running"]))
    if sample_only:
        filters.append(Email.is_sample.is_(True))
    rows = db.scalars(select(Email).where(*filters)
        .order_by(Email.ai_attempted_at.asc().nulls_first(), Email.id)
        .limit(max(1, min(int(limit), 100)))).all()
    deadline = time.monotonic() + max(1, min(float(wall_seconds), 90))
    for row in rows:
        remaining = deadline - time.monotonic()
        if remaining <= 0.01:
            break
        old_attempts = row.ai_attempts
        claimed = db.execute(update(Email).where(Email.id == row.id,
            Email.ai_attempts == old_attempts,
            or_(Email.ai_status != "running", Email.ai_attempted_at < now-timedelta(seconds=120)))
            .values(ai_status="running", ai_attempted_at=datetime.utcnow(),
                    ai_attempts=old_attempts+1, ai_model=settings.llm_model[:128],
                    ai_prompt_version=PROMPT_VERSION))
        db.commit()
        if claimed.rowcount != 1:
            continue
        old_handler = signal.signal(signal.SIGALRM, _deadline_signal)
        signal.setitimer(signal.ITIMER_REAL, min(60, remaining))
        try:
            outcome = (refine_fn or maybe_refine)(row)
        except Exception:
            outcome = "failed"
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
        counts["processed"] += 1
        if outcome in counts:
            counts[outcome] += 1
        if outcome == "refined":
            row.ai_status = "agreement" if (row.ai_category, row.ai_priority) == (row.category, row.priority) else "suggested"
            row.ai_next_attempt_at = None
        elif outcome == "off":
            row.ai_status = "pending"
            row.ai_attempts = old_attempts  # disabled/unconfigured is not a failed attempt
        else:
            row.ai_status = outcome
            row.ai_next_attempt_at = datetime.utcnow() + timedelta(seconds=60 * row.ai_attempts)
        db.commit()
    return counts
