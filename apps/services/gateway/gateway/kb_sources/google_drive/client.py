"""The Google Drive adapter of a member source (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_curation.md`` §7.1 and §7.2 step 1, and
``project-docs/specs/knowledge_base.md`` §7.1 and §7.2.

**Metadata only.** Discovery reads ``files.list`` with a ``fields`` mask and
``drives.list``. It never asks ``alt=media`` and never calls ``files.export``.
:meth:`GoogleDriveSource.fetch_text` and :meth:`GoogleDriveSource.fetch_bytes`
raise ``NotImplementedError`` until KC-5, and
:meth:`GoogleDriveSource.list_changed` until KC-3.

**The phases of one discovery.** The cursor walks four phases:

1. ``root`` — ``files/root``: the member's own drive, as one root folder;
2. ``drives`` — ``drives.list``: each shared drive the member can open, as a
   root folder whose id is the drive id;
3. ``files`` — ``files.list`` over every corpus (``corpora=allDrives``,
   ``includeItemsFromAllDrives=true``, ``supportsAllDrives=true``), flat;
4. ``children`` — the same call for the children of ONE folder. The level
   walk of a tree past the node cap uses it (``kb_sync/discovery.py``).

**The size of a page.** The guard keeps at most 64 KiB of an answer. A page
whose body reached the cap is read again with half the page size, down to
:data:`MIN_PAGE`. Past that the read raises :class:`SourceError`.

**A throttle.** HTTP 429, a 403 with a rate reason, and HTTP 5xx wait 1, 2
and 4 seconds, then raise :class:`RateLimited`. A 401 raises
:class:`SourceError` with the code ``unauthorized``.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from gateway.kb_sources.base import (
    KIND_FILE,
    KIND_FOLDER,
    NOT_IN_KC1,
    AccountIdentity,
    DiscoverCursor,
    DiscoverPage,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceError,
    SourceNode,
)
from gateway.kb_sources.google_drive import auth
from gateway.kb_sources.http import GuardedHttp, Http, HttpAnswer

PROVIDER = "google_drive"

API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

PHASE_ROOT = "root"
PHASE_DRIVES = "drives"
PHASE_FILES = "files"
PHASE_CHILDREN = "children"

#: The first page size, and the smallest one a shrink may reach.
PAGE_SIZE = 100
MIN_PAGE = 10

#: The fields of each file. Metadata only: no content, no export link.
FILE_FIELDS = (
    "nextPageToken,files(id,name,mimeType,size,modifiedTime,parents,driveId,webViewLink)"
)
DRIVE_FIELDS = "nextPageToken,drives(id,name)"

#: A Drive id. Anything else is skipped, because an id goes into a query.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")

#: The longest name a node keeps.
MAX_NAME = 500

#: The hosts a deep link may point at. A link off the list is dropped.
_LINK_HOSTS = frozenset({"drive.google.com", "docs.google.com"})

#: The waits of a throttled call, in seconds.
_BACKOFF = (1.0, 2.0, 4.0)

#: The reasons of a 403 that is a throttle, not a refusal.
_RATE_REASONS = frozenset({"userRateLimitExceeded", "rateLimitExceeded"})

Sleep = Callable[[float], Awaitable[None]]


def _safe_id(value: Any) -> str | None:
    text = str(value or "")
    return text if _ID.match(text) else None


def _link(value: Any) -> str | None:
    url = str(value or "")
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme == "https" and (parts.hostname or "") in _LINK_HOSTS:
        return url
    return None


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _size(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _name(value: Any) -> str:
    return str(value or "").strip()[:MAX_NAME] or "(no name)"


def _node(item: Mapping[str, Any]) -> SourceNode | None:
    """One ``files.list`` item as a node, or ``None`` for a malformed id."""
    ext_id = _safe_id(item.get("id"))
    if ext_id is None:
        return None
    parents = item.get("parents") or []
    parent = _safe_id(parents[0]) if isinstance(parents, list) and parents else None
    mime = str(item.get("mimeType") or "") or None
    return SourceNode(
        ext_id=ext_id,
        parent_ext_id=parent,
        kind=KIND_FOLDER if mime == FOLDER_MIME else KIND_FILE,
        name=_name(item.get("name")),
        mime=mime,
        size=_size(item.get("size")),
        modified_at=_when(item.get("modifiedTime")),
        web_url=_link(item.get("webViewLink")),
        drive_id=_safe_id(item.get("driveId")),
    )


class GoogleDriveSource:
    """:class:`gateway.kb_sources.base.KbSource` for Google Drive.

    *client* is Metorite's one Google client, which the route reads and
    passes in. *http* is the network door, :class:`GuardedHttp` on
    :data:`auth.GOOGLE_HOSTS` by default. A test passes a fake.
    """

    provider = PROVIDER

    def __init__(
        self,
        client: OAuthClientConfig,
        *,
        http: Http | None = None,
        clock: auth.Clock = auth.utcnow,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._http: Http = http or GuardedHttp(auth.GOOGLE_HOSTS)
        self._clock = clock
        self._sleep = sleep

    # ── The token life ────────────────────────────────────────────────────

    def consent_url(self, *, state: str, scope_kind: str) -> str:
        return auth.consent_url(self._client, state=state, scope_kind=scope_kind)

    async def exchange_code(self, *, code: str) -> SourceCredential:
        return await auth.exchange_code(self._client, self._http, code=code, clock=self._clock)

    async def refresh(self, cred: SourceCredential) -> SourceCredential:
        return await auth.refresh(self._client, self._http, cred, clock=self._clock)

    async def revoke(self, cred: SourceCredential) -> bool:
        return await auth.revoke(self._http, cred)

    # ── Reads ─────────────────────────────────────────────────────────────

    async def _get(
        self, cred: SourceCredential, path: str, params: Mapping[str, str],
    ) -> HttpAnswer:
        """One GET of the Drive API, with the backoff of the module doc."""
        headers = {"Authorization": f"Bearer {cred.access_token}"}
        for attempt in range(len(_BACKOFF) + 1):
            answer = await self._http.get(f"{API}/{path}", params=params, headers=headers)
            if answer.status_code == 200:
                return answer
            if answer.status_code == 401:
                exc = SourceError("Google refused the Drive token")
                exc.code = "unauthorized"
                raise exc
            if not self._throttled(answer):
                exc = SourceError(f"Google answered {answer.status_code}")
                exc.code = f"http_{answer.status_code}"
                raise exc
            if attempt < len(_BACKOFF):
                await self._sleep(_BACKOFF[attempt])
        raise RateLimited("Google kept throttling the Drive read", tries=len(_BACKOFF) + 1)

    @staticmethod
    def _throttled(answer: HttpAnswer) -> bool:
        if answer.status_code == 429 or answer.status_code >= 500:
            return True
        if answer.status_code != 403:
            return False
        body = answer.json()
        errors = (body.get("error") or {}).get("errors") if isinstance(body, Mapping) else None
        reasons = {str(e.get("reason")) for e in errors or [] if isinstance(e, Mapping)}
        return bool(reasons & _RATE_REASONS)

    async def account_identity(self, cred: SourceCredential) -> AccountIdentity:
        answer = await self._get(
            cred, "about", {"fields": "user(displayName,emailAddress,permissionId)"})
        user = answer.json().get("user") if isinstance(answer.json(), Mapping) else None
        if not isinstance(user, Mapping):
            raise SourceError("Google gave no Drive account")
        account_id = str(user.get("permissionId") or "").strip()
        email = str(user.get("emailAddress") or "").strip().lower()
        if not account_id or not email:
            raise SourceError("Google gave no Drive account id or address")
        return AccountIdentity(
            account_id=account_id[:200], email=email[:320],
            display_name=str(user.get("displayName") or "")[:200],
        )

    async def discover(
        self, cred: SourceCredential, cursor: DiscoverCursor | None = None,
    ) -> DiscoverPage:
        cursor = cursor or DiscoverCursor(phase=PHASE_ROOT)
        if cursor.phase == PHASE_ROOT:
            return await self._root(cred)
        if cursor.phase == PHASE_DRIVES:
            return await self._drives(cred, cursor)
        if cursor.phase in (PHASE_FILES, PHASE_CHILDREN):
            return await self._files(cred, cursor)
        raise ValueError(f"unknown discovery phase {cursor.phase!r}")

    async def _root(self, cred: SourceCredential) -> DiscoverPage:
        answer = await self._get(cred, "files/root", {"fields": "id,name"})
        body = answer.json()
        root_id = _safe_id(body.get("id") if isinstance(body, Mapping) else None)
        nodes: tuple[SourceNode, ...] = ()
        if root_id is not None:
            nodes = (SourceNode(
                ext_id=root_id, parent_ext_id=None, kind=KIND_FOLDER, name="My Drive",
                mime=FOLDER_MIME, size=None, modified_at=None,
                web_url="https://drive.google.com/drive/my-drive",
            ),)
        return DiscoverPage(nodes=nodes, next_cursor=DiscoverCursor(phase=PHASE_DRIVES))

    async def _drives(self, cred: SourceCredential, cursor: DiscoverCursor) -> DiscoverPage:
        params = {"pageSize": str(PAGE_SIZE), "fields": DRIVE_FIELDS}
        if cursor.page_token:
            params["pageToken"] = cursor.page_token
        body = (await self._get(cred, "drives", params)).json()
        nodes = []
        for item in (body.get("drives") or []) if isinstance(body, Mapping) else []:
            drive_id = _safe_id(item.get("id")) if isinstance(item, Mapping) else None
            if drive_id is None:
                continue
            nodes.append(SourceNode(
                ext_id=drive_id, parent_ext_id=None, kind=KIND_FOLDER,
                name=_name(item.get("name")), mime=FOLDER_MIME, size=None,
                modified_at=None,
                web_url=f"https://drive.google.com/drive/folders/{drive_id}",
                drive_id=drive_id,
            ))
        token = body.get("nextPageToken") if isinstance(body, Mapping) else None
        nxt = (DiscoverCursor(phase=PHASE_DRIVES, page_token=str(token)) if token
               else DiscoverCursor(phase=PHASE_FILES))
        return DiscoverPage(nodes=tuple(nodes), next_cursor=nxt)

    async def _files(self, cred: SourceCredential, cursor: DiscoverCursor) -> DiscoverPage:
        query = "trashed = false"
        if cursor.phase == PHASE_CHILDREN:
            parent = _safe_id(cursor.parent_ext_id)
            if parent is None:
                raise ValueError("a children read names one folder by a plain id")
            query = f"'{parent}' in parents and trashed = false"
        size = cursor.page_size or PAGE_SIZE
        while True:
            params = {
                "corpora": "allDrives",
                "includeItemsFromAllDrives": "true",
                "supportsAllDrives": "true",
                "pageSize": str(size),
                "q": query,
                "fields": FILE_FIELDS,
            }
            if cursor.page_token:
                params["pageToken"] = cursor.page_token
            answer = await self._get(cred, "files", params)
            if not answer.truncated:
                break
            if size <= MIN_PAGE:
                exc = SourceError("a Drive page is larger than the guard's cap")
                exc.code = "page_too_large"
                raise exc
            size = max(MIN_PAGE, size // 2)
        body = answer.json()
        items = body.get("files") if isinstance(body, Mapping) else None
        nodes = tuple(n for n in (
            _node(i) for i in (items or []) if isinstance(i, Mapping)) if n is not None)
        token = body.get("nextPageToken") if isinstance(body, Mapping) else None
        nxt = None
        if token:
            nxt = DiscoverCursor(phase=cursor.phase, page_token=str(token),
                                 parent_ext_id=cursor.parent_ext_id, page_size=size)
        return DiscoverPage(nodes=nodes, next_cursor=nxt)

    # ── Later slices ──────────────────────────────────────────────────────

    async def list_changed(
        self, cred: SourceCredential, cursor: str | None, folder_ext_ids: Sequence[str],
    ) -> DiscoverPage:
        raise NotImplementedError(NOT_IN_KC1)

    async def fetch_text(self, cred: SourceCredential, node: SourceNode) -> str:
        raise NotImplementedError(NOT_IN_KC1)

    async def fetch_bytes(self, cred: SourceCredential, node: SourceNode) -> bytes:
        raise NotImplementedError(NOT_IN_KC1)

    def deep_link(self, node: SourceNode) -> str:
        return node.web_url or f"https://drive.google.com/open?id={node.ext_id}"


__all__ = ["PROVIDER", "GoogleDriveSource"]
