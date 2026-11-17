"""Minimal Gmail API client plus message parsing.

Parsing helpers are pure functions so they can be unit-tested without
network access or credentials.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

_HEADER_KEYS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def extract_headers(payload: dict) -> dict:
    headers = {}
    for h in payload.get("headers", []):
        name = h.get("name", "")
        if name in _HEADER_KEYS:
            headers[name] = h.get("value", "")
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
