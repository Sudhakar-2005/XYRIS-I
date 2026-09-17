"""
JARVIS Gmail plugin - read, search, send, and manage email via the Gmail API.

Requires:
  - plugins/client_secret.json  (Google OAuth Desktop App credentials)
  - First use opens a browser for Google consent; token is cached after that.
"""
from __future__ import annotations

import base64
import json
import sys
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

_PLUGINS_DIR = Path(__file__).resolve().parent
if str(_PLUGINS_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_DIR))

from _google_core import get_service, credentials_valid, revoke_token

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
_SERVICE_CACHE: Optional[object] = None


def _get_service():
    global _SERVICE_CACHE
    if _SERVICE_CACHE is not None:
        return _SERVICE_CACHE
    _SERVICE_CACHE = get_service("gmail", "v1", _SCOPES, namespace="gmail")
    return _SERVICE_CACHE


def _headers(headers: list[dict]) -> dict:
    return {h["name"].lower(): h["value"] for h in headers}


def _snippet(msg: dict) -> str:
    hdrs = _headers(msg.get("payload", {}).get("headers", []))
    subject = hdrs.get("subject", "(no subject)")
    sender = hdrs.get("from", "unknown")
    date = hdrs.get("date", "")
    snip = msg.get("snippet", "")
    return f"{sender} -- {subject} [{date}]: {snip}"


def _decode_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    for part in payload.get("parts", []):
        result = _decode_body(part)
        if result:
            return result
    return ""


def _parse_max(params: dict) -> int:
    try:
        return max(1, min(50, int(params.get("max_results", 10))))
    except (TypeError, ValueError):
        return 10


PLUGIN = {
    "name": "gmail",
    "description": (
        "Gmail email actions. Use this to read, search, send, or manage emails. "
        "Trigger phrases: 'check my email', 'read my emails', 'search email from [person]', "
        "'send an email to [person]', 'how many unread emails', 'read email about [subject]', "
        "'mark email as read', 'delete email'. Do NOT use send_message for email - "
        "this tool handles all Gmail operations."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The email action to perform: "
                    "'list' (recent/unread emails), "
                    "'search' (find emails by query), "
                    "'read' (read a specific email by ID), "
                    "'send' (compose and send an email), "
                    "'unread_count' (count unread messages), "
                    "'labels' (list Gmail labels), "
                    "'mark_read' (mark an email as read by ID), "
                    "'delete' (move an email to trash by ID)."
                ),
            },
            "query": {
                "type": "STRING",
                "description": "Search query for 'search' action (Gmail search syntax, e.g. 'from:alice subject:meeting').",
            },
            "message_id": {
                "type": "STRING",
                "description": "Gmail message ID for 'read', 'mark_read', or 'delete' actions.",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address for 'send' action.",
            },
            "subject": {
                "type": "STRING",
                "description": "Email subject line for 'send' action.",
            },
            "body": {
                "type": "STRING",
                "description": "Email body text for 'send' action.",
            },
            "max_results": {
                "type": "STRING",
                "description": "Maximum number of results for 'list' or 'search' (default: 10).",
            },
        },
        "required": ["action"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").strip().lower()

    if action == "status":
        return _handle_status()

    svc = _get_service()
    if svc is None:
        return (
            "Gmail is not connected yet. Please place your Google OAuth "
            "client_secret.json in the plugins/ folder, then ask me to "
            "check email - I will guide you through the first-time login."
        )

    try:
        if action == "list":
            return _handle_list(svc, parameters)
        elif action == "search":
            return _handle_search(svc, parameters)
        elif action == "read":
            return _handle_read(svc, parameters)
        elif action == "send":
            return _handle_send(svc, parameters)
        elif action == "unread_count":
            return _handle_unread_count(svc)
        elif action == "labels":
            return _handle_labels(svc)
        elif action == "mark_read":
            return _handle_mark_read(svc, parameters)
        elif action == "delete":
            return _handle_delete(svc, parameters)
        else:
            return (
                f"Unknown Gmail action '{action}'. "
                "Use: list, search, read, send, unread_count, labels, mark_read, or delete."
            )
    except Exception as e:
        return f"Gmail action failed: {e}"


def _handle_status() -> str:
    if credentials_valid("gmail"):
        return "Gmail is connected and ready."
    return "Gmail is not connected yet. Place client_secret.json in plugins/ and ask me to check email."


def _handle_list(svc, params: dict) -> str:
    max_results = _parse_max(params)
    query = params.get("query", "").strip() or "in:inbox"
    if "in:" not in query and "label:" not in query:
        query = f"in:inbox {query}".strip()

    result = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    messages = result.get("messages", [])
    if not messages:
        return "No emails found matching that query."

    lines = []
    for i, msg_ref in enumerate(messages, 1):
        msg = svc.users().messages().get(
            userId="me", id=msg_ref["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        lines.append(f"{i}. {_snippet(msg)}")

    header = f"Found {len(messages)} email(s):" if len(messages) < max_results else f"Top {len(messages)} emails:"
    return header + "\n" + "\n".join(lines)


def _handle_search(svc, params: dict) -> str:
    query = params.get("query", "").strip()
    if not query:
        return "Please provide a search query (e.g. 'from:alice', 'subject:meeting')."
    return _handle_list(svc, {**params, "query": query})


def _handle_read(svc, params: dict) -> str:
    msg_id = params.get("message_id", "").strip()
    if not msg_id:
        return "Please provide a message_id to read. Use 'list' or 'search' first to find it."

    msg = svc.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    hdrs = _headers(msg.get("payload", {}).get("headers", []))
    subject = hdrs.get("subject", "(no subject)")
    sender = hdrs.get("from", "unknown")
    date = hdrs.get("date", "")
    body = _decode_body(msg.get("payload", {}))
    if len(body) > 3000:
        body = body[:3000] + "\n... (truncated)"

    labels = msg.get("labelIds", [])
    label_str = ", ".join(labels) if labels else "none"

    return (
        f"From: {sender}\n"
        f"Subject: {subject}\n"
        f"Date: {date}\n"
        f"Labels: {label_str}\n"
        f"ID: {msg_id}\n\n"
        f"{body}"
    )


def _handle_send(svc, params: dict) -> str:
    to = params.get("to", "").strip()
    subject = params.get("subject", "").strip()
    body = params.get("body", "").strip()

    if not to:
        return "Please provide a recipient email address (the 'to' field)."
    if not subject:
        return "Please provide an email subject."
    if not body:
        return "Please provide the email body text."

    message = MIMEText(body)
    message["to"] = to
    message["subject"] = subject

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    sent = svc.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()

    return f"Email sent to {to} (subject: {subject}). Message ID: {sent['id']}"


def _handle_unread_count(svc) -> str:
    result = svc.users().messages().list(
        userId="me", q="is:unread in:inbox", maxResults=1
    ).execute()
    total = result.get("resultSizeEstimate", 0)
    if total == 0:
        return "You have no unread emails."
    return f"You have {total} unread email(s)."


def _handle_labels(svc) -> str:
    result = svc.users().labels().list(userId="me").execute()
    labels = result.get("labels", [])
    if not labels:
        return "No labels found."
    lines = [f"  {l['name']} ({l['id']})" for l in labels[:20]]
    return f"Gmail labels ({len(labels)} total):\n" + "\n".join(lines)


def _handle_mark_read(svc, params: dict) -> str:
    msg_id = params.get("message_id", "").strip()
    if not msg_id:
        return "Please provide a message_id to mark as read."
    svc.users().messages().modify(
        userId="me", id=msg_id,
        body={"removeLabelIds": ["UNREAD"]}
    ).execute()
    return f"Email {msg_id} marked as read."


def _handle_delete(svc, params: dict) -> str:
    msg_id = params.get("message_id", "").strip()
    if not msg_id:
        return "Please provide a message_id to delete."
    svc.users().messages().trash(userId="me", id=msg_id).execute()
    return f"Email {msg_id} moved to trash."
