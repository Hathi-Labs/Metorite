"""The rows of one mail in a Gmail mailbox and an Outlook mailbox (WS-17 EM-G9).

Spec: ``project-docs/specs/email_app_master_plan.md`` §12.3.11. Each row comes
from the REAL read of its provider, never from a row built by hand:

- Gmail: ``GmailProvider.get_message``, the one caller of the parse, over an
  EM-G2 fixture in ``tests/unit/fixtures/gmail/``.
- Outlook: ``OutlookProvider.list_messages``, the page read of the sweep, over
  one Graph message. Its ``internetMessageId`` holds the Message-ID as Graph
  gives it: as it came, with its angle brackets and its case.

``automation/identity.py`` compares ``internet_message_id`` with ``=``. So a
pair works only when the two parses give one value for one mail, and only
these two reads can show that.

``httpx.MockTransport`` answers each request, so no request leaves the
process. ``write`` stores a message through ``persist.upsert_message``, the
one ingest write, with the reclaim flag of its provider (D-EM-34).

Not named ``test_*``, so pytest imports it without collecting it.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import httpx
from email_ingestion.persist import upsert_message
from email_ingestion.providers.base import EmailMessage
from email_ingestion.providers.gmail import GMAIL_API_BASE, GmailProvider
from email_ingestion.providers.outlook import GRAPH_API_BASE, OutlookProvider
from gateway.routes.email import core
from sqlalchemy import text

GMAIL_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gmail"

#: Fixture (a): Ravi writes to Asha. One plain Message-ID.
INBOUND = "a_mixed_alternative_pdf.json"
#: Fixture (d): Asha writes to Ravi. The header name and the value are in
#: mixed case.
MIXED_CASE = "d_message_id_mixed_case.json"

#: The Message-ID of each fixture, written as Graph gives it. It is a literal
#: on purpose: a value taken from the Gmail parse agrees with each defect of
#: that parse, so the pair would prove nothing.
GRAPH_MESSAGE_ID = {
    INBOUND: "<CAKx7Qm1+A9zBq=Ef4@mail.example.org>",
    MIXED_CASE: "<5F2C9A1B-77D3-4E0A-9C1B-AbCdEf012345@example.com>",
}

#: The sender and the one recipient of each fixture, as the From and To
#: headers name them. The Outlook copy of the same mail names the same two.
ASHA = "asha@example.com"
RAVI = "ravi@example.org"
PARTIES = {INBOUND: (RAVI, ASHA), MIXED_CASE: (ASHA, RAVI)}

#: The time of each fixture (``internalDate``), as Graph writes a time.
GRAPH_RECEIVED = {
    INBOUND: "2024-09-30T08:02:01Z",
    MIXED_CASE: "2024-10-03T07:41:09Z",
}


def gmail_raw(name: str) -> dict[str, Any]:
    """One fixture, as ``users.messages.get`` gives it with ``format=full``."""
    return json.loads((GMAIL_FIXTURES / name).read_text(encoding="utf-8"))


async def gmail_read(name: str, *, pmid: str, labels: list[str],
                     thread: str | None = None,
                     internal_date: int | None = None) -> EmailMessage:
    """The real Gmail read of one fixture, under a Gmail id of its own.

    ``labels`` replaces the label list, so one fixture can be the Sent copy,
    the Inbox copy or a draft. ``thread`` is the Gmail thread id, and the id
    of the message by default. ``internal_date`` is in epoch milliseconds."""
    raw = gmail_raw(name)
    raw["id"] = pmid
    raw["threadId"] = thread or pmid
    raw["labelIds"] = labels
    if internal_date is not None:
        raw["internalDate"] = str(internal_date)

    def _answer(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/me/labels"):
            return httpx.Response(200, json={"labels": []})
        if (path.endswith(f"/users/me/messages/{pmid}")
                and request.url.params.get("format") == "full"):
            return httpx.Response(200, json=raw)
        return httpx.Response(404, json={"error": f"unexpected GET {path}"})

    provider = GmailProvider({"access_token": "x", "refresh_token": "y"})
    provider._http = httpx.AsyncClient(
        base_url=GMAIL_API_BASE, transport=httpx.MockTransport(_answer))
    try:
        return await provider.get_message(pmid)
    finally:
        await provider._http.aclose()


def graph_message(pmid: str, *, imid: str | None, sender: str,
                  to: Iterable[str], thread: str, received: str,
                  subject: str = "Signed quote") -> dict[str, Any]:
    """One Graph message with the fields of ``_MESSAGE_SELECT``.

    ``parentFolderId`` is an opaque id, as Graph sends it. The page read
    replaces it with the folder that it reads."""
    return {
        "id": pmid,
        "internetMessageId": imid,
        "subject": subject,
        "from": {"emailAddress": {"name": "", "address": sender}},
        "toRecipients": [{"emailAddress": {"name": "", "address": a}} for a in to],
        "ccRecipients": [],
        "bccRecipients": [],
        "receivedDateTime": received,
        "isRead": False,
        "parentFolderId": "AAMkAGI2-opaque-folder-id",
        "conversationId": thread,
        "importance": "normal",
        "hasAttachments": False,
        "bodyPreview": "Confirmed.",
        "body": {"contentType": "text", "content": "Confirmed."},
        "categories": [],
        "flag": {"flagStatus": "notFlagged"},
    }


async def outlook_read(raw: dict[str, Any], *, folder: str) -> EmailMessage:
    """The real Outlook read of one Graph message: one page of ``folder``."""
    want = f"/mailFolders/{folder}/messages"

    def _answer(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(want):
            return httpx.Response(200, json={"value": [raw]})
        return httpx.Response(404, json={"error": f"unexpected GET {request.url.path}"})

    provider = OutlookProvider({"access_token": "x", "refresh_token": "y"})
    provider._http = httpx.AsyncClient(
        base_url=GRAPH_API_BASE, transport=httpx.MockTransport(_answer))
    try:
        [msg], _next = await provider.list_messages(folder=folder)
    finally:
        await provider._http.aclose()
    return msg


async def write(account_id: str, msg: EmailMessage,
                provider: type[GmailProvider] | type[OutlookProvider]) -> None:
    """Store ``msg`` as one sync does, in the tenant that the caller bound."""
    async with core._tenant_session() as db:
        await upsert_message(db, account_id, msg,
                             reclaim=provider.REKEYS_MESSAGE_IDS)


def row_id(admin: Any, account_id: str, pmid: str) -> str:
    """The id of the row of ``pmid`` in the mailbox ``account_id``."""
    with admin.connect() as c:
        return str(c.execute(text(
            "SELECT id FROM email_messages WHERE account_id = CAST(:a AS uuid) "
            "AND provider_message_id = :p"), {"a": account_id, "p": pmid}).scalar_one())
