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

1. ⚠️ **Two tables, two postures.** ``agent_token`` is RLS-EXEMPT, because the
   edge must find a token BEFORE it knows the tenant. ``agent_connection`` is
   tenant-scoped under FORCE RLS, and an unbound read of it returns no rows.
   So a token act opens an UNBOUND session through :func:`_session` (one
   reviewed ``get_session_factory()`` site, pinned in
   ``tests/unit/test_db_engine_seam.py``), reads the token row, and only then
   binds the tenant FROM THAT ROW, inside the same transaction, with the
   seam's own statement (:data:`_BIND_SQL`). ``tenant_session()`` cannot do
   this, because it needs the tenant before the transaction starts.
   ``org_settings`` binds the same way.

2. ⚠️ **The tenant never comes from the caller's input.** A connection and a
   code are created under the tenant the request already bound
   (``tenant_session()`` with no argument, so ``TenantUnbound`` when nothing is
   bound). Every later act takes the organization from a token row. That row's
   ``organization_id`` equals its connection's, because a composite foreign key
   holds the two together.

3. ⚠️ **Lock the connection BEFORE you write a token, every time.** An act that
   issues tokens (:func:`rotate_refresh`, :func:`issue_tokens`) first locks the
   live connection row with :data:`_LOCK_LIVE_CONNECTION_SQL` (``FOR NO KEY
   UPDATE``). :func:`revoke_connection` takes the same row lock with its
   ``UPDATE``. So a revoke and an issue cannot overlap. Without the lock they
   did: the token INSERT takes only ``FOR KEY SHARE`` on the connection, which
   does not conflict with the revoke's ``UPDATE``. The revoke's token
   ``UPDATE`` then missed the new, uncommitted pair, and a revoked connection
   kept a live access token and a live refresh token (review finding P0,
   2026-10-11).

   The lock order is the same on every path: the connection row, then the
   token rows. So two rotations and a revoke cannot deadlock.

   ``FOR NO KEY UPDATE`` and not ``FOR SHARE``: an issue also slides the idle
   window, which is an ``UPDATE`` of the same row. Two rotations that each held
   ``FOR SHARE`` and then both tried to upgrade would deadlock. The stronger
   lock up front makes the second rotation wait instead. ``NO KEY`` still lets
   an unrelated token INSERT take its ``FOR KEY SHARE``.

4. ⚠️ **A spend is ONE statement.** A refresh token and a code are spent with
   ``UPDATE ... WHERE used_at IS NULL ... RETURNING``. Exactly one caller wins.

5. ⚠️ **There is no grace window.** A refresh token presented after it was
   spent, by the client it belongs to, is a reuse. The store then revokes the
   connection and every token of it, in the transaction that holds the
   connection lock. Two refreshes sent at once are a reuse too. The client
   must serialize its refreshes (spec §5.5). A refresh token presented by
   ANOTHER client (or for another resource) is refused and spends nothing,
   and it does NOT count as a reuse (:data:`WRONG_CLIENT`).

6. **The idle window slides.** Each issue moves the connection's
   ``expires_at`` to now plus ``agent_connection_idle_ttl_s`` and stamps
   ``last_used_at``. A connection past its ``expires_at`` issues nothing, and
   :func:`lookup_token` refuses every token of it.

7. **Only the hash is stored.** :func:`hash_token` is the one hash. A reader of
   ``agent_token`` cannot use what it reads.

Fence: ``tests/unit/test_agent_connection_tokens.py`` (R8, a phase-4-promoted
catalog, as a NON-privileged role).
"""
from __future__ import annotations

import hashlib
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
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

    The raw tokens live only in this object, and its ``repr`` hides them, so a
    log line that prints the object does not leak them.
    """

    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_in: int
    connection_id: str
    organization_id: str
    client_id: str | None
    scope: str | None


# ── Why an issue was refused ────────────────────────────────────────────────
#
# Every refusal maps to the OAuth error ``invalid_grant`` (RFC 6749 §5.2). The
# reason exists for the log and the audit row, and because exactly one of them
# (:data:`REUSED`) has revoked the connection.

#: The token is unknown, already revoked, or expired. Nothing changed.
INVALID = "invalid"

#: The token belongs to another client, or to another resource. Nothing was
#: spent and nothing was revoked: a client that presents a token it does not
#: own must not be able to burn, or kill, the real client's connection.
WRONG_CLIENT = "wrong_client"

#: The token was already spent by its own client. The connection and every
#: token of it are now revoked.
REUSED = "reused"

#: The connection is revoked or past its idle expiry. Nothing changed.
CONNECTION_DEAD = "connection_dead"


@dataclass(frozen=True)
class Refused:
    """An issue that did not happen, and why. See the reason constants.

    For :data:`REUSED`, :data:`WRONG_CLIENT` and :data:`CONNECTION_DEAD` the
    store knows the connection, so it names it here, and EA-2 can write an
    audit row about the right agent. For :data:`INVALID` both ids stay None on
    purpose: the caller must not learn whether a token existed.
    """

    reason: str
    connection_id: str | None = None
    organization_id: str | None = None


# ── SQL. Every parameter CAST explicitly: asyncpg cannot infer the type of a
# bound NULL, and the email-OTP store learned that the hard way.

#: The seam's own tenant bind (``acb_common.db.tenant_session``), word for
#: word: LOCAL, so it ends with the transaction, and a bound parameter, so the
#: tenant is never interpolated into SQL.
_BIND_SQL = "SELECT set_config('app.tenant_id', :tenant, true)"

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

#: Which connection a refresh token belongs to, in any state. Read with NO
#: lock, only to learn which tenant to bind and which row to lock. Every
#: decision is taken after the lock, by the statements below.
_PEEK_REFRESH_SQL = """
SELECT connection_id, organization_id
  FROM agent_token
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'refresh'
"""

#: ⚠️ The lock that serializes every issue against every revoke (rule 3 of the
#: module docstring). Runs bound to the token's tenant. Under READ COMMITTED a
#: caller that waits behind a revoke re-reads the row when the revoke commits,
#: finds ``revoked_at`` set, and gets no row.
_LOCK_LIVE_CONNECTION_SQL = """
SELECT id
  FROM agent_connection
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
   AND expires_at > now()
   FOR NO KEY UPDATE
"""

#: Spend a refresh token. The single-row UPDATE the spec names (§15 EA-1).
#: The client must match, so a token presented by the wrong client spends
#: nothing. A ``resource`` the caller OMITS matches the stored one: RFC 8707
#: lets a refresh request leave it out. A ``resource`` the caller SENDS must
#: equal the stored value exactly.
_SPEND_REFRESH_SQL = """
UPDATE agent_token
   SET used_at = now()
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'refresh'
   AND used_at IS NULL
   AND revoked_at IS NULL
   AND expires_at > now()
   AND client_id = CAST(:client_id AS TEXT)
   AND (CAST(:resource AS TEXT) IS NULL OR resource = CAST(:resource AS TEXT))
RETURNING connection_id, organization_id, client_id, resource, scope
"""

#: After a failed spend, the token's state, to tell the refusals apart.
_REFRESH_STATE_SQL = """
SELECT client_id, resource, used_at
  FROM agent_token
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'refresh'
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

#: Slide the idle window. Runs under the connection lock.
_SLIDE_SQL = """
UPDATE agent_connection
   SET last_used_at = now(),
       expires_at = now() + make_interval(secs => CAST(:idle_s AS DOUBLE PRECISION))
 WHERE id = CAST(:connection_id AS UUID)
"""

#: Find a live token by its hash. Runs UNBOUND: ``agent_token`` is exempt.
#: One point read on the unique hash index.
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

#: Is the token's connection live? Bound to the token's tenant. One point read
#: on the primary key.
_CONNECTION_LIVE_SQL = """
SELECT 1
  FROM agent_connection
 WHERE id = CAST(:connection_id AS UUID)
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

    A token act starts here, because it does not know the tenant until it has
    read the token row. It binds the tenant itself, with :data:`_BIND_SQL`,
    before it touches ``agent_connection``. Imported inside the function so
    that importing this module does not load SQLAlchemy's async stack.
    """
    from acb_common.db import get_session_factory

    return get_session_factory()()


def _tenant_session(organization_id: str | None = None) -> Any:
    """``acb_common.db.tenant_session``, imported late for the same reason."""
    from acb_common.db import tenant_session

    return tenant_session(organization_id)


async def _bind(session: Any, organization_id: str) -> None:
    """Bind *organization_id* for the rest of this transaction (LOCAL)."""
    from sqlalchemy import text

    await session.execute(text(_BIND_SQL), {"tenant": str(organization_id)})


async def _lock_live_connection(
    session: Any, organization_id: str, connection_id: str
) -> bool:
    """Lock the connection row if it is live. False when it is not."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(_LOCK_LIVE_CONNECTION_SQL),
            {"connection_id": connection_id, "org": organization_id},
        )
    ).one_or_none()
    return row is not None


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


async def _revoke_in(session: Any, organization_id: str, connection_id: str) -> None:
    """Revoke a connection and every token of it, on a BOUND *session*.

    The connection ``UPDATE`` comes first, so the row lock order is the same as
    in an issue: the connection, then the tokens.
    """
    from sqlalchemy import text

    params = {"connection_id": connection_id, "org": organization_id}
    await session.execute(text(_REVOKE_CONNECTION_SQL), params)
    await session.execute(text(_REVOKE_TOKENS_SQL), params)


async def revoke_connection(organization_id: str, connection_id: str) -> None:
    """Revoke a connection and every token of it, in one transaction.

    The session is bound to *organization_id*, so the UPDATE of
    ``agent_connection`` passes RLS only for a connection of that tenant. The
    caller passes an organization it got from a token row or from the bound
    request, never from request input.

    The connection ``UPDATE`` waits for any issue that holds the connection
    lock. So the token ``UPDATE`` that follows sees the issued rows.
    """
    async with _tenant_session(organization_id) as db:
        await _revoke_in(db, organization_id, connection_id)
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

    A code minted while a revoke is in flight can survive the revoke. That is
    safe: :func:`issue_tokens` locks the connection and refuses a dead one.
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


async def _insert_pair_and_slide(
    session: Any,
    *,
    organization_id: str,
    connection_id: str,
    client_id: str | None,
    resource: str | None,
    scope: str | None,
    with_refresh: bool,
) -> IssuedTokens:
    """Insert a new pair and slide the idle window. Needs the connection lock."""
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
    await session.execute(
        text(_SLIDE_SQL),
        {
            "connection_id": connection_id,
            "idle_s": float(settings.agent_connection_idle_ttl_s),
        },
    )
    return IssuedTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=int(settings.agent_access_token_ttl_s),
        connection_id=connection_id,
        organization_id=organization_id,
        client_id=client_id,
        scope=scope,
    )


async def _run(work: Any) -> Any:
    """Run ``await work(session)`` in one transaction on an unbound session."""
    session = await _session()
    try:
        await session.begin()
        result = await work(session)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
    return result


async def _issue_in(
    session: Any, grant: CodeGrant, *, with_refresh: bool
) -> IssuedTokens | Refused:
    """The body of :func:`issue_tokens`, on an open, unbound transaction."""
    await _bind(session, grant.organization_id)
    if not await _lock_live_connection(
        session, grant.organization_id, grant.connection_id
    ):
        return Refused(
            CONNECTION_DEAD, grant.connection_id, grant.organization_id
        )
    return await _insert_pair_and_slide(
        session,
        organization_id=grant.organization_id,
        connection_id=grant.connection_id,
        client_id=grant.client_id,
        resource=grant.resource,
        scope=grant.scope,
        with_refresh=with_refresh,
    )


async def issue_tokens(
    grant: CodeGrant, *, with_refresh: bool
) -> IssuedTokens | Refused:
    """Issue the first access token (and refresh token) for a spent code.

    Takes the :class:`CodeGrant` that :func:`consume_code` returned, so the
    organization and the connection come from the code row and never from the
    caller. ``with_refresh`` is true when the client asked for
    ``offline_access``. Refused with :data:`CONNECTION_DEAD` when the
    connection was revoked or expired after the code was minted.
    """
    return await _run(lambda s: _issue_in(s, grant, with_refresh=with_refresh))


async def _rotate_in(
    session: Any, hashed: str, client_id: str, resource: str | None
) -> IssuedTokens | Refused:
    """The body of :func:`rotate_refresh`, on an open, unbound transaction.

    Split out so a test can hold the transaction open at the exact point a
    concurrent revoke would race it.
    """
    from sqlalchemy import text

    peek = (
        await session.execute(text(_PEEK_REFRESH_SQL), {"hash": hashed})
    ).one_or_none()
    if peek is None:
        return Refused(INVALID)
    organization_id = str(peek.organization_id)
    connection_id = str(peek.connection_id)

    await _bind(session, organization_id)
    if not await _lock_live_connection(session, organization_id, connection_id):
        return Refused(CONNECTION_DEAD, connection_id, organization_id)

    row = (
        await session.execute(
            text(_SPEND_REFRESH_SQL),
            {"hash": hashed, "client_id": client_id, "resource": resource},
        )
    ).one_or_none()
    if row is None:
        state = (
            await session.execute(text(_REFRESH_STATE_SQL), {"hash": hashed})
        ).one_or_none()
        if state is None:
            return Refused(INVALID)
        # The same rule as the spend: an omitted resource matches any.
        if state.client_id != client_id or (
            resource is not None and state.resource != resource
        ):
            return Refused(WRONG_CLIENT, connection_id, organization_id)
        if state.used_at is not None:
            # We hold the connection lock, so this revoke needs no upgrade and
            # sees every pair a racing rotation committed before us.
            await _revoke_in(session, organization_id, connection_id)
            return Refused(REUSED, connection_id, organization_id)
        # Revoked or expired. Name nothing (see :class:`Refused`).
        return Refused(INVALID)

    return await _insert_pair_and_slide(
        session,
        organization_id=organization_id,
        connection_id=connection_id,
        client_id=row.client_id,
        resource=row.resource,
        scope=row.scope,
        with_refresh=True,
    )


async def rotate_refresh(
    raw_refresh: str, *, client_id: str, resource: str | None = None
) -> IssuedTokens | Refused:
    """Spend a refresh token and issue a new pair, or say why not.

    *client_id* is the authenticated client of the token request. *resource*
    is the ``resource`` parameter, when the client sent one. RFC 8707 lets a
    refresh request omit it, and None then matches the stored resource. A
    token that belongs to another client, or that the client sends for
    another resource, is refused with :data:`WRONG_CLIENT` and is NOT spent.

    ⚠️ A refresh token that its own client ALREADY spent is a reuse. The store
    revokes the connection and every token of it, the new pair included, and
    answers :data:`REUSED`. There is no grace window, so two concurrent
    refreshes end the connection (spec §5.5).
    """
    if not (raw_refresh or "").strip():
        return Refused(INVALID)
    result = await _run(
        lambda s: _rotate_in(s, hash_token(raw_refresh), client_id, resource)
    )
    if isinstance(result, Refused) and result.reason == REUSED:
        # Ids only. A token or its hash never goes into a log line.
        _log.warning(
            "agent_tokens.refresh_reused", connection_id=result.connection_id
        )
        _log.warning(
            "agent_tokens.connection_revoked",
            connection_id=result.connection_id,
            reason="refresh_reused",
        )
    return result


async def lookup_token(raw: str, *, kind: str = ACCESS) -> TokenLookup | None:
    """Find a live token of a live connection by its hash, with NO tenant bound.

    Returns the connection id and the organization id, so the caller can bind
    that tenant before it reads anything else. ``None`` covers an unknown, an
    expired, a spent and a revoked token alike, and a token whose connection
    is revoked or past its idle expiry.

    Two point reads in one transaction: the token by its unique hash, then,
    bound to the token's tenant, its connection by primary key.
    """
    from sqlalchemy import text

    if kind not in (ACCESS, REFRESH, CODE):
        raise ValueError(f"unknown token kind {kind!r}")
    if not (raw or "").strip():
        return None

    async def _work(session: Any) -> Any:
        row = (
            await session.execute(
                text(_LOOKUP_SQL), {"hash": hash_token(raw), "kind": kind}
            )
        ).one_or_none()
        if row is None:
            return None
        await _bind(session, str(row.organization_id))
        live = (
            await session.execute(
                text(_CONNECTION_LIVE_SQL),
                {"connection_id": str(row.connection_id)},
            )
        ).one_or_none()
        return row if live is not None else None

    row = await _run(_work)
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
