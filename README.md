# MailPilot

A read-only email workspace with explainable classification and an experimental, private-model advisory layer.

MailPilot turns stored Gmail messages into a searchable inbox, source-linked thread reviews and a bounded sample briefing. The project explores a practical question: how can an email assistant add useful intelligence without giving a language model control of the mailbox?

**Status (18 November 2026):** working public fictional sample plus a restricted owner-private Gmail reader. The configured tester can read current Inbox pages, browse older conversations, search Gmail, open plain-text threads and refresh deliberately. Actual Gmail reads and token refresh passed on the encrypted ARM-hosted release using the existing same-account Google session. This release did not force a new Google sign-in. AI and background/full-mailbox sync stay off. Sending, deleting, relabelling and general Gmail signup are not enabled. HTML-only messages and attachment downloads are not supported yet.

## Open the website

- [Try the fictional sample](https://mailpilot-workspace.duckdns.org/): no login, download or local setup. Sample decisions stay in that browser.
- [Open the private current Gmail reader](https://mailpilot-workspace.duckdns.org/mail): Google sign-in is required and restricted to the configured tester. The [older stored workspace](https://mailpilot-workspace.duckdns.org/workspace) remains available. [Direct sign-in](https://mailpilot-workspace.duckdns.org/login) is also available. Other visitors cannot connect their own Gmail yet.

The public sample is not evidence of real-mail actions. Connecting a mailbox does not publish its contents. Google Testing status, verification and any required security assessment remain separate release gates.

## Current source and deployment

The editable source includes the current reader from reviewed deployment revision `9ef2c1b1753d733fb5043963b125c82d4dee03c1`, layered on the earlier stored pilot. The separately retained source-only ZIP is a versioned snapshot, not the application delivery method. The live private release uses a source-hash-verified launcher and `app.private_reader_entrypoint_candidate.create_reader_from_environment`, with the same pinned SQLCipher runtime, explicit schema version, owner allowlist and readonly scopes. Candidate names are historical source-module names, not a claim that all experimental routes are live.

The reader fetches current data on navigation/refresh and does not persist new mail or write refreshed credentials. A new access token is held only for that page request, which adds token-endpoint latency and traffic. Signed page continuations expire and bind the account/search/folder but are not encrypted. Plain-text bodies are escaped; remote HTML/images are not loaded. HTTP byte/time checks are bounded but do not hard-cancel DNS or a stalled stream. Existing-session compatibility, Inbox/older/search/thread/refresh and old stored-workspace compatibility were checked against the actual private deployment. General-user verification and policy gates remain separate.

The private pilot uses an encrypted active database and separately encrypted OAuth token values. The original plaintext database remains as a restricted historical rollback artifact; it has not been erased. After new writes, restoring that old file would lose state and is not a valid automatic rollback. No backup-erasure, retention, recovery-time or availability guarantee is made.

## What works

| Capability | Current boundary |
| --- | --- |
| Current private reader | Current-provider Inbox/Starred/Sent/All mail, custom labels, Gmail-wide search, older pages, plain-text threads and deliberate refresh; readonly owner pilot |
| Gmail OAuth and ingestion | Read-only scope; encrypted OAuth tokens; message deduplication |
| Local stored inbox (`app.main`) | Account selector, literal search, category/priority filters, cursor pagination and escaped plain-text detail |
| Google-only stored inbox (`app.google_main`) | Subject-bound literal search, category/priority filters and older stored-record pages; qualified on the configured owner pilot |
| Explainable classification | Deterministic header/label rules remain authoritative; no model required |
| Thread review | Sample-only extractive highlights and literal request/deadline candidates with source offsets, not inferred commitments |
| Sample briefing | Read-only evidence inventory, with source links and explicit scan limits; no scheduled delivery |
| Private AI suggestions | Default off; explicitly marked synthetic messages only; bounded worker, strict output validation and separate advisory fields |
| Hosted Google sign-in | Verified immutable Google subject, signed Secure/HttpOnly sessions and subject-bound own-mail routes; no separate user access key |

## Try it without Google credentials

Python 3.12 on Linux is the tested reference environment. The synthetic demo uses SQLite and makes no Google or model calls.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-lock-py312-linux.txt
export DATABASE_URL=sqlite:////tmp/mailpilot-demo.db
export LLM_REFINE=0
python scripts/seed_sample_data.py
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/inbox` or `http://127.0.0.1:8000/dashboard`. All seeded messages carry SAMPLE labels. Use a new database path for a fresh demo; the seed command adds records on each run. Keep the server bound to loopback.

![Stored inbox with synthetic messages](docs/3-sample-inbox.png)

*Screenshot from the local synthetic demo. These are fabricated messages, not a connected inbox.*

## Architecture

```text
Gmail API (read-only OAuth)
          |
          v
FastAPI ingestion -> SQLAlchemy storage -> deterministic classification
          |                                   |
          +----------> stored inbox <---------+
                         |
                         +-> sample thread review / evidence inventory
                         |
                         +-> sample-only job queue -> private model endpoint
                                                       |
                                          validated advisory fields only
```

Python, FastAPI, SQLAlchemy and server-rendered HTML. The local synthetic demo uses SQLite; the current hosted private pilot uses SQLCipher-encrypted SQLite. PostgreSQL is an intended later deployment choice, not the live database. A separate CLI worker handles optional inference outside HTTP requests. LangGraph and a React frontend are roadmap choices, not installed capabilities.

## Engineering decisions

- **Rules before models.** Model suggestions never overwrite the baseline category or priority and have no mail-action sink.
- **Evidence before claims.** Sample thread candidates link back to exact text; scan limits and omissions are visible. A phrase match is not a verified deadline or accepted task.
- **Fail closed.** Wrong account/message pairs, mixed real/sample review data, invalid model responses and inconsistent hosted configuration are rejected.
- **Bounded inference.** Persisted claims, per-attempt limits, retries/backoff and per-row commits keep slow or interrupted model work separate from ingestion.
- **Honest security boundaries.** The current active private database and OAuth tokens are encrypted. This does not protect against an authorized running server, erase the retained original plaintext file, or establish encrypted backups. Host access and secret custody remain deployment requirements.

## Test

```bash
cd backend
pytest -q
```

The earlier Google-only inbox revision passed **190 tests, with 2 optional integration tests skipped** on the actual ARM64 Python 3.12.3 host. Optional PostgreSQL integration tests were not run in that qualification. Tests cover ingestion, classification, inbox scoping, source offsets, sample-only gates, worker recovery, strict model parsing and owner-session/Origin/Host boundaries. These are functional tests, not a security audit or a general model-accuracy claim.

The current sign-in entry fix passed 32 focused private-composition and integrated-route tests on Python 3.12, including two new regressions. These use fictional fixtures and do not establish native encryption. Separate native ARM cipher/conversion checks and the actual configured-tester browser journey qualified the current private release. Full test counts from older revisions must not be treated as a fresh full-suite run of this revision.

Earlier disposable Chromium checks covered synthetic OAuth cookie mechanics. The hosted Google-only pilot was then checked in an actual browser: real Google OpenID identity plus read-only Gmail consent, a five-message same-mailbox migration dry run, atomic subject binding preserving existing mail/ciphertext, flags-off normal sign-in, service-restart session persistence and logout. This is one configured owner, not two-real-account tenant acceptance or a security audit.

## Hosted Google-only pilot

The current hosted composition is the explicit `app.private_entrypoint_candidate.create_from_environment` factory, launched only after source/runtime custody and storage validation. `app.google_main:app` is an earlier composition, not the current live entrypoint. It offers a Google sign-in button with no user-entered access key. Only the configured owner is currently enabled. OAuth requests use OpenID identity/email and `gmail.readonly`, never send/delete scopes. Mailbox ownership comes from the verified immutable Google subject, not an address supplied by the browser. Legacy shared account-selector/API routes are absent from this entrypoint. The current composition has no import or provider-refresh route. Its five messages were imported earlier; connecting does not import new messages or imply a complete or continuously synced inbox.

The one-off migration is disabled by default and was removed after the owner binding. It is not a general email-matching account-claim feature. See [Google-only hosting notes](docs/GOOGLE-ONLY-HOSTING.md) for boundaries and actual qualification.

The current `/workspace` provides literal search, category/priority filters and stored conversation detail, deriving the account only from the verified subject. `/app` redirects to that workspace. The earlier hosted inbox added stored-ID pagination to `/app`. The reviewed revision passed 190 tests with 2 optional skips on the actual ARM64 Python 3.12.3 host, separately reproduced on Python 3.10. Actual owner-session search, filters, older pages and detail were checked after restart without another import; stored mail and mailbox binding stayed unchanged.

## Connect Gmail locally

Copy `.env.example` to `.env`, set the database credentials and configure the Google OAuth client, exact callback and token-encryption key. The detailed [engineering notes](docs/ENGINEERING.md#setup) describe the local flow. Use only the `gmail.readonly` scope. Do not commit `.env`, database files, credentials or message content.

Before putting this on the internet, follow [hosted access requirements](docs/HOSTED-ACCESS.md). The source hardening does not make an unconfigured server safe to expose.

## Limitations and next work

- Single-owner application, not a multi-tenant service.
- Real mail can be read in the stored inbox, but AI, thread-review and briefing experiments stay synthetic-only.
- No verified contextual summarisation, automatic replies, drafts, task scheduling or daily briefing delivery.
- Model output can be semantically manipulated even when its JSON is valid. Validation is not factual verification.
- CPU model latency and evidence capacity remain under evaluation. Tiny synthetic examples do not establish production accuracy or RAG suitability.
- The hosted HTTPS owner pilot is running, but there is no automatic backup/restore plan or availability guarantee. Certificate renewal is configured, not yet observed. Google testing mode and the exact owner allowlist remain; broad Google release is not enabled.

[Detailed implementation and limits](docs/ENGINEERING.md) · [Hosted access checklist](docs/HOSTED-ACCESS.md)

## License

MIT. See [LICENSE](LICENSE).
