"""Gmail API provider.

Uses the Gmail REST API with OAuth 2.0 authentication.
Supports service accounts (domain-wide delegation) and standard OAuth.

API reference: https://developers.google.com/gmail/api/reference/rest
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import random
import re
import secrets
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timezone
from email import encoders, message_from_bytes
from email import policy as mail_policy
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.message import Message
from email.mime.base import MIMEBase
from email.mime.message import MIMEMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses, parsedate_to_datetime
from typing import Any

import httpx

from .app_credentials import OAuthApp, token_fields
from .base import (
    Attachment,
    BaseEmailProvider,
    EmailAddress,
    EmailFolder,
    EmailMessage,
    EstimateCallback,
    ProviderMailTooLarge,
    ProviderRateLimited,
    RefreshingBearer,
    SyncResult,
    canonical_folder,
    find_unsubscribe_link_in_html,
    received_key,
)

logger = logging.getLogger(__name__)

# Gmail system label IDs → canonical folder keys (D-EM-33). A message can carry
# several labels, and the first match in this order wins. A message with none of
# them is archived mail, so it files as ``archive`` (GM-5, WS-17 EM-G2 item 7).
# A user label never sets the folder (O-GM-1). It lands in ``categories`` only.
_GMAIL_LABEL_TO_FOLDER = [
    ("TRASH", "trash"),
    ("SPAM", "junk"),
    ("DRAFT", "drafts"),
    ("SENT", "sent"),
    ("INBOX", "inbox"),
]

#: Gmail has no ``archive`` label. The Archive folder is the mail outside the
#: Inbox, Sent and Drafts (D-EM-33, GM-6). ``messages.list`` leaves out spam
#: and trash by default, so the query needs no ``-in:spam`` or ``-in:trash``.
GMAIL_ARCHIVE_QUERY = "-in:inbox -in:sent -in:drafts"


def _gmail_folder_from_labels(label_ids: list[str]) -> str:
    labels = set(label_ids or [])
    for label, folder in _GMAIL_LABEL_TO_FOLDER:
        if label in labels:
            return folder
    # ⚠️ Not ``inbox``. The old fallback showed archived mail in the Inbox,
    # and ``tests/unit/test_email_folders.py`` pins ``archive`` on purpose.
    return "archive"


def _is_archive_key(name: str | None) -> bool:
    """True when a folder name or a folder key means the Archive folder."""
    return bool(name) and canonical_folder(name) == "archive"


def _gmail_part_header(part: dict, name: str) -> str:
    """Case-insensitively read a MIME header value off a Gmail payload part."""
    target = name.lower()
    for h in part.get("headers", []) or []:
        if str(h.get("name", "")).lower() == target:
            return str(h.get("value", ""))
    return ""


def _gmail_part_is_inline(part: dict) -> bool:
    """True when a part is an inline body image (a ``cid:`` reference such as a
    signature logo or pasted screenshot), not a real downloadable attachment.

    Gmail flags these with ``Content-Disposition: inline``; some senders omit
    the disposition but still set a ``Content-ID``, which means the image is
    referenced from the HTML body rather than offered as a file. Either way it
    belongs in the body, not in the attachment list — counting them is what
    floods a one-line signature email with "3 attachments".
    """
    disposition = _gmail_part_header(part, "Content-Disposition").strip().lower()
    if disposition.startswith("inline"):
        return True
    if not disposition and _gmail_part_header(part, "Content-ID"):
        return True
    return False


def _iter_gmail_parts(
    part: dict, *, descend: Callable[[dict], bool] | None = None,
) -> Iterator[dict]:
    """Each part of a Gmail payload tree, at any depth, the root first.

    The ONE walk of the MIME tree. The attachment list and the body walk both
    use it. ``descend`` returns false for a part whose child parts the walk
    skips. With no ``descend``, the walk reads each part.
    """
    yield part
    if descend is not None and not descend(part):
        return
    for sub in part.get("parts", []) or []:
        yield from _iter_gmail_parts(sub, descend=descend)


def _collect_gmail_attachments(part: dict, msg_id: str) -> list[Attachment]:
    """Walk a Gmail payload tree and collect real (non-inline) attachments.

    Recurses through nested multiparts (``multipart/mixed`` →
    ``multipart/related`` → …) so attachments below the top level are found,
    and skips inline body images (see ``_gmail_part_is_inline``). A part counts
    as an attachment only when it has both a filename and a provider
    ``attachmentId`` (the handle used to download its bytes).
    """
    out: list[Attachment] = []
    for p in _iter_gmail_parts(part):
        body = p.get("body", {}) or {}
        att_id = body.get("attachmentId")
        filename = p.get("filename")
        if filename and att_id and not _gmail_part_is_inline(p):
            out.append(Attachment(
                id=f"{msg_id}_{att_id}",
                filename=filename,
                mime_type=p.get("mimeType", "application/octet-stream"),
                size_bytes=int(body.get("size", 0) or 0),
                provider_attachment_id=att_id,
            ))
    return out


def _gmail_part_is_file(part: dict) -> bool:
    """True when a part is a file and not the body of the mail.

    A file has a name, or its disposition is ``attachment``. An attached mail
    (``message/rfc822``) is a file too, so its body never becomes the body of
    the mail that carries it.
    """
    if part.get("filename"):
        return True
    if str(part.get("mimeType", "")).lower().startswith("message/"):
        return True
    disposition = _gmail_part_header(part, "Content-Disposition")
    return disposition.strip().lower().startswith("attachment")


def _gmail_part_charset(part: dict) -> str:
    """The ``charset`` of the ``Content-Type`` of a part, else UTF-8."""
    content_type = _gmail_part_header(part, "Content-Type")
    if content_type:
        holder = Message()
        holder["Content-Type"] = content_type
        charset = holder.get_content_charset()
        if charset:
            return charset
    return "utf-8"


def _decode_gmail_part(part: dict, data: str) -> str:
    """The text of one body part (WS-17 EM-G2 item 3).

    Gmail sends the bytes as base64url. The charset of the part decodes them,
    and UTF-8 decodes them when the part names no charset or an unknown one.
    The padding is added, because base64url can come without it.

    ⚠️ A sender picks the charset. ``idna``, ``punycode`` and ``undefined``
    are real codecs, and each raises ``UnicodeError`` even with
    ``errors="replace"``. A raise here fails the parse, and
    ``list_messages`` then skips the mail at each sync. So a codec that
    raises falls back to UTF-8 too (EM-G2 review round 1, P3a).
    """
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    try:
        return raw.decode(_gmail_part_charset(part), errors="replace")
    except (LookupError, UnicodeError):
        return raw.decode("utf-8", errors="replace")


def _gmail_bodies(payload: dict) -> tuple[str, str | None]:
    """The text body and the HTML body of a Gmail payload (EM-G2 items 1-3).

    The walk reads the tree at any depth, so it finds the
    ``multipart/alternative`` inside a ``multipart/mixed`` (GM-3). It takes the
    first ``text/plain`` part and the first ``text/html`` part that hold data.
    It skips each file and each part below a file. A single-part HTML mail
    fills only the HTML body, as Outlook does, and ``body_backfill`` derives
    the text later.
    """
    found: dict[str, str] = {}
    for part in _iter_gmail_parts(
            payload, descend=lambda p: not _gmail_part_is_file(p)):
        if _gmail_part_is_file(part):
            continue
        mime = str(part.get("mimeType", "")).lower()
        if mime not in ("text/plain", "text/html") or mime in found:
            continue
        data = (part.get("body") or {}).get("data")
        if data:
            found[mime] = _decode_gmail_part(part, data)
    return found.get("text/plain", ""), found.get("text/html")


def _gmail_message_id(headers: dict[str, str]) -> str | None:
    """The Message-ID in the form in which Graph gives ``internetMessageId``.

    The value is trimmed, and it keeps its angle brackets and its case
    (EM-G2 item 5). ``automation/identity.py`` compares the column with
    ``=``, so one mail in a Gmail and an Outlook mailbox must have one value.
    ``headers`` has lower-case keys (``_parse_headers``).
    """
    return headers.get("message-id", "").strip() or None


def _decode_display_name(name: str) -> str:
    """Decode the RFC 2047 encoded words of one display name.

    A name that does not decode stays as it came.
    """
    if "=?" not in name:
        return name
    try:
        return str(make_header(decode_header(name)))
    except (LookupError, ValueError, HeaderParseError):
        return name


def _split_addresses(header: str) -> list[tuple[str, str]]:
    """The ``(name, address)`` pairs of one raw address header.

    ``getaddresses`` runs with ``strict=False``. Since the fix of
    CVE-2023-27043 the strict parser refuses a whole header for one defect,
    so ``a@x.org, b@x.org,`` gave no address at all. The lenient parser keeps
    each real address. It also splits on ``;`` and keeps a quoted ``;``
    inside its name. A Python with no ``strict`` keyword parses leniently
    already (EM-G2 review round 1, P3b).
    """
    try:
        return getaddresses([header], strict=False)
    except TypeError:  # a Python from before the CVE-2023-27043 fix
        return getaddresses([header])


def _parse_list_unsubscribe(header: str) -> str | None:
    """Pick the best link from a List-Unsubscribe header.

    The header is a comma-separated list of <...> targets, e.g.
    ``<https://x.com/unsub?id=1>, <mailto:unsub@x.com>``. Prefer an https
    one-click URL; fall back to a mailto:. Returns None if neither is present.
    """
    if not header:
        return None
    targets: list[str] = []
    for part in header.split(","):
        part = part.strip()
        if part.startswith("<") and part.endswith(">"):
            part = part[1:-1].strip()
        if part:
            targets.append(part)
    for t in targets:
        if t.lower().startswith("http"):
            return t
    for t in targets:
        if t.lower().startswith("mailto:"):
            return t
    return targets[0] if targets else None


GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"
#: D-EM-31 (WS-17 EM-G7). The callback refuses a grant that lacks either one.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
]

# ── Rate limits (WS-17 EM-G4a, GM-9) ─────────────────────────────────────
# ``email_app_master_plan.md`` §12.3.5.1. Fence: test_gmail_rate_limits.py.

#: The tries of one request that Gmail refuses for a rate limit (item 9).
GMAIL_MAX_TRIES = 3
#: The longest single wait. A ``Retry-After`` that asks more ends the tries,
#: because a shorter wait gets one more refusal.
GMAIL_MAX_WAIT_SECS = 30.0
#: The sum of the waits of one client. One provider serves one sync cycle,
#: so a cycle waits this long at most for rate limits.
GMAIL_WAIT_BUDGET_SECS = 60.0
#: The first back-off when Gmail sends no ``Retry-After``. It doubles.
_GMAIL_BACKOFF_SECS = 1.0
#: The two reasons of a 403 that are a rate limit (item 8). Every other 403
#: is a refusal, such as a missing scope, and a second try cannot help it.
_RATE_LIMIT_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
#: The methods that RFC 9110 calls idempotent.
_REPEATABLE_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})
#: The POST actions that set a state, so a second try changes nothing.
_REPEATABLE_POST_ACTIONS = ("/modify", "/batchModify", "/trash", "/untrash")


class GmailRateLimited(httpx.HTTPStatusError, ProviderRateLimited):
    """Gmail refused a request for a rate limit, and the helper sends it no
    more (EM-G4a item 9). A sync that gets it fails, and the loop backs off.

    It is an ``httpx.HTTPStatusError``, so each caller that handles an HTTP
    failure handles it too. It is a ``ProviderRateLimited``, so the
    scheduler never degrades it (EM-G4b review round 1, F2)."""

    def __init__(self, response: httpx.Response, *, tries: int) -> None:
        request = response.request
        super().__init__(
            f"Gmail rate limit: {response.status_code} on {request.method} "
            f"{request.url.path} after {tries} tries",
            request=request, response=response)
        self.tries = tries


def _repeatable(request: httpx.Request) -> bool:
    """True when a second try of *request* cannot change the mailbox.

    Each answer of Google proves that the request reached Google, so a send
    must go once only. ``messages.send``, ``drafts.create``, ``drafts.send``
    and each other POST are not repeatable."""
    if request.method in _REPEATABLE_METHODS:
        return True
    return (request.method == "POST"
            and request.url.path.endswith(_REPEATABLE_POST_ACTIONS))


async def _is_rate_limit(response: httpx.Response) -> bool:
    """True for a 429, and for a 403 whose reason is a rate limit.

    A 5xx is not a rate limit (§12.3.5.1). It goes back to the caller."""
    if response.status_code == 429:
        return True
    if response.status_code != 403:
        return False
    await response.aread()
    try:
        errors = response.json()["error"]["errors"]
        return any(e.get("reason") in _RATE_LIMIT_REASONS for e in errors)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _retry_after(response: httpx.Response) -> float | None:
    """The seconds that ``Retry-After`` asks for, or None with no value.

    The value is a count of seconds or an HTTP date."""
    value = response.headers.get("Retry-After", "").strip()
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def _raise_rate_limit(exc: BaseException) -> None:
    """Raise *exc* again when it is a rate limit whose tries are spent.

    A sweep skips a label that fails, and the history path records a fetch
    that fails. A rate limit must fail the sync instead, so the loop backs
    off and the cursor stays (EM-G4a item 9)."""
    if isinstance(exc, GmailRateLimited):
        raise exc


async def _wait(seconds: float) -> None:
    """Wait for a rate limit. The tests replace it, so no test sleeps."""
    await asyncio.sleep(seconds)


# ── The history cursor (WS-17 EM-G4b, spec §12.3.5.2) ─────────────────────────

#: The history types that the cursor reads (GM-17).
_HISTORY_TYPES = ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"]
#: The history events that put a message into the fetch set (items 1 to 3). A
#: label event fetches the message in full, as an added message does, so the
#: upsert applies its new folder, read mark, star and labels.
_HISTORY_FETCH_EVENTS = ("messagesAdded", "labelsAdded", "labelsRemoved")
#: A fetch on the history path that a later cycle can fix (a 5xx or a
#: transport error) holds the cursor at its old position. The cursor counts
#: the cycles that it was held. On the cycle that reaches this count, it moves
#: on past each message that still fails (EM-G4a-f2, review round 1 F1).
GMAIL_FETCH_HOLD_CYCLES = 3
#: The name of the sweep after a stale cursor in ``catch_up_folders``. The
#: scheduler puts it into the ``sync_error`` note of a short catch-up.
GMAIL_RESET_SWEEP_NAME = "all mail"


class _StaleCursor(Exception):
    """``history.list`` answered 404. Gmail holds no history after the
    cursor, so the provider seeds again and sweeps (item 5)."""


def _is_history_id(text: str) -> bool:
    """True for a Gmail history id: ASCII digits only. ``str.isdigit`` also
    takes other digits, such as Arabic-Indic digits (review round 1 F5)."""
    return bool(text) and text.isascii() and text.isdigit()


def _parse_cursor(raw: Any) -> tuple[str, int] | None:
    """The history id of a stored cursor, and the cycles that it was held.

    The cursor is a plain history id, or JSON while a failed fetch holds it:
    ``{"held_cycles": <n>, "history_id": "<id>", "v": 1}``. Text that is
    neither is no cursor, so the sync seeds again and does not send text to
    Gmail that it refuses on each cycle."""
    text = str(raw or "").strip()
    if _is_history_id(text):
        return text, 0
    try:
        data = json.loads(text)
        history_id = str(data["history_id"])
        held_cycles = max(int(data.get("held_cycles", 0)), 0)
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return (history_id, held_cycles) if _is_history_id(history_id) else None


def _format_cursor(history_id: str, held_cycles: int) -> str:
    """The stored form of a cursor: the plain id, or JSON with the count of
    held cycles. It holds no message id, so its size never grows (review
    round 1 F7)."""
    if held_cycles <= 0:
        return history_id
    return json.dumps({"v": 1, "history_id": history_id,
                       "held_cycles": held_cycles},
                      sort_keys=True, separators=(",", ":"))


def _collect_history(
    records: list[dict[str, Any]], fetch: dict[str, None],
    deleted: dict[str, None],
) -> None:
    """Add the message ids of one history page to the fetch set and to the
    delete set. Dicts keep the order of the history."""
    for record in records:
        for event in _HISTORY_FETCH_EVENTS:
            for change in record.get(event) or []:
                mid = (change.get("message") or {}).get("id")
                if mid:
                    fetch[mid] = None
        for change in record.get("messagesDeleted") or []:
            mid = (change.get("message") or {}).get("id")
            if mid:
                deleted[mid] = None


def _deleted_marker(message_id: str) -> EmailMessage:
    """The ``[DELETED]`` marker of a message that Gmail deleted (item 4).
    ``scheduler._write_messages`` moves its row to trash, and deletes a row
    in ``drafts`` (E-B2)."""
    return EmailMessage(provider_message_id=message_id, thread_id=None,
                        folder="TRASH", labels=["TRASH"], subject="[DELETED]",
                        deletion_marker=True)


def _transient(exc: BaseException) -> bool:
    """True for a failed fetch that a later cycle can fix: a 5xx or a
    transport error (EM-G4a-f2). A 404 means that the message is gone, and
    a parse error or another 4xx stays the same, so none of them holds the
    cursor."""
    if isinstance(exc, httpx.TransportError):
        return True
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return isinstance(status, int) and status >= 500


def _cursor_after_read(
    start: str, last_id: str | None, held_cycles: int, failed: list[str],
    errors: list[str],
) -> str:
    """The cursor after one read of the history (item 2, EM-G4a-f2).

    With no failed fetch, it is the ``historyId`` of the last answer, and the
    count of held cycles goes back to 0. A fetch that a later cycle can fix
    keeps the OLD position, and the count goes up by 1. The count belongs to
    the cursor, not to a message, so a new failure on each cycle cannot hold
    the cursor longer (review round 1 F1). On the cycle that reaches
    ``GMAIL_FETCH_HOLD_CYCLES``, the cursor moves to the last answer. Each
    message that still fails gets a log line and a record in ``errors``,
    with its id only. A message that succeeds is not in them."""
    if not failed:
        return last_id or start
    cycles = held_cycles + 1
    if cycles < GMAIL_FETCH_HOLD_CYCLES:
        return _format_cursor(start, cycles)
    for mid in failed:
        logger.warning("gmail.fetch_abandoned message_id=%s held_cycles=%d",
                       mid, cycles)
        errors.append(f"gmail.fetch_abandoned id={mid} held_cycles={cycles}")
    return last_id or start


def _reset_sweep_after(
    catch_up: datetime | None, since: datetime | None,
) -> datetime | None:
    """How far back the sweep after a stale cursor reads (item 6): the
    catch-up watermark, or the floor with no watermark. The floor binds
    each sync (EM-T6a), so the later of the two wins."""
    dates = [d if d.tzinfo else d.replace(tzinfo=UTC)
             for d in (catch_up, since) if d is not None]
    return max(dates) if dates else None


# ── The import (WS-17 EM-G5a, spec §12.3.6.1) ────────────────────────────────
# Fence: tests/unit/test_gmail_import.py.

#: The fetches of one import page that run at once (item 4).
GMAIL_IMPORT_FETCHES = 10
#: ``messages.list`` gives 500 ids at most on one page.
_GMAIL_LIST_MAX = 500


def _utc(value: datetime) -> datetime:
    """*value* with a time zone. A naive value is read as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _import_query(since: datetime | None, until: datetime | None) -> str | None:
    """The ``q`` of the import list, in epoch seconds (items 1 and 3).

    ``after:`` is the floor. ``before:`` is the whole second of ``until``
    plus 1 second (E-G5-3), as ``_received_filter`` in ``outlook.py`` does.
    So a resume reads the second that it reached again, and the upsert makes
    the overlap harmless. A message of that second that is newer than
    ``until`` drops in ``_in_import_window``."""
    terms = []
    if since is not None:
        terms.append(f"after:{int(_utc(since).timestamp())}")
    if until is not None:
        terms.append(f"before:{int(_utc(until).timestamp()) + 1}")
    return " ".join(terms) or None


def _in_import_window(
    msg: EmailMessage, since: datetime | None, until: datetime | None,
) -> bool:
    """True when *msg* is in the window of the import (item 2).

    A message newer than ``until`` drops, as in the base. A message older
    than the floor drops too. A message with no date stays, as in the base,
    and the core decides on it (EM-T6a)."""
    if msg.received_at is None:
        return True
    at = received_key(msg)
    if until is not None and at > _utc(until):
        return False
    return since is None or at >= _utc(since)


def _estimate_of(data: dict[str, Any]) -> int | None:
    """The ``resultSizeEstimate`` of one list answer, or None when it has
    none (item 5, D-EM-16)."""
    try:
        value = int(data["resultSizeEstimate"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _fresh_ids(data: dict[str, Any], seen: set[str]) -> list[str]:
    """The ids of one list page that the import has not read yet (item 4).

    A list can give one id again at a page edge, for example when new mail
    arrives during the import. Each id goes into ``seen``, so the import
    fetches each message once."""
    fresh: list[str] = []
    for ref in data.get("messages") or []:
        mid = ref.get("id")
        if mid and mid not in seen:
            seen.add(mid)
            fresh.append(mid)
    return fresh


# ── The reconcile (WS-17 EM-G5b, spec §12.3.6.2) ─────────────────────────────
# Fence: tests/unit/test_gmail_import.py, and the R8 cases in
# ``TestTheImportOnARealDatabase`` of tests/unit/test_email_import_batches.py.

#: The form of a Gmail message id that the confirm puts into a path. Gmail
#: gives hex ids. Other text, such as a ``/``, a ``?`` or a ``#``, can send
#: the read to another resource, so the confirm refuses it (item 10).
_GMAIL_MESSAGE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _says_not_found(response: httpx.Response) -> bool:
    """True when a Gmail error body gives the reason ``notFound`` (item 10,
    C2). Google sends ``{"error": {"errors": [{"reason": "notFound"}]}}``
    for a deleted message. A 404 with any other body, or with no body, is
    not a proof of a delete, so the confirm raises for it."""
    try:
        errors = response.json()["error"]["errors"]
        return any(isinstance(e, dict) and e.get("reason") == "notFound"
                   for e in errors)
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


class GmailBearer(RefreshingBearer):
    """The bearer of the Gmail client, with the rate limits of Gmail.

    WS-17 EM-G4a (``email_app_master_plan.md`` §12.3.5.1). ``_get_client``
    sets it, so each Gmail call gets the rule, and no call site changes.

    * A 429, or a 403 with a rate-limit reason, waits for ``Retry-After``
      or a back-off. Then the same request goes again, at most
      ``GMAIL_MAX_TRIES`` times in all.
    * The tries end at once for a wait longer than ``GMAIL_MAX_WAIT_SECS``,
      or for a wait past the budget of this client. Then, and after the last
      try, the flow raises :class:`GmailRateLimited`.
    * A request that is not :func:`_repeatable` goes back to the caller with
      its answer. So a send never goes out twice.
    * Each try runs the flow of :class:`RefreshingBearer`, so a 401 still
      refreshes once. A rate limit and a 401 in one call cost one refresh.
    """

    def __init__(self, provider: Any) -> None:
        super().__init__(provider)
        #: The seconds that this client waited for rate limits.
        self.waited = 0.0

    def _next_wait(self, response: httpx.Response, tries: int) -> float | None:
        """The wait before the next try, or None when the tries end."""
        if tries >= GMAIL_MAX_TRIES:
            return None
        asked = _retry_after(response)
        wait = (asked if asked is not None
                else _GMAIL_BACKOFF_SECS * 2 ** (tries - 1) + random.uniform(0, 1))
        if wait > GMAIL_MAX_WAIT_SECS or self.waited + wait > GMAIL_WAIT_BUDGET_SECS:
            return None
        return wait

    async def async_auth_flow(
        self, request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        tries = 0
        while True:
            tries += 1
            # One try: the flow of RefreshingBearer, with its one refresh.
            flow = super().async_auth_flow(request)
            try:
                response = yield await flow.__anext__()
                while True:
                    try:
                        follow = await flow.asend(response)
                    except StopAsyncIteration:
                        break
                    response = yield follow
            finally:
                await flow.aclose()
            if not _repeatable(request) or not await _is_rate_limit(response):
                return
            # Read the answer, so the connection goes back to the pool and
            # the error keeps its body.
            await response.aread()
            wait = self._next_wait(response, tries)
            if wait is None:
                logger.warning(
                    "gmail.rate_limit_gave_up method=%s path=%s status=%s "
                    "tries=%d waited=%.1f", request.method, request.url.path,
                    response.status_code, tries, self.waited)
                raise GmailRateLimited(response, tries=tries)
            self.waited += wait
            logger.info(
                "gmail.rate_limited method=%s path=%s status=%s try=%d wait=%.1f",
                request.method, request.url.path, response.status_code, tries, wait)
            await _wait(wait)


# ── Send and drafts (WS-17 EM-G3a, GM-10 to GM-13) ───────────────────────
# ``email_app_master_plan.md`` §12.3.3. Fence: test_gmail_send_and_drafts.py.

#: ``drafts.list`` gives 500 drafts at most on one page.
GMAIL_DRAFT_PAGE_SIZE = 500
#: The pages of one draft lookup, so a lookup has a bound. 20 pages hold
#: 10,000 drafts.
GMAIL_DRAFT_MAX_PAGES = 20
#: The send route gives this type when the caller gives none, so it counts
#: as no type (item 2).
_OCTET_STREAM = "application/octet-stream"
#: The parent read of a reply asks for these two headers only (item 4).
_PARENT_PARAMS: dict[str, Any] = {
    "format": "metadata", "metadataHeaders": ["Message-ID", "References"]}


class GmailDraftNotFound(LookupError):
    """Gmail holds no draft with this message id (EM-G3a item 7).

    ``update_draft`` and ``send_draft`` take the message id that the local
    row holds (O-GM-2). A message id that is no draft raises this error, and
    never an HTTP 400 of Gmail."""

    def __init__(self, message_id: str) -> None:
        super().__init__(f"Gmail has no draft with the message id {message_id}")
        self.message_id = message_id


def _attachment_type(att: dict[str, Any]) -> tuple[str, str]:
    """The MIME type of one attachment (EM-G3a item 2, GM-12).

    The type that the caller gives wins. Else the type comes from the file
    name (``mimetypes.guess_type``), else it is ``application/octet-stream``.
    """
    given = str(att.get("mime_type") or "").split(";", 1)[0].strip().lower()
    if given and given != _OCTET_STREAM:
        main, _, sub = given.partition("/")
        if main and sub:
            return main, sub
    guessed, _ = mimetypes.guess_type(str(att.get("filename") or ""))
    if guessed:
        main, _, sub = guessed.partition("/")
        return main, sub
    return "application", "octet-stream"


def _attachment_part(att: dict[str, Any]) -> Message:
    """One attachment of a send or a draft (EM-G3a items 2 and 3).

    An attached mail (``message/*``) goes in as ONE mail part, with the mail
    inside it, because RFC 2046 §5.2 gives such a part no base64. So a
    forwarded mail stays one file (review round 2, P3-4)."""
    main, sub = _attachment_type(att)
    content = att.get("content") or b""
    part: Message
    if main == "message":
        raw = content if isinstance(content, bytes) else str(content).encode()
        part = MIMEMessage(message_from_bytes(raw), sub)
    else:
        part = MIMEBase(main, sub)
        part.set_payload(content)
        encoders.encode_base64(part)
    # ``add_header`` writes the name with RFC 2231 when it needs it, so a
    # quote or a letter outside ASCII survives (item 3).
    part.add_header("Content-Disposition", "attachment",
                    filename=str(att.get("filename") or "attachment"))
    return part


def _build_gmail_mail(
    *,
    to: list[str] | None,
    subject: str | None,
    body_text: str | None,
    body_html: str | None,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    reply_headers: dict[str, str] | None = None,
) -> bytes:
    """The RFC 5322 bytes of a send or a draft.

    The ONE builder of ``send_message``, ``create_draft`` and
    ``update_draft`` (EM-G3a item 1, GM-10). With ``body_html`` the body is
    ``multipart/alternative``, with the text part first. With attachments the
    body sits inside ``multipart/mixed`` (item 2). ``reply_headers`` holds
    ``In-Reply-To`` and ``References`` of a reply (item 4).

    A header is written only when its value is given, so an update that
    names no ``Cc`` writes none.

    EM-G3c item 1: the builder gives bytes. The plain URI wraps them in
    base64url with :func:`_gmail_raw`, and the upload URI sends them as they
    are. So the two forms come from one builder.
    """
    text_part = MIMEText(body_text or "", "plain", "utf-8")
    body: Message
    if body_html:
        body = MIMEMultipart("alternative")
        body.attach(text_part)
        body.attach(MIMEText(body_html, "html", "utf-8"))
    else:
        body = text_part
    msg: Message
    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(body)
        for att in attachments:
            msg.attach(_attachment_part(att))
    else:
        msg = body
    if to is not None:
        msg["To"] = ", ".join(a for a in to if a)
    for name, addresses in (("Cc", cc), ("Bcc", bcc)):
        listed = [a for a in (addresses or []) if a]
        if listed:
            msg[name] = ", ".join(listed)
    if subject is not None:
        msg["Subject"] = subject
    for name, value in (reply_headers or {}).items():
        msg[name] = value
    return msg.as_bytes()


def _gmail_raw(mail: bytes) -> str:
    """The ``raw`` value of a write on the plain URI: the mail of
    :func:`_build_gmail_mail` in base64url (EM-G3c item 1)."""
    return base64.urlsafe_b64encode(mail).decode()


def _newest_parent(thread: dict[str, Any]) -> dict[str, Any] | None:
    """The newest message of a thread that is not a draft (EM-G3a item 5).

    Gmail lists a thread oldest first, so a tie keeps the later message."""
    newest: dict[str, Any] | None = None
    newest_at = -1
    for message in thread.get("messages") or []:
        if "DRAFT" in (message.get("labelIds") or []):
            continue
        try:
            at = int(message.get("internalDate") or 0)
        except (TypeError, ValueError):
            at = 0
        if at >= newest_at:
            newest, newest_at = message, at
    return newest


def _reply_headers_of(parent: dict[str, Any] | None) -> dict[str, str]:
    """``In-Reply-To`` and ``References`` that answer ``parent`` (item 4).

    ``References`` is the ``References`` of the parent, then its
    ``Message-ID`` (RFC 5322 §3.6.4). A parent with no ``Message-ID`` gives
    no header, because no header can name it."""
    if not parent:
        return {}
    headers = GmailProvider._parse_headers(
        (parent.get("payload") or {}).get("headers", []))
    message_id = _gmail_message_id(headers)
    if not message_id:
        return {}
    references = headers.get("references", "").split()
    if message_id not in references:
        references.append(message_id)
    return {"In-Reply-To": message_id, "References": " ".join(references)}


# ── The size of a mail (WS-17 EM-G3c-1, EM-G3a-f8) ───────────────────────
# ``email_app_master_plan.md`` §12.3.3b, items 1 to 8. Fence:
# test_gmail_mail_size.py.

#: The base of the upload URI of the three writes (item 3). The Gmail
#: discovery document names ``/upload/gmail/v1/users/{userId}/...`` as the
#: ``simple`` media upload of ``messages.send``, ``drafts.create`` and
#: ``drafts.update``.
GMAIL_UPLOAD_BASE = "https://gmail.googleapis.com/upload/gmail/v1"
#: The most bytes of one built mail (item 5). It is the ``maxSize`` of the
#: media upload of the three writes in the discovery document (revision
#: 20260928, read 2026-10-05). A larger mail raises before its write.
GMAIL_MAIL_MAX_BYTES = 36_700_160
#: A mail with no file and at most this many bytes keeps the plain URI and
#: ``raw`` (item 2). Google documents no limit for the plain request, so a
#: larger mail goes to the upload URI.
GMAIL_PLAIN_MAX_BYTES = 1_048_576


class GmailMailTooLarge(ProviderMailTooLarge):
    """Gmail cannot take this mail (EM-G3c item 6).

    The write raises it before its request when the built mail is over
    :data:`GMAIL_MAIL_MAX_BYTES`. A 413 from Google raises it too. The
    write reads only the status code of that answer, because Google can
    answer a 413 with HTML. The text holds two sizes and no URL."""


def _upload_body(meta: dict[str, Any], mail: bytes) -> tuple[bytes, str]:
    """The body and the ``Content-Type`` of one upload write (items 3, 4).

    The body is ``multipart/related``. Part 1 is the metadata in JSON. Part
    2 is the mail as ``message/rfc822``, in its raw bytes and not in
    base64url. The boundary is random, and it never occurs in the mail."""
    boundary = f"metorite-{secrets.token_hex(16)}"
    while boundary.encode() in mail:
        boundary = f"metorite-{secrets.token_hex(16)}"
    head = (f"--{boundary}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(meta)}\r\n"
            f"--{boundary}\r\n"
            "Content-Type: message/rfc822\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + mail + tail, f"multipart/related; boundary={boundary}"


class GmailProvider(BaseEmailProvider):
    """Gmail API email provider."""

    # Gmail can list message ids per label, so labels really can be read back.
    SUPPORTS_LABEL_READBACK = True

    def __init__(
        self, credentials: dict[str, Any], *, app: OAuthApp | None = None,
    ):
        super().__init__(credentials)
        self._access_token: str | None = credentials.get("access_token")
        self._refresh_token: str | None = credentials.get("refresh_token")
        # The app credentials come from settings through the factory, never
        # from the blob (EM-T3a item 1). With none, a refresh fails closed.
        self._app: OAuthApp = app or OAuthApp()
        self._token_expiry: str | None = credentials.get("token_expiry")
        self._http: httpx.AsyncClient | None = None
        self._creds_dirty = False
        # RefreshingBearer refreshes under this lock, so two requests that
        # get a 401 at the same time cause one refresh (EM-T4c).
        self._refresh_lock = asyncio.Lock()
        # Gmail returns opaque label IDs on every message; the *names* live in a
        # separate /labels listing. Cache both directions for the life of the
        # provider instance (one instance per sync/run) so parsing a message can
        # resolve its user labels synchronously, and so set_labels stops re-
        # fetching the whole label list once per label per message.
        self._label_ids_by_name: dict[str, str] | None = None   # lower name → id
        self._label_names_by_id: dict[str, str] = {}            # id → display name
        #: The record of each message that a fetch could not read, for the
        #: life of this instance (EM-G4a item 10). No entry names content.
        self.fetch_failures: list[str] = []
        #: The draft id of each draft message that this instance saw, by the
        #: message id (EM-G3a item 7). One provider serves one request, so
        #: the cache never spans two saves.
        self._draft_ids: dict[str, str] = {}
        #: True after one lookup read each page of ``drafts.list``.
        self._drafts_listed = False
        #: The message id of each draft that ``trash_message`` discarded. The
        #: caller deletes the local row of each one (EM-G3a, E-A5).
        self.discarded_drafts: set[str] = set()
        #: True when the last ``import_batches`` stopped at
        #: ``IMPORT_MAX_PAGES``. The scheduler then runs no reconcile, because
        #: the mail below the cap stays unread (WS-17 EM-G5b item 12, C5).
        self.import_capped = False

    def credentials_dirty(self) -> bool:
        return self._creds_dirty

    def export_credentials(self) -> dict[str, Any]:
        """Return the token fields with the latest (possibly refreshed) tokens.

        No app credential is written, so the next token write removes one
        that an old callback left in the blob (EM-T3a item 1).
        """
        return {
            **token_fields(self.credentials),
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        # No Authorization header here. GmailBearer is a RefreshingBearer: it
        # sets the current token on each request, and refreshes once on a 401
        # (EM-T4c). It also waits out a rate limit (EM-G4a), so each Gmail
        # call through this client gets one rule.
        if self._http is None:
            await self.authenticate()
            self._http = httpx.AsyncClient(
                base_url=GMAIL_API_BASE,
                headers={"Content-Type": "application/json"},
                auth=GmailBearer(self),
                timeout=30.0,
            )
        return self._http

    def _record_fetch_failure(self, message_id: Any, exc: BaseException) -> str:
        """Log and count one message that a fetch could not read (EM-G4a
        item 10), and return its record.

        The record names the message id, the class of the error and the HTTP
        status. It never names a subject or an address."""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        note = (f"gmail.fetch_failed id={message_id} "
                f"error={type(exc).__name__} status={status}")
        self.fetch_failures.append(note)
        logger.warning("gmail.fetch_failed message_id=%s error=%s status=%s",
                       message_id, type(exc).__name__, status)
        return note

    async def _refresh_access_token(self) -> None:
        """Refresh the OAuth access token using the refresh token."""
        if not self._refresh_token:
            raise ValueError("Missing OAuth credentials for token refresh")
        if not self._app.configured:
            raise ValueError(
                "Gmail OAuth app credentials are not configured "
                "(GMAIL_OAUTH_CLIENT_ID and GMAIL_OAUTH_CLIENT_SECRET)"
            )

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": self._app.client_id,
                    "client_secret": self._app.client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            if "refresh_token" in data:
                self._refresh_token = data["refresh_token"]
            self._creds_dirty = True

    async def authenticate(self) -> bool:
        """Validate and refresh token if needed."""
        if not self._access_token:
            if self._refresh_token:
                await self._refresh_access_token()
            else:
                return False

        # Test the token with a lightweight API call
        try:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=10.0,
            ) as client:
                resp = await client.get(f"{GMAIL_API_BASE}/users/me/profile")
                # A token that a refresh on this instance made, and that
                # still gets a 401, gets no second refresh (EM-T4c). A sync
                # calls this twice, once itself and once in _get_client.
                if (resp.status_code == 401 and self._refresh_token
                        and not self._creds_dirty):
                    await self._refresh_access_token()
                    return True
                return resp.is_success
        except Exception:
            return False

    async def list_folders(self) -> list[EmailFolder]:
        client = await self._get_client()
        resp = await client.get("/users/me/labels")
        resp.raise_for_status()
        data = resp.json()

        folders: list[EmailFolder] = []
        for label in data.get("labels", []):
            folders.append(EmailFolder(
                provider_folder_id=label["id"],
                name=label["name"],
                type=label.get("type", "user"),
                message_count=label.get("messagesTotal", 0),
                unread_count=label.get("messagesUnread", 0),
            ))
        return folders

    async def list_messages(
        self,
        folder: str = "INBOX",
        query: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        canonical_override: str | None = None,
    ) -> tuple[list[EmailMessage], str | None]:
        """List the messages that carry ``folder`` (a Gmail label ID).

        Each message keeps the folder of its parse, from its system labels
        (WS-17 EM-G2 item 8, D-EM-33). ``canonical_override`` never sets the
        folder. So a page of a user label files its Inbox mail as ``inbox``
        and its other mail as ``archive`` (GM-7, O-GM-1).

        Gmail has no ``archive`` label. The folder key ``archive`` sends
        ``GMAIL_ARCHIVE_QUERY`` and the query of the caller, and no
        ``labelIds`` (item 9, GM-6). A ``canonical_override`` of ``archive``
        does the same, so a user label named Archive never takes the place of
        the folder (item 10).
        """
        client = await self._get_client()
        params: dict[str, Any] = {"maxResults": min(max_results, 500)}
        if _is_archive_key(folder) or _is_archive_key(canonical_override):
            params["q"] = (f"{GMAIL_ARCHIVE_QUERY} {query}" if query
                           else GMAIL_ARCHIVE_QUERY)
        else:
            params["labelIds"] = [folder]
            if query:
                params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        resp = await client.get("/users/me/messages", params=params)
        resp.raise_for_status()
        data = resp.json()

        messages: list[EmailMessage] = []
        for msg_ref in data.get("messages", []):
            # Fetch full message
            try:
                messages.append(await self.get_message(msg_ref["id"]))
            except GmailRateLimited:
                # The tries are spent. The sync fails and backs off, and the
                # next cycle reads this message (EM-G4a item 9).
                raise
            except Exception as exc:
                # No silent skip (item 10). The other messages still parse.
                self._record_fetch_failure(msg_ref.get("id"), exc)

        next_token = data.get("nextPageToken")
        return messages, next_token

    async def fetch_label_assignments(
        self, max_pages: int = 20,
    ) -> dict[str, list[str]]:
        """``{message id: [user label name, …]}`` via one paged list per label.

        Gmail can filter the message list by label ID, so this reads the whole
        mailbox's label assignments in ~one request per label per 500 messages —
        orders of magnitude cheaper than re-fetching messages. Used to restore
        ``email_messages.categories`` after it was lost locally.
        """
        client = await self._get_client()
        await self._ensure_label_names()
        out: dict[str, list[str]] = {}
        for lid, name in self._label_names_by_id.items():
            token: str | None = None
            for _ in range(max_pages):
                params: dict[str, Any] = {"labelIds": [lid], "maxResults": 500}
                if token:
                    params["pageToken"] = token
                try:
                    resp = await client.get("/users/me/messages", params=params)
                    resp.raise_for_status()
                except httpx.HTTPError:
                    break  # one bad label must not abort the whole restore
                data = resp.json()
                for ref in data.get("messages", []) or []:
                    mid = ref.get("id")
                    if mid:
                        out.setdefault(mid, []).append(name)
                token = data.get("nextPageToken")
                if not token:
                    break
        return out

    async def _ensure_label_names(self) -> None:
        """Best-effort warm of the label-id → name cache before parsing.

        Parsing is synchronous but needs the names, so the (cached) fetch has to
        happen here. A failure is non-fatal: the message still syncs, it just
        reports ``categories_authoritative=False`` so ingest keeps the labels
        already stored rather than erasing them.
        """
        if self._label_ids_by_name is not None:
            return
        try:
            await self._label_name_id_map()
        except Exception:  # noqa: BLE001 — labels are optional for a sync
            pass

    async def get_message(self, provider_message_id: str) -> EmailMessage:
        client = await self._get_client()
        await self._ensure_label_names()
        resp = await client.get(
            f"/users/me/messages/{provider_message_id}",
            params={"format": "full"},
        )
        resp.raise_for_status()
        return self._parse_gmail_message(resp.json())

    async def send_message(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        reply_to_message_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        thread_id: str | None = None,
    ) -> str:
        """Send a mail (``messages.send``) and return its message id.

        ``_build_gmail_mail`` builds the body, the attachments and the reply
        headers (WS-17 EM-G3a items 1 to 4). A reply also keeps ``threadId``.
        The rate-limit helper sends this POST once only (EM-G4a), on the
        plain URI and on the upload URI (EM-G3c item 8).
        """
        mail = _build_gmail_mail(
            to=to, subject=subject, body_text=body_text, body_html=body_html,
            cc=cc, bcc=bcc, attachments=attachments,
            reply_headers=await self._reply_headers(
                reply_to_message_id, thread_id))
        # Gmail threads by threadId. Prefer the real conversation id
        # (``thread_id``); fall back to ``reply_to_message_id`` only when a caller
        # passes a message id as the thread anchor. Passing a non-thread message
        # id here would fail to thread — the "reply shows as a separate email" bug.
        data = await self._write_mail(
            "POST", "/messages/send", mail,
            thread_id=thread_id or reply_to_message_id,
            has_files=bool(attachments), draft=False)
        return data["id"]

    async def create_draft(
        self,
        to: list[str],
        subject: str,
        body_text: str,
        body_html: str | None = None,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> str:
        """Create a Gmail draft (``drafts.create``) and return the MESSAGE id
        of the draft (EM-G3a item 6, O-GM-2).

        The local row holds that id, which is the id that the sync finds. So
        one draft keeps one row (GM-13). A reply keeps ``threadId`` and gets
        ``In-Reply-To`` and ``References`` (item 4).
        """
        mail = _build_gmail_mail(
            to=to, subject=subject, body_text=body_text, body_html=body_html,
            cc=cc, bcc=bcc, attachments=attachments,
            reply_headers=await self._reply_headers(
                reply_to_message_id, thread_id))
        return self._remember_draft(await self._write_mail(
            "POST", "/drafts", mail,
            thread_id=thread_id or reply_to_message_id,
            has_files=bool(attachments), draft=True))

    async def update_draft(
        self,
        draft_id: str,
        to: list[str] | None = None,
        subject: str | None = None,
        body_text: str | None = None,
        body_html: str | None = None,
        thread_id: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Replace a Gmail draft (``drafts.update``) and return its NEW message
        id (EM-G3a items 6 and 7, O-GM-2).

        ``draft_id`` keeps the name of the base class, but it is the MESSAGE
        id that the local row holds. The draft id comes from ``drafts.list``.
        Gmail gives the draft a new message id at each update, so the caller
        moves its local row to the id that this returns (item 8, E-A2).

        Gmail replaces the whole draft. So the update builds the whole mail
        again with the one builder, and it sets ``In-Reply-To`` and
        ``References`` again from the newest message of the thread (item 5).
        ``threadId`` MUST be sent again too, or Gmail drops the draft from its
        conversation, and the sent reply starts a new one.

        ``attachments`` are files to ADD, as the base class says. The files
        that the draft holds stay: the update reads them from the draft first
        and builds them in again (review round 1, F1). Metorite keeps no bytes
        of a draft file, so Gmail is the one source of them.

        EM-G3c item 2: the files AFTER the read-back decide the URI, not
        ``attachments``. So an autosave that adds no file still goes to the
        upload URI when the draft holds one.
        """
        gmail_draft_id = await self._draft_id_for(draft_id)
        files = [*await self._draft_files(draft_id), *(attachments or [])]
        mail = _build_gmail_mail(
            to=to, subject=subject, body_text=body_text, body_html=body_html,
            cc=cc, bcc=bcc, attachments=files or None,
            reply_headers=await self._reply_headers(None, thread_id))
        data = await self._write_mail(
            "PUT", f"/drafts/{gmail_draft_id}", mail,
            thread_id=thread_id, has_files=bool(files), draft=True)
        return self._remember_draft(data, old_message_id=draft_id)

    async def send_draft(self, draft_id: str) -> str | None:
        """Send a Gmail draft natively (``drafts.send``): Drafts → Sent.

        ``draft_id`` is the MESSAGE id of the draft, as the local row holds it
        (EM-G3a item 7). A draft made in Gmail web has only that id, and it
        now sends. Returns the id of the sent message.
        """
        gmail_draft_id = await self._draft_id_for(draft_id)
        client = await self._get_client()
        resp = await client.post(
            "/users/me/drafts/send", json={"id": gmail_draft_id})
        resp.raise_for_status()
        self._draft_ids.pop(draft_id, None)
        return (resp.json() or {}).get("id")

    async def _reply_headers(
        self, reply_to_message_id: str | None, thread_id: str | None,
    ) -> dict[str, str]:
        """``In-Reply-To`` and ``References`` of a reply (EM-G3a items 4, 5).

        The parent is the message that ``reply_to_message_id`` names. With
        only ``thread_id``, it is the newest message of the thread that is not
        a draft. The two headers come from Gmail (``format=metadata``), never
        from the local row. With neither id, there is no parent.

        A read that fails gives no header, and the mail still goes (E-A3).
        The log line names the kind and the id only, never a subject or an
        address.
        """
        if not (reply_to_message_id or thread_id):
            return {}
        kind, ref = (("message", reply_to_message_id) if reply_to_message_id
                     else ("thread", thread_id))
        try:
            client = await self._get_client()
            resp = await client.get(f"/users/me/{kind}s/{ref}",
                                    params=_PARENT_PARAMS)
            resp.raise_for_status()
            data = resp.json()
            parent = data if kind == "message" else _newest_parent(data)
            return _reply_headers_of(parent)
        except Exception as exc:  # E-A3: a failed read never fails the send
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "gmail.parent_read_failed kind=%s id=%s error=%s status=%s",
                kind, ref, type(exc).__name__, status)
            return {}

    async def _write_mail(
        self, method: str, path: str, mail: bytes, *,
        thread_id: str | None, has_files: bool, draft: bool,
    ) -> dict[str, Any]:
        """Send one write of a built mail, and return the answer of Gmail.

        WS-17 EM-G3c-1, items 2 to 6. ``path`` is the path of the write
        under ``/users/me``. ``draft`` puts the mail inside ``message``, as
        ``drafts.create`` and ``drafts.update`` need. With no ``thread_id``
        the write sends no ``threadId``, on either URI.

        * A mail over :data:`GMAIL_MAIL_MAX_BYTES` raises
          :class:`GmailMailTooLarge` before the request (item 5).
        * A mail with a file, or over :data:`GMAIL_PLAIN_MAX_BYTES`, goes to
          the upload URI as ``multipart/related`` (items 2 to 4). The client
          is still ``_get_client()``, so ``GmailBearer`` and its rate-limit
          rule stay. ``Content-Type`` replaces the JSON type of the client.
        * Each other mail keeps the plain URI and ``raw``, as before.
        * A 413 raises :class:`GmailMailTooLarge`, read from the status
          code only (item 6).
        """
        size = len(mail)
        if size > GMAIL_MAIL_MAX_BYTES:
            raise GmailMailTooLarge(size, GMAIL_MAIL_MAX_BYTES)
        meta: dict[str, Any] = {"threadId": thread_id} if thread_id else {}
        client = await self._get_client()
        write = client.post if method == "POST" else client.put
        if has_files or size > GMAIL_PLAIN_MAX_BYTES:
            body, content_type = _upload_body(
                {"message": meta} if draft else meta, mail)
            resp = await write(
                f"{GMAIL_UPLOAD_BASE}/users/me{path}",
                params={"uploadType": "multipart"}, content=body,
                headers={"Content-Type": content_type})
        else:
            message = {"raw": _gmail_raw(mail), **meta}
            resp = await write(f"/users/me{path}",
                               json={"message": message} if draft else message)
        if resp.status_code == 413:
            raise GmailMailTooLarge(size, GMAIL_MAIL_MAX_BYTES)
        resp.raise_for_status()
        return resp.json()

    async def _draft_files(self, message_id: str) -> list[dict[str, Any]]:
        """The files of a draft, in the shape of ``attachments``.

        WS-17 EM-G3a review round 1, F1. ``drafts.update`` replaces the whole
        draft, so an update that leaves out the files drops them. A read that
        fails raises, because an update with no read loses files.

        Review round 2, P3-4: the read takes the draft as one RFC 5322 mail
        (``format=raw``). With ``format=full`` Gmail opens an attached mail
        into its parts and gives no bytes for it, so an update lost it, or
        put its inner files at the top. ``iter_attachments`` of the standard
        library gives each file of the top level once and never opens one.
        An attached mail stays one file: the mail, with its name, else
        ``attached.eml``. The body parts are no files, so they stay out.
        """
        client = await self._get_client()
        resp = await client.get(f"/users/me/messages/{message_id}",
                                params={"format": "raw"})
        resp.raise_for_status()
        raw = str((resp.json() or {}).get("raw") or "")
        if not raw:
            return []
        mail = message_from_bytes(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)),
            policy=mail_policy.default)
        files: list[dict[str, Any]] = []
        for part in mail.iter_attachments():
            if part.get_content_maintype() == "message":
                inner = part.get_payload()
                content = (inner[0].as_bytes()
                           if isinstance(inner, list) and inner else b"")
                name = part.get_filename() or "attached.eml"
            else:
                content = part.get_payload(decode=True) or b""
                name = part.get_filename() or "attachment"
            files.append({"filename": name, "content": content,
                          "mime_type": part.get_content_type()})
        return files

    def _remember_draft(
        self, data: dict[str, Any], *, old_message_id: str | None = None,
    ) -> str:
        """Cache the pair of one draft answer, and return its message id.

        ``drafts.create`` and ``drafts.update`` answer with the draft id and
        the message of the draft (EM-G3a item 6). An update gives the draft a
        new message id, so the old one leaves the cache."""
        message_id = str((data.get("message") or {}).get("id") or "")
        gmail_draft_id = str(data.get("id") or "")
        if not message_id or not gmail_draft_id:
            raise ValueError(
                "Gmail answered a draft write with no draft id or message id")
        if old_message_id and old_message_id != message_id:
            self._draft_ids.pop(old_message_id, None)
        self._draft_ids[message_id] = gmail_draft_id
        return message_id

    async def _draft_id_for(
        self, message_id: str, *, required: bool = True,
    ) -> str | None:
        """The draft id of the draft whose message id is ``message_id``.

        EM-G3a item 7 (O-GM-2). The local row holds the message id, and
        ``drafts.list`` maps it to the draft id. The cache lives for this
        instance. A lookup reads one page at a time, and it stops at the page
        that holds the id, or after ``GMAIL_DRAFT_MAX_PAGES`` pages.

        ``required`` (``update_draft`` and ``send_draft``): a miss of the
        cache reads ``drafts.list`` again, and a message id that is no draft
        raises :class:`GmailDraftNotFound`. Not ``required``
        (``trash_message``): a miss after one full read answers None, so
        many trashes cost one read.
        """
        if message_id in self._draft_ids:
            return self._draft_ids[message_id]
        if not required and self._drafts_listed:
            return None
        client = await self._get_client()
        token: str | None = None
        for _ in range(GMAIL_DRAFT_MAX_PAGES):
            params: dict[str, Any] = {"maxResults": GMAIL_DRAFT_PAGE_SIZE}
            if token:
                params["pageToken"] = token
            resp = await client.get("/users/me/drafts", params=params)
            resp.raise_for_status()
            data = resp.json() or {}
            for draft in data.get("drafts") or []:
                mid = (draft.get("message") or {}).get("id")
                if mid and draft.get("id"):
                    self._draft_ids[str(mid)] = str(draft["id"])
            token = data.get("nextPageToken")
            if not token or message_id in self._draft_ids:
                break
        found = self._draft_ids.get(message_id)
        # The cache holds each draft after the last page, or after the bound
        # of pages. A stop at the page of the id reads the rest later.
        if not token or not found:
            self._drafts_listed = True
        if found:
            return found
        if required:
            raise GmailDraftNotFound(message_id)
        return None

    async def modify_message(
        self,
        provider_message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        client = await self._get_client()
        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        resp = await client.post(
            f"/users/me/messages/{provider_message_id}/modify",
            json=body,
        )
        resp.raise_for_status()

    async def trash_message(self, provider_message_id: str) -> None:
        """Move a message to Trash, or discard a draft (WS-17 EM-G3a, E-A5).

        A draft goes through ``drafts.delete``, which is "Discard draft" in
        Gmail. It removes the draft for good, as Gmail itself does, and the
        scope ``gmail.modify`` allows it. Its message id goes into
        ``discarded_drafts``, so a caller that moves rows deletes the local
        row instead. Each other message goes to Trash (``messages.trash``).
        """
        draft_id = await self._draft_id_for_trash(provider_message_id)
        client = await self._get_client()
        if draft_id:
            resp = await client.delete(f"/users/me/drafts/{draft_id}")
            resp.raise_for_status()
            self._draft_ids.pop(provider_message_id, None)
            self.discarded_drafts.add(provider_message_id)
            return
        resp = await client.post(
            f"/users/me/messages/{provider_message_id}/trash"
        )
        resp.raise_for_status()

    async def _draft_id_for_trash(self, provider_message_id: str) -> str | None:
        """The draft id for a trash, or None when the id is no draft.

        A lookup that fails for a reason other than a rate limit trashes the
        message as before, so a failed ``drafts.list`` never blocks the trash
        of a mail. A rate limit raises, as each Gmail call does (EM-G4a)."""
        try:
            return await self._draft_id_for(provider_message_id, required=False)
        except GmailRateLimited:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "gmail.draft_lookup_failed message_id=%s error=%s status=%s",
                provider_message_id, type(exc).__name__, status)
            return None

    async def apply_flags(
        self,
        provider_message_id: str,
        *,
        is_read: bool | None = None,
        is_starred: bool | None = None,
        is_flagged: bool | None = None,
    ) -> None:
        """Translate flag changes into Gmail label add/remove operations."""
        add: list[str] = []
        remove: list[str] = []
        if is_read is not None:
            (remove if is_read else add).append("UNREAD")
        if is_starred is not None:
            (add if is_starred else remove).append("STARRED")
        if is_flagged is not None:
            # Gmail's closest analogue to a flag is the IMPORTANT marker.
            (add if is_flagged else remove).append("IMPORTANT")
        if add or remove:
            await self.modify_message(
                provider_message_id, add_labels=add or None, remove_labels=remove or None
            )

    # Gmail's documented ceiling for users.messages.batchModify.
    _BATCH_MODIFY_MAX = 1000

    # Bulk actions expressible as a pure label mutation → (add, remove).
    # Trash is deliberately absent: it has its own endpoint with its own
    # semantics, and guessing that batchModify accepts the TRASH system label
    # is not something to find out on a destructive path.
    _BULK_LABEL_OPS: dict[str, tuple[list[str], list[str]]] = {
        "archive": ([], ["INBOX"]),
        "read": ([], ["UNREAD"]),
        "unread": (["UNREAD"], []),
        "star": (["STARRED"], []),
        "unstar": ([], ["STARRED"]),
    }

    async def bulk_apply(
        self, provider_message_ids: list[str], action: str,
        failed_out: list[str] | None = None,
    ) -> dict[str, str]:
        """Collapse a bulk label action into batchModify calls of 1000.

        The base implementation is one HTTP round-trip per message, which at
        mailbox scale is hours of wall-clock and a rate-limit ban. Gmail applies
        the same label mutation to 1000 messages in a single request, so
        archiving a 40k-message newsletter backlog costs 40 calls, not 40,000.

        ``failed_out`` collects ids the per-message fallback could not apply, so
        the caller can retry them (see the base contract).

        Gmail never re-keys a message id, so the return is always empty.
        """
        ops = self._BULK_LABEL_OPS.get(action)
        if ops is None:
            return await super().bulk_apply(
                provider_message_ids, action, failed_out)
        add, remove = ops
        client = await self._get_client()
        for i in range(0, len(provider_message_ids), self._BATCH_MODIFY_MAX):
            chunk = provider_message_ids[i:i + self._BATCH_MODIFY_MAX]
            body: dict[str, Any] = {"ids": chunk}
            if add:
                body["addLabelIds"] = add
            if remove:
                body["removeLabelIds"] = remove
            try:
                resp = await client.post("/users/me/messages/batchModify", json=body)
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                # A whole chunk failing is worth a retry one message at a time:
                # batchModify is all-or-nothing, so one stale id would otherwise
                # cost the other 999 their update.
                logger.warning(
                    "gmail.batch_modify_failed action=%s size=%d error=%s",
                    action, len(chunk), str(exc)[:160],
                )
                await super().bulk_apply(chunk, action, failed_out)
        return {}

    # ── A move to a user label (WS-17 EM-G3b, GM-14, D-EM-33) ─────────────
    # ``email_app_master_plan.md`` §12.3.4, items 1 to 5. A user label is a
    # label and never a folder (O-GM-1), so a move to one files the message
    # as ``archive``. Fence: test_gmail_move_and_filters.py.

    #: The folder keys that a move reaches through a system label.
    _MOVE_SYSTEM_KEYS = frozenset({"inbox", "archive", "trash", "junk"})
    #: The folder keys that a move never reaches. Gmail sets ``SENT`` and
    #: ``DRAFT`` itself, on a send and on a draft save (E-M2).
    _MOVE_REFUSED_KEYS = frozenset({"sent", "drafts"})
    #: A move to a user label or to Archive removes these, so a move out of
    #: Trash or Spam leaves ``archive`` (D-EM-33, E-M5, review round 1).
    _LABEL_MOVE_REMOVES = ("INBOX", "TRASH", "SPAM")
    #: A move to Junk removes these, so a move out of Trash leaves ``junk``
    #: (review round 1). Gmail ranks ``TRASH`` before ``SPAM``.
    _JUNK_MOVE_REMOVES = ("INBOX", "TRASH")

    def _is_system_label_name(self, name: str) -> bool:
        """True when ``name`` names a reserved system label or a ``CATEGORY_*``
        label (E-M3). Without this rule, "Starred" stars the message."""
        upper = (name or "").strip().upper()
        return upper in self._GMAIL_RESERVED or upper.startswith("CATEGORY_")

    def folder_after_move(self, name: str) -> str | None:
        """The folder key after a move to ``name`` (EM-G3b item 5).

        The key for inbox, archive, trash and junk, with each alias of
        ``canonical_folder``. ``None`` for sent, drafts and a system label
        name, because the move refuses them. ``archive`` for a user label."""
        key = canonical_folder(name)
        if key in self._MOVE_SYSTEM_KEYS:
            return key
        if key in self._MOVE_REFUSED_KEYS or self._is_system_label_name(name):
            return None
        return "archive"

    async def _user_label_id_for_move(self, name: str) -> str:
        """The id of the USER label ``name``, made when it is new (E-M2 to E-M4).

        ``_ensure_label_id`` maps every label, system labels too, so the move
        checks the result against the user labels. A label that Gmail could
        not make raises, so no caller stores ``archive`` for a move that did
        not occur. The name goes to Google unchanged, also with a "/"."""
        if self._is_system_label_name(name):
            raise ValueError(f"Gmail cannot move a message to the system label {name!r}")
        label_id = await self._ensure_label_id(name)
        if not label_id:
            raise ValueError(f"Could not create Gmail label: {name!r}")
        if label_id not in self._label_names_by_id:
            raise ValueError(f"Gmail cannot move a message to the system label {name!r}")
        return label_id

    async def move_to_folder(self, provider_message_id: str, folder: str) -> None:
        """Move via Gmail label mutation (Gmail has labels, not folders).

        ``canonical_folder`` reads the name, so "Bin", "Deleted Items" and
        "Junk Email" reach the system branch (E-M1). Sent and drafts raise
        ``ValueError`` (E-M2). Each other name is a user label: one
        ``modify`` adds it and removes ``INBOX``, ``TRASH`` and ``SPAM``. Gmail
        keeps the id, so the move returns ``None``.

        Archive removes ``INBOX``, ``TRASH`` and ``SPAM`` too, and Junk adds
        ``SPAM`` and removes ``INBOX`` and ``TRASH``. Gmail ranks ``TRASH``
        first, so a move out of Trash that kept it left the message in Trash
        (review round 1)."""
        name = (folder or "").strip()
        key = canonical_folder(name)
        if key in self._MOVE_REFUSED_KEYS:
            raise ValueError(f"Gmail cannot move a message to {key}")
        if key == "trash":
            await self.trash_message(provider_message_id)
        elif key == "archive":
            # Archiving in Gmail = no INBOX, and out of Trash and Spam too.
            await self.modify_message(
                provider_message_id, remove_labels=list(self._LABEL_MOVE_REMOVES)
            )
        elif key == "inbox":
            await self.modify_message(
                provider_message_id, add_labels=["INBOX"], remove_labels=["TRASH", "SPAM"]
            )
        elif key == "junk":
            await self.modify_message(
                provider_message_id, add_labels=["SPAM"],
                remove_labels=list(self._JUNK_MOVE_REMOVES),
            )
        else:
            label_id = await self._user_label_id_for_move(name)
            await self.modify_message(
                provider_message_id, add_labels=[label_id],
                remove_labels=list(self._LABEL_MOVE_REMOVES),
            )

    # ── Labels ───────────────────────────────────────────────────────────

    # Gmail's reserved label ids never offered as user-applicable labels.
    _GMAIL_RESERVED = {
        "INBOX", "SENT", "DRAFT", "TRASH", "SPAM", "UNREAD", "STARRED",
        "IMPORTANT", "CHAT",
    }

    async def list_labels(self) -> list[dict[str, str | None]]:
        """User labels as ``{name, color}`` (excludes system labels/categories).

        ``color`` is a canonical preset token mapped from the label's Gmail
        ``backgroundColor`` (None when the label has no colour set)."""
        from .label_colors import preset_from_gmail_bg
        client = await self._get_client()
        resp = await client.get("/users/me/labels")
        resp.raise_for_status()
        out: list[dict[str, str | None]] = []
        for lbl in resp.json().get("labels", []):
            if lbl.get("type") == "user" and not lbl.get("name", "").startswith(
                "CATEGORY_"
            ):
                bg = (lbl.get("color") or {}).get("backgroundColor")
                out.append(
                    {"name": lbl["name"], "color": preset_from_gmail_bg(bg)}
                )
        return sorted(out, key=lambda x: (x["name"] or "").lower())

    async def _label_name_id_map(self, *, refresh: bool = False) -> dict[str, str]:
        """Lower-cased label name → id for the account's labels.

        Cached per provider instance. Also populates ``_label_names_by_id`` with
        the *user* labels only, which is what ``_parse_gmail_message`` projects
        into ``EmailMessage.categories`` — Gmail puts opaque IDs on the message
        and the names only exist here.
        """
        if self._label_ids_by_name is not None and not refresh:
            return self._label_ids_by_name
        client = await self._get_client()
        resp = await client.get("/users/me/labels")
        resp.raise_for_status()
        labels = resp.json().get("labels", [])
        self._label_ids_by_name = {
            lbl.get("name", "").lower(): lbl["id"]
            for lbl in labels
            if lbl.get("name")
        }
        # User labels only: system IDs (INBOX/SENT/UNREAD/…) are folder & flag
        # state, already mapped elsewhere, and Gmail's own CATEGORY_* tabs
        # (Promotions/Social/…) are not user labels — neither belongs in the
        # categories column the rule engine owns.
        self._label_names_by_id = {
            lbl["id"]: lbl["name"]
            for lbl in labels
            if lbl.get("name") and lbl.get("id")
            and lbl.get("type") == "user"
            and not lbl["name"].startswith("CATEGORY_")
        }
        return self._label_ids_by_name

    async def _ensure_label_id(
        self, name: str, color: str | None = None
    ) -> str | None:
        """Resolve a label name to its id, creating the label if it's new.

        ``color`` is a canonical preset token applied to a freshly-created
        label (ignored if the label already exists — use ``set_label_color``)."""
        existing = await self._label_name_id_map()
        if name.lower() in existing:
            return existing[name.lower()]
        from .label_colors import gmail_color
        client = await self._get_client()
        body: dict[str, object] = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        gc = gmail_color(color) if color else None
        if gc:
            body["color"] = gc
        resp = await client.post("/users/me/labels", json=body)
        # A bad colour is the only likely rejection here — retry without it so
        # label creation never fails on colour alone.
        if not resp.is_success and gc:
            body.pop("color", None)
            resp = await client.post("/users/me/labels", json=body)
        if resp.is_success:
            new_id = resp.json().get("id")
            if new_id:
                # Keep the cache coherent so the very next message that carries
                # this label resolves its name instead of falling back to a
                # stale map (and so we don't re-POST a duplicate).
                if self._label_ids_by_name is not None:
                    self._label_ids_by_name[name.lower()] = new_id
                self._label_names_by_id[new_id] = name
            return new_id
        # Creation failed — most often because the label already exists and our
        # cache predates it. Re-read the label list once and try to resolve.
        return (await self._label_name_id_map(refresh=True)).get(name.lower())

    async def set_label_color(self, name: str, color: str) -> None:
        """Set a Gmail user label's colour (creating the label if needed)."""
        from .label_colors import gmail_color, is_preset
        if not is_preset(color):
            return
        # Ensure the label exists; if brand-new, the colour is set on create.
        existing = await self._label_name_id_map()
        label_id = existing.get(name.lower())
        if not label_id:
            await self._ensure_label_id(name, color=color)
            return
        gc = gmail_color(color)
        if not gc:
            return
        client = await self._get_client()
        await client.patch(f"/users/me/labels/{label_id}", json={"color": gc})

    async def create_folder(self, name: str) -> EmailFolder:
        """Gmail has labels, not folders — create (or reuse) a user label."""
        if not name or not name.strip():
            raise ValueError("Folder/label name is required")
        name = name.strip()
        label_id = await self._ensure_label_id(name)
        if not label_id:
            raise ValueError(f"Could not create Gmail label: {name!r}")
        return EmailFolder(
            provider_folder_id=label_id, name=name, type="user"
        )

    async def create_filter(
        self,
        *,
        from_email: str,
        archive: bool = True,
        label: str | None = None,
    ) -> str | None:
        """Create a Gmail settings filter so future mail from ``from_email``
        skips the inbox (and is optionally labeled) — provider-native, so it
        applies instantly and on every client, not just our sync sweep."""
        if not from_email:
            return None
        remove_ids: list[str] = ["INBOX"] if archive else []
        add_ids: list[str] = []
        if label:
            lid = await self._ensure_label_id(label)
            if lid:
                add_ids.append(lid)
        action: dict[str, Any] = {}
        if remove_ids:
            action["removeLabelIds"] = remove_ids
        if add_ids:
            action["addLabelIds"] = add_ids
        if not action:
            return None
        client = await self._get_client()
        resp = await client.post(
            "/users/me/settings/filters",
            json={"criteria": {"from": from_email}, "action": action},
        )
        # Gmail 409s when an identical filter already exists — treat as success
        # (the future-mail rule is in place either way), no duplicate.
        if resp.status_code == 409:
            return None
        resp.raise_for_status()
        return resp.json().get("id")

    async def delete_filter(self, filter_id: str) -> None:
        """Delete a Gmail settings filter (e.g. when a sender is re-approved)."""
        if not filter_id:
            return
        client = await self._get_client()
        resp = await client.delete(f"/users/me/settings/filters/{filter_id}")
        if resp.status_code not in (200, 204, 404):
            resp.raise_for_status()

    async def list_filters(self) -> list[dict[str, Any]]:
        """Read the Gmail settings filters for the rules screen (GM-15).

        WS-17 EM-G3b items 12 to 15 (§12.3.4). Each filter maps to the shape
        of ``OutlookProvider.list_filters``. ``users.settings.filters.list``
        answers with the key ``filter``, which is singular, and with ``{}``
        for a mailbox with no filter (E-F1). A plain 403, such as a missing
        ``gmail.settings.basic`` scope, gives ``[]``. ``GmailRateLimited``
        passes up, and the caller answers ``provider_rules_supported: false``
        for it (E-F5)."""
        client = await self._get_client()
        resp = await client.get("/users/me/settings/filters")
        if resp.status_code == 403:
            return []
        resp.raise_for_status()
        filters = resp.json().get("filter") or []
        if any(self._filter_user_label_ids(f) for f in filters):
            # Fills ``_label_names_by_id`` with the names of the user labels.
            await self._label_name_id_map()
        return [self._filter_view(f) for f in filters]

    def _filter_user_label_ids(self, gmail_filter: dict[str, Any]) -> list[str]:
        """The ids of the user labels that a filter adds. A system label id is
        an action of its own (star, trash), or the map ignores it."""
        action = gmail_filter.get("action") or {}
        return [lid for lid in action.get("addLabelIds") or []
                if not self._is_system_label_name(lid)]

    def _filter_view(self, gmail_filter: dict[str, Any]) -> dict[str, Any]:
        """One Gmail filter as ``{id, name, enabled, from_addresses, summary}``.

        A Gmail filter is always enabled. The name is ``From <from>``, else
        the query, else the subject. ``from_addresses`` holds ``criteria.from``
        as one whole string, because it is a Gmail query such as "a OR b"
        (E-F4). The summary tokens keep the order of item 14 (E-F2, E-F3)."""
        criteria = gmail_filter.get("criteria") or {}
        action = gmail_filter.get("action") or {}
        added = action.get("addLabelIds") or []
        removed = action.get("removeLabelIds") or []
        sender = criteria.get("from") or ""
        summary: list[str] = []
        if criteria.get("subject"):
            summary.append(f"subject contains “{criteria['subject']}”")
        if criteria.get("to"):
            summary.append(f"to contains “{criteria['to']}”")
        if criteria.get("query"):
            summary.append(f"matches “{criteria['query']}”")
        if criteria.get("hasAttachment"):
            summary.append("has attachment")
        label_ids = self._filter_user_label_ids(gmail_filter)
        if label_ids:
            # An unknown id shows as it is (E-F3).
            summary.append("label: " + ", ".join(
                self._label_names_by_id.get(lid, lid) for lid in label_ids))
        if "INBOX" in removed:
            summary.append("skip inbox")
        if "UNREAD" in removed:
            summary.append("mark read")
        if "STARRED" in added:
            summary.append("star")
        if action.get("forward"):
            summary.append("forward")
        if "TRASH" in added:
            summary.append("trash")
        name = (f"From {sender}" if sender
                else criteria.get("query") or criteria.get("subject") or "")
        return {
            "id": gmail_filter.get("id", ""),
            "name": name,
            "enabled": True,
            "from_addresses": [sender] if sender else [],
            "summary": summary,
        }

    async def set_labels(
        self,
        provider_message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        add_ids: list[str] = []
        for name in add or []:
            lid = await self._ensure_label_id(name)
            if lid:
                add_ids.append(lid)
        remove_ids: list[str] = []
        if remove:
            name_to_id = await self._label_name_id_map()
            for name in remove:
                lid = name_to_id.get(name.lower())
                if lid:
                    remove_ids.append(lid)
        if add_ids or remove_ids:
            await self.modify_message(
                provider_message_id,
                add_labels=add_ids or None,
                remove_labels=remove_ids or None,
            )

    # Deep initial sync page ceiling per label. A page holds ``max_results``
    # messages, 100 from the scheduler. The after:-query window normally
    # exhausts well before this. The sweep after a stale cursor uses the same
    # ceiling (EM-G4b E-B1).
    DEEP_SYNC_MAX_PAGES = 50
    #: The pages of one import (WS-17 EM-G5a, E-G5-6). One list reads all
    #: mail, so the cap is far above the deep sweep. It only guards against a
    #: list that never ends. At the cap the import ends and logs
    #: ``gmail.import_capped``. The model is ``OutlookProvider.IMPORT_MAX_PAGES``.
    IMPORT_MAX_PAGES = 5000
    #: The import reads all mail back to the floor, so the deep sync of a
    #: member act reconciles the deletions from it (WS-17 EM-G5b item 7).
    #: ``message_gone`` confirms each candidate by its id before a trash.
    import_full_snapshot = True
    #: Gmail gives a draft a new message id at each update (O-GM-2), so the
    #: reconcile leaves out each row in drafts (EM-G5b item 9, C3). The
    #: history removes an old draft row (EM-G4b E-B2).
    import_reconcile_skips_drafts = True

    async def _sweep_label(
        self,
        label: str,
        max_results: int,
        since: datetime | None = None,
        canonical_override: str | None = None,
    ) -> list[EmailMessage]:
        """Page a label to exhaustion (or the deep ceiling), optionally bounded
        to mail received after ``since`` via Gmail's ``after:`` query."""
        out: list[EmailMessage] = []
        token: str | None = None
        q = f"after:{since.strftime('%Y/%m/%d')}" if since else None
        for _ in range(self.DEEP_SYNC_MAX_PAGES):
            msgs, token = await self.list_messages(
                folder=label, query=q, max_results=max_results,
                page_token=token, canonical_override=canonical_override,
            )
            out.extend(msgs)
            if not token:
                break
        return out

    async def sync_messages(
        self,
        history_id: str | None = None,
        max_results: int = 100,
        deep: bool = False,
        since: datetime | None = None,
        catch_up: datetime | None = None,
        *,
        delta_shadow: bool = False,
    ) -> SyncResult:
        """One sync of a Gmail mailbox (WS-17 EM-G4b, spec §12.3.5.2).

        * ``deep`` pages each label back to ``since``, and it returns no
          cursor. The import does not use it since EM-G5a (E-G5-8):
          ``import_batches`` reads one list of all mail.
        * With a cursor, the sync reads ``history.list`` from it, page by
          page (items 2 to 4). The new cursor is the ``historyId`` of the
          last answer. A fetch that a later cycle can fix holds the cursor
          (EM-G4a-f2, ``_cursor_after_read``).
        * With no cursor, ``users.getProfile`` gives the seed FIRST. The
          sweep then reads the newest page of each label and returns the
          seed (item 1). So the next history read gets each change made
          during the sweep.
        * A stale cursor (404) seeds again and sweeps back to the catch-up
          watermark (items 5 and 6). A sweep that stops short returns no
          cursor, so the stale one stays (E-B1).

        ``catch_up`` is unused with a cursor, because the history reads each
        change since the cursor (item 7, D-EM-13). Gmail keeps its history
        for about one week, often longer. After a longer pause the cursor is
        stale, and the sweep of item 6 is the catch-up. ``delta_shadow`` is
        the Graph delta of Outlook, and Gmail ignores it (EM-T4d).
        """
        await self._get_client()
        # The fetch records of this call go into ``SyncResult.errors``
        # (EM-G4a item 10). A failed label still skips. A rate limit whose
        # tries are spent raises instead, so the sync fails (item 9).
        first_failure = len(self.fetch_failures)
        if deep:
            return await self._deep_sweep(max_results, since, first_failure)
        cursor = _parse_cursor(history_id) if history_id else None
        if cursor is not None:
            return await self._history_sync(cursor, max_results, since, catch_up)
        return await self._first_sweep(max_results, first_failure)

    async def seed_cursor(self) -> str | None:
        """The history id of the mailbox now, from ``users.getProfile``
        (WS-17 EM-G4b item 1, E-B3).

        The call goes through the client seam, so a rate limit raises
        ``GmailRateLimited``. Each other failure raises too. The scheduler
        and the sweeps catch it (``_try_seed``), so a failed seed never
        stops new mail."""
        client = await self._get_client()
        resp = await client.get("/users/me/profile")
        resp.raise_for_status()
        history_id = resp.json().get("historyId")
        return str(history_id) if history_id else None

    async def _try_seed(self) -> str | None:
        """``seed_cursor`` for a sweep. A failure never stops new mail (owner
        answer Q2): it logs ``gmail.seed_failed`` and gives None, so the
        sweep still runs and the next cycle seeds again. A rate limit whose
        tries are spent raises, so the sync fails (EM-G4a item 9)."""
        try:
            return await self.seed_cursor()
        except Exception as exc:
            _raise_rate_limit(exc)
            logger.warning("gmail.seed_failed error=%s status=%s",
                           type(exc).__name__, getattr(
                               getattr(exc, "response", None),
                               "status_code", None))
            return None

    async def _fetch_into(self, ids: list[str], result: SyncResult) -> list[str]:
        """Fetch each message of *ids* in full into *result* (items 3 and 6).

        A rate limit whose tries are spent raises, so the sync fails and the
        cursor stays (EM-G4a item 9). Each other failure leaves its record in
        ``result.errors`` (item 10). Returns the ids whose failure a later
        cycle can fix (``_transient``)."""
        failed: list[str] = []
        for mid in ids:
            try:
                result.messages.append(await self.get_message(mid))
            except Exception as exc:
                _raise_rate_limit(exc)
                result.errors.append(self._record_fetch_failure(mid, exc))
                if _transient(exc):
                    failed.append(mid)
                continue
            result.messages_synced += 1
        return failed

    async def _read_history(
        self, start: str, max_results: int,
    ) -> tuple[dict[str, None], dict[str, None], str | None]:
        """Read ``history.list`` from *start* to the last page (item 2).

        Returns the fetch set, the delete set and the ``historyId`` of the
        last answer. A 404 raises ``_StaleCursor`` (item 5). Any other
        failure raises, so the sync fails and the cursor stays."""
        client = await self._get_client()
        params: dict[str, Any] = {"startHistoryId": start,
                                  "maxResults": max_results,
                                  "historyTypes": _HISTORY_TYPES}
        fetch: dict[str, None] = {}
        deleted: dict[str, None] = {}
        last_id: str | None = None
        while True:
            resp = await client.get("/users/me/history", params=params)
            if resp.status_code == 404:
                raise _StaleCursor
            resp.raise_for_status()
            data = resp.json()
            _collect_history(data.get("history") or [], fetch, deleted)
            last_id = data.get("historyId") or last_id
            token = data.get("nextPageToken")
            if not token:
                return fetch, deleted, last_id
            params["pageToken"] = token

    async def _history_sync(
        self, cursor: tuple[str, int], max_results: int,
        since: datetime | None, catch_up: datetime | None,
    ) -> SyncResult:
        """The sync from a cursor (items 2 to 5, EM-G4a-f2)."""
        start, held_cycles = cursor
        try:
            fetch, deleted, last_id = await self._read_history(start, max_results)
        except _StaleCursor:
            return await self._recover_stale_cursor(max_results, since, catch_up)
        result = SyncResult()
        for mid in deleted:
            result.messages.append(_deleted_marker(mid))
        # A message that Gmail deleted has nothing to fetch.
        failed = await self._fetch_into(
            [mid for mid in fetch if mid not in deleted], result)
        result.messages_synced += len(deleted)
        result.new_history_id = _cursor_after_read(
            start, last_id, held_cycles, failed, result.errors)
        return result

    async def _recover_stale_cursor(
        self, max_results: int, since: datetime | None,
        catch_up: datetime | None,
    ) -> SyncResult:
        """A stale cursor (items 5 and 6, E-B1). Seed FIRST, then sweep all
        mail back to the watermark.

        A sweep that reads to the end returns the seed as the new cursor. A
        sweep that stops short returns no new cursor, so the stale one
        stays, and sets ``catch_up_incomplete``. The scheduler then keeps the
        watermark, and the next cycle sweeps again. ``reseed_history_id``
        always holds the seed, and the scheduler writes it only when it
        abandons the catch-up (``CATCH_UP_MAX_MISSES``). A failed seed makes
        the sweep short too, because it has no cursor to return.
        ``cursor_reset`` tells the scheduler to log the reset with the
        mailbox id, also when the seed failed (review round 1 F6)."""
        seed = await self._try_seed()
        result = SyncResult(reseed_history_id=seed, cursor_reset=True)
        after = _reset_sweep_after(catch_up, since)
        complete = await self._sweep_after_reset(max_results, after, result)
        if complete and seed is not None:
            result.new_history_id = seed
        else:
            result.catch_up_incomplete = True
            result.catch_up_folders = [GMAIL_RESET_SWEEP_NAME]
        return result

    async def _sweep_after_reset(
        self, max_results: int, after: datetime | None, result: SyncResult,
    ) -> bool:
        """Page all mail after *after* into *result* (item 6, E-B1).

        One list with ``q=after:<epoch seconds>``, ``includeSpamTrash`` and
        no ``labelIds``. It reads ``DEEP_SYNC_MAX_PAGES`` pages at most.
        Returns True when it read to the end. A page that fails, the page
        cap, or a fetch that a later cycle can fix makes it short. A rate
        limit raises."""
        client = await self._get_client()
        params: dict[str, Any] = {"maxResults": max_results,
                                  "includeSpamTrash": "true"}
        if after is not None:
            params["q"] = f"after:{int(after.timestamp())}"
        complete = True
        for _ in range(self.DEEP_SYNC_MAX_PAGES):
            try:
                resp = await client.get("/users/me/messages", params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                _raise_rate_limit(exc)
                logger.warning("gmail.reset_sweep_failed error=%s status=%s",
                               type(exc).__name__, getattr(
                                   getattr(exc, "response", None),
                                   "status_code", None))
                return False
            ids = [ref["id"] for ref in data.get("messages") or [] if ref.get("id")]
            if await self._fetch_into(ids, result):
                complete = False
            token = data.get("nextPageToken")
            if not token:
                return complete
            params["pageToken"] = token
        return False

    async def _deep_sweep(
        self, max_results: int, since: datetime | None, first_failure: int,
    ) -> SyncResult:
        """The deep sync: page each label back to ``since``. It returns no
        cursor. The import does not call it since EM-G5a (E-G5-8), and a
        fence in ``test_gmail_import.py`` proves it. It stays for a direct
        call of ``sync_messages(deep=True)``."""
        # One-time deep backfill: page every label back to ``since`` (incl.
        # SENT/DRAFT). The order of the labels does not set the folder:
        # each message keeps the folder of its parse (WS-17 EM-G2 item 8).
        deep_messages: list[EmailMessage] = []
        try:
            folders = await self.list_folders()
        except Exception as exc:
            _raise_rate_limit(exc)
            folders = []
        for f in folders:
            if f.type != "user":
                continue
            canon = canonical_folder(f.name)
            if canon in ("inbox", "sent", "drafts", "trash", "junk", "archive"):
                continue
            try:
                deep_messages.extend(await self._sweep_label(
                    f.provider_folder_id, max_results, since, canon
                ))
            except Exception as exc:
                _raise_rate_limit(exc)
                continue
        for label in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH"):
            try:
                deep_messages.extend(
                    await self._sweep_label(label, max_results, since)
                )
            except Exception as exc:
                _raise_rate_limit(exc)
                continue
        return SyncResult(
            messages_synced=len(deep_messages), messages=deep_messages,
            new_history_id=None,
            errors=self.fetch_failures[first_failure:],
        )

    async def _first_sweep(
        self, max_results: int, first_failure: int,
    ) -> SyncResult:
        """The sync with no cursor (item 1). Seed FIRST from
        ``users.getProfile``, then read the newest page of each label, and
        return the seed as the new cursor. The next history read then gets
        each change made during the sweep. A failed seed returns no cursor,
        and the next cycle seeds again (``_try_seed``)."""
        seed = await self._try_seed()
        # Initial full sync. The user labels, then five system labels.
        # The order does not set the folder: ``list_messages`` keeps the
        # folder of the parse, from the system labels only (WS-17 EM-G2
        # item 8, D-EM-33). A message that two labels carry comes twice,
        # and both copies have one folder.
        messages: list[EmailMessage] = []

        # User labels — so archived mail with a user label syncs. Such a
        # message files as ``archive``, and its labels go to
        # ``categories`` (O-GM-1). ``canonical_override`` sets no folder.
        try:
            folders = await self.list_folders()
        except Exception as exc:
            _raise_rate_limit(exc)
            folders = []
        for f in folders:
            if f.type != "user":
                continue
            canon = canonical_folder(f.name)
            # Skip anything that collapses onto a system folder key.
            if canon in ("inbox", "sent", "drafts", "trash", "junk", "archive"):
                continue
            try:
                user_msgs, _ = await self.list_messages(
                    folder=f.provider_folder_id,
                    max_results=max_results,
                    canonical_override=canon,
                )
                messages.extend(user_msgs)
            except Exception as exc:
                _raise_rate_limit(exc)
                continue

        # System labels. No label of this sweep reaches archived mail with
        # no user label (GM-8). The import reads that mail since EM-G5a, and
        # the history cursor reads each later change. This sweep runs only
        # with no cursor, after a failed seed. Known limit EM-G5-f1 records
        # it (``email_app_master_plan.md`` §12.3.6.1, E-G5-7).
        for label in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH"):
            try:
                label_msgs, _ = await self.list_messages(
                    folder=label, max_results=max_results
                )
                messages.extend(label_msgs)
            except Exception as exc:
                _raise_rate_limit(exc)
                continue

        return SyncResult(
            messages_synced=len(messages),
            messages=messages,
            new_history_id=seed,
            errors=self.fetch_failures[first_failure:],
        )

    async def import_batches(
        self,
        since: datetime | None,
        until: datetime | None = None,
        size: int = 100,
        *,
        on_estimate: EstimateCallback | None = None,
    ) -> AsyncIterator[list[EmailMessage]]:
        """The import of all mail, newest first (WS-17 EM-G5a, spec §12.3.6.1).

        One ``messages.list`` pages all mail, with ``q=after:<since>
        before:<bound>`` in epoch seconds, ``includeSpamTrash=true`` and no
        ``labelIds`` (item 1). So archived mail with no label comes too
        (GM-8), and a message with two labels comes once. Gmail lists the
        newest mail first, so each page is one batch, sorted again newest
        first (item 2). A message newer than ``until`` or older than
        ``since`` drops (``_in_import_window``).

        * A resume passes ``until``, and the bound is its whole second plus
          1 second (item 3, E-G5-3).
        * Each page fetches its ids in parallel, ``GMAIL_IMPORT_FETCHES`` at
          once, through the client seam, and each id once (item 4). The page
          gathers each fetch before it yields, so the storage limit closes
          the import with no fetch in flight (E-G5-5, ``_fetch_page``).
        * A fetch that a later cycle can fix fails the import, so the resume
          reads it again. A fetch that stays failed leaves its record, and
          the batch goes on (E-G5-4, ``_import_fetch``).
        * ``on_estimate`` is awaited once, with the ``resultSizeEstimate`` of
          the first answer, before the first fetch (item 5, D-EM-16).
        * ``IMPORT_MAX_PAGES`` pages at most. At the cap the import ends,
          logs ``gmail.import_capped`` (E-G5-6) and sets ``import_capped``,
          so the scheduler runs no reconcile (EM-G5b item 12).

        It never calls ``sync_messages(deep=True)`` (E-G5-8). A failed list
        page and a rate limit whose tries are spent raise, and the next
        cycle resumes from the point that the import reached."""
        self.import_capped = False
        size = max(size, 1)
        client = await self._get_client()
        await self._ensure_label_names()
        params: dict[str, Any] = {"maxResults": min(size, _GMAIL_LIST_MAX),
                                  "includeSpamTrash": "true"}
        query = _import_query(since, until)
        if query:
            params["q"] = query
        seen: set[str] = set()
        for page in range(1, self.IMPORT_MAX_PAGES + 1):
            resp = await client.get("/users/me/messages", params=params)
            resp.raise_for_status()
            data = resp.json()
            if page == 1 and on_estimate is not None:
                await on_estimate(_estimate_of(data))
            fetched = await self._fetch_page(_fresh_ids(data, seen))
            batch = [m for m in fetched if _in_import_window(m, since, until)]
            if batch:
                batch.sort(key=received_key, reverse=True)
                yield batch
            token = data.get("nextPageToken")
            if not token:
                return
            params["pageToken"] = token
        self.import_capped = True
        logger.warning("gmail.import_capped pages=%d messages=%d",
                       self.IMPORT_MAX_PAGES, len(seen))

    async def _fetch_page(self, ids: list[str]) -> list[EmailMessage]:
        """Fetch each message of *ids* in full, ``GMAIL_IMPORT_FETCHES`` at
        once (item 4, E-G5-5).

        The call gathers every fetch before it returns, so the import never
        yields with a fetch in flight. When one fetch raises, the call
        cancels the other fetches of the page, waits for them to end, and
        raises. A fetch that stays failed gives None, and drops here."""
        gate = asyncio.Semaphore(GMAIL_IMPORT_FETCHES)
        tasks = [asyncio.create_task(self._import_fetch(mid, gate)) for mid in ids]
        try:
            found = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return [m for m in found if m is not None]

    async def _import_fetch(
        self, message_id: str, gate: asyncio.Semaphore,
    ) -> EmailMessage | None:
        """One fetch of the import, through the client seam (E-G5-4).

        A rate limit whose tries are spent raises (EM-G4a item 9). Each other
        failure leaves its record (EM-G4a item 10). A failure that a later
        cycle can fix, a 5xx or a transport error, then raises too, so the
        import fails and the resume reads the message again. A failure that
        stays, such as a 404 or a parse error, gives None."""
        async with gate:
            try:
                return await self.get_message(message_id)
            except Exception as exc:
                _raise_rate_limit(exc)
                self._record_fetch_failure(message_id, exc)
                if _transient(exc):
                    raise
                return None

    async def message_gone(self, provider_message_id: str) -> bool:
        """True when Gmail deleted the message *provider_message_id* (WS-17
        EM-G5b items 6 and 10, C1 and C2).

        The reconcile of a member-act import asks this before it trashes a
        row (``scheduler._confirm_gone``). Gmail never changes the id of a
        message, so the provider id is the key. ``message_exists`` of Outlook
        asks by the Message-ID, and Gmail has no such method on purpose: one
        Message-ID can be on two Gmail messages.

        * A 404 whose body gives the reason ``notFound`` gives True.
        * A 200 gives False, also for a message in ``TRASH`` or ``SPAM``,
          because the import read both.
        * Each other answer raises, and that includes a 404 with no
          ``notFound`` reason. The caller then keeps the row. A rate limit
          whose tries are spent raises ``GmailRateLimited``, and the caller
          stops the confirm (item 11).
        * An id that is not a plain Gmail id raises ``ValueError``, and no
          request goes out.

        The read goes through ``_get_client``, so the rules of EM-G4a apply.
        It sends ``format=minimal`` only, so no body of the mail comes back."""
        if not _GMAIL_MESSAGE_ID.fullmatch(provider_message_id or ""):
            raise ValueError("the confirm takes a Gmail message id only")
        client = await self._get_client()
        resp = await client.get(f"/users/me/messages/{provider_message_id}",
                                params={"format": "minimal"})
        if resp.status_code == 404 and _says_not_found(resp):
            return True
        # Each answer that is not a 2xx raises here, a bare 404 too.
        resp.raise_for_status()
        return False

    async def get_attachment(
        self, provider_message_id: str, provider_attachment_id: str
    ) -> bytes:
        client = await self._get_client()
        resp = await client.get(
            f"/users/me/messages/{provider_message_id}/attachments/{provider_attachment_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        return base64.urlsafe_b64decode(data["data"])

    # ── Helpers ──────────────────────────────────────────────────────────

    def _parse_gmail_message(self, raw: dict[str, Any]) -> EmailMessage:
        """Parse a Gmail API message into our normalized EmailMessage.

        WS-17 EM-G2 (``email_app_master_plan.md`` §12.3.2). The body walk
        reads the MIME tree at any depth. Each header name reads in any case.
        The Message-ID keeps the form of Graph. The address headers split on
        the raw text. The folder comes from the system labels only.
        """
        payload = raw.get("payload", {}) or {}
        headers = self._parse_headers(payload.get("headers", []))

        # Extract body — the whole tree, each part in its own charset.
        body_text, body_html = _gmail_bodies(payload)

        # Snippet
        snippet = raw.get("snippet", "")

        # Attachments — real files only; inline ``cid:`` body images (signature
        # logos, pasted screenshots) are skipped, and nested multiparts walked.
        attachments = _collect_gmail_attachments(payload, raw["id"])

        # Labels
        label_ids = raw.get("labelIds", [])
        is_read = "UNREAD" not in label_ids
        is_starred = "STARRED" in label_ids
        is_flagged = "IMPORTANT" in label_ids
        importance = "high" if "IMPORTANT" in label_ids else "normal"
        # Gmail puts opaque label IDs on the message; the display names live in
        # the /labels listing, cached in _label_names_by_id (user labels only).
        # Projecting them here is what makes a rule-applied label ("Newsletter",
        # "Cold Email", …) survive a re-sync — set_labels writes the label to
        # Gmail, and this reads it back, so the round-trip is closed and the
        # ingest upsert can safely treat categories as authoritative.
        # If the map was never loaded (a label-list fetch failure), we say so
        # rather than reporting a confident empty list that would erase labels.
        names_by_id = self._label_names_by_id
        categories: list[str] = [
            names_by_id[lid] for lid in label_ids if lid in names_by_id
        ]

        unsubscribe_link = _parse_list_unsubscribe(
            headers.get("list-unsubscribe", "")
        ) or find_unsubscribe_link_in_html(body_html)

        return EmailMessage(
            provider_message_id=raw["id"],
            thread_id=raw.get("threadId"),
            folder=_gmail_folder_from_labels(label_ids),
            internet_message_id=_gmail_message_id(headers),
            labels=label_ids,
            from_address=self._parse_from(headers.get("from", "")),
            to_addresses=self._parse_address_list(headers.get("to", "")),
            cc_addresses=self._parse_address_list(headers.get("cc", "")),
            bcc_addresses=self._parse_address_list(headers.get("bcc", "")),
            subject=headers.get("subject", "(no subject)"),
            body_text=body_text,
            body_html=body_html,
            snippet=snippet[:200] if snippet else body_text[:200],
            has_attachments=len(attachments) > 0,
            attachments=attachments,
            is_read=is_read,
            is_starred=is_starred,
            is_flagged=is_flagged,
            importance=importance,
            categories=categories,
            # Only authoritative once the label map actually loaded — otherwise
            # an empty list is ignorance, not "the user removed the labels", and
            # ingest must keep whatever is already stored.
            categories_authoritative=self._label_ids_by_name is not None,
            unsubscribe_link=unsubscribe_link,
            received_at=self._parse_internal_date(raw.get("internalDate")),
            raw=raw,
        )

    @staticmethod
    def _parse_internal_date(internal_date: str | None) -> datetime | None:
        """Parse Gmail's internalDate (epoch ms as string) into a datetime."""
        if not internal_date:
            return None
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_headers(headers: list[dict]) -> dict[str, str]:
        """The headers of a message, keyed by the LOWER-CASE name.

        A header name has no case (RFC 5322). Senders write ``Message-ID``,
        ``Message-Id`` and ``message-id``, so each read uses the lower-case
        name (WS-17 EM-G2 item 4, GM-1). The first value of a name wins.
        """
        result: dict[str, str] = {}
        for h in headers or []:
            name = str(h.get("name", "")).lower()
            if name and name not in result:
                result[name] = str(h.get("value", ""))
        return result

    @staticmethod
    def _extract_email(header: str) -> str:
        """Extract email address from a header like 'Name <email>'."""
        if "<" in header and ">" in header:
            return header.split("<")[1].split(">")[0].strip()
        return header.strip()

    @staticmethod
    def _parse_address_list(header: str) -> list[EmailAddress]:
        """Each address of one address header (WS-17 EM-G2 item 6, GM-4).

        ``_split_addresses`` splits the RAW header, so a quoted comma, as in
        ``"Doe, John" <j@x.com>``, stays inside its name. The encoded words of
        each name decode AFTER the split, because a decoded name can hold a
        comma with no quotes.

        An entry with no ``@`` is left out. A comma outside quotes, as in
        ``Doe, John <j@x.com>``, splits off the fragment ``Doe``, and a
        fragment must never become a recipient (review round 1, P2).
        """
        if not header:
            return []
        addresses: list[EmailAddress] = []
        for name, email in _split_addresses(header):
            email = email.strip()
            if "@" in email:
                addresses.append(EmailAddress(
                    name=_decode_display_name(name.strip()), email=email))
        return addresses

    @classmethod
    def _parse_from(cls, header: str) -> EmailAddress:
        """The sender: the first address of ``From``.

        A comma outside quotes, as in ``Doe, John <j@x.com>``, makes the
        first entry the fragment ``Doe``, with no ``@`` (review round 1, P2).
        Then the old reading holds: the address is the text inside the angle
        brackets, and the name is the text before them. A header with no
        angle brackets keeps its text as the address, so the sender never
        goes blank.
        """
        pairs = _split_addresses(header) if header else []
        name, email = pairs[0] if pairs else ("", "")
        if "@" in email:
            return EmailAddress(
                name=_decode_display_name(name.strip()), email=email.strip())
        if "<" in header and ">" in header:
            before = header.split("<", 1)[0].strip().strip('"').strip()
            return EmailAddress(name=_decode_display_name(before),
                                email=cls._extract_email(header))
        return EmailAddress(name="", email=header.strip())
