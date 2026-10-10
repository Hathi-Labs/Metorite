"""Outside-agent connections and tokens — the store (WS-55 EA-1).

Spec: ``project-docs/specs/external_agent_access.md`` §5.5, §7, §10.3 and §15
EA-1 · decision D97 · migration ``infra/postgres/241_agent_access_tokens.sql``.

**What this module is.** The one place that mints, finds, spends and revokes
the tokens an outside agent holds. An access token, a refresh token and an
authorization code are all random opaque strings. The database keeps only the
SHA-256 of each one, in ``agent_token``. The connection a token belongs to is a
row of ``agent_connection``. EA-1 adds no HTTP surface. The authorization
server (EA-2) and the MCP edge (EA-3) call these functions.

**Why it is in ``acb_auth``.** A token is an identity artifact, and this
package owns authentication. It follows ``acb_auth.email_otp``, the other
store that must work before a tenant is known.

## The rules that are easy to get wrong

1. ⚠️ **Two session kinds, and each table has its own.** ``agent_token`` is
   RLS-EXEMPT, because the edge must find a token BEFORE it knows the tenant.
   So the token acts open an UNBOUND session through :func:`_session` (one
   reviewed ``get_session_factory()`` site, pinned in
   ``tests/unit/test_db_engine_seam.py``). ``agent_connection`` is
   tenant-scoped under FORCE RLS, so every read and write of it goes through
   ``acb_common.db.tenant_session``. An unbound read of it returns no rows.

2. ⚠️ **The tenant never comes from the caller's input.** A connection and a
   code are created under the tenant the request already bound
   (``tenant_session()`` with no argument, so ``TenantUnbound`` when nothing is
   bound). Every later act takes the organization from the token row itself.
   That row's ``organization_id`` must equal its connection's, because a
   composite foreign key holds the two together.

3. ⚠️ **A spend is ONE statement.** A refresh token and a code are spent with
   ``UPDATE ... WHERE used_at IS NULL ... RETURNING``. Under concurrency the
   second caller waits for the row lock, then reads ``used_at`` as set and
   matches nothing. So exactly one caller wins.

4. ⚠️ **There is no grace window.** A refresh token presented after it was
   spent is a reuse. The store then revokes the connection and every token of
   it, in one tenant-bound transaction. Two refreshes sent at once are a reuse
   too, so the client loses the connection. The client must serialize its
   refreshes (spec §5.5).

5. **Only the hash is stored.** :func:`hash_token` is the one hash. A reader of
   ``agent_token`` cannot use what it reads.

Fence: ``tests/unit/test_agent_connection_tokens.py`` (R8, a phase-4-promoted
catalog, as a NON-privileged role).
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from acb_common import get_logger, get_settings

_log = get_logger("acb_auth.agent_tokens")

#: The three kinds of row in ``agent_token``. The migration's CHECK names the
#: same three.
ACCESS = "access"
REFRESH = "refresh"
CODE = "code"

#: Bytes of randomness in each token. 32 bytes is 256 bits.
_TOKEN_BYTES = 32


def new_token() -> str:
    """A fresh random opaque token, URL-safe."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw: str) -> str:
    """The one hash of a token: lowercase hex SHA-256 of its UTF-8 bytes.

    A token carries 256 random bits, so a plain hash is enough. A salt or a
    slow hash protects a guessable secret, and these are not guessable.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── What the functions return ───────────────────────────────────────────────


@dataclass(frozen=True)
class TokenLookup:
    """A live token, found by its hash. The edge binds ``organization_id``."""

    connection_id: str
    organization_id: str
    kind: str
    client_id: str | None
    resource: str | None
    scope: str | None
    expires_at: datetime


@dataclass(frozen=True)
class CodeGrant:
    """A spent authorization code, with what the client asked for.

    EA-2 checks the PKCE verifier, the client, the redirect and the resource
    against these fields before it calls :func:`issue_tokens`.
    """

    connection_id: str
    organization_id: str
    client_id: str
    code_challenge: str
    redirect_uri: str
    resource: str
    scope: str | None


@dataclass(frozen=True)
class IssuedTokens:
    """A new access token, and a refresh token when one was asked for.

    The raw tokens live only in this object. The database keeps their hashes.
    """

    access_token: str
    refresh_token: str | None
    expires_in: int
    connection_id: str
    organization_id: str
    scope: str | None


# ── SQL. Every parameter CAST explicitly: asyncpg cannot infer the type of a
# bound NULL, and the email-OTP store learned that the hard way.

_INSERT_CONNECTION_SQL = """
INSERT INTO agent_connection
       (organization_id, member_email, client_id, client_name, client_origin,
        scopes, pinned_projects, include_personal, expires_at)
VALUES (CAST(:org AS UUID),
        CAST(:member AS TEXT),
        CAST(:client_id AS TEXT),
        CAST(:client_name AS TEXT),
        CAST(:client_origin AS TEXT),
        CAST(:scopes AS TEXT[]),
        CAST(:pins AS UUID[]),
        CAST(:personal AS BOOLEAN),
        now() + make_interval(secs => CAST(:idle_s AS DOUBLE PRECISION)))
RETURNING id
"""

#: A code is minted only for a live connection that the BOUND tenant can see.
#: The SELECT runs under RLS, so a connection of another organization is
#: invisible and the INSERT writes nothing.
_INSERT_CODE_SQL = """
INSERT INTO agent_token
       (organization_id, connection_id, kind, token_hash, expires_at,
        client_id, code_challenge, redirect_uri, resource, scope)
SELECT c.organization_id, c.id, 'code', CAST(:hash AS TEXT),
       now() + make_interval(secs => CAST(:ttl_s AS DOUBLE PRECISION)),
       CAST(:client_id AS TEXT), CAST(:challenge AS TEXT),
       CAST(:redirect_uri AS TEXT), CAST(:resource AS TEXT),
       CAST(:scope AS TEXT)
  FROM agent_connection c
 WHERE c.id = CAST(:connection_id AS UUID)
   AND c.revoked_at IS NULL
   AND c.expires_at > now()
RETURNING id
"""

#: Spend a code. Single use, expiry and revocation are all in the WHERE.
_SPEND_CODE_SQL = """
UPDATE agent_token
   SET used_at = now()
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'code'
   AND used_at IS NULL
   AND revoked_at IS NULL
   AND expires_at > now()
RETURNING connection_id, organization_id, client_id, code_challenge,
          redirect_uri, resource, scope
"""

#: Spend a refresh token. The single-row UPDATE the spec names (§15 EA-1).
_SPEND_REFRESH_SQL = """
UPDATE agent_token
   SET used_at = now()
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'refresh'
   AND used_at IS NULL
   AND revoked_at IS NULL
   AND expires_at > now()
RETURNING connection_id, organization_id, client_id, resource, scope
"""

#: After a failed spend: was this a refresh token that was already spent?
_SPENT_REFRESH_SQL = """
SELECT connection_id, organization_id
  FROM agent_token
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'refresh'
   AND used_at IS NOT NULL
"""

_INSERT_TOKEN_SQL = """
INSERT INTO agent_token
       (organization_id, connection_id, kind, token_hash, expires_at,
        client_id, resource, scope)
VALUES (CAST(:org AS UUID), CAST(:connection_id AS UUID), CAST(:kind AS TEXT),
        CAST(:hash AS TEXT),
        now() + make_interval(secs => CAST(:ttl_s AS DOUBLE PRECISION)),
        CAST(:client_id AS TEXT), CAST(:resource AS TEXT), CAST(:scope AS TEXT))
"""

#: Find a live token by its hash. Runs UNBOUND: ``agent_token`` is exempt.
_LOOKUP_SQL = """
SELECT connection_id, organization_id, kind, client_id, resource, scope,
       expires_at
  FROM agent_token
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = CAST(:kind AS TEXT)
   AND used_at IS NULL
   AND revoked_at IS NULL
   AND expires_at > now()
"""

_REVOKE_CONNECTION_SQL = """
UPDATE agent_connection
   SET revoked_at = now()
 WHERE id = CAST(:connection_id AS UUID)
   AND revoked_at IS NULL
"""

_REVOKE_TOKENS_SQL = """
UPDATE agent_token
   SET revoked_at = now()
 WHERE connection_id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
"""


async def _session() -> Any:
    """A session from the ONE shared engine, deliberately UNBOUND.

    Only the token acts use it, and they touch only ``agent_token``, which is
    RLS-EXEMPT. Imported inside the function so that importing this module
    does not load SQLAlchemy's async stack.
    """
    from acb_common.db import get_session_factory

    return get_session_factory()()


def _tenant_session(organization_id: str | None = None) -> Any:
    """``acb_common.db.tenant_session``, imported late for the same reason."""
    from acb_common.db import tenant_session

    return tenant_session(organization_id)


# ── Connections ─────────────────────────────────────────────────────────────


async def create_connection(
    member_email: str,
    client_id: str,
    *,
    client_name: str = "",
    client_origin: str = "",
    scopes: Sequence[str] = (),
    pinned_projects: Sequence[str] = (),
    include_personal: bool = False,
) -> str:
    """Create a connection in the BOUND tenant and return its id.

    The organization is the tenant the request already bound. It is never a
    parameter, so no caller can pass one from a request (R11). With nothing
    bound, ``tenant_session`` raises ``TenantUnbound``.
    """
    from acb_common.db import current_tenant
    from sqlalchemy import text

    member = (member_email or "").strip().lower()
    if not member:
        raise ValueError("a connection needs the member's email")
    if not (client_id or "").strip():
        raise ValueError("a connection needs a client id")

    async with _tenant_session() as db:
        row = (
            await db.execute(
                text(_INSERT_CONNECTION_SQL),
                {
                    "org": current_tenant(),
                    "member": member,
                    "client_id": client_id,
                    "client_name": client_name,
                    "client_origin": client_origin,
                    "scopes": list(scopes),
                    "pins": [str(p) for p in pinned_projects],
                    "personal": bool(include_personal),
                    "idle_s": float(get_settings().agent_connection_idle_ttl_s),
                },
            )
        ).one()
    _log.info("agent_tokens.connection_created", client_id=client_id)
    return str(row.id)


async def revoke_connection(organization_id: str, connection_id: str) -> None:
    """Revoke a connection and every token of it, in one transaction.

    The session is bound to *organization_id*, so the UPDATE of
    ``agent_connection`` passes RLS only for a connection of that tenant. The
    caller passes an organization it got from a token row or from the bound
    request, never from request input.
    """
    from sqlalchemy import text

    params = {"connection_id": connection_id, "org": organization_id}
    async with _tenant_session(organization_id) as db:
        await db.execute(text(_REVOKE_CONNECTION_SQL), params)
        await db.execute(text(_REVOKE_TOKENS_SQL), params)
    _log.warning("agent_tokens.connection_revoked", connection_id=connection_id)


# ── Authorization codes ─────────────────────────────────────────────────────


async def mint_code(
    connection_id: str,
    *,
    client_id: str,
    code_challenge: str,
    redirect_uri: str,
    resource: str,
    scope: str | None = None,
) -> str:
    """Mint a single-use authorization code for a connection, return it raw.

    Runs in the BOUND tenant, so a connection of another organization is
    invisible and the call raises ``LookupError``. The code lives
    ``agent_code_ttl_s`` seconds.
    """
    from sqlalchemy import text

    for name, value in (
        ("client_id", client_id),
        ("code_challenge", code_challenge),
        ("redirect_uri", redirect_uri),
        ("resource", resource),
    ):
        if not (value or "").strip():
            raise ValueError(f"a code needs {name}")

    raw = new_token()
    async with _tenant_session() as db:
        row = (
            await db.execute(
                text(_INSERT_CODE_SQL),
                {
                    "hash": hash_token(raw),
                    "ttl_s": float(get_settings().agent_code_ttl_s),
                    "client_id": client_id,
                    "challenge": code_challenge,
                    "redirect_uri": redirect_uri,
                    "resource": resource,
                    "scope": scope,
                    "connection_id": connection_id,
                },
            )
        ).one_or_none()
    if row is None:
        raise LookupError("no live connection with that id in this tenant")
    return raw


async def consume_code(raw_code: str) -> CodeGrant | None:
    """Spend a code and return what it carries, or ``None``.

    ``None`` covers an unknown, a spent, an expired and a revoked code alike.
    A code works once: a second call with the same code returns ``None``.
    Runs UNBOUND, because the token endpoint knows no tenant yet.
    """
    from sqlalchemy import text

    if not (raw_code or "").strip():
        return None
    session = await _session()
    try:
        await session.begin()
        row = (
            await session.execute(
                text(_SPEND_CODE_SQL), {"hash": hash_token(raw_code)}
            )
        ).one_or_none()
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    if row is None:
        return None
    return CodeGrant(
        connection_id=str(row.connection_id),
        organization_id=str(row.organization_id),
        client_id=row.client_id,
        code_challenge=row.code_challenge,
        redirect_uri=row.redirect_uri,
        resource=row.resource,
        scope=row.scope,
    )


# ── Access and refresh tokens ───────────────────────────────────────────────


async def _insert_pair(
    session: Any,
    *,
    organization_id: str,
    connection_id: str,
    client_id: str | None,
    resource: str | None,
    scope: str | None,
    with_refresh: bool,
) -> IssuedTokens:
    """Insert a new access token, and a refresh token if asked, on *session*."""
    from sqlalchemy import text

    settings = get_settings()
    access = new_token()
    refresh = new_token() if with_refresh else None
    base = {
        "org": organization_id,
        "connection_id": connection_id,
        "client_id": client_id,
        "resource": resource,
        "scope": scope,
    }
    await session.execute(
        text(_INSERT_TOKEN_SQL),
        {
            **base,
            "kind": ACCESS,
            "hash": hash_token(access),
            "ttl_s": float(settings.agent_access_token_ttl_s),
        },
    )
    if refresh is not None:
        await session.execute(
            text(_INSERT_TOKEN_SQL),
            {
                **base,
                "kind": REFRESH,
                "hash": hash_token(refresh),
                "ttl_s": float(settings.agent_refresh_token_ttl_s),
            },
        )
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(settings.agent_access_token_ttl_s),
        connection_id=connection_id,
        organization_id=organization_id,
        scope=scope,
    )


async def issue_tokens(grant: CodeGrant, *, with_refresh: bool) -> IssuedTokens:
    """Issue the first access token (and refresh token) for a spent code.

    Takes the :class:`CodeGrant` that :func:`consume_code` returned, so the
    organization and the connection come from the code row and never from the
    caller. ``with_refresh`` is true when the client asked for
    ``offline_access``.
    """
    session = await _session()
    try:
        await session.begin()
        issued = await _insert_pair(
            session,
            organization_id=grant.organization_id,
            connection_id=grant.connection_id,
            client_id=grant.client_id,
            resource=grant.resource,
            scope=grant.scope,
            with_refresh=with_refresh,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    return issued


async def rotate_refresh(raw_refresh: str) -> IssuedTokens | None:
    """Spend a refresh token and issue a new pair, or return ``None``.

    The spend and the new pair share one transaction. So a second caller with
    the same token waits for the first to commit, then finds the token spent.

    ⚠️ A refresh token that was ALREADY spent is a reuse. The store revokes the
    connection and every token of it, the new pair included, and returns
    ``None``. There is no grace window, so two concurrent refreshes end the
    connection (spec §5.5).
    """
    from sqlalchemy import text

    if not (raw_refresh or "").strip():
        return None
    hashed = hash_token(raw_refresh)
    issued: IssuedTokens | None = None
    reused: tuple[str, str] | None = None

    session = await _session()
    try:
        await session.begin()
        row = (
            await session.execute(text(_SPEND_REFRESH_SQL), {"hash": hashed})
        ).one_or_none()
        if row is not None:
            issued = await _insert_pair(
                session,
                organization_id=str(row.organization_id),
                connection_id=str(row.connection_id),
                client_id=row.client_id,
                resource=row.resource,
                scope=row.scope,
                with_refresh=True,
            )
        else:
            spent = (
                await session.execute(text(_SPENT_REFRESH_SQL), {"hash": hashed})
            ).one_or_none()
            if spent is not None:
                reused = (str(spent.organization_id), str(spent.connection_id))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()

    if reused is not None:
        organization_id, connection_id = reused
        _log.warning(
            "agent_tokens.refresh_reused", connection_id=connection_id
        )
        await revoke_connection(organization_id, connection_id)
        return None
    return issued


async def lookup_token(raw: str, *, kind: str = ACCESS) -> TokenLookup | None:
    """Find a live token by its hash, with NO tenant bound.

    Returns the connection id and the organization id, so the caller can bind
    that tenant before it reads anything else. ``None`` covers an unknown, an
    expired, a spent and a revoked token alike.
    """
    from sqlalchemy import text

    if kind not in (ACCESS, REFRESH, CODE):
        raise ValueError(f"unknown token kind {kind!r}")
    if not (raw or "").strip():
        return None
    session = await _session()
    try:
        row = (
            await session.execute(
                text(_LOOKUP_SQL), {"hash": hash_token(raw), "kind": kind}
            )
        ).one_or_none()
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    if row is None:
        return None
    return TokenLookup(
        connection_id=str(row.connection_id),
        organization_id=str(row.organization_id),
        kind=row.kind,
        client_id=row.client_id,
        resource=row.resource,
        scope=row.scope,
        expires_at=row.expires_at,
    )
