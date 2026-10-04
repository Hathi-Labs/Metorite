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
import random
from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime, timezone
from email.errors import HeaderParseError
from email.header import decode_header, make_header
from email.message import Message
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
    ProviderRateLimited,
    RefreshingBearer,
    SyncResult,
    canonical_folder,
    find_unsubscribe_link_in_html,
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
GMAIL_SCOPES = ["https://mail.google.com/"]

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
        # Build RFC 2822 message — multipart when file attachments are present.
        msg: Any
        if attachments:
            from email import encoders  # noqa: PLC0415
            from email.mime.base import MIMEBase  # noqa: PLC0415
            from email.mime.multipart import MIMEMultipart  # noqa: PLC0415
            msg = MIMEMultipart()
            msg.attach(MIMEText(body_text, "plain" if not body_html else "html"))
            for att in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att.get("content") or b"")
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{att.get("filename", "attachment")}"',
                )
                msg.attach(part)
        else:
            msg = MIMEText(body_text, "plain" if not body_html else "html")
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = ", ".join(cc)
        if bcc:
            msg["Bcc"] = ", ".join(bcc)

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

        body: dict[str, Any] = {"raw": raw}
        # Gmail threads by threadId. Prefer the real conversation id
        # (``thread_id``); fall back to ``reply_to_message_id`` only when a caller
        # passes a message id as the thread anchor. Passing a non-thread message
        # id here would fail to thread — the "reply shows as a separate email" bug.
        tid = thread_id or reply_to_message_id
        if tid:
            body["threadId"] = tid

        client = await self._get_client()
        resp = await client.post("/users/me/messages/send", json=body)
        resp.raise_for_status()
        data = resp.json()
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
        """Create a Gmail draft (drafts.create); threads it when a thread id is
        given so the reply lands in the right conversation. Adds file
        attachments via a MIME multipart message when provided."""
        msg: Any
        if attachments:
            from email.mime.multipart import MIMEMultipart  # noqa: PLC0415
            from email.mime.base import MIMEBase  # noqa: PLC0415
            from email import encoders  # noqa: PLC0415
            msg = MIMEMultipart()
            msg.attach(MIMEText(body_text, "plain" if not body_html else "html"))
            for att in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att.get("content") or b"")
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{att.get("filename", "attachment")}"',
                )
                msg.attach(part)
        else:
            msg = MIMEText(body_text, "plain" if not body_html else "html")
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(a for a in cc if a)
        if bcc:
            msg["Bcc"] = ", ".join(a for a in bcc if a)
        msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message: dict[str, Any] = {"raw": raw}
        tid = thread_id or reply_to_message_id
        if tid:
            message["threadId"] = tid
        client = await self._get_client()
        resp = await client.post("/users/me/drafts", json={"message": message})
        resp.raise_for_status()
        return resp.json().get("id", "")

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
        """Replace a Gmail draft's content in place (drafts.update). Returns the
        (unchanged) draft id so the editor keeps tracking the same draft.

        When ``body_html`` is given the draft is written as HTML (so a signed
        HTML body survives the update); otherwise it stays plain text. When
        ``attachments`` are supplied the draft is rebuilt as a MIME multipart so
        the files ride along (Gmail replaces the whole draft on update)."""
        body_part = MIMEText(
            body_html or body_text or "", "html" if body_html else "plain")
        if attachments:
            from email.mime.multipart import MIMEMultipart  # noqa: PLC0415
            from email.mime.base import MIMEBase  # noqa: PLC0415
            from email import encoders  # noqa: PLC0415
            msg: Any = MIMEMultipart()
            msg.attach(body_part)
            for att in attachments:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(att.get("content") or b"")
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    f'attachment; filename="{att.get("filename", "attachment")}"',
                )
                msg.attach(part)
        else:
            msg = body_part
        if to is not None:
            msg["To"] = ", ".join(to)
        if cc is not None:
            msg["Cc"] = ", ".join(a for a in cc if a)
        if bcc is not None:
            msg["Bcc"] = ", ".join(a for a in bcc if a)
        if subject is not None:
            msg["Subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        message: dict[str, Any] = {"raw": raw}
        # threadId MUST be re-supplied on update — Gmail drops the draft's thread
        # otherwise, so the sent reply would start a new conversation.
        if thread_id:
            message["threadId"] = thread_id
        client = await self._get_client()
        resp = await client.put(
            f"/users/me/drafts/{draft_id}", json={"message": message}
        )
        resp.raise_for_status()
        return resp.json().get("id", draft_id)

    async def send_draft(self, draft_id: str) -> str | None:
        """Send an existing Gmail draft natively (drafts.send) — Drafts → Sent."""
        client = await self._get_client()
        resp = await client.post("/users/me/drafts/send", json={"id": draft_id})
        resp.raise_for_status()
        return (resp.json() or {}).get("id")

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
        client = await self._get_client()
        resp = await client.post(
            f"/users/me/messages/{provider_message_id}/trash"
        )
        resp.raise_for_status()

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

    async def move_to_folder(self, provider_message_id: str, folder: str) -> None:
        """Move via Gmail label mutation (Gmail has labels, not folders)."""
        folder = (folder or "").lower()
        if folder == "trash":
            await self.trash_message(provider_message_id)
        elif folder == "archive":
            # Archiving in Gmail = removing the INBOX label.
            await self.modify_message(provider_message_id, remove_labels=["INBOX"])
        elif folder == "inbox":
            await self.modify_message(
                provider_message_id, add_labels=["INBOX"], remove_labels=["TRASH", "SPAM"]
            )
        elif folder in ("junk", "spam"):
            await self.modify_message(
                provider_message_id, add_labels=["SPAM"], remove_labels=["INBOX"]
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

        * ``deep`` pages each label back to ``since``. The import uses it,
          and it returns no cursor.
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
        """The deep backfill of the import: page each label back to
        ``since``. It returns no cursor."""
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

        # System labels. EM-G5 owns the archived mail with no user label
        # (GM-8), which no label of this sweep reaches.
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
