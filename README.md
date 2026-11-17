# MailPilot

A personal AI email management agent. MailPilot manages the inbox rather than
merely processing emails: it ingests Gmail, understands and classifies
messages, tracks follow-ups and commitments, drafts responses, and escalates
anything important to a human for approval.

Architecture: standalone application (Python/FastAPI + LangGraph + PostgreSQL)
with a planned Instinct agent integration. Autonomy model: hybrid - routine
work is automatic; important replies, commitments and sensitive actions
require explicit approval.

## Phase 1 - Gmail Foundation (this scaffold)

- Google OAuth (Gmail read-only scope only)
- Gmail message and thread retrieval
- PostgreSQL persistence with deduplication (no duplicate emails)
- FastAPI endpoints for auth, ingestion and reading stored mail
- Basic inbox dashboard at `/dashboard` (served by FastAPI; the React + Vite
  frontend from the plan arrives in a later phase)
- Docker Compose for the database

Phase 1 is strictly read-only. No sending, deleting, or label changes.

## Phase 2 - Email Intelligence (started)

- Deterministic baseline classification on ingest: every stored email gets a
  `category` (receipt, newsletter, notification, promotions, social, updates,
  forums, personal) and a `priority` (high, normal, low) from transparent,
  auditable rules over headers and Gmail labels - no LLM required
- `POST /classify/{account_id}` backfills older rows (idempotent)
- These categories and priorities are rule-based suggestions only: they never
  trigger any mail action (Phase 1/2 stays read-only), and the LLM-refined
  second opinion stays separate once a private model endpoint is configured
  (LLM_BASE_URL/LLM_MODEL)
- Known limitation: emails ingested before the header-sourcing parser fix
  (2026-11-17) were stored with stripped headers; backfill classifies them
  from subject/sender/labels only and never silently re-fetches from Gmail

## Phase 3 - private AI suggestions (experimental, sample-only)

Default off: `LLM_REFINE=0`. All Gmail access remains read-only. The supported
AI path only selects records explicitly marked `is_sample=True`, so turning
it on does not send an existing real inbox to the model.

1. Configure `LLM_BASE_URL` with the private gateway's OpenAI-compatible URL
   and `LLM_MODEL` with its installed model name. Access uses an authenticated
   SSH tunnel; never expose Ollama or the gateway to the public internet.
2. With `LLM_REFINE=1`, `POST /refine/{account_id}?limit=25` persists sample
   jobs only. It never runs inference in an HTTP request. `POST /classify`
   stays deterministic and backfills at most1000 rows per request.
3. From the repository root, run the Linux/Unix worker as a separate process:

       python scripts/refine_pending.py --account-id 1 --limit 25 --wall-seconds 90

   This runs one slice and exits. No recurring worker or automatic model
   calls are installed. Queue state persists across process restarts.

Job semantics:
- Separate AI state, timestamps, model and prompt version leave authoritative
  `category`/`priority`/`classification_source=rules` unchanged. Green badges
  show the baseline; amber badges show category AND priority disagreements.
- Queue claims use atomic compare-and-set; each outcome commits separately.
  Oldest-attempt-first selection and60/120/180second backoff prevent a bad
  newest email from starving older work. Three failed/invalid attempts is
  the ceiling. Interrupted running claims recover after120seconds; a final-
  attempt crash becomes exhausted without another inference call.
- Status distinguishes pending/running/agreement/suggested/invalid/failed.
  Model/prompt provenance is persisted at claim, including invalid/failed
  attempts. These fields describe the latest attempt, not an audit history.
  Disabled/unconfigured inference is not successful work and remains pending.
  A local lock prevents overlapping CLI workers on the same host; the shared
  private gateway rejects concurrent inference with429. This is not yet a
  distributed multi-project/tenant-aware queue.
- Worker ceiling:100jobs/90seconds per run,60seconds per inference attempt,
  hard Unix alarms, per-row commits. Ingestion never depends on inference.
- Whole-response JSON is required: exactly string category/priority values
  from the allowed vocabularies. Prose/fences/arrays/nested values/duplicate
  keys/trailing content are invalid. Errors, truncated outputs and malformed
  responses never alter the baseline.
- Every input field is capped (subject300/sender200/labels10x50/snippet500),
  AND the combined system+user messages must fit1400UTF8bytes. Oversized
  requests are rejected, not silently truncated to fit the pilot. Output is
  capped at128tokens,64KiB transport bytes and4000content characters. Model
  name, completion reason and assistant role must match the expected contract.
- The private gateway pilot uses2048context, a1400UTF8byte message budget,
  reported-context checks and one active request. These conservative bounds
  do not prove exact tokenisation or long-context RAG suitability. Gateway
  readiness is not model readiness; cold model loads can still be slow.
- Prompt boundaries and output validation do NOT prevent semantic prompt
  manipulation. An attacker can influence a valid-vocabulary suggestion.
  Separate advisory storage and no mail-action sink contain the effects.

Evaluation: `python scripts/evaluate_classification.py` scores12 synthetic
fixtures offline, including an injection-shaped case (11/12 rule matches in
our initial run). `--llm` also requires `MAILPILOT_TEST_LLM_URL`; its default
includes all12 cases. This is a small harness, not a representative accuracy
claim. Proposed accuracy/recall/JSON-validity targets from planning remain
unproven. The actual updated client/gateway completed one synthetic security
email with the expected notification/high suggestion in15.91seconds. That
proves short-request compatibility only, not broad model quality or uptime.

The existing CPU-only14B Q4 model measured about3.8generated tokens/second
on a4core ARM64/24GB VM. Cold load/context/cache materially change elapsed
latency. Both installed variants returned the expected strings on a tiny
synthetic email/grounded-answer check; cache reuse prevents choosing a speed
winner from those timings. No smaller model or embedding model is benchmarked
or selected. Longer RAG evidence and1024token answers do not fit this pilot.

## Repository layout

    backend/
      app/
        main.py                  FastAPI application
        config.py                Environment configuration
        database.py              Engine / session / Base
        models.py                GmailAccount, Thread, Email
        integrations/gmail/
          oauth.py               Google OAuth flow (read-only)
          client.py              Gmail API client + message parsing
        services/ingestion.py    Inbox ingestion with deduplication
        api/routes.py            HTTP endpoints
      tests/                     Unit tests (no live credentials needed)
    docker-compose.yml
    .env.example

## Setup

    cp .env.example .env          # fill in Google OAuth credentials
    docker compose up -d db
    python -m venv .venv && source .venv/bin/activate
    pip install -r backend/requirements.txt
    cd backend && uvicorn app.main:app --reload

Google Cloud setup: create a project, enable the Gmail API, configure the
OAuth consent screen, and create an OAuth client (Web application) with
redirect URI `http://localhost:8000/auth/google/callback`. While the consent
screen is in Testing mode, add each connecting Google account under
APIs & Services -> OAuth consent screen -> Test users, or Google blocks the
login with "Access blocked". The Gmail API enablement can take a few minutes
to propagate; a 403 `accessNotConfigured` right after enabling just means
retry shortly.

Then open `http://localhost:8000/auth/google/login` to connect a Gmail
account, and `POST /ingest/{account_id}` to pull mail into PostgreSQL.

## Tests

    cd backend && pytest

## Roadmap

1. Gmail Foundation (this phase)
2. Email Intelligence (classification, priority, intent, summaries)
3. Autonomous operations (labels, drafts, approvals)
4. Persistent memory and follow-ups
5. Instinct integration
6. Executive assistant (daily briefings, analytics)

Security notes: emails are untrusted input; a deterministic policy engine -
not the LLM - authorizes actions. OAuth login state is stored server-side,
expires after 10 minutes, is single-use, and is bound to the initiating
browser with an HMAC-signed HttpOnly cookie (OAUTH_STATE_SECRET). The PKCE
code verifier is stored server-side with the state row (same expiry,
deleted on consume) so the token exchange can prove it initiated the
request. OAuth
tokens are stored per account, Fernet-encrypted at rest (TOKEN_ENCRYPTION_KEY
required; storage fails closed without it). LLM features (Phase 2) use a
config-driven OpenAI-compatible endpoint (LLM_BASE_URL/LLM_MODEL) intended
for a private VM or tunnel, never a public unauthenticated endpoint. The
dashboard labels seeded sample data with a banner and SAMPLE badges.

## Sample thread understanding (literal, read-only)

`GET /accounts/{account_id}/threads/{thread_id}/review` and the matching
`/dashboard/accounts/{account_id}/threads/{thread_id}` view provide a
synthetic-only thread review. Sample subjects in the inbox link to it.
Mixed real/sample threads, wrong account IDs and inconsistent message
ownership fail closed. This remains the single-owner access boundary, not a
multi-user account login or tenant system.

The view orders stored messages oldest first (unknown dates last), shows an
extractive overview from each message's first line, and lists narrow literal
request/deadline phrase matches. Each item links to its source message and
carries exact Unicode code-point offsets into its returned source text.
Candidates are unconfirmed untrusted text, including quoted replies and
signatures, not facts, accepted requests, promises or tasks. No intent is
inferred from sender identity. Relative/ambiguous date phrases are unresolved;
ISO date literals are checked for calendar validity but never scheduled.
No timezone, time of day or deadline is guessed. No match does not mean no
request/deadline exists. Conflicts are displayed in source, not adjudicated.

Bounds: first 50 messages, first8000 characters of body (or snippet fallback)
per message,30 candidate items; omitted/truncated evidence is disclosed. No
HTML from mail is executed. No LLM, network, persistence/migration, mail
mutation, reminder or autonomous workflow is involved in this review view.
This is evidence inspection toward Phase2, not contextual AI summarisation,
full intent/deadline extraction, follow-up tracking or the full product.

## Sample briefing API (evidence inventory only)

`GET /accounts/{account_id}/sample-briefing` composes a bounded read-only
inventory across sample threads. It never sends or schedules a briefing.
It returns stored rule-high-priority message counts (not a fresh importance
judgment), source-linked first-line highlights and unconfirmed phrase
candidates from the sample thread review. No dates are resolved, no reply
obligations inferred, and no follow-up tasks created. It is not a contextual
summary or a complete daily view.

The entire account's message/thread ownership and sample status is checked
before selecting output. Any real message, mixed ownership or orphaned
account message makes this synthetic route fail closed. Empty accounts
return an empty inventory. Existing global token/loopback protection still
applies; account IDs select data within the single-owner app, not user
identities in a multi-tenant service.

Bounds: 20 nonempty threads ordered by their actual latest stored message
(not the possibly stale thread timestamp), unknown timestamps last. Each
thread reuses the 50-message/8000-character review limits and includes at
most two first-line highlights and five phrase candidates. Counts describe
all account messages or the named full thread; omissions and source-scan
limits are separate explicit fields. Evidence offsets refer to source text
from that thread's review endpoint. No returned result claims freshness,
daily completeness, verified urgency or absence of omitted obligations.
No LLM, network call, persistent write or mail effect is involved.

The same accepted inventory is available at
`/dashboard/accounts/{account_id}/sample-briefing`. Eligible sample accounts
among the first 20 account IDs get a navigation link on `/dashboard`; a
missing navigation link is not an account-completeness claim. The page uses
the same API data, escaped excerpts, source anchors, scan limits and explicit
omission counts. Source links lead to the read-only sample thread review.
No delivery, refresh worker or daily automation is installed by this view.

### Reproducible test environment

Python 3.10 Linux reference lock:
`pip install -r backend/requirements-lock-py310-linux.txt`.
This exact dependency set was resolved into a new empty virtual environment
and passed 105 tests (two optional integrations skipped), with `pip check`
clean. It is a tested reference snapshot, not a claim that every platform,
Python version or future lower-bound combination was tested. New deployments
should regenerate and test a platform-specific lock before changing it.

The flexible `requirements.txt` now explicitly requires Starlette >=0.44.0:
its `TestClient(client=...)` option is used to verify remote access denial and
was introduced in that release (official notes:
https://starlette.dev/release-notes/). Resolver constraints from FastAPI still
apply. Merely forcing0.50 into an older incompatible FastAPI is not safe.

## Stored inbox workspace

`/inbox` lists up to 100 stored account records for the app's single owner.
Select one to open `/inbox/{account_id}`, with literal subject/sender/snippet
search, category/priority filters, older-record navigation and account-scoped
stored-message detail. A link from `/dashboard` opens the account selector.
These read-only surfaces work with already stored sample or real records;
adding them does not connect Google, ingest any message or enable AI.

APIs: `GET /accounts`, `GET /accounts/{account_id}/inbox` and
`GET /accounts/{account_id}/messages/{message_id}`. Inbox query parameters:
`q` (up to 200 characters), `category`, `priority`, `limit` (1-50, default 25),
and `before` (positive stored-record ID cursor). `%`, `_` and escape markers
are literal search characters, not wildcard syntax. Case folding follows
the database's collation and may differ across scripts/backends. Message
bodies and attachments are not searched. Search matches full stored snippets;
returned snippet previews are capped at 1000 characters with a flag.

Pagination uses stored record IDs descending, not sent/received date. It
is not a frozen mailbox snapshot: newly ingested records require returning
to the first page; concurrent deletions/changes can affect results. Changing
filters resets the page in the UI. The detail view shows escaped plain text,
not HTML, with no remote images; body preview max 32000 characters, flagged.
Source dates are timezone-unspecified stored values. Nothing indicates live
sync, complete mailbox coverage, confirmed urgency or a safe email sender.

Global owner access protection covers every route. Wrong account/message
pairs return 404; account IDs do not authenticate separate users. Legacy
single-owner overview endpoints remain available and are not multi-tenant
APIs. No token/OAuth payload is returned by the account list. No network,
LLM, mail mutation, draft, send or automatic follow-up is introduced.

Recipient previews are capped at 2000 characters with an explicit API flag
and visible warning. Real stored-source badges use separate styling from
red SAMPLE labels; neither authenticates the source or sender.
