"""Microsoft 365 / Outlook provider.

Uses the Microsoft Graph REST API with OAuth 2.0 authentication.
Supports both personal Outlook.com accounts and Microsoft 365 work/school accounts.

API reference: https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import httpx

from .base import (
    Attachment,
    BaseEmailProvider,
    EmailAddress,
    EmailFolder,
    EmailMessage,
    SyncResult,
    canonical_folder,
    find_unsubscribe_link_in_html,
)


GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

# Cap on how long we'll honour a Graph 429 ``Retry-After`` before giving up on
# the retry. Graph throttles bulk labelling; a short, bounded wait lets the apply
# succeed instead of the caller eating the 429 and logging a phantom failure —
# but we never block a whole sync cycle on one throttled message.
_MAX_RETRY_AFTER_SECS = 30.0

# Canonical keys that mean "system folder" (used to classify by display name on
# personal/consumer accounts, which omit wellKnownName — see below).
_CORE_CANONICAL = frozenset({"inbox", "sent", "drafts", "trash", "junk", "archive"})

# Display names Graph reports for well-known/system folders. We classify against
# these because personal (MSA/consumer) accounts reject ``$select=wellKnownName``
# with HTTP 400, so that property is never requested/returned for them and the
# only reliable signal left is the (English) folder name.
_OUTLOOK_SYSTEM_FOLDER_NAMES = frozenset({
    "inbox", "drafts", "sent items", "deleted items", "junk email",
    "junk e-mail", "archive", "outbox", "conversation history", "clutter",
    "notes", "rss feeds", "rss subscriptions", "sync issues",
    "scheduled", "snoozed",
})

# Diagnostic folders Outlook desktop clients auto-create to log synchronization
# problems (the "Sync Issues" tree). They aren't real mailboxes — hide them and
# their whole subtree from the folder list so they don't clutter the sidebar.
_OUTLOOK_HIDDEN_FOLDER_NAMES = frozenset({
    "sync issues", "conflicts", "local failures", "server failures",
})


def _classify_folder_type(folder: dict[str, Any]) -> str:
    """Return 'system' or 'user' for a Graph mailFolder.

    Prefers Graph's ``wellKnownName`` (present on work/school accounts) and falls
    back to matching the display name, since consumer accounts never return it.
    """
    if folder.get("wellKnownName"):
        return "system"
    name = (folder.get("displayName") or "").strip().lower()
    if name in _OUTLOOK_SYSTEM_FOLDER_NAMES:
        return "system"
    return "system" if canonical_folder(name) in _CORE_CANONICAL else "user"


GRAPH_SCOPES = [
    # offline_access is REQUIRED on refresh — without it Microsoft returns a new
    # access token but NOT a renewed refresh token, so the refresh chain expires
    # and the account eventually needs a manual "reconnect".
    "offline_access",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/User.Read",
    # Lets us create Outlook master categories (coloured labels) via
    # /me/outlook/masterCategories — without it that endpoint 403s and a rule's
    # new label is only tagged on the message, never created as a real category.
    "https://graph.microsoft.com/MailboxSettings.ReadWrite",
]


class OutlookProvider(BaseEmailProvider):
    """Microsoft Graph API email provider."""

    def __init__(self, credentials: dict[str, Any]):
        super().__init__(credentials)
        self._access_token: str | None = credentials.get("access_token")
        self._refresh_token: str | None = credentials.get("refresh_token")
        self._client_id: str | None = credentials.get("client_id")
        self._client_secret: str | None = credentials.get("client_secret")
        self._tenant_id: str = credentials.get("tenant_id", "common")
        self._http: httpx.AsyncClient | None = None
        self._creds_dirty = False
        # Lower-cased master-category names, fetched once per provider instance.
        # A sweep or rule run applies labels to many messages through the SAME
        # instance; without this every apply re-GET the whole master list just to
        # check existence, so this turns the 3-Graph-call apply into 2. None =
        # not yet fetched; a set (possibly empty for MSA/403) = fetched.
        self._master_categories: set[str] | None = None

    def credentials_dirty(self) -> bool:
        return self._creds_dirty

    def export_credentials(self) -> dict[str, Any]:
        """Return credentials with the latest (possibly refreshed) tokens."""
        return {
            **self.credentials,
            "access_token": self._access_token,
            "refresh_token": self._refresh_token,
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None:
            await self.authenticate()
            self._http = httpx.AsyncClient(
                base_url=GRAPH_API_BASE,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        return self._http

    async def _graph_send(
        self, method: str, url: str, **kwargs: Any,
    ) -> httpx.Response:
        """One Graph request that honours a single 429 ``Retry-After`` backoff.

        Graph throttles the label-apply loop under bulk categorisation. Without
        this, a 429 surfaces as a failed apply — a phantom "FAILED" audit row for
        a message the mailbox would have accepted a second later. Wait out one
        Retry-After (bounded) and retry once; anything past that the caller
        handles as before.
        """
        client = await self._get_client()
        for attempt in (0, 1):
            resp = await client.request(method, url, **kwargs)
            if resp.status_code == 429 and attempt == 0:
                try:
                    delay = float(resp.headers.get("Retry-After", "1") or 1)
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(min(max(delay, 0.0), _MAX_RETRY_AFTER_SECS))
                continue
            return resp
        return resp

    async def _master_category_names(self) -> set[str]:
        """Lower-cased master-category names for this account, fetched once and
        cached on the instance. Personal/MSA accounts 403 on the endpoint; that
        (and any other failure) caches an empty set so we don't re-GET per apply.
        """
        if self._master_categories is not None:
            return self._master_categories
        client = await self._get_client()
        try:
            resp = await client.get("/me/outlook/masterCategories")
            resp.raise_for_status()
            self._master_categories = {
                (c.get("displayName") or "").lower()
                for c in resp.json().get("value", [])
            }
        except Exception:  # noqa: BLE001 — 403 (MSA) or transient: treat as none
            self._master_categories = set()
        return self._master_categories

    async def _refresh_access_token(self) -> None:
        """Refresh the OAuth access token."""
        if not self._refresh_token or not self._client_id or not self._client_secret:
            raise ValueError("Missing OAuth credentials for token refresh")

        token_url = (
            f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        )
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                token_url,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                    "scope": " ".join(GRAPH_SCOPES),
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data["access_token"]
            # Microsoft rotates refresh tokens on every use — persist the new one
            # or subsequent refreshes will fail with the stale token.
            if "refresh_token" in data:
                self._refresh_token = data["refresh_token"]
            self._creds_dirty = True

    async def authenticate(self) -> bool:
        """Validate the access token."""
        if not self._access_token:
            if self._refresh_token:
                await self._refresh_access_token()
            else:
                return False

        try:
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self._access_token}"},
                timeout=10.0,
            ) as client:
                resp = await client.get(f"{GRAPH_API_BASE}/me")
                if resp.status_code == 401 and self._refresh_token:
                    await self._refresh_access_token()
                    return True
                return resp.is_success
        except Exception:
            return False

    @staticmethod
    def _folder_from_graph(folder: dict[str, Any]) -> EmailFolder:
        return EmailFolder(
            provider_folder_id=folder["id"],
            name=folder["displayName"],
            type=_classify_folder_type(folder),
            message_count=folder.get("totalItemCount", 0),
            unread_count=folder.get("unreadItemCount", 0),
        )

    async def list_folders(self) -> list[EmailFolder]:
        """List ALL mail folders, including user-created and nested ones.

        Graph's ``/me/mailFolders`` defaults to ~10 top-level folders and omits
        children, so we request ``$top=200``, follow ``@odata.nextLink``, and
        descend into ``childFolders`` (one level covers inbox-zero's flat set;
        deeper nesting is followed only when a child reports its own children).
        """
        client = await self._get_client()
        # NB: ``wellKnownName`` is intentionally NOT requested — personal/consumer
        # (MSA) accounts reject it in ``$select`` with HTTP 400, which used to fail
        # the whole folder listing. We classify system vs user by name instead
        # (see ``_classify_folder_type``).
        select = "id,displayName,totalItemCount,unreadItemCount,childFolderCount"

        async def _page(url: str, params: dict[str, Any] | None) -> list[dict[str, Any]]:
            items: list[dict[str, Any]] = []
            # First request uses params; subsequent ones follow the absolute
            # @odata.nextLink URL verbatim (httpx keeps the client's auth header).
            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                # Defensive: if a $select field is still rejected (400) on some
                # account type, retry once with default properties so listing
                # degrades instead of failing outright.
                if (
                    params and "$select" in params
                    and exc.response is not None
                    and exc.response.status_code == 400
                ):
                    params = {k: v for k, v in params.items() if k != "$select"}
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                else:
                    raise
            while True:
                data = resp.json()
                items.extend(data.get("value", []))
                next_link = data.get("@odata.nextLink")
                if not next_link:
                    return items
                resp = await client.get(next_link)
                resp.raise_for_status()

        async def _descend(raw: dict[str, Any]) -> list[EmailFolder]:
            # Skip the Outlook "Sync Issues" diagnostic tree entirely (the folder
            # and its Conflicts / Local Failures / Server Failures children).
            if (raw.get("displayName") or "").strip().lower() in \
                    _OUTLOOK_HIDDEN_FOLDER_NAMES:
                return []
            out = [self._folder_from_graph(raw)]
            if raw.get("childFolderCount"):
                try:
                    children = await _page(
                        f"/me/mailFolders/{raw['id']}/childFolders",
                        {"$top": 200, "$select": select},
                    )
                    for child in children:
                        out.extend(await _descend(child))
                except Exception:  # noqa: BLE001
                    pass  # a forbidden subtree shouldn't drop the parent
            return out

        top = await _page("/me/mailFolders", {"$top": 200, "$select": select})
        folders: list[EmailFolder] = []
        for raw in top:
            folders.extend(await _descend(raw))
        return folders

    async def _get_or_create_folder_id(self, name: str) -> str | None:
        """Return the Graph id of the folder named ``name``, creating it if absent.

        Dedups by ``displayName`` and tolerates the create race
        (``ErrorFolderExists`` / HTTP 409) by re-reading — mirrors upstream
        inbox-zero's ``getOrCreateOutlookFolderIdByName``.
        """
        if not name or not name.strip():
            return None
        name = name.strip()
        client = await self._get_client()
        esc = name.replace("'", "''")

        async def _find() -> str | None:
            resp = await client.get(
                "/me/mailFolders",
                params={"$filter": f"displayName eq '{esc}'",
                        "$select": "id,displayName", "$top": 1},
            )
            resp.raise_for_status()
            vals = resp.json().get("value", [])
            return vals[0]["id"] if vals else None

        existing = await _find()
        if existing:
            return existing
        resp = await client.post("/me/mailFolders", json={"displayName": name})
        if resp.is_success:
            return resp.json().get("id")
        if resp.status_code == 409 or "ErrorFolderExists" in resp.text:
            return await _find()
        resp.raise_for_status()
        return None

    async def create_folder(self, name: str) -> EmailFolder:
        """Create (or reuse) a top-level mail folder; return it normalized."""
        folder_id = await self._get_or_create_folder_id(name)
        if not folder_id:
            raise ValueError(f"Could not create Outlook folder: {name!r}")
        return EmailFolder(
            provider_folder_id=folder_id, name=name.strip(), type="user"
        )

    async def list_messages(
        self,
        folder: str = "inbox",
        query: str | None = None,
        max_results: int = 50,
        page_token: str | None = None,
        canonical_override: str | None = None,
        since: datetime | None = None,
    ) -> tuple[list[EmailMessage], str | None]:
        client = await self._get_client()

        if page_token and "://" in page_token:
            # The page cursor is Graph's @odata.nextLink — follow it VERBATIM.
            # Message collections paginate with ``$skip`` (a numeric offset), not
            # ``$skiptoken``, so the cursor can't be reduced to a bare token and
            # rebuilt; re-issuing the exact URL is the only reliable way to
            # advance. (Re-adding our own params here is what previously capped
            # every folder at one page of 100.) httpx keeps the client's auth
            # header for absolute URLs, and the URL already carries $orderby,
            # $select and any $filter from the first page.
            resp = await client.get(page_token)
        else:
            # First page: build the query for the requested folder.
            well_known: dict[str, str] = {
                "inbox": "inbox",
                "sent": "sentitems",
                "sentitems": "sentitems",
                "drafts": "drafts",
                "trash": "deleteditems",
                "deleteditems": "deleteditems",
                "archive": "archive",
                "junk": "junkemail",
                "junkemail": "junkemail",
            }
            folder_path = well_known.get(folder.lower(), folder)

            url = f"/me/mailFolders/{folder_path}/messages"
            params: dict[str, Any] = {
                "$top": min(max_results, 100),
                "$orderby": "receivedDateTime desc",
                "$select": "id,internetMessageId,subject,from,toRecipients,"
                           "ccRecipients,bccRecipients,receivedDateTime,isRead,"
                           "hasAttachments,flag,bodyPreview,categories,"
                           "parentFolderId,conversationId,importance,"
                           "internetMessageHeaders",
            }
            if query:
                params["$search"] = f'"{query}"'
            elif since is not None:
                # Server-side time bound for the deep initial sync. Filtering and
                # ordering on the same property (receivedDateTime) is allowed by
                # Graph; $search would NOT combine with $orderby, so they're
                # mutually exclusive here (sync never passes a query).
                iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")
                params["$filter"] = f"receivedDateTime ge {iso}"
            resp = await client.get(url, params=params)

        resp.raise_for_status()
        data = resp.json()

        # Normalize every message to the canonical folder key for the folder we
        # queried — Graph's ``parentFolderId`` is an opaque ID that would never
        # match the gateway's ``WHERE folder = 'inbox'`` filter.
        canon = canonical_override or canonical_folder(folder)
        messages: list[EmailMessage] = []
        for msg_data in data.get("value", []):
            msg = self._parse_graph_message(msg_data)
            msg.folder = canon
            messages.append(msg)

        # The page cursor is the full @odata.nextLink URL (None when exhausted),
        # fed straight back into ``page_token`` to fetch the next page verbatim.
        next_token = data.get("@odata.nextLink")
        return messages, next_token

    async def get_message(self, provider_message_id: str) -> EmailMessage:
        client = await self._get_client()
        resp = await client.get(
            f"/me/messages/{provider_message_id}",
            params={"$expand": "attachments"},
        )
        resp.raise_for_status()
        return self._parse_graph_message(resp.json())

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
        # ``thread_id`` (conversationId) is unused: Graph threads a reply via the
        # /reply action on ``reply_to_message_id``; a fresh sendMail can't be
        # forced into a conversation, so callers reply-thread through drafts.
        client = await self._get_client()

        message: dict[str, Any] = {
            "subject": subject,
            "body": {
                "contentType": "html" if body_html else "text",
                "content": body_html or body_text,
            },
            "toRecipients": [{"emailAddress": {"address": addr}} for addr in to],
        }
        if cc:
            message["ccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in cc
            ]
        if bcc:
            message["bccRecipients"] = [
                {"emailAddress": {"address": addr}} for addr in bcc
            ]
        if attachments:
            import base64 as _b64  # noqa: PLC0415
            # Inline file attachments (base64). Graph accepts these for small
            # files; very large files would need an upload session.
            message["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.get("filename", "attachment"),
                    "contentType": att.get(
                        "mime_type", "application/octet-stream"),
                    "contentBytes": _b64.b64encode(att.get("content") or b"").decode(),
                }
                for att in attachments
            ]

        if reply_to_message_id:
            # Send as reply
            resp = await client.post(
                f"/me/messages/{reply_to_message_id}/reply",
                json={"message": message},
            )
        else:
            resp = await client.post("/me/sendMail", json={"message": message})

        resp.raise_for_status()
        return "sent"  # Graph API doesn't return the sent message ID

    @staticmethod
    def _recipient_list(addrs: list[str] | None) -> list[dict[str, Any]]:
        return [{"emailAddress": {"address": a}} for a in (addrs or []) if a]

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
        """Create an Outlook draft. For replies, use createReply (keeps threading)
        then set the body; otherwise create a standalone draft message. File
        attachments are added to the draft via the Graph attachments endpoint."""
        client = await self._get_client()
        body_block = {
            "contentType": "html" if body_html else "text",
            "content": body_html or body_text,
        }
        # Cc/Bcc live ON the draft so they survive a reopen/edit (Graph stores
        # ccRecipients/bccRecipients on the message). Only set when provided, so
        # createReply's prefilled recipients aren't wiped by an empty list.
        recipients: dict[str, Any] = {}
        if cc is not None:
            recipients["ccRecipients"] = self._recipient_list(cc)
        if bcc is not None:
            recipients["bccRecipients"] = self._recipient_list(bcc)
        if reply_to_message_id:
            resp = await client.post(
                f"/me/messages/{reply_to_message_id}/createReply"
            )
            resp.raise_for_status()
            draft_id = resp.json().get("id", "")
            patch = await client.patch(
                f"/me/messages/{draft_id}", json={"body": body_block, **recipients}
            )
            patch.raise_for_status()
            await self._attach_files(client, draft_id, attachments)
            return draft_id
        message: dict[str, Any] = {
            "subject": subject,
            "body": body_block,
            "toRecipients": self._recipient_list(to),
            **recipients,
        }
        resp = await client.post("/me/messages", json=message)
        resp.raise_for_status()
        draft_id = resp.json().get("id", "")
        await self._attach_files(client, draft_id, attachments)
        return draft_id

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
        """Update an existing Outlook draft in place (PATCH /me/messages/{id}).

        Only the supplied fields are changed. Returns the (unchanged) draft id so
        callers can keep tracking the same provider message — this is what lets
        the editor save repeatedly without spawning duplicate drafts.

        ``thread_id`` is ignored: a Graph reply-draft (createReply) keeps its
        conversation across a PATCH, so threading needs no re-assertion.

        ``attachments`` are POSTed to the draft's attachments collection (the same
        endpoint ``create_draft`` uses). Callers supply them once, at the pre-send
        save, so this doesn't re-upload on every keystroke auto-save.
        """
        client = await self._get_client()
        patch: dict[str, Any] = {}
        if body_text is not None or body_html is not None:
            patch["body"] = {
                "contentType": "html" if body_html else "text",
                "content": body_html if body_html is not None else (body_text or ""),
            }
        if subject is not None:
            patch["subject"] = subject
        if to is not None:
            patch["toRecipients"] = self._recipient_list(to)
        if cc is not None:
            patch["ccRecipients"] = self._recipient_list(cc)
        if bcc is not None:
            patch["bccRecipients"] = self._recipient_list(bcc)
        if patch:
            resp = await client.patch(f"/me/messages/{draft_id}", json=patch)
            resp.raise_for_status()
        await self._attach_files(client, draft_id, attachments)
        return draft_id

    async def send_draft(self, draft_id: str) -> str | None:
        """Send an existing draft natively (POST /me/messages/{id}/send).

        Graph moves the message Drafts → Sent itself (no leftover draft, no
        duplicate). Returns None — Graph's send endpoint yields no message id.
        """
        client = await self._get_client()
        resp = await client.post(f"/me/messages/{draft_id}/send")
        resp.raise_for_status()
        return None

    @staticmethod
    async def _attach_files(
        client: httpx.AsyncClient, draft_id: str,
        attachments: list[dict[str, Any]] | None,
    ) -> None:
        """Attach files to a Graph draft via POST /messages/{id}/attachments."""
        import base64 as _b64  # noqa: PLC0415
        for att in attachments or []:
            try:
                content = att.get("content") or b""
                await client.post(
                    f"/me/messages/{draft_id}/attachments",
                    json={
                        "@odata.type": "#microsoft.graph.fileAttachment",
                        "name": att.get("filename", "attachment"),
                        "contentType": att.get(
                            "mime_type", "application/octet-stream"),
                        "contentBytes": _b64.b64encode(content).decode(),
                    },
                )
            except Exception:  # noqa: BLE001 — one bad attachment shouldn't fail the draft
                continue

    # ── Change-notification subscriptions (push) ─────────────────────────────

    async def create_subscription(
        self,
        notification_url: str,
        client_state: str,
        resource: str = "/me/mailFolders('inbox')/messages",
        minutes: int = 4000,
    ) -> dict[str, Any]:
        """Create a Graph change-notification subscription for new inbox mail.

        Graph validates ``notification_url`` synchronously (it POSTs a
        validationToken that the endpoint must echo within 10s). Mail
        subscriptions live at most ~4230 min, so callers must renew.
        """
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415
        exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        body = {
            "changeType": "created",
            "notificationUrl": notification_url,
            "resource": resource,
            "expirationDateTime": exp,
            "clientState": client_state,
        }
        client = await self._get_client()
        resp = await client.post("/subscriptions", json=body)
        resp.raise_for_status()
        return resp.json()

    async def renew_subscription(
        self, subscription_id: str, minutes: int = 4000
    ) -> dict[str, Any]:
        """Extend a subscription's expiry."""
        from datetime import datetime, timedelta, timezone  # noqa: PLC0415
        exp = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        client = await self._get_client()
        resp = await client.patch(
            f"/subscriptions/{subscription_id}",
            json={"expirationDateTime": exp},
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_subscription(self, subscription_id: str) -> None:
        """Best-effort delete of a subscription."""
        client = await self._get_client()
        try:
            await client.delete(f"/subscriptions/{subscription_id}")
        except Exception:  # noqa: BLE001
            pass

    async def modify_message(
        self,
        provider_message_id: str,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> None:
        client = await self._get_client()
        patch: dict[str, Any] = {}

        if add_labels:
            if "READ" in add_labels:
                patch["isRead"] = True
            if "UNREAD" in add_labels:
                patch["isRead"] = False
            if "FLAGGED" in add_labels:
                patch["flag"] = {"flagStatus": "flagged"}
        if remove_labels:
            if "UNREAD" in remove_labels:
                patch["isRead"] = True

        if patch:
            resp = await client.patch(
                f"/me/messages/{provider_message_id}",
                json=patch,
            )
            resp.raise_for_status()

    async def trash_message(self, provider_message_id: str) -> str | None:
        # Graph "delete" moves the item to Deleted Items (soft delete), which is
        # what we want for a trash action. /move re-keys the message, so return
        # the new id for the caller to persist.
        return await self.move_to_folder(provider_message_id, "trash")

    # Canonical folder key → Graph well-known folder name for moves.
    _MOVE_TARGETS = {
        "inbox": "inbox",
        "archive": "archive",
        "trash": "deleteditems",
        "drafts": "drafts",
        "junk": "junkemail",
        "sent": "sentitems",
    }

    async def apply_flags(
        self,
        provider_message_id: str,
        *,
        is_read: bool | None = None,
        is_starred: bool | None = None,
        is_flagged: bool | None = None,
    ) -> None:
        """PATCH read state / flag on the Graph message.

        Outlook has no "star" concept, so ``is_starred`` is ignored (the star is
        kept as a local-only marker in Metorite).
        """
        patch: dict[str, Any] = {}
        if is_read is not None:
            patch["isRead"] = is_read
        if is_flagged is not None:
            patch["flag"] = {"flagStatus": "flagged" if is_flagged else "notFlagged"}
        if patch:
            client = await self._get_client()
            resp = await client.patch(f"/me/messages/{provider_message_id}", json=patch)
            resp.raise_for_status()

    async def move_to_folder(
        self, provider_message_id: str, folder: str
    ) -> str | None:
        # Well-known system folders use their Graph well-known name; any other
        # name is treated as a user folder and created on demand (inbox-zero
        # parity — promo/automation rules file into same-named folders).
        target = self._MOVE_TARGETS.get((folder or "").lower())
        if not target:
            target = await self._get_or_create_folder_id(folder)
        if not target:
            return None
        client = await self._get_client()
        resp = await client.post(
            f"/me/messages/{provider_message_id}/move",
            json={"destinationId": target},
        )
        resp.raise_for_status()
        # Graph /move creates the message in the destination folder with a NEW
        # id; the old id is no longer valid. Return it so the caller can re-key
        # the stored provider_message_id (otherwise follow-up actions 404 until
        # the next full sync).
        try:
            return resp.json().get("id")
        except Exception:  # noqa: BLE001
            return None

    async def create_filter(
        self,
        *,
        from_email: str,
        archive: bool = True,
        label: str | None = None,
    ) -> str | None:
        """Create an Inbox message rule so future mail from ``from_email`` is
        moved to Archive (and optionally categorized) at the provider.

        Consumer (MSA) accounts or a missing ``MailboxSettings.ReadWrite`` scope
        make ``/messageRules`` 403/404 — degrade to ``None`` so the server-side
        AUTO_ARCHIVED sweep keeps handling future mail."""
        if not from_email:
            return None
        actions: dict[str, Any] = {}
        if archive:
            actions["moveToFolder"] = self._MOVE_TARGETS.get("archive", "archive")
        if label:
            await self._ensure_categories([label])
            actions["assignCategories"] = [label]
        if not actions:
            return None
        actions["stopProcessingRules"] = True
        body = {
            "displayName": f"Auto-archive {from_email}"[:255],
            "sequence": 1,
            "isEnabled": True,
            "conditions": {
                "fromAddresses": [{"emailAddress": {"address": from_email}}]
            },
            "actions": actions,
        }
        client = await self._get_client()
        resp = await client.post("/me/mailFolders/inbox/messageRules", json=body)
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json().get("id")

    async def delete_filter(self, filter_id: str) -> None:
        """Delete an Inbox message rule (e.g. when a sender is re-approved)."""
        if not filter_id:
            return
        client = await self._get_client()
        resp = await client.delete(
            f"/me/mailFolders/inbox/messageRules/{filter_id}"
        )
        if resp.status_code not in (200, 204, 403, 404):
            resp.raise_for_status()

    async def list_filters(self) -> list[dict[str, Any]]:
        """Read the Inbox message rules for display in the app's rules screen.

        Same scope caveat as ``create_filter``: consumer (MSA) accounts or a
        missing ``MailboxSettings.ReadWrite`` scope make ``/messageRules``
        403/404 — degrade to an empty list rather than failing the caller."""
        client = await self._get_client()
        resp = await client.get("/me/mailFolders/inbox/messageRules")
        if resp.status_code in (403, 404):
            return []
        resp.raise_for_status()
        out: list[dict[str, Any]] = []
        for r in resp.json().get("value", []):
            conds = r.get("conditions") or {}
            froms = [
                (a.get("emailAddress") or {}).get("address", "")
                for a in conds.get("fromAddresses") or []
            ]
            summary: list[str] = []
            for phrase in conds.get("subjectContains") or []:
                summary.append(f"subject contains “{phrase}”")
            for phrase in conds.get("senderContains") or []:
                summary.append(f"sender contains “{phrase}”")
            acts = r.get("actions") or {}
            if acts.get("moveToFolder"):
                summary.append("move to folder")
            if acts.get("delete"):
                summary.append("delete")
            if acts.get("assignCategories"):
                summary.append(
                    "label: " + ", ".join(acts["assignCategories"]))
            if acts.get("forwardTo"):
                summary.append("forward")
            if acts.get("markAsRead"):
                summary.append("mark read")
            if acts.get("markImportance"):
                summary.append(f"importance: {acts['markImportance']}")
            out.append({
                "id": r.get("id", ""),
                "name": r.get("displayName", ""),
                "enabled": bool(r.get("isEnabled", True)),
                "from_addresses": [f for f in froms if f],
                "summary": summary,
            })
        return out

    # ── Labels (Outlook categories) ──────────────────────────────────────

    async def list_labels(self) -> list[dict[str, str | None]]:
        """Outlook master categories as ``{name, color}`` dicts.

        ``color`` is the category's preset token ('preset0'..'preset24'), which
        is already our canonical colour id.  Personal/consumer (MSA) accounts
        don't expose ``/me/outlook/masterCategories`` (HTTP 403), so treat that
        as "no master categories" rather than an error — the caller degrades to
        the standard category set and categories can still be applied.
        """
        from .label_colors import is_preset
        client = await self._get_client()
        resp = await client.get("/me/outlook/masterCategories")
        if resp.status_code in (403, 404):
            return []
        resp.raise_for_status()
        out: list[dict[str, str | None]] = []
        for c in resp.json().get("value", []):
            name = c.get("displayName")
            if not name:
                continue
            color = c.get("color")
            out.append({"name": name, "color": color if is_preset(color) else None})
        return sorted(out, key=lambda x: (x["name"] or "").lower())

    async def _ensure_categories(self, names: list[str]) -> None:
        """Create any missing Outlook master categories so an applied category is
        a real, coloured category (matches Gmail's label-on-apply behaviour).

        Uses the per-instance cache: when every requested category already
        exists (the common case once the mailbox is warm) this makes NO Graph
        call at all — the saving that turns the 3-call apply into 2."""
        names = [n for n in names if n]
        if not names:
            return
        existing = await self._master_category_names()
        missing = [n for n in names if n.lower() not in existing]
        if not missing:
            return  # all present — no Graph round-trip
        client = await self._get_client()
        from .label_colors import preset_for_name
        for name in missing:
            # Stable colour from the name so the same category is consistent.
            color = preset_for_name(name)
            try:
                await client.post(
                    "/me/outlook/masterCategories",
                    json={"displayName": name, "color": color},
                )
                existing.add(name.lower())  # keep the cache current
            except Exception:  # noqa: BLE001
                pass  # best-effort — applying the category still works

    async def set_label_color(self, name: str, color: str) -> None:
        """Set an Outlook master category's colour (creating it if needed).

        Categories are identified by id; we look up the id by displayName, then
        PATCH the colour (or POST a new category when it doesn't exist yet)."""
        from .label_colors import is_preset
        if not name or not is_preset(color):
            return
        client = await self._get_client()
        try:
            resp = await client.get("/me/outlook/masterCategories")
            if resp.status_code in (403, 404):
                return  # MSA / missing scope — categories aren't manageable
            resp.raise_for_status()
            cat_id = next(
                (
                    c.get("id")
                    for c in resp.json().get("value", [])
                    if (c.get("displayName") or "").lower() == name.lower()
                ),
                None,
            )
        except Exception:  # noqa: BLE001
            cat_id = None
        if cat_id:
            await client.patch(
                f"/me/outlook/masterCategories/{cat_id}",
                json={"color": color},
            )
        else:
            await client.post(
                "/me/outlook/masterCategories",
                json={"displayName": name, "color": color},
            )
            # Keep the per-instance cache consistent so a following apply doesn't
            # try to re-create the category it just made.
            if self._master_categories is not None:
                self._master_categories.add(name.lower())

    async def set_labels(
        self,
        provider_message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> None:
        """Add/remove categories on a message (categories are plain names).

        Missing categories are first created in the account's master category
        list so they show up as real, coloured Outlook categories."""
        await self._ensure_categories(add or [])
        resp = await self._graph_send(
            "GET", f"/me/messages/{provider_message_id}",
            params={"$select": "categories"},
        )
        resp.raise_for_status()
        current: list[str] = list(resp.json().get("categories", []) or [])
        for name in add or []:
            if name not in current:
                current.append(name)
        for name in remove or []:
            if name in current:
                current.remove(name)
        patch = await self._graph_send(
            "PATCH", f"/me/messages/{provider_message_id}",
            json={"categories": current},
        )
        patch.raise_for_status()

    # Recurring polls stay shallow (newest pages only) — cheap and enough to
    # catch new mail + recent inbound changes. The one-time deep initial sync
    # pages until the $filter(since) window is exhausted, capped for safety.
    RECURRING_SYNC_MAX_PAGES = 2
    DEEP_SYNC_MAX_PAGES = 200  # ~20k/folder ceiling; since-filter normally exhausts first

    async def _sweep_folder(
        self,
        folder: str,
        max_results: int,
        canonical_override: str | None = None,
        *,
        max_pages: int | None = None,
        since: datetime | None = None,
    ) -> list[EmailMessage]:
        """Page a single folder (newest-first), following ``@odata.nextLink``.

        ``max_pages`` bounds the number of pages; ``since`` adds a server-side
        receivedDateTime floor so the deep sweep naturally exhausts ~1 year back.
        """
        out: list[EmailMessage] = []
        token: str | None = None
        pages = max_pages or self.RECURRING_SYNC_MAX_PAGES
        for _ in range(pages):
            msgs, token = await self.list_messages(
                folder=folder,
                max_results=max_results,
                page_token=token,
                canonical_override=canonical_override,
                since=since,
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

        # Delta sync is DISABLED: in production the inbox delta token returned 0
        # changes every cycle even as new mail arrived, silently halting sync.
        # Force the reliable multi-folder full sweep and return
        # new_history_id=None — which also auto-clears any stuck token already
        # persisted on the account (the scheduler writes it back), so a
        # previously-broken account self-heals on its next cycle. Re-enable delta
        # only behind a verified implementation.
        history_id = None

        if history_id:
            # We persist Graph's @odata.deltaLink (a full URL) as history_id, but
            # the delta endpoint wants only the bare $deltatoken value — extract
            # it (handles both a stored deltaLink URL and an already-bare token).
            token = history_id
            if "://" in history_id:
                from urllib.parse import parse_qs, urlparse  # noqa: PLC0415
                token = parse_qs(urlparse(history_id).query).get(
                    "$deltatoken", [history_id]
                )[0]
            # Delta query for incremental sync
            resp = await client.get(
                "/me/mailFolders/inbox/messages/delta",
                params={"$deltatoken": token, "$top": max_results},
            )
            resp.raise_for_status()
            data = resp.json()

            messages: list[EmailMessage] = []
            removed_count = 0
            for item in data.get("value", []):
                if item.get("@removed"):
                    removed_count += 1
                    messages.append(EmailMessage(
                        provider_message_id=item["id"],
                        folder="TRASH",
                        labels=["TRASH"],
                        subject="[DELETED]",
                    ))
                else:
                    msg = self._parse_graph_message(item)
                    # The delta query runs against the inbox folder.
                    msg.folder = "inbox"
                    messages.append(msg)

            return SyncResult(
                messages_synced=len(data.get("value", [])),
                messages_skipped=removed_count,
                messages=messages,
                new_history_id=data.get("@odata.deltaLink"),
            )
        else:
            # Full multi-folder sweep so messages land in the right folder in the
            # UI (not just inbox/sent). DEEP sync (first connect / forced) pages
            # ~1 year back per folder via the since-filter; RECURRING polls page
            # only the newest pages (cheap). Older-than-window mail is pulled
            # lazily by the /backfill endpoint.
            max_pages = self.DEEP_SYNC_MAX_PAGES if deep else self.RECURRING_SYNC_MAX_PAGES
            sweep_since = since if deep else None
            messages = []
            for folder_key in ("inbox", "sent", "drafts", "archive", "junk", "trash"):
                try:
                    messages.extend(await self._sweep_folder(
                        folder_key, max_results,
                        max_pages=max_pages, since=sweep_since,
                    ))
                except Exception:
                    # A missing/forbidden folder shouldn't abort the whole sync.
                    continue

            # User-created folders — each Outlook message lives in exactly one
            # folder, so storing folder=canonical(displayName) is unambiguous and
            # makes the user's own folders openable in the UI.
            try:
                folders = await self.list_folders()
            except Exception:
                folders = []
            for f in folders:
                if f.type == "system":
                    continue
                canon = canonical_folder(f.name)
                if canon in ("inbox", "sent", "drafts", "trash", "junk", "archive"):
                    continue
                try:
                    messages.extend(await self._sweep_folder(
                        f.provider_folder_id, max_results, canonical_override=canon,
                        max_pages=max_pages, since=sweep_since,
                    ))
                except Exception:
                    continue

            # IMPORTANT: keep the account in full-sync mode (new_history_id=None).
            #
            # We previously seeded an inbox delta token here (via
            # _bootstrap_inbox_delta) to detect upstream deletions. In production
            # that delta token returned 0 changes every cycle even when new mail
            # had arrived — i.e. it SILENTLY STOPPED syncing new email. The
            # multi-folder full sweep above is the reliable path (it reliably
            # picks up new mail), so we stay on it. Deletion-detection needs a
            # different, verified approach before delta is re-enabled.
            return SyncResult(
                messages_synced=len(messages),
                messages=messages,
                new_history_id=None,
                # A full multi-folder snapshot → the gateway can reconcile
                # provider-side deletions (messages gone from every folder).
                full_snapshot=True,
            )

    async def get_attachment(
        self, provider_message_id: str, provider_attachment_id: str
    ) -> bytes:
        client = await self._get_client()
        resp = await client.get(
            f"/me/messages/{provider_message_id}/attachments/{provider_attachment_id}"
        )
        resp.raise_for_status()
        data = resp.json()
        # Graph API returns content as base64 in contentBytes
        import base64
        return base64.b64decode(data.get("contentBytes", ""))

    # ── Helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_received_datetime(received_dt: str | None) -> datetime | None:
        """Parse Microsoft Graph receivedDateTime ISO string into datetime."""
        if not received_dt:
            return None
        try:
            return datetime.fromisoformat(received_dt.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None

    def _parse_graph_message(self, raw: dict[str, Any]) -> EmailMessage:
        """Parse a Microsoft Graph message into our normalized EmailMessage."""

        def _parse_recipients(recipients: list[dict] | None) -> list[EmailAddress]:
            if not recipients:
                return []
            return [
                EmailAddress(
                    name=r.get("emailAddress", {}).get("name", ""),
                    email=r.get("emailAddress", {}).get("address", ""),
                )
                for r in recipients
            ]

        from_addr = None
        if raw.get("from"):
            fa = raw["from"].get("emailAddress", {})
            from_addr = EmailAddress(name=fa.get("name", ""), email=fa.get("address", ""))

        # Categories (Outlook user categories, e.g. "Red category"). Graph
        # returns the full category list on every message, and set_labels writes
        # back to the same field, so this genuinely round-trips — an empty list
        # means the user cleared them, not that we failed to look.
        categories = raw.get("categories", []) or []

        # Flag status
        flag = raw.get("flag", {})
        is_flagged = flag.get("flagStatus") == "flagged"

        # Importance: 'low' | 'normal' | 'high'
        importance = str(raw.get("importance", "normal")).lower()
        if importance not in ("low", "normal", "high"):
            importance = "normal"

        # Attachments — real files only (inline ``cid:`` body images skipped).
        attachments = _outlook_attachments(raw)

        # Body
        body = raw.get("body", {})
        body_text = body.get("content", "") if body.get("contentType") == "text" else ""
        body_html = body.get("content") if body.get("contentType") == "html" else None

        # List-Unsubscribe (present when internetMessageHeaders was $select'd);
        # fall back to scraping the HTML body for an unsubscribe link.
        unsubscribe_link = None
        for h in raw.get("internetMessageHeaders", []) or []:
            if str(h.get("name", "")).lower() == "list-unsubscribe":
                unsubscribe_link = _parse_list_unsubscribe(h.get("value", ""))
                break
        if not unsubscribe_link:
            unsubscribe_link = find_unsubscribe_link_in_html(body_html)

        return EmailMessage(
            provider_message_id=raw["id"],
            internet_message_id=raw.get("internetMessageId"),
            thread_id=raw.get("conversationId"),
            folder=raw.get("parentFolderId", "inbox"),
            labels=categories,
            from_address=from_addr,
            to_addresses=_parse_recipients(raw.get("toRecipients")),
            cc_addresses=_parse_recipients(raw.get("ccRecipients")),
            bcc_addresses=_parse_recipients(raw.get("bccRecipients")),
            subject=raw.get("subject", "(no subject)"),
            body_text=body_text,
            body_html=body_html,
            snippet=raw.get("bodyPreview", "") or body_text[:200],
            # When attachments were expanded (detail fetch), trust the filtered
            # count so an inline-only message doesn't show an empty paperclip;
            # for the list fetch (no $expand) fall back to Graph's flag.
            has_attachments=(
                len(attachments) > 0
                if raw.get("attachments")
                else raw.get("hasAttachments", False)
            ),
            attachments=attachments,
            is_read=raw.get("isRead", False),
            is_starred=False,  # Outlook doesn't have stars — use flag/categories
            is_flagged=is_flagged,
            importance=importance,
            categories=categories,
            categories_authoritative=True,
            unsubscribe_link=unsubscribe_link,
            received_at=self._parse_received_datetime(raw.get("receivedDateTime")),
            raw=raw,
        )


def _outlook_attachments(raw: dict) -> list[Attachment]:
    """Real (non-inline) attachments from an expanded Graph message.

    Microsoft Graph returns inline body images (signature logos, pasted
    screenshots referenced from the HTML via ``cid:``) in the same
    ``attachments`` collection as real files, flagged with ``isInline: true``.
    Skip those — they belong in the body, not the attachment list — otherwise a
    signature with three logos shows as "3 attachments".
    """
    out: list[Attachment] = []
    for att in raw.get("attachments", []) or []:
        if att.get("isInline"):
            continue
        out.append(Attachment(
            id=att["id"],
            filename=att.get("name", "attachment"),
            mime_type=att.get("contentType", "application/octet-stream"),
            size_bytes=att.get("size", 0),
            provider_attachment_id=att["id"],
        ))
    return out


def _parse_list_unsubscribe(header: str) -> str | None:
    """Pick the best link from a List-Unsubscribe header (https preferred)."""
    if not header:
        return None
    targets: list[str] = []
    for part in header.split(","):
        p = part.strip()
        if p.startswith("<") and p.endswith(">"):
            p = p[1:-1].strip()
        if p:
            targets.append(p)
    for t in targets:
        if t.lower().startswith("http"):
            return t
    for t in targets:
        if t.lower().startswith("mailto:"):
            return t
    return targets[0] if targets else None
