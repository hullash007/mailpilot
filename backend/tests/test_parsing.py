"""Unit tests for Gmail message parsing. No network or credentials needed."""
import base64

from app.integrations.gmail.client import extract_body_text, extract_headers, parse_message


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_extract_headers_keeps_known_fields():
    payload = {
        "headers": [
            {"name": "From", "value": "alice@example.com"},
            {"name": "Subject", "value": "Hello"},
            {"name": "X-Spam-Score", "value": "0.1"},
            {"name": "To", "value": "bob@example.com"},
        ]
    }
    headers = extract_headers(payload)
    assert headers == {"From": "alice@example.com", "Subject": "Hello", "To": "bob@example.com"}


def test_extract_headers_matches_names_case_insensitively():
    # Gmail returns header names in arbitrary case.
    payload = {
        "headers": [
            {"name": "from", "value": "alice@example.com"},
            {"name": "SUBJECT", "value": "Hello"},
            {"name": "list-unsubscribe", "value": "<mailto:leave@example.com>"},
        ]
    }
    headers = extract_headers(payload)
    assert headers == {
        "From": "alice@example.com",
        "Subject": "Hello",
        "List-Unsubscribe": "<mailto:leave@example.com>",
    }


def test_extract_headers_keeps_classification_signals():
    payload = {
        "headers": [
            {"name": "Auto-Submitted", "value": "auto-generated"},
            {"name": "Precedence", "value": "bulk"},
            {"name": "List-Unsubscribe", "value": "<mailto:leave@example.com>"},
        ]
    }
    headers = extract_headers(payload)
    assert headers == {
        "Auto-Submitted": "auto-generated",
        "Precedence": "bulk",
        "List-Unsubscribe": "<mailto:leave@example.com>",
    }


def test_extract_body_text_plain():
    payload = {"mimeType": "text/plain", "body": {"data": b64("Hello there")}}
    assert extract_body_text(payload) == "Hello there"


def test_extract_body_text_multipart_prefers_plain():
    payload = {
        "mimeType": "multipart/alternative",
        "body": {},
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>html body</p>")}},
        ],
    }
    assert extract_body_text(payload) == "plain body"


def test_parse_message_full():
    message = {
        "id": "msg1",
        "threadId": "thr1",
        "snippet": "snippet text",
        "internalDate": "1700000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "alice@example.com"},
                {"name": "To", "value": "bob@example.com"},
                {"name": "Subject", "value": "Test subject"},
            ],
            "body": {"data": b64("body content")},
        },
    }
    parsed = parse_message(message)
    assert parsed["gmail_message_id"] == "msg1"
    assert parsed["gmail_thread_id"] == "thr1"
    assert parsed["subject"] == "Test subject"
    assert parsed["sender"] == "alice@example.com"
    assert parsed["body_text"] == "body content"
    assert parsed["internal_date"] is not None
