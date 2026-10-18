"""Outside-agent connections and tokens — the store (WS-55 EA-1).

Spec: ``project-docs/specs/external_agent_access.md`` §5.5, §7, §10.3 and §15
EA-1 · decision D97 · migration ``infra/postgres/248_agent_access_tokens.sql``.

**What this module is.** The one place that mints, finds, spends and revokes
the tokens an outside agent holds. An access token, a refresh token and an
authorization code are all random opaque strings. The database keeps only the
SHA-256 of each one, in ``agent_token``. The connection a token belongs to is a
row of ``agent_connection``. The authorization server (EA-2a, the edge's
``token`` and ``revoke`` and the gateway's ``/agent-access``) and the MCP edge
(EA-3) call these functions. EA-2a added three: :func:`revoke_replayed_code`
(a spent code seen again revokes its connection) and the ``agent_client``
cache, :func:`get_client` and :func:`cache_cimd_client`. EA-3a added
:func:`read_live_connection` for branch 0 of the gateway, and
:func:`lookup_token` now returns the scopes, the pins, the personal opt-in and
the origin of the connection it read. EA-4b added :func:`list_connections`
and :func:`revoke_connection_scoped` for the AI agents view of the
Integrations app. Both take the organization from the bound request, and the
revoke matches the member too when the caller is not an admin.

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

8. ⚠️ **The organization allowlist (EA-10p, spec §10.6).** An organization
   that ``agent_oauth.org_allowed`` refuses gets no token and no connection.
   :func:`issue_tokens` and :func:`rotate_refresh` answer
   :data:`ORG_NOT_ALLOWED`. The refresh check comes BEFORE the spend, so it
   spends nothing. It comes AFTER the reuse check (fix round 1): a replay of
   a spent refresh token still revokes the connection, list or no list. :func:`read_live_connection` answers ``None``, so branch
   0 refuses. The organization is the one of the token row or the connection
   row, never an argument. :func:`lookup_token` does NOT check the list:
   ``/oauth/revoke`` must still find a token to revoke it, and the edge
   bearer check tests the list itself.

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

from acb_auth import agent_oauth

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
    #: The connection row, read in the same transaction (EA-3a, spec §7.1
    #: step 3). The edge intersects ``scope`` with ``scopes``.
    scopes: tuple[str, ...] = ()
    pinned_projects: tuple[str, ...] = ()
    include_personal: bool = False
    client_origin: str = ""
    #: The CONNECTION's ``client_id`` (EA-13a). The edge stamps
    #: ``agent_oauth.actor_via_for(client_origin, client_id=...)`` with it,
    #: as branch 0 does with :attr:`LiveConnection.client_id`. ``client_id``
    #: above is the token's own copy, and no stamp reads it.
    connection_client_id: str = ""


@dataclass(frozen=True)
class LiveConnection:
    """A live connection, read by its id for branch 0 (EA-3a, spec §10.1).

    ``member_email`` is the ONE source of the member of a signed request. No
    header can name it.
    """

    connection_id: str
    organization_id: str
    member_email: str
    client_origin: str
    scopes: tuple[str, ...]
    pinned_projects: tuple[str, ...]
    include_personal: bool
    #: ``agent_connection.client_id`` (EA-13a). Branch 0 stamps
    #: ``agent_oauth.actor_via_for(client_origin, client_id=...)`` with it.
    client_id: str = ""


@dataclass(frozen=True)
class CodeGrant:
    """A spent authorization code, with what the client asked for.

    :func:`consume_code` already matched the PKCE challenge, the client, the
    redirect and the resource in its WHERE clause, so these fields equal what
    the token request sent.
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

#: The organization of the token is not on ``agent_access_orgs`` (EA-10p,
#: spec §10.6). Nothing was spent and nothing was revoked, so the client can
#: refresh again when the organization comes back on the list.
ORG_NOT_ALLOWED = "org_not_allowed"


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
#:
#: ⚠️ So are the client, the redirect URI, the PKCE challenge and the
#: resource (EA-2a fix round 1). A presentation that names the wrong client
#: or redirect, or that cannot prove the verifier, matches no row: it spends
#: nothing and revokes nothing. Without these four clauses a thief who
#: intercepted a code could burn it without the verifier. The caller passes
#: ``code_challenge`` as the S256 of the verifier it received.
_SPEND_CODE_SQL = """
UPDATE agent_token
   SET used_at = now()
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'code'
   AND used_at IS NULL
   AND revoked_at IS NULL
   AND expires_at > now()
   AND client_id = CAST(:client_id AS TEXT)
   AND redirect_uri = CAST(:redirect_uri AS TEXT)
   AND code_challenge = CAST(:challenge AS TEXT)
   AND resource = CAST(:resource AS TEXT)
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

#: The live connection, with what the edge and branch 0 need (EA-3a, spec
#: §7.1 step 3 and §10.1 step 4). Runs bound to the tenant. One point read on
#: the primary key. "Live" is not revoked and not past ``expires_at``.
#:
#: ⚠️ The organization is in the WHERE as well as in the bound GUC. Under
#: FORCE RLS the bind alone hides a connection of another tenant. The
#: predicate keeps that answer for a role that bypasses RLS too.
#:
#: ``pinned_projects`` comes back as ``text[]``, so a pin is a plain string
#: and never an asyncpg ``UUID`` object. ``organization_id`` comes back too:
#: :func:`read_live_connection` reports the ROW's organization, never the
#: one it was asked for, so branch 0 can compare the two.
_CONNECTION_LIVE_SQL = """
SELECT CAST(organization_id AS TEXT) AS organization_id,
       member_email, client_origin, client_id, scopes,
       CAST(pinned_projects AS TEXT[]) AS pinned_projects, include_personal
  FROM agent_connection
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
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

#: A code that was ALREADY spent, by its hash (EA-2a). A replay of it revokes
#: the connection the code belongs to. Read unbound: ``agent_token`` is exempt.
#:
#: The replay must prove what the spend proved: the same client, redirect
#: URI, PKCE challenge and resource. And it acts only inside a short window
#: after the code expired (``agent_code_replay_window_s``), so a code that
#: leaked long ago cannot end a live connection (fix round 1).
_PEEK_SPENT_CODE_SQL = """
SELECT connection_id, organization_id
  FROM agent_token
 WHERE token_hash = CAST(:hash AS TEXT)
   AND kind = 'code'
   AND used_at IS NOT NULL
   AND client_id = CAST(:client_id AS TEXT)
   AND redirect_uri = CAST(:redirect_uri AS TEXT)
   AND code_challenge = CAST(:challenge AS TEXT)
   AND resource = CAST(:resource AS TEXT)
   AND expires_at > now() - make_interval(
           secs => CAST(:window_s AS DOUBLE PRECISION))
"""

#: One cached client, in any state. ``agent_client`` is global and exempt.
_CLIENT_SQL = """
SELECT client_id, registered_via, metadata, expires_at,
       (expires_at IS NULL OR expires_at > now()) AS live
  FROM agent_client
 WHERE client_id = CAST(:client_id AS TEXT)
"""

#: Cache a Client ID Metadata Document. A row that a dynamic registration
#: wrote is never replaced: the WHERE of the update refuses it, and the caller
#: then gets no row back.
_CACHE_CIMD_SQL = """
INSERT INTO agent_client (client_id, registered_via, metadata, fetched_at,
                          expires_at)
VALUES (CAST(:client_id AS TEXT), 'cimd', CAST(:metadata AS JSONB), now(),
        now() + make_interval(secs => CAST(:ttl_s AS DOUBLE PRECISION)))
ON CONFLICT (client_id) DO UPDATE
   SET metadata   = EXCLUDED.metadata,
       fetched_at = EXCLUDED.fetched_at,
       expires_at = EXCLUDED.expires_at
 WHERE agent_client.registered_via = 'cimd'
RETURNING client_id
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


# ── The list and the scoped revoke of the Integrations app (EA-4b) ──────────


@dataclass(frozen=True)
class ConnectionRow:
    """One live connection, as the AI agents view shows it (EA-4b, spec §5.4).

    It holds no token, no hash and no client id. ``last_used_at`` is the last
    token issue or refresh (rule 6), not the last call, so the view labels it
    "Last token refresh".
    """

    id: str
    member_email: str
    client_name: str
    client_origin: str
    scopes: tuple[str, ...]
    pinned_projects: tuple[str, ...]
    include_personal: bool
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime


#: The live connections of one organization, or of one member in it. Runs
#: bound to the tenant. The organization is in the WHERE as well as in the
#: GUC, for the reason of :data:`_CONNECTION_LIVE_SQL`. The partial index
#: ``idx_agent_connection_member_live`` of migration 248 serves both reads.
_LIST_CONNECTIONS_SQL = """
SELECT CAST(id AS TEXT) AS id, member_email, client_name, client_origin,
       scopes, CAST(pinned_projects AS TEXT[]) AS pinned_projects,
       include_personal, created_at, last_used_at, expires_at
  FROM agent_connection
 WHERE organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
   AND expires_at > now()
   AND (CAST(:member AS TEXT) IS NULL OR member_email = CAST(:member AS TEXT))
 ORDER BY created_at DESC, id
"""

#: Revoke ONE connection, only when the caller may: the organization always,
#: and the member too for a member's own revoke. ``RETURNING`` tells the
#: caller whether a row matched, so an id of another member or of another
#: organization changes nothing and the route answers 404.
#:
#: ⚠️ The lock order of rule 3 holds. This ``UPDATE`` takes the row lock of
#: the connection (``FOR NO KEY UPDATE``, because it changes no key), as
#: :data:`_REVOKE_CONNECTION_SQL` does. The tokens come after it.
_REVOKE_CONNECTION_SCOPED_SQL = """
UPDATE agent_connection
   SET revoked_at = now()
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
   AND (CAST(:member AS TEXT) IS NULL OR member_email = CAST(:member AS TEXT))
RETURNING id
"""


def _member_filter(member_email: str | None) -> str | None:
    """The member of a member's read or revoke, normalised as the row is.

    ``None`` means no member filter, which is the admin's act. An empty
    string is a caller bug: it would match no row, so it raises.
    """
    if member_email is None:
        return None
    member = member_email.strip().lower()
    if not member:
        raise ValueError("a member filter needs the member's email")
    return member


async def list_connections(
    organization_id: str, *, member_email: str | None = None
) -> list[ConnectionRow]:
    """The live connections of *organization_id*, newest first (EA-4b).

    With *member_email*, only that member's rows. Without it, every row of the
    organization, which is the admin's list. The caller passes the bound
    tenant and the session's member, never request input (R11). A revoked
    connection and one past ``expires_at`` are not live, so they do not show.
    """
    from sqlalchemy import text

    member = _member_filter(member_email)
    org = _canonical_uuid(str(organization_id))
    if org is None:
        return []
    async with _tenant_session(org) as db:
        rows = (
            await db.execute(
                text(_LIST_CONNECTIONS_SQL), {"org": org, "member": member}
            )
        ).all()
    return [
        ConnectionRow(
            id=row.id,
            member_email=row.member_email,
            client_name=row.client_name or "",
            client_origin=row.client_origin or "",
            scopes=tuple(row.scopes or ()),
            pinned_projects=tuple(str(p) for p in row.pinned_projects or ()),
            include_personal=bool(row.include_personal),
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
        )
        for row in rows
    ]


async def revoke_connection_scoped(
    organization_id: str,
    connection_id: str,
    *,
    member_email: str | None = None,
) -> bool:
    """Revoke *connection_id* when the caller may, and say whether it did (EA-4b).

    The row must be of *organization_id*, and of *member_email* when the
    caller names one. Then the store revokes the connection and every token
    of it, in one transaction, in the lock order of rule 3. ``False`` means
    that no row matched: an unknown id, a revoked one, or one of another
    member or organization. Nothing changes then, and the tokens stay as
    they are.

    :func:`revoke_connection` stays as it is. It serves the edge and the
    approve path, which know the connection from a token row.
    """
    from sqlalchemy import text

    member = _member_filter(member_email)
    org = _canonical_uuid(str(organization_id))
    connection = _canonical_uuid(str(connection_id).lower())
    if org is None or connection is None:
        return False
    params = {"connection_id": connection, "org": org}
    async with _tenant_session(org) as db:
        row = (
            await db.execute(
                text(_REVOKE_CONNECTION_SCOPED_SQL), {**params, "member": member}
            )
        ).one_or_none()
        if row is None:
            return False
        await db.execute(text(_REVOKE_TOKENS_SQL), params)
    _log.warning(
        "agent_tokens.connection_revoked",
        connection_id=connection,
        reason="admin" if member is None else "member",
    )
    return True


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


def _code_params(
    raw_code: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
) -> dict[str, Any] | None:
    """The bound parameters of a code act, or None when one is empty."""
    values = (raw_code, client_id, redirect_uri, code_challenge, resource)
    if not all((v or "").strip() for v in values):
        return None
    return {
        "hash": hash_token(raw_code),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "challenge": code_challenge,
        "resource": resource,
    }


async def consume_code(
    raw_code: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
) -> CodeGrant | None:
    """Spend a code that this presentation proves, and return what it carries.

    *code_challenge* is the S256 of the verifier that the token request sent.
    The code is spent only when the client, the redirect URI, that challenge
    and the resource all equal the stored values (:data:`_SPEND_CODE_SQL`).

    ``None`` covers an unknown, a spent, an expired and a revoked code, and a
    presentation that does not match. A mismatch spends nothing, so the real
    client can still use the code. A code works once: a second call with the
    same code returns ``None``. Runs UNBOUND, because the token endpoint knows
    no tenant yet.
    """
    from sqlalchemy import text

    params = _code_params(raw_code, client_id, redirect_uri, code_challenge, resource)
    if params is None:
        return None
    session = await _session()
    try:
        await session.begin()
        row = (
            await session.execute(text(_SPEND_CODE_SQL), params)
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


async def revoke_replayed_code(
    raw_code: str,
    *,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
) -> str | None:
    """Revoke the connection of a code that was ALREADY spent (EA-2a).

    OAuth 2.1 §4.1.2: a server that sees an authorization code twice SHOULD
    revoke the tokens issued from it. Each approval makes one connection and
    mints one code for it, so "the tokens of this code" are the tokens of its
    connection. The token endpoint calls this function after
    :func:`consume_code` returned ``None``.

    The replay must prove the same four values that the spend proved: the
    client, the redirect URI, the S256 of the verifier and the resource. So
    only a holder of the code AND its verifier can end the connection. It
    acts only while the code is less than ``agent_code_replay_window_s``
    seconds past its expiry (default 600), so a code that leaked long ago
    cannot end a live connection.

    Returns the id of the connection it revoked, else ``None``. Nothing
    changes for an unknown code, an unspent code, a presentation that does
    not match, or a spent code past the window.

    The organization comes from the code row, never from the caller.
    """
    from sqlalchemy import text

    params = _code_params(raw_code, client_id, redirect_uri, code_challenge, resource)
    if params is None:
        return None
    params["window_s"] = float(get_settings().agent_code_replay_window_s)

    async def _work(session: Any) -> str | None:
        peek = (
            await session.execute(text(_PEEK_SPENT_CODE_SQL), params)
        ).one_or_none()
        if peek is None:
            return None
        organization_id = str(peek.organization_id)
        connection_id = str(peek.connection_id)
        await _bind(session, organization_id)
        await _revoke_in(session, organization_id, connection_id)
        return connection_id

    connection_id = await _run(_work)
    if connection_id is not None:
        # Ids only. A code or its hash never goes into a log line.
        _log.warning(
            "agent_tokens.connection_revoked",
            connection_id=connection_id,
            reason="code_replayed",
        )
    return connection_id


# ── Client metadata (``agent_client``, EA-2a) ───────────────────────────────


@dataclass(frozen=True)
class ClientRecord:
    """One row of ``agent_client``. ``live`` is false past ``expires_at``."""

    client_id: str
    registered_via: str
    metadata: dict[str, Any]
    live: bool


async def get_client(client_id: str) -> ClientRecord | None:
    """The cached client *client_id*, in any state, or ``None``.

    ``agent_client`` holds no tenant data, so the read runs unbound.
    """
    from sqlalchemy import text

    if not (client_id or "").strip():
        return None

    async def _work(session: Any) -> Any:
        return (
            await session.execute(text(_CLIENT_SQL), {"client_id": client_id})
        ).one_or_none()

    row = await _run(_work)
    if row is None:
        return None
    metadata = row.metadata if isinstance(row.metadata, dict) else {}
    return ClientRecord(
        client_id=row.client_id,
        registered_via=row.registered_via,
        metadata=dict(metadata),
        live=bool(row.live),
    )


async def cache_cimd_client(client_id: str, metadata: dict[str, Any]) -> bool:
    """Cache a verified Client ID Metadata Document for ``agent_cimd_cache_ttl_s``.

    The caller has already fetched the document and checked it. Returns False
    when a dynamic registration holds the same id, which this call never
    replaces.
    """
    import json

    from acb_common.pg_text import storable
    from sqlalchemy import text

    if not (client_id or "").strip():
        raise ValueError("a client needs an id")

    async def _work(session: Any) -> Any:
        return (
            await session.execute(
                text(_CACHE_CIMD_SQL),
                {
                    "client_id": client_id,
                    "metadata": json.dumps(storable(metadata)),
                    "ttl_s": float(get_settings().agent_cimd_cache_ttl_s),
                },
            )
        ).one_or_none()

    return await _run(_work) is not None


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
    # Rule 8: the organization comes from the code row.
    if not agent_oauth.org_allowed(grant.organization_id):
        return Refused(ORG_NOT_ALLOWED, grant.connection_id, grant.organization_id)
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


def _same_presentation(state: Any, client_id: str, resource: str | None) -> bool:
    """True when the refresh token's own client presents it for its resource.

    The same rule as the spend: an omitted resource matches any. A token that
    another client presents is not a reuse, so it revokes nothing.
    """
    return state.client_id == client_id and (
        resource is None or state.resource == resource
    )


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

    # Rule 8 (fix round 1). An organization off the list gets no token, and
    # the refusal spends nothing. ⚠️ The reuse check comes FIRST: a replay
    # of a spent refresh token still revokes the connection, whatever the
    # list says. Else the theft signal is lost, and a thief's rotated chain
    # lives again when the organization comes back on the list.
    if not agent_oauth.org_allowed(organization_id):
        state = (
            await session.execute(text(_REFRESH_STATE_SQL), {"hash": hashed})
        ).one_or_none()
        if (
            state is not None
            and state.used_at is not None
            and _same_presentation(state, client_id, resource)
        ):
            await _revoke_in(session, organization_id, connection_id)
            return Refused(REUSED, connection_id, organization_id)
        return Refused(ORG_NOT_ALLOWED, connection_id, organization_id)

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
        if not _same_presentation(state, client_id, resource):
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
                {
                    "connection_id": str(row.connection_id),
                    "org": str(row.organization_id),
                },
            )
        ).one_or_none()
        return (row, live) if live is not None else None

    found = await _run(_work)
    if found is None:
        return None
    row, live = found
    return TokenLookup(
        connection_id=str(row.connection_id),
        organization_id=str(row.organization_id),
        kind=row.kind,
        client_id=row.client_id,
        resource=row.resource,
        scope=row.scope,
        expires_at=row.expires_at,
        scopes=tuple(live.scopes or ()),
        pinned_projects=tuple(str(p) for p in live.pinned_projects or ()),
        include_personal=bool(live.include_personal),
        client_origin=live.client_origin or "",
        connection_client_id=live.client_id or "",
    )


def _canonical_uuid(value: str | None) -> str | None:
    """*value* when it is the canonical lower-case form of a UUID, else None."""
    import uuid

    try:
        parsed = str(uuid.UUID(value or ""))
    except ValueError:
        return None
    return parsed if parsed == value else None


async def read_live_connection(
    organization_id: str, connection_id: str
) -> LiveConnection | None:
    """The live connection *connection_id* of *organization_id*, or ``None``.

    Branch 0 of ``deps.get_current_user`` calls it (EA-3a, spec §10.1 step 4)
    with the organization and the connection of a request whose HMAC it has
    already checked. It opens the one unbound :func:`_session` and binds
    *organization_id* with :data:`_BIND_SQL` inside the transaction, so the
    tenant-scoped read runs under RLS. A connection of another organization,
    a revoked one and one past ``expires_at`` all give ``None``.

    ``member_email`` of the result is the ONE source of the member of a
    signed request, and ``organization_id`` is the one the ROW holds. Each
    call reads the row again and keeps no cache, so a revoke takes effect on
    the next call.
    """
    from sqlalchemy import text

    org = _canonical_uuid(organization_id)
    connection = _canonical_uuid(connection_id)
    if org is None or connection is None:
        return None

    async def _work(session: Any) -> Any:
        await _bind(session, org)
        return (
            await session.execute(
                text(_CONNECTION_LIVE_SQL),
                {"connection_id": connection, "org": org},
            )
        ).one_or_none()

    row = await _run(_work)
    if row is None:
        return None
    # Rule 8: the ROW's organization must be on the list. Branch 0 then
    # refuses the request as it refuses a dead connection.
    if not agent_oauth.org_allowed(row.organization_id):
        return None
    return LiveConnection(
        connection_id=connection,
        # The ROW's organization, never the argument: branch 0 compares them.
        organization_id=str(row.organization_id),
        member_email=(row.member_email or "").strip().lower(),
        client_origin=row.client_origin or "",
        scopes=tuple(row.scopes or ()),
        pinned_projects=tuple(str(p) for p in row.pinned_projects or ()),
        include_personal=bool(row.include_personal),
        client_id=row.client_id or "",
    )
