"""The connector seam for a company file source (WS-61 KC-1a).

Spec: ``project-docs/specs/knowledge_base.md`` §7.1 (the protocol, the shape
of record) and §7.5, and ``project-docs/specs/knowledge_curation.md`` §7.1,
§7.2 steps 1 to 3 and §7.4.

The Knowledge sync calls :class:`KbSource` and never a provider client. A
second provider (OneDrive, KC-2) adds one adapter package beside
``google_drive/`` and one line in ``registry.py``.

**What this module reuses, and does not copy.** The credential shape and the
errors come from ``gateway.crm_sources.base``: :class:`SourceCredential`,
:class:`OAuthClientConfig`, :class:`SourceError`, :class:`NeedsReconnect`,
:class:`RateLimited` and :class:`UntrustedHost`. The connection store
(``connections/store.py``) already seals a :class:`SourceCredential`, and
``connections/shared_token.py`` already refreshes one under a row lock. So a
file source and a CRM source share one token life.

Three rules hold this seam:

* **The app credential enters through the constructor, and the member's
  token enters as an argument.** No file under ``kb_sources`` reads the
  settings, the environment, a file on disk or ``acb_common``. The route
  reads the client id and secret and passes them in. The fence is
  ``tests/unit/test_kb_sources_no_global_creds.py``.
* **Read only.** A source asks for read scopes only and sends no write verb.
  The one POST of an adapter is its token call and its revoke call. The
  fence is ``tests/unit/test_kc_sources_read_only_scopes.py``.
* **An adapter never stores a credential.** :meth:`KbSource.refresh` returns
  a new one, and ``shared_token`` writes it back.

**KC-1a builds discovery only.** :meth:`KbSource.list_changed`,
:meth:`KbSource.fetch_text` and :meth:`KbSource.fetch_bytes` are part of the
protocol, and an adapter may raise ``NotImplementedError("KC-3/KC-5")`` for
them until KC-3 (the change reader) and KC-5 (the text) ship.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from gateway.crm_sources.base import (
    NeedsReconnect,
    OAuthClientConfig,
    RateLimited,
    SourceCredential,
    SourceError,
    UntrustedHost,
)

#: The two kinds of a source (``knowledge_curation.md`` §7.1). A member
#: source reaches the member's own files. An org source (an admin picks
#: shared drives) is a later slice.
SCOPE_MEMBER = "member"
SCOPE_ORG = "org"

#: The two kinds of a node. Mirrors the CHECK of ``kb_source_nodes.kind``.
KIND_FOLDER = "folder"
KIND_FILE = "file"

#: The message of a method that a later slice builds.
NOT_IN_KC1 = "KC-3/KC-5"


@dataclass(frozen=True)
class SourceNode:
    """One folder or file, as metadata only. It never holds text or bytes.

    The eight fields of ``knowledge_base.md`` §7.1, plus ``drive_id``, the
    shared drive that holds the node (``None`` for the member's own drive),
    which ``kb_source_nodes.drive_id`` keeps.
    """

    ext_id: str
    parent_ext_id: str | None
    kind: str
    name: str
    mime: str | None
    size: int | None
    modified_at: datetime | None
    web_url: str | None
    drive_id: str | None = None


@dataclass(frozen=True)
class AccountIdentity:
    """The provider account of a credential, for the same-account check.

    ``account_id`` is the provider's stable id of the account. ``email`` is
    the address in lower case. Neither is a secret.
    """

    account_id: str
    email: str
    display_name: str = ""


@dataclass(frozen=True)
class DiscoverCursor:
    """Where a discovery stands. The caller passes it back as it came.

    ``phase`` is the adapter's own word for the part of the tree it reads.
    ``parent_ext_id`` asks for the children of one folder only, which the
    level walk of a tree past the node cap uses (``knowledge_base.md`` §7.2).
    ``page_size`` lets an adapter shrink a page that came back too large.
    """

    phase: str = ""
    page_token: str | None = None
    parent_ext_id: str | None = None
    page_size: int = 0


@dataclass(frozen=True)
class DiscoverPage:
    """One page of the tree. ``next_cursor`` is ``None`` when the read is done."""

    nodes: tuple[SourceNode, ...]
    next_cursor: DiscoverCursor | None


class KbSource(Protocol):
    """The operations of ``knowledge_base.md`` §7.1, in its order."""

    provider: str

    # The provider's consent page, with read-only scopes only. *scope_kind* is
    # SCOPE_MEMBER in KC-1a. The caller signs *state*.
    def consent_url(self, *, state: str, scope_kind: str) -> str: ...

    # The token life. The caller checks the granted scopes and stores the
    # credential. ``refresh`` raises NeedsReconnect only for a dead refresh
    # token. ``revoke`` is True when the provider confirms.
    async def exchange_code(self, *, code: str) -> SourceCredential: ...

    async def refresh(self, cred: SourceCredential) -> SourceCredential: ...

    async def revoke(self, cred: SourceCredential) -> bool: ...

    # The remote account of a credential.
    async def account_identity(self, cred: SourceCredential) -> AccountIdentity: ...

    # One page of the folder tree, metadata only.
    async def discover(
        self, cred: SourceCredential, cursor: DiscoverCursor | None = None,
    ) -> DiscoverPage: ...

    # KC-3: the changes since a cursor, with the provider's own delta API.
    async def list_changed(
        self, cred: SourceCredential, cursor: str | None, folder_ext_ids: Sequence[str],
    ) -> DiscoverPage: ...

    # KC-5: the text of one document, capped. KC-5: the bytes, only for
    # "Keep a copy".
    async def fetch_text(self, cred: SourceCredential, node: SourceNode) -> str: ...

    async def fetch_bytes(self, cred: SourceCredential, node: SourceNode) -> bytes: ...

    # The URL that opens the node at the provider. Never fetched.
    def deep_link(self, node: SourceNode) -> str: ...


__all__ = [
    "KIND_FILE",
    "KIND_FOLDER",
    "NOT_IN_KC1",
    "SCOPE_MEMBER",
    "SCOPE_ORG",
    "AccountIdentity",
    "DiscoverCursor",
    "DiscoverPage",
    "KbSource",
    "NeedsReconnect",
    "OAuthClientConfig",
    "RateLimited",
    "SourceCredential",
    "SourceError",
    "SourceNode",
    "UntrustedHost",
]
