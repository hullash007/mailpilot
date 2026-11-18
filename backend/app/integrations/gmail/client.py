"""Minimal Gmail API client plus message parsing.

Parsing helpers are pure functions so they can be unit-tested without
network access or credentials.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

_HEADER_KEYS = (
    "From", "To", "Cc", "Subject", "Date", "Message-ID",
    # Classification signals (services/classify.py) - dropped here before the
    # fix, which made real ingestion unable to classify.
    "List-Unsubscribe", "Precedence", "Auto-Submitted",
)
_HEADER_KEYS_BY_LOWER = {key.lower(): key for key in _HEADER_KEYS}


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def extract_headers(payload: dict) -> dict:
    # Gmail header names arrive in arbitrary case; match case-insensitively
    # and store under the canonical key so downstream .get("From") works.
    headers = {}
    for h in payload.get("headers", []):
        canonical = _HEADER_KEYS_BY_LOWER.get(str(h.get("name", "")).lower())
        if canonical:
            headers[canonical] = h.get("value", "")
    return headers


def extract_body_text(payload: dict) -> str:
    """Pull the best-effort plain-text body out of a message payload."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {}) or {}
    if mime == "text/plain" and body.get("data"):
        return _b64url_decode(body["data"])
    for part in payload.get("parts", []) or []:
        text = extract_body_text(part)
        if text:
            return text
    return ""


def parse_message(message: dict) -> dict:
    """Normalize a raw Gmail API message into a storage-ready dict."""
    payload = message.get("payload", {}) or {}
    headers = extract_headers(payload)
    internal_ms = message.get("internalDate")
    internal_date = (
        datetime.fromtimestamp(int(internal_ms) / 1000, tz=timezone.utc).replace(tzinfo=None)
        if internal_ms
        else None
    )
    to = headers.get("To", "")
    cc = headers.get("Cc", "")
    return {
        "gmail_message_id": message["id"],
        "gmail_thread_id": message.get("threadId", ""),
        "subject": headers.get("Subject", ""),
        "sender": headers.get("From", ""),
        "recipients": ", ".join(x for x in (to, cc) if x),
        "snippet": message.get("snippet", ""),
        "body_text": extract_body_text(payload),
        "headers": headers,
        "label_ids": message.get("labelIds", []) or [],
        "internal_date": internal_date,
    }


class GmailClient:
    """Thin wrapper around the Gmail REST API (read-only usage in Phase 1)."""

    def __init__(self, credentials_dict: dict):
        creds = Credentials(**credentials_dict)
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    def get_profile(self) -> dict:
        return self.service.users().getProfile(userId="me").execute()

    def list_message_ids(self, max_results: int = 20, query: str | None = None) -> list[str]:
        ids: list[str] = []
        page_token = None
        while len(ids) < max_results:
            resp = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    maxResults=min(100, max_results - len(ids)),
                    pageToken=page_token,
                    q=query,
                )
                .execute()
            )
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def get_message(self, message_id: str) -> dict:
        raw = (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        return parse_message(raw)

    def get_thread(self, thread_id: str) -> list[dict]:
        raw = (
            self.service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        return [parse_message(m) for m in raw.get("messages", [])]

    def list_message_page(self, page_token: str | None = None, page_size: int = 25) -> dict:
        """One bounded page, excluding Spam/Trash; caller persists continuation."""
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError('Invalid Gmail page size')
        return self.service.users().messages().list(
            userId='me', maxResults=page_size, pageToken=page_token,
            includeSpamTrash=False,
        ).execute(num_retries=0)

    def list_history_page(self, start_history_id: str, page_token: str | None = None) -> dict:
        """One page; do not advance start_history_id between continuation pages."""
        if not start_history_id or not start_history_id.isdecimal():
            raise ValueError('Invalid Gmail history cursor')
        return self.service.users().history().list(
            userId='me', startHistoryId=start_history_id, pageToken=page_token,
            maxResults=100,
        ).execute(num_retries=0)


class GmailSyncClient(GmailClient):
    """Candidate-only bounded network transport, never selected by live app."""
    def __init__(self, credentials_dict: dict):
        import httplib2
        from google_auth_httplib2 import AuthorizedHttp
        creds = Credentials(**credentials_dict)
        self.http = httplib2.Http(timeout=20)
        self.service = build('gmail', 'v1', http=AuthorizedHttp(creds, http=self.http),
                             cache_discovery=False)

    def get_profile(self) -> dict:
        return self.service.users().getProfile(userId='me').execute(num_retries=0)

    def get_thread(self, thread_id: str) -> list[dict]:
        raw = self.service.users().threads().get(userId='me', id=thread_id, format='full').execute(num_retries=0)
        return [parse_message(m) for m in raw.get('messages', [])]


    def get_thread_snapshot(self, thread_id: str) -> dict:
        raw = self.service.users().threads().get(userId='me', id=thread_id, format='full').execute(num_retries=0)
        return {'history_id': raw['historyId'], 'messages': [parse_message(m) for m in raw.get('messages', [])]}


    def set_request_timeout(self, seconds: float):
        # httplib2 caches connections; update their socket timeout too.
        self.http.timeout = min(20, seconds)
        for connection in self.http.connections.values():
            connection.timeout = self.http.timeout
            if getattr(connection, 'sock', None):
                connection.sock.settimeout(self.http.timeout)
