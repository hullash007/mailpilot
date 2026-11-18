# Action review candidate design

This is a local, synthetic-fixture-first candidate on public0391767. It is not imported by the deployed entrypoint. It adds no Gmail, AI, model, background worker, sending or live migration.

The product promise is small: identify explicit attention signals in already stored text, show the evidence as untrusted plain text and let the person record a local decision. Signals do not establish that the owner has an obligation, urgency or deadline.

## Lifecycle

Detect, show evidence, record a human decision, survive a process restart, change source evidence, require review again. One observation per source email and detector version initially. Multiple actions in one email are not supported.

## Derived observation

A pure detector gets bounded stored plain text and explicit bulk/automated metadata. It returns zero or one reason code and character span. No excerpts or bodies are duplicated in the decision table. A fingerprint binds the complete source revision, detector version, reason and evidence offsets. Display reconstructs the excerpt from that exact source revision, validates bounds, and escapes it.

Narrow signals: direct question/request, explicit please request, stated commitment, follow-up wording. Dates alone are not sufficient. No timezone/calendar parsing or urgency ranking. Explicit List-Unsubscribe, Precedence bulk/list, or Auto-Submitted other than no can suppress a candidate. Sender familiarity grants nothing. Quoted-line and signature text should not become a confident obligation. Everything remains labelled Potential action - unverified.

## Durable human decision

Unique key: account, source email, detector version and evidence fingerprint. Status: reviewed, confirmed, dismissed or completed. Reviewed is not confirmed. Revision increments atomically on updates; a stale browser revision gets a conflict instead of overwriting a newer choice. Created and updated timestamps support local audit. Rendering never writes or mutates status.

A changed source revision or detector version creates an unreviewed observation. Prior decisions remain historical, never transferred to new evidence and never automatically purged. The view calls this needs review when prior decisions exist. Missing source cannot display a reconstructed excerpt or accept a decision. Retention and deletion are separate design work.

## Browser boundary

Own-subject dependency and account predicates on every source and decision query. Same-origin POST check, bounded form input, strict allowed statuses and optimistic revision required. No secret-bearing URLs. Email text cannot choose tools, recipients, action targets, status updates or instructions. It is only evidence shown to a person. Confirming an action is a local record, never permission to send or change Gmail.

## Exit

One synthetic lifecycle and local UI, account isolation, exact-evidence change rejection, CAS conflict, no-write GET, no provider path, escaped hostile text, and desktop/mobile pixels. The deployed Google entrypoint must not register the new model/table/routes. Any future live decision-table migration requires a separate reviewed plan. No owner decision is recorded by the assistant.

## Local verification checkpoint

Run from backend/: `python -m pytest -q`, with repository scripts/ present. The accepted r2 synthetic prototype passed222checks and2optional skips, independently reproduced with7probes. It adds no deployed routes or metadata. Actual local synthetic Chromium320/390/1280 lifecycle confirm/reload/changed source/needs review/dismiss/reload/source passed; controls have separated touch targets and status says needs review. Subsequent migration and guarded-identity documents are design only and not part of that source acceptance. No actual-host Action Inbox activation or real-message scan occurred.
