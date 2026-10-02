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
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Any

import httpx

from .app_credentials import OAuthApp, token_fields
from .base import (
    Attachment,
    BaseEmailProvider,
    EmailAddress,
    EmailFolder,
    EmailMessage,
    RefreshingBearer,
    SyncResult,
    canonical_folder,
    find_unsubscribe_link_in_html,
)

logger = logging.getLogger(__name__)

# Gmail label IDs → canonical folder keys.  A message can carry several labels;
# the first match (in this priority order) wins.
_GMAIL_LABEL_TO_FOLDER = [
    ("TRASH", "trash"),
    ("SPAM", "junk"),
    ("DRAFT", "drafts"),
    ("SENT", "sent"),
    ("INBOX", "inbox"),
]


def _gmail_folder_from_labels(label_ids: list[str]) -> str:
    labels = set(label_ids or [])
    for label, folder in _GMAIL_LABEL_TO_FOLDER:
        if label in labels:
            return folder
    return "inbox"


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


def _collect_gmail_attachments(part: dict, msg_id: str) -> list[Attachment]:
    """Walk a Gmail payload tree and collect real (non-inline) attachments.

    Recurses through nested multiparts (``multipart/mixed`` →
    ``multipart/related`` → …) so attachments below the top level are found,
    and skips inline body images (see ``_gmail_part_is_inline``). A part counts
    as an attachment only when it has both a filename and a provider
    ``attachmentId`` (the handle used to download its bytes).
    """
    out: list[Attachment] = []

    def _walk(p: dict) -> None:
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
        for sub in p.get("parts", []) or []:
            _walk(sub)

    _walk(part)
    return out


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
        # No Authorization header here. RefreshingBearer sets the current
        # token on each request, and refreshes once on a 401 (EM-T4c).
        if self._http is None:
            await self.authenticate()
            self._http = httpx.AsyncClient(
                base_url=GMAIL_API_BASE,
                headers={"Content-Type": "application/json"},
                auth=RefreshingBearer(self),
                timeout=30.0,
            )
        return self._http

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
        """List messages carrying ``folder`` (a Gmail label ID).

        ``canonical_override`` forces the stored folder key (used when syncing a
        user label whose *name* — not its opaque ID — is the UI folder key).
        """
        client = await self._get_client()
        params: dict[str, Any] = {
            "maxResults": min(max_results, 500),
            "labelIds": [folder],
        }
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        resp = await client.get("/users/me/messages", params=params)
        resp.raise_for_status()
        data = resp.json()

        canon = canonical_override or canonical_folder(folder)
        messages: list[EmailMessage] = []
        for msg_ref in data.get("messages", []):
            # Fetch full message
            try:
                msg = await self.get_message(msg_ref["id"])
                msg.folder = canon
                messages.append(msg)
            except Exception:
                continue

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

    # Deep initial sync page ceiling per label (×500/page). The after:-query
    # window normally exhausts well before this.
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
    ) -> SyncResult:
        client = await self._get_client()

        if deep:
            # One-time deep backfill: page every label back to ``since`` (incl.
            # SENT/DRAFT). User labels first so system labels win the upsert.
            deep_messages: list[EmailMessage] = []
            try:
                folders = await self.list_folders()
            except Exception:
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
                except Exception:
                    continue
            for label in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH"):
                try:
                    deep_messages.extend(
                        await self._sweep_label(label, max_results, since)
                    )
                except Exception:
                    continue
            return SyncResult(
                messages_synced=len(deep_messages), messages=deep_messages,
                new_history_id=None,
            )

        if history_id:
            # Incremental sync via history.list
            params: dict[str, Any] = {
                "startHistoryId": history_id,
                "maxResults": max_results,
                "historyTypes": ["messageAdded", "messageDeleted", "labelAdded", "labelRemoved"],
            }
            resp = await client.get("/users/me/history", params=params)
            resp.raise_for_status()
            data = resp.json()

            result = SyncResult(new_history_id=data.get("historyId"))
            message_ids_to_fetch: set[str] = set()
            message_ids_deleted: set[str] = set()

            for history in data.get("history", []):
                for msg_added in history.get("messagesAdded", []):
                    mid = msg_added.get("message", {}).get("id")
                    if mid:
                        message_ids_to_fetch.add(mid)
                for msg_deleted in history.get("messagesDeleted", []):
                    mid = msg_deleted.get("message", {}).get("id")
                    if mid:
                        message_ids_deleted.add(mid)

            # Mark deleted messages as such
            for mid in message_ids_deleted:
                result.messages.append(EmailMessage(
                    provider_message_id=mid,
                    folder="TRASH",
                    labels=["TRASH"],
                    subject="[DELETED]",
                ))

            # Fetch full message for each added/updated message
            for mid in message_ids_to_fetch:
                try:
                    msg = await self.get_message(mid)
                    result.messages.append(msg)
                    result.messages_synced += 1
                except Exception as e:
                    result.errors.append(f"Failed to fetch message {mid}: {e}")

            result.messages_synced += len(message_ids_deleted)
            return result
        else:
            # Initial full sync. User labels are fetched FIRST and the system
            # labels LAST: the persistence layer upserts in list order, so when a
            # message carries both INBOX and a user label the system folder wins
            # and the message still shows in the inbox (Gmail labels are
            # many-to-many, our ``folder`` column is single-valued).
            messages: list[EmailMessage] = []

            # User labels — so the user's own folders aren't empty in the UI.
            try:
                folders = await self.list_folders()
            except Exception:
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
                except Exception:
                    continue

            # System labels last (so they win the upsert on shared messages).
            for label in ("INBOX", "SENT", "DRAFT", "SPAM", "TRASH"):
                try:
                    label_msgs, _ = await self.list_messages(
                        folder=label, max_results=max_results
                    )
                    messages.extend(label_msgs)
                except Exception:
                    continue

            return SyncResult(
                messages_synced=len(messages),
                messages=messages,
                new_history_id=None,  # will be set after first history call
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
        """Parse a Gmail API message into our normalized EmailMessage."""
        headers = self._parse_headers(raw.get("payload", {}).get("headers", []))

        # Extract body
        body_text = ""
        body_html = None
        payload = raw.get("payload", {})
        if "parts" in payload:
            for part in payload["parts"]:
                mime = part.get("mimeType", "")
                data = part.get("body", {}).get("data", "")
                if data:
                    decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                    if mime == "text/plain":
                        body_text = decoded
                    elif mime == "text/html":
                        body_html = decoded
        else:
            data = payload.get("body", {}).get("data", "")
            if data:
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
                body_text = decoded

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
            headers.get("List-Unsubscribe", "")
        ) or find_unsubscribe_link_in_html(body_html)

        return EmailMessage(
            provider_message_id=raw["id"],
            thread_id=raw.get("threadId"),
            folder=_gmail_folder_from_labels(label_ids),
            labels=label_ids,
            from_address=EmailAddress(
                name=headers.get("From", "").split("<")[0].strip(),
                email=self._extract_email(headers.get("From", "")),
            ),
            to_addresses=self._parse_address_list(headers.get("To", "")),
            cc_addresses=self._parse_address_list(headers.get("Cc", "")),
            bcc_addresses=self._parse_address_list(headers.get("Bcc", "")),
            subject=headers.get("Subject", "(no subject)"),
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
        result: dict[str, str] = {}
        for h in headers:
            name = h.get("name", "")
            value = h.get("value", "")
            if name not in result:
                result[name] = value
        return result

    @staticmethod
    def _extract_email(header: str) -> str:
        """Extract email address from a header like 'Name <email>'."""
        if "<" in header and ">" in header:
            return header.split("<")[1].split(">")[0].strip()
        return header.strip()

    @staticmethod
    def _parse_address_list(header: str) -> list[EmailAddress]:
        """Parse a comma-separated list of addresses."""
        if not header:
            return []
        addresses: list[EmailAddress] = []
        for part in header.split(","):
            part = part.strip()
            if not part:
                continue
            if "<" in part:
                name = part.split("<")[0].strip()
                email = part.split("<")[1].split(">")[0].strip()
            else:
                name = ""
                email = part
            addresses.append(EmailAddress(name=name, email=email))
        return addresses
