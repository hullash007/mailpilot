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
redirect URI `http://localhost:8000/auth/google/callback`.

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
browser with an HMAC-signed HttpOnly cookie (OAUTH_STATE_SECRET). OAuth
tokens are stored per account, Fernet-encrypted at rest (TOKEN_ENCRYPTION_KEY
required; storage fails closed without it). LLM features (Phase 2) use a
config-driven OpenAI-compatible endpoint (LLM_BASE_URL/LLM_MODEL) intended
for a private VM or tunnel, never a public unauthenticated endpoint. The
dashboard labels seeded sample data with a banner and SAMPLE badges.
