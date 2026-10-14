"""Parse helpers for a Gmail API message resource (WBS 1.3).

⚠️ The service-account Gmail path is gone (WS-17 EM-G11b, 2026-10-14). It
held a second set of Gmail scopes (``gmail.readonly`` and ``gmail.metadata``)
and the functions ``_service``, ``fetch_message`` and ``list_history``, which
had no caller outside tests. The Gmail app asks for one set of scopes only:
``GMAIL_SCOPES`` in ``email_ingestion/providers/gmail.py`` (D-EM-31). Fence:
``tests/unit/test_gmail_one_scope_set.py``.

What stays is the message projection and the parse helpers, which
``tests/unit/test_gmail_normaliser.py`` uses.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any


@dataclass
class GmailMessageRaw:
    """Lightweight projection of a single Gmail API message resource."""

    id: str
    thread_id: str
    label_ids: list[str]
    snippet: str
    payload: dict[str, Any]
    internal_date_ms: int


def _decode_b64url(data: str) -> str:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding).decode("utf-8", errors="replace")


def _extract_body(payload: dict[str, Any]) -> str:
    """Walk the MIME tree and return the best-effort plain-text body."""
    if (body := payload.get("body")) and body.get("data"):
        return _decode_b64url(body["data"])
    for part in payload.get("parts") or []:
        mime = part.get("mimeType") or ""
        if mime == "text/plain" and part.get("body", {}).get("data"):
            return _decode_b64url(part["body"]["data"])
    # second pass: text/html as fallback
    for part in payload.get("parts") or []:
        mime = part.get("mimeType") or ""
        if mime == "text/html" and part.get("body", {}).get("data"):
            return _decode_b64url(part["body"]["data"])
    return ""


def _header(payload: dict[str, Any], name: str) -> str:
    name_lc = name.lower()
    for h in payload.get("headers") or []:
        if (h.get("name") or "").lower() == name_lc:
            return h.get("value") or ""
    return ""


__all__ = ["GmailMessageRaw", "_decode_b64url", "_extract_body", "_header"]
