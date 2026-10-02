"""Abstract base class for email providers.

All email providers (Gmail, Microsoft Graph, IMAP) implement this interface.
The sync engine calls these methods without knowing the provider details.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)


class _RefreshableProvider(Protocol):
    """What :class:`RefreshingBearer` reads from an OAuth provider."""

    _access_token: str | None
    _refresh_token: str | None
    _refresh_lock: asyncio.Lock

    async def _refresh_access_token(self) -> None: ...


def _refresh_refused(exc: BaseException) -> bool:
    """True when the token endpoint refused a refresh for good (EM-T4c).

    ``_refresh_access_token`` sends one request, the POST to the token
    endpoint. A 400 or a 401 from that endpoint refuses the refresh token or
    the app. A ``ValueError`` means that the refresh token or the app
    credentials are missing. Each of these fails again on the next try.

    A timeout, a transport error, a 5xx or a body that is not JSON can pass,
    so this gives ``False`` for them."""
    if isinstance(exc, httpx.HTTPStatusError):
        return (exc.response.status_code in (400, 401)
                and exc.request.url.path.endswith("/token"))
    return (isinstance(exc, ValueError)
            and not isinstance(exc, json.JSONDecodeError))


class RefreshingBearer(httpx.Auth):
    """The bearer of an OAuth mail provider, with one refresh on a 401.

    WS-17 EM-T4c (``email_app_master_plan.md`` §10.4.6). The Gmail and the
    Outlook client used to carry the bearer as a header, written once when
    the client was built. A token that expired during a sync then failed each
    later request, and the sweep dropped each folder after it.

    This class reads ``provider._access_token`` on EACH request. On a 401,
    when the provider holds a refresh token, it refreshes under
    ``provider._refresh_lock`` and sends the same request once more. It
    refreshes only when the token is still the one that the request used,
    because another request may have refreshed it while this one waited.

    * A second 401 goes back to the caller. There is no third try.
    * A refresh that fails raises to the caller, which then fails as it did
      on the 401 before.
    * Every body on these clients is JSON or a query, so httpx can send it
      again. The flow reads the body into memory before the first try, so a
      stream cannot reach the second try empty.

    **One refresh for each token that a refresh cannot help.** A mailbox can
    answer 401 to each request while the token endpoint still works. Then
    each request would post to the token endpoint. So the flow remembers a
    token for which a refresh did not help: the token endpoint refused the
    refresh (:func:`_refresh_refused`), or the new token got a 401 too. Each
    later 401 with that token goes back to the caller with no refresh and no
    second try. A success with that token clears it, so a token that expires
    later can refresh again. A timeout, a transport error or a 5xx of the
    token endpoint is not remembered, so the next 401 tries the refresh
    again (fix round 2).

    The fence is ``tests/unit/test_email_provider_401_retry.py``.
    """

    def __init__(self, provider: _RefreshableProvider) -> None:
        self._provider = provider
        #: The token for which a refresh did not help, or ``None``.
        self._no_refresh_for: str | None = None

    def sync_auth_flow(
        self, request: httpx.Request,
    ) -> Generator[httpx.Request, httpx.Response, None]:
        raise RuntimeError("RefreshingBearer serves an httpx.AsyncClient only")

    def _cannot_help(self, token: str | None) -> bool:
        """True when a refresh did not help *token* before. ``None`` is
        never remembered, so a request with no token can still refresh."""
        return token is not None and token == self._no_refresh_for

    def _note_success(self, token: str | None, response: httpx.Response) -> None:
        """A success with a remembered token clears it."""
        if response.is_success and self._cannot_help(token):
            self._no_refresh_for = None

    async def _refresh_once(self, used: str | None) -> None:
        """Refresh under the lock, unless another request did it already or a
        refresh cannot help *used*."""
        provider = self._provider
        async with provider._refresh_lock:
            # Another request may have refreshed while this one waited.
            if provider._access_token != used or self._cannot_help(used):
                return
            try:
                await provider._refresh_access_token()
            except Exception as exc:
                if _refresh_refused(exc):
                    self._no_refresh_for = used
                raise
            logger.info(
                "provider.token_refreshed_on_401 provider=%s",
                provider.__class__.__name__,
            )

    async def async_auth_flow(
        self, request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        provider = self._provider
        await request.aread()
        used = provider._access_token
        request.headers["Authorization"] = f"Bearer {used}"
        response = yield request
        if response.status_code != 401 or not provider._refresh_token:
            self._note_success(used, response)
            return
        await self._refresh_once(used)
        fresh = provider._access_token
        if fresh == used:
            # No new token: the token endpoint refused this token in another
            # request, or it sent the same token back. A second try with
            # that token gets the same 401.
            self._no_refresh_for = used
            return
        request.headers["Authorization"] = f"Bearer {fresh}"
        retry = yield request
        if retry.status_code == 401:
            self._no_refresh_for = fresh
            logger.warning(
                "provider.token_refused_after_refresh provider=%s",
                provider.__class__.__name__,
            )
        else:
            self._note_success(fresh, retry)


# Canonical folder keys shared by the whole stack (DB, gateway query, UI store).
# Provider-specific folder names/IDs (Outlook ``parentFolderId``, Gmail label IDs,
# IMAP mailbox names) MUST be normalized to one of these before persisting so the
# inbox query ``WHERE folder = 'inbox'`` actually matches.  The workbench email
# store uses these exact lowercase keys.
_CANONICAL_FOLDERS: dict[str, str] = {
    "inbox": "inbox",
    "sent": "sent",
    "sentitems": "sent",
    "sent items": "sent",
    "sent mail": "sent",
    "drafts": "drafts",
    "draft": "drafts",
    "trash": "trash",
    "deleteditems": "trash",
    "deleted items": "trash",
    "bin": "trash",
    "archive": "archive",
    "junk": "junk",
    "junkemail": "junk",
    "junk email": "junk",
    "spam": "junk",
}


def canonical_folder(name: str | None) -> str:
    """Normalize a provider folder name/ID to a canonical lowercase key.

    Unknown names fall through lowercased (so user-created folders keep a stable
    key) and a missing name defaults to ``inbox``.
    """
    if not name:
        return "inbox"
    key = name.strip().lower()
    return _CANONICAL_FOLDERS.get(key, key)


# Anchor tags + the words that signal an unsubscribe / opt-out link in a body.
_UNSUB_ANCHOR_RE = re.compile(
    r'<a\b[^>]*\bhref\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_UNSUB_WORDS = (
    "unsubscribe", "opt out", "opt-out", "optout", "manage preferences",
    "manage your subscription", "manage subscription", "email preferences",
    "notification settings", "subscription preferences", "stop receiving",
    "remove me", "update your preferences",
)


def find_unsubscribe_link_in_html(html: str | None) -> str | None:
    """Best-effort: scrape an unsubscribe URL from an email's HTML body.

    Fallback for senders that ship no ``List-Unsubscribe`` header but do put an
    "Unsubscribe" link in the body (very common for marketing mail). Returns the
    first ``http(s)`` href whose URL or visible anchor text mentions
    unsubscribe / opt-out / preferences, else ``None``. Mirrors inbox-zero's
    ``findUnsubscribeLink`` (which uses cheerio); we use a dependency-free regex.
    """
    if not html:
        return None
    for m in _UNSUB_ANCHOR_RE.finditer(html):
        href = (m.group(1) or "").strip()
        if not href.lower().startswith("http"):
            continue
        text = re.sub(r"<[^>]+>", " ", m.group(2) or "").lower()
        haystack = f"{href.lower()} {text}"
        if any(word in haystack for word in _UNSUB_WORDS):
            # Decode the entity-escaped ampersands mailers put in href query
            # strings so the resulting URL is actually fetchable.
            return href.replace("&amp;", "&")
    return None


def best_unsubscribe_link(header_value: str | None, html: str | None) -> str | None:
    """Pick the best unsubscribe target: the RFC ``List-Unsubscribe`` header
    (one-click capable — https preferred, else mailto:) when present, otherwise a
    link scraped from the HTML body. Returns ``None`` when neither exists."""
    return _parse_list_unsubscribe(header_value) or find_unsubscribe_link_in_html(html)


def _parse_list_unsubscribe(header: str | None) -> str | None:
    """Pick the best target from a ``List-Unsubscribe`` header.

    The header is a comma-separated list of ``<...>`` targets, e.g.
    ``<https://x.com/unsub?id=1>, <mailto:unsub@x.com>``. Prefer an https
    one-click URL (RFC 8058); fall back to a ``mailto:``. ``None`` if neither.
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


@dataclass
class EmailAddress:
    name: str
    email: str


@dataclass
class Attachment:
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    provider_attachment_id: str


@dataclass
class EmailMessage:
    """Normalized email message across all providers."""
    provider_message_id: str
    thread_id: str | None
    folder: str
    # The RFC 5322 Message-ID header — stable across a provider re-key. Outlook
    # changes provider_message_id when a message moves folders, which would
    # otherwise insert a duplicate "ghost" row; the ingest upsert dedupes on this
    # instead. None when the provider doesn't expose it (kept nullable so nothing
    # that omits it breaks).
    internet_message_id: str | None = None
    labels: list[str] = field(default_factory=list)
    from_address: EmailAddress | None = None
    to_addresses: list[EmailAddress] = field(default_factory=list)
    cc_addresses: list[EmailAddress] = field(default_factory=list)
    bcc_addresses: list[EmailAddress] = field(default_factory=list)
    subject: str = ""
    body_text: str = ""
    body_html: str | None = None
    snippet: str = ""
    has_attachments: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    is_read: bool = False
    is_starred: bool = False
    is_flagged: bool = False
    importance: str = "normal"  # 'high' | 'normal' | 'low'
    categories: list[str] = field(default_factory=list)
    # True only when this provider genuinely round-trips user labels/categories,
    # so an empty ``categories`` means "the user removed them" rather than "this
    # provider doesn't report them". Ingest REPLACES the stored categories only
    # when this is set; otherwise it keeps what's already there. Without this,
    # a provider that never fills ``categories`` (generic IMAP, and Gmail before
    # its label-name map existed) silently erased every label the rule engine had
    # applied on the next re-sync — and since ``rules_processed_at`` was already
    # stamped, the rules never re-applied them. See persist._ON_CONFLICT_UPDATE.
    categories_authoritative: bool = False
    # Best unsubscribe target parsed from the List-Unsubscribe header (https
    # one-click preferred, else mailto:). Powers bulk unsubscribe.
    unsubscribe_link: str | None = None
    received_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class EmailFolder:
    """Normalized folder/label."""
    provider_folder_id: str
    name: str
    type: str  # 'system' | 'user'
    message_count: int = 0
    unread_count: int = 0
    # Canonical colour token ('preset0'..'preset24') for user labels, or None
    # when uncoloured. See providers/label_colors.py.
    color: str | None = None


@dataclass
class SyncResult:
    """Result of a sync operation."""
    messages_synced: int = 0
    messages_skipped: int = 0
    new_history_id: str | None = None
    messages: list[EmailMessage] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # True when ``messages`` is a complete multi-folder snapshot (every folder
    # swept), so the caller may reconcile provider-side deletions: a stored
    # message absent from the snapshot was removed on the provider.  False for
    # incremental syncs (Gmail history, IMAP UIDNEXT) where absence means
    # "unchanged", not "deleted".
    full_snapshot: bool = False
    # True when a sweep page failed before its folder read back to the
    # catch-up watermark (EM-T6b fix round 2). The messages above hold what
    # the sweep read. Phase (d) writes them and keeps ``last_synced_at``.
    catch_up_incomplete: bool = False


#: The callback that an import calls once, before its first batch, with the
#: count of the messages that it expects to read. ``None`` means "unknown"
#: (WS-17 EM-T6b item 3).
EstimateCallback = Callable[[int | None], Awaitable[None]]

#: The sort key of a message with no ``received_at``: older than any date.
_NO_DATE = datetime.min.replace(tzinfo=UTC)


def _aware(value: datetime) -> datetime:
    """``value`` with a time zone. A naive value is read as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def received_key(msg: EmailMessage) -> datetime:
    """The time of *msg* for a newest-first order, always with a time zone.

    A message with no date sorts as the oldest."""
    return _NO_DATE if msg.received_at is None else _aware(msg.received_at)


class BaseEmailProvider(ABC):
    """Abstract email provider interface."""

    #: True when ``import_batches`` reads every folder of the sweep, so its
    #: messages are a full snapshot of each folder back to the floor. The
    #: deep sync of a member act then reconciles deletions from it (EM-T6b).
    import_full_snapshot: bool = False

    def __init__(self, credentials: dict[str, Any]):
        self.credentials = credentials

    def credentials_dirty(self) -> bool:
        """Whether the in-memory credentials changed (e.g. token refresh).

        Providers that rotate OAuth tokens override this so the caller can
        persist the refreshed credentials back to storage.  Defaults to False
        for providers (like IMAP) that never mutate their credentials.
        """
        return False

    def export_credentials(self) -> dict[str, Any]:
        """Return the current credentials for persistence after a refresh."""
        return self.credentials

    @abstractmethod
    async def authenticate(self) -> bool:
        """Validate credentials and obtain an access token.

        Returns True if authentication succeeded.
        """
        ...

    @abstractmethod
    async def list_folders(self) -> list[EmailFolder]:
        """List all folders/labels for the account."""
        ...

    @abstractmethod
    async def list_messages(
        self,
        folder: str = "INBOX",
        query: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
    ) -> tuple[list[EmailMessage], str | None]:
        """List email headers (without full body) for a folder.

        Returns (messages, next_page_token).
        """
        ...

    @abstractmethod
    async def get_message(self, provider_message_id: str) -> EmailMessage:
        """Get full email message including body."""
        ...

    @abstractmethod
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
        """Send an email. Returns the provider message ID of the sent message.

        ``attachments`` is a list of ``{"filename", "content" (bytes),
        "mime_type"}`` dicts to attach to the outgoing message.

        ``reply_to_message_id`` / ``thread_id`` carry threading intent. Their
        exact meaning is provider-specific (Gmail threads by ``thread_id``,
        Outlook replies to ``reply_to_message_id``, IMAP sets In-Reply-To from
        whichever is given) — callers should pass both when replying and let the
        provider use what it needs."""
        ...

    @abstractmethod
    async def modify_message(
        self,
        provider_message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        """Modify labels/read-state on a message."""
        ...

    @abstractmethod
    async def trash_message(self, provider_message_id: str) -> str | None:
        """Move message to trash.

        Returns the message's new provider id if the operation re-keys it
        (e.g. Outlook /move issues a new id), otherwise ``None``.
        """
        ...

    async def apply_flags(
        self,
        provider_message_id: str,
        *,
        is_read: bool | None = None,
        is_starred: bool | None = None,
        is_flagged: bool | None = None,
    ) -> None:
        """Push read/star/flag state changes to the provider (two-way sync).

        Default implementation is a no-op so providers that don't support a
        given flag (e.g. IMAP stars) degrade gracefully.  Gmail/Outlook override.
        """
        return None

    async def move_to_folder(
        self, provider_message_id: str, folder: str
    ) -> str | None:
        """Move a message to the given canonical folder on the provider.

        ``folder`` is a canonical key (inbox/archive/trash/junk/...).  Default
        implementation is a no-op; providers override with their semantics
        (Gmail = label changes, Outlook = /move, IMAP = COPY+EXPUNGE).

        Returns the message's new provider id if the move re-keys it (Outlook
        /move returns a fresh id), otherwise ``None`` (id unchanged).
        """
        return None

    # Canonical bulk actions, shared by every provider so callers name them once.
    BULK_ACTIONS = ("archive", "trash", "read", "unread", "star", "unstar")

    async def bulk_apply(
        self, provider_message_ids: list[str], action: str,
        failed_out: list[str] | None = None,
    ) -> dict[str, str]:
        """Apply one of :attr:`BULK_ACTIONS` to many messages.

        Returns ``{old_provider_id: new_provider_id}`` for messages the provider
        re-keyed (Outlook ``/move`` mints a fresh id); callers MUST persist those
        or every follow-up action on the message 404s until the next full sync.

        ``failed_out``, when given, collects the ids this could NOT apply. One
        failed message never aborts the rest — at 10,000 messages a single 404 on
        a since-deleted mail would otherwise strand the other 9,999 half-applied
        — but swallowing the failure entirely was its own bug: the local mirror
        had already been written, so the caller believed a message was archived
        that the provider still had in the inbox, and the next sync silently
        pulled it back. Callers that care pass a list and retry what's in it.

        The default walks the per-message API one call at a time — correct
        everywhere, slow at volume. Providers with a native batch endpoint
        override this (see :class:`GmailProvider`).
        """
        rekeys: dict[str, str] = {}
        for pmid in provider_message_ids:
            try:
                new_id: str | None = None
                if action == "archive":
                    new_id = await self.move_to_folder(pmid, "archive")
                elif action == "trash":
                    new_id = await self.trash_message(pmid)
                elif action in ("read", "unread"):
                    await self.apply_flags(pmid, is_read=action == "read")
                elif action in ("star", "unstar"):
                    await self.apply_flags(pmid, is_starred=action == "star")
                else:
                    raise ValueError(f"unknown bulk action {action!r}")
                if new_id and new_id != pmid:
                    rekeys[pmid] = new_id
            except Exception as exc:  # noqa: BLE001
                if failed_out is not None:
                    failed_out.append(pmid)
                logger.warning(
                    "provider.bulk_apply_item_failed provider=%s action=%s "
                    "pmid=%s error=%s",
                    self.__class__.__name__, action, pmid, str(exc)[:120],
                )
        return rekeys

    async def create_folder(self, name: str) -> EmailFolder:
        """Create (or reuse) a folder named ``name`` and return it normalized.

        Default raises NotImplementedError so callers can log and skip on
        providers without a folder concept.  Outlook creates a mail folder;
        Gmail creates a label; IMAP creates a mailbox.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support creating folders"
        )

    async def create_filter(
        self,
        *,
        from_email: str,
        archive: bool = True,
        label: str | None = None,
    ) -> str | None:
        """Create a provider-native filter so FUTURE mail from ``from_email`` is
        auto-archived (skips the inbox) and optionally labeled.

        Returns a provider filter/rule id, or ``None`` when the provider has no
        filter concept (generic IMAP) or the filter already exists. Gmail
        creates a settings filter; Outlook creates an Inbox message rule. When
        this returns ``None`` the server-side ``_maybe_auto_archive`` sweep is
        the fallback that keeps the inbox clean on each sync.
        """
        return None

    async def delete_filter(self, filter_id: str) -> None:
        """Remove a previously-created auto-archive filter/rule by its id.

        Called when a sender is re-approved ("Keep") so future mail stops being
        auto-archived at the provider. Default no-op for providers without
        filters; an already-missing filter is ignored."""
        return None

    async def list_filters(self) -> list[dict[str, Any]]:
        """The provider-native inbox rules/filters, read-only, as plain dicts:

        ``{id, name, enabled, from_addresses: [str], summary: [str]}``

        ``summary`` is a list of human tokens describing conditions beyond the
        sender plus the rule's actions (e.g. ``["subject contains 'invoice'",
        "move to folder", "mark read"]``). Used to DISPLAY upstream rules in the
        app's rules screen — never to mutate them. Default: empty, for
        providers with no filter concept or no read scope.
        """
        return []

    async def list_labels(self) -> list[dict[str, str | None]]:
        """User-applicable labels/categories as ``{name, color}`` dicts.

        ``color`` is a canonical preset token ('preset0'..'preset24') or None.
        Gmail = user labels, Outlook = master categories.  Default (e.g. generic
        IMAP, which has no label concept) returns nothing.
        """
        return []

    async def set_labels(
        self,
        provider_message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """Apply/remove labels (by name) on a message; creating labels if needed.

        Default is a no-op so providers without labels degrade gracefully.
        """
        return None

    # True only where fetch_label_assignments below is really implemented.
    # Callers MUST branch on this rather than on an empty result: "{}" from a
    # provider that cannot read labels back is indistinguishable from "{}"
    # meaning the mailbox genuinely has no labels, and reporting the second
    # when the first is true tells the user their labels are gone when they are
    # sitting right there upstream.
    SUPPORTS_LABEL_READBACK: bool = False

    async def fetch_label_assignments(
        self, max_pages: int = 20,
    ) -> dict[str, list[str]]:
        """``{provider_message_id: [label name, …]}`` for the whole mailbox.

        The cheap way to answer "which of my messages carry which labels" without
        re-downloading a single message body — Gmail can list message IDs per
        label, so this costs roughly one paged request PER LABEL rather than per
        message.

        Exists to repair local state: labels live upstream, and a mailbox whose
        stored ``categories`` were lost (see EmailMessage.categories_
        authoritative) can be restored from this in seconds instead of forcing a
        full deep re-sync. Default {} for providers with no label concept —
        guard with ``SUPPORTS_LABEL_READBACK`` before believing that empty.
        """
        return {}

    async def set_label_color(self, name: str, color: str) -> None:
        """Set a label/category's colour (canonical 'presetN' token).

        Creates the label/category if it doesn't exist yet so the colour
        sticks.  Default is a no-op for providers without label colours.
        """
        return None

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
        """Create a DRAFT message (not sent) on the provider; return its id.

        ``cc`` / ``bcc`` (optional): additional recipients stored ON the draft, so
        a draft with a Cc survives a reopen/edit instead of silently losing it (the
        reason the composer used to fall back to a full send for any Cc'd reply).

        ``attachments`` (optional): a list of ``{"filename": str, "content":
        bytes, "mime_type": str}`` to attach to the draft.

        Used by Assistant reply/forward/draft rule actions. Raises
        NotImplementedError if the provider doesn't support drafts so the caller
        can log and skip rather than fail the whole rule.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support drafts"
        )

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
        """Update an existing draft in place; return the (possibly new) draft id.

        ``thread_id`` (when the provider needs it, e.g. Gmail) keeps the draft
        attached to its conversation across the update — omitting it would strip
        the draft's threading. Providers that thread implicitly ignore it.

        ``attachments`` (optional, same ``{"filename", "content", "mime_type"}``
        shape as ``create_draft``): files to add to the draft. Callers pass these
        only once, at the explicit pre-send save — the debounced auto-save omits
        them — so providers may add them unconditionally without duplicating.

        Default raises NotImplementedError so callers can fall back to creating a
        fresh draft on providers without an update primitive.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support updating drafts"
        )

    async def send_draft(self, draft_id: str) -> str | None:
        """Send an existing draft natively (Drafts → Sent), no duplicate.

        Default raises NotImplementedError so callers can fall back to sending a
        fresh message and deleting the draft.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support sending drafts"
        )

    @abstractmethod
    async def sync_messages(
        self,
        history_id: str | None = None,
        max_results: int = 100,
        deep: bool = False,
        since: datetime | None = None,
        catch_up: datetime | None = None,
    ) -> SyncResult:
        """Incremental sync — fetch new/updated messages since history_id.

        If history_id is None, performs an initial full sync.  ``deep`` requests
        the one-time deep backfill (page each folder back to ``since``); when
        False the sync stays shallow/incremental.  ``since`` is the import floor.
        The scheduler passes it on every sync, deep or shallow (EM-T6a).
        Providers that support a server-side date filter use it.

        ``catch_up`` is the watermark of the recurring sweep after a pause
        (WS-17 EM-T6b item 9, D-EM-13). A provider that pages newest first
        reads more pages while its last page holds only mail newer than it.
        A provider with an incremental cursor ignores it.
        """
        ...

    async def import_batches(
        self,
        since: datetime | None,
        until: datetime | None = None,
        size: int = 100,
        *,
        on_estimate: EstimateCallback | None = None,
    ) -> AsyncIterator[list[EmailMessage]]:
        """The import of a mailbox, in lists of up to ``size`` messages.

        WS-17 EM-T6b item 1. Each list is newest first, and the lists in
        sequence are newest first across every swept folder. The import reads
        no message older than ``since``, and none newer than ``until`` when
        ``until`` is set. A resume passes the point that it reached as
        ``until``.

        ``on_estimate`` is awaited at most once, before the first list. This
        default never calls it. It calls ``sync_messages(deep=True,
        since=since)``, drops each message newer than ``until``, sorts and
        cuts. Gmail and IMAP use it. Outlook merges its folders page by page.
        """
        size = max(size, 1)
        result = await self.sync_messages(deep=True, since=since)
        top = None if until is None else _aware(until)
        kept = [m for m in result.messages
                if top is None or m.received_at is None or received_key(m) <= top]
        kept.sort(key=received_key, reverse=True)
        for start in range(0, len(kept), size):
            yield kept[start:start + size]

    @abstractmethod
    async def get_attachment(
        self, provider_message_id: str, provider_attachment_id: str
    ) -> bytes:
        """Download an attachment's raw bytes."""
        ...
