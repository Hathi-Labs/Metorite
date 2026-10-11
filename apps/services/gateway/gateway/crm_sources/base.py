"""The connector seam for an external CRM (WS-53 CRM-Z1, ``crm_platform.md`` §5.1).

The sync engine calls :class:`CrmSource` and never a provider client. A second
provider adds one adapter beside ``zoho/`` and one line in ``registry.py``. The
engine, the write guard and every reader stay as they are.

Two rules hold this seam:

* **A credential comes in through the constructor.** No file under
  ``crm_sources`` reads the settings, the environment or a file on disk. The
  fence is ``tests/unit/test_crm_sources_no_global_creds.py``.
* **An adapter never stores a credential.** It keeps the current one in
  memory and exposes it as :attr:`CrmSource.credential`. An adapter can
  refresh the token by itself in the middle of a read, so after each call the
  caller reads ``credential`` and stores it when it changed
  (``crm_connections``, CRM-Z2).

The shapes here carry raw provider fields. Mapping them to canonical columns
is CRM-Z3 and CRM-Z4.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

#: The entities a source reads records for. The four that exist (lead, deal,
#: contact, company) plus the activities. Users have their own operation.
CANONICAL_ENTITIES: tuple[str, ...] = (
    "lead",
    "deal",
    "contact",
    "company",
    "note",
    "call",
    "meeting",
)


# ── Credentials ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OAuthClientConfig:
    """The platform's OAuth client for one provider.

    CRM-Z2 reads it and passes it in. ``client_secret`` is kept out of
    ``repr`` so a log line or a traceback cannot print it.
    """

    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str


@dataclass(frozen=True)
class SourceCredential:
    """The tokens of one connection. Both tokens are kept out of ``repr``.

    ``provider_meta`` holds what one provider needs and another does not, as
    strings. For Zoho that is the data centre: ``accounts_server``,
    ``api_domain`` and ``location``. The adapter checks each host against its
    allowlist before a request. The shape names no provider's words, so a
    second provider adds keys and no fields.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime
    scopes: tuple[str, ...] = ()
    provider_meta: Mapping[str, str] = field(default_factory=dict)


# ── Records and paging ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceRecord:
    """One record as the provider sent it. ``fields`` is the raw row."""

    entity: str
    ext_id: str
    modified_at: datetime | None
    fields: dict[str, Any]


@dataclass(frozen=True)
class SourceCursor:
    """Where a read stands. The caller stores it as it is, and resumes with it.

    ``since`` is the incremental cursor. ``page`` and ``page_token`` say which
    page comes next. When ``page_token`` is set, the adapter sends it and no
    page number. ``seen_ids`` holds the ids the read already returned at
    exactly ``since``. A keyset read uses it to skip them when it reads that
    second again (see ``ZohoSource.list_changed``).
    """

    since: datetime | None = None
    page: int = 1
    page_token: str | None = None
    seen_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcePage:
    """One page of a read. ``next_cursor`` is ``None`` when the read is done."""

    records: tuple[SourceRecord, ...]
    next_cursor: SourceCursor | None


# ── Errors ──────────────────────────────────────────────────────────────────


class SourceError(RuntimeError):
    """A provider call failed. The message never holds a token or a secret."""


class NeedsReconnect(SourceError):
    """The refresh token is dead. An admin must connect again.

    The caller sets ``needs_reconnect`` on the connection and stops. It must
    never retry a dead token.
    """


class RateLimited(SourceError):
    """The provider still refused the call after the last backoff."""

    def __init__(self, message: str, *, tries: int) -> None:
        super().__init__(message)
        self.tries = tries


class BudgetExhausted(SourceError):
    """The next call would go past the credit budget, so it was not sent."""

    def __init__(self, message: str, *, used: int, budget: int) -> None:
        super().__init__(message)
        self.used = used
        self.budget = budget


class UntrustedHost(SourceError):
    """A URL is not on the provider's allowlist, or it is not https."""


# ── The protocol ────────────────────────────────────────────────────────────


class CrmSource(Protocol):
    """The eight operations of ``crm_platform.md`` §5.1.

    Each numbered comment names the operation of the spec.
    """

    provider: str

    # The current credential. A refresh, explicit or automatic, replaces it,
    # and the caller stores the new one. ``None`` before a consent.
    @property
    def credential(self) -> SourceCredential | None: ...

    # 1. Start the consent flow and finish it. ``params`` is the query of the
    # provider's redirect. The caller checks the signed ``state`` first.
    def consent_url(self, *, scopes: Sequence[str], state: str) -> str: ...

    async def finish_consent(
        self,
        *,
        params: Mapping[str, str],
        scopes: Sequence[str],
    ) -> SourceCredential: ...

    # 2. Refresh the credential. Raise NeedsReconnect only for a dead refresh
    # token, RateLimited for a throttle, and SourceError for any other failure.
    async def refresh(self) -> SourceCredential: ...

    # 3. The field definitions and the pipelines of each entity.
    async def list_field_defs(self, entity: str) -> list[dict[str, Any]]: ...

    async def list_pipelines(self, entity: str) -> list[dict[str, Any]]: ...

    # 4. The records changed since a cursor, one page for each call.
    async def list_changed(
        self,
        entity: str,
        cursor: SourceCursor | None = None,
    ) -> SourcePage: ...

    # 5. The records deleted since a cursor, one page for each call.
    async def list_deleted(
        self,
        entity: str,
        cursor: SourceCursor | None = None,
    ) -> SourcePage: ...

    # 6. The users, so the sync can map an owner to a member by email.
    async def list_users(self) -> list[SourceRecord]: ...

    # 7. The deep link to one record.
    def deep_link(self, entity: str, ext_id: str) -> str: ...

    # 8. The budget used, and what is left.
    @property
    def credits_used(self) -> int: ...

    async def credits_remaining(self) -> int: ...


__all__ = [
    "CANONICAL_ENTITIES",
    "BudgetExhausted",
    "CrmSource",
    "NeedsReconnect",
    "OAuthClientConfig",
    "RateLimited",
    "SourceCredential",
    "SourceCursor",
    "SourceError",
    "SourcePage",
    "SourceRecord",
    "UntrustedHost",
]
