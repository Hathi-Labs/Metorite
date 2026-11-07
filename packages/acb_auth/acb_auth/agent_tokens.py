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
:func:`lookup_token` now returns the scopes, the pins, the personal switch and
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

9. ⚠️ **First-party clients (D-EA-11, spec §10.7).** For a client on
   ``agent_first_party_client_ids``, :func:`create_connection` with
   ``replace_same_client=True`` revokes the member's other connections of
   that client, and their tokens, in the transaction of the insert.
   :func:`live_connections_of_client` gives the live ones, so the consent
   route can mint a code on a connection that already covers the request.
   Both run in the bound tenant and take the member from the caller, who
   passes the session's member. No other caller passes ``True``.

10. ⚠️ **Change access (EA-17c-1, D-EA-13, spec §10.8).**
    :func:`update_connection_grant` changes the scopes, the pins, the
    left-out list and the personal switch of ONE live connection, in one
    transaction. Its WHERE names the id, the organization AND the member,
    and the caller passes the member of the session. It locks the connection
    row first, as rule 3 says. A resource scope must be in the ceiling, the
    scopes that every live token of the connection covers
    (:func:`ceiling_of`), because the edge intersects the token scope with
    the connection scopes. The ceiling binds a turn-on only: a scope that the
    row holds, or that a held scope covers, needs no check. A mark that the
    member no longer sees stays, and a new one is refused. A grant that
    equals the row writes nothing. ``offline_access`` and
    ``requested_scopes`` never change here.

11. ⚠️ **Each revoke stores its reason (EA-19a, spec §10.9).** Every writer
    that revokes a connection takes a REQUIRED keyword ``reason``, one of
    :data:`REVOKE_REASONS`, and writes it to ``revoked_reason``. An unknown
    reason raises ``ValueError`` before any SQL runs. The column has no
    CHECK, so a wrong string can never roll back a reuse revoke. Fence:
    ``tests/unit/test_agent_revoke_reason.py`` (R8).

12. ⚠️ **Box tokens (EA-19b, D-EA-5, spec §10.9).** A box connection has
    ``credential`` ``box`` and ONE kind of token, ``access``, with no
    refresh row. :func:`create_box_connection` makes the connection and its
    token in one transaction, under the tenant the request bound.
    :func:`rotate_box_token` locks the connection FIRST (rule 3), and a
    connection holds at most two live tokens. **Use never moves the expiry.**
    :data:`_SLIDE_SQL` skips a box, and only a rotation sets ``expires_at``.
    :func:`touch_box_use` stamps ``last_used_at`` at most once a minute, and
    marks a new address at most once an hour. ``agent_oauth.box_tokens_enabled``
    is read in :func:`read_live_connection` and in the edge bearer check,
    never in :func:`lookup_token` (rule 8). Fence:
    ``tests/unit/test_agent_box_token_store.py`` (R8).

13. ⚠️ **The box routes (EA-19c, spec §10.9).** :func:`revoke_box_connections`
    revokes every box connection of the bound organization with the reason
    ``flag_off``, the connection rows first and then their tokens.
    :func:`update_connection_grant` never reads or writes a box, so Change
    access refuses one. The list reads ``credential``, ``last_seen_addr``
    and ``addr_changed_at``. :func:`box_address_event_id` gives one audit
    row id for each new-address mark, and :func:`audited_events` tells the
    gateway list route which marks have their row. This module writes no
    audit row. Fence: ``tests/unit/test_agent_box_token_routes.py`` (R8).

Fence: ``tests/unit/test_agent_connection_tokens.py`` (R8, a phase-4-promoted
catalog, as a NON-privileged role). D-EA-11:
``tests/unit/test_agent_first_party.py`` (R8). EA-17c-1:
``tests/unit/test_agent_change_access.py`` (R8).
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
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

#: EA-19b. The ``credential`` of a box connection (spec §10.9).
BOX = agent_oauth.BOX_CREDENTIAL

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


# ── Why a connection was revoked (EA-19a, spec §10.9 question 6) ────────────
#
# Each revoke of a connection stores one of these in
# ``agent_connection.revoked_reason`` (migration 348). The column has NO CHECK
# on purpose: a CHECK would let one wrong string roll back a revoke, and a
# reuse revoke must never fail. So :func:`_reason` refuses an unknown value in
# Python, before any SQL runs. Fence: ``tests/unit/test_agent_revoke_reason.py``.

#: The member revoked their own connection (``DELETE /agent-access/...``).
REVOKE_MEMBER = "member"
#: An admin revoked a connection (``DELETE /integrations/agent-connections``).
REVOKE_ADMIN = "admin"
#: A first-party approval replaced the older connection (D-EA-11).
REVOKE_REPLACED = "replaced"
#: A spent refresh token was presented again (rule 5, both reuse sites).
REVOKE_REFRESH_REUSE = "refresh_reuse"
#: A spent authorization code was presented again (:func:`revoke_replayed_code`).
REVOKE_CODE_REPLAY = "code_replay"
#: The client revoked its token at ``/oauth/revoke`` (RFC 7009).
REVOKE_OAUTH_REVOKE = "oauth_revoke"
#: ``mint_code`` failed after the approve made the connection.
REVOKE_APPROVE_ROLLBACK = "approve_rollback"
#: The bulk revoke of the box tokens of an organization
#: (:func:`revoke_box_connections`, EA-19c).
REVOKE_FLAG_OFF = "flag_off"

#: Every reason a revoke may store. A reason outside this set raises.
REVOKE_REASONS: frozenset[str] = frozenset({
    REVOKE_MEMBER,
    REVOKE_ADMIN,
    REVOKE_REPLACED,
    REVOKE_REFRESH_REUSE,
    REVOKE_CODE_REPLAY,
    REVOKE_OAUTH_REVOKE,
    REVOKE_APPROVE_ROLLBACK,
    REVOKE_FLAG_OFF,
})


def _reason(reason: str) -> str:
    """*reason* when it is in :data:`REVOKE_REASONS`, else ``ValueError``.

    Each revoke calls this before it opens a session, so a caller bug fails
    loud and never reaches the database.
    """
    if reason not in REVOKE_REASONS:
        raise ValueError(f"unknown revoke reason: {reason!r}")
    return reason


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
    #: The left-out list of the connection (D-EA-12, spec §10.8, EA-17a).
    left_out_projects: tuple[str, ...] = ()
    client_origin: str = ""
    #: The CONNECTION's ``client_id`` (EA-13a). The edge stamps
    #: ``agent_oauth.actor_via_for(client_origin, client_id=...)`` with it,
    #: as branch 0 does with :attr:`LiveConnection.client_id`. ``client_id``
    #: above is the token's own copy, and no stamp reads it.
    connection_client_id: str = ""
    #: The connection's ``credential``, ``oauth`` or ``box`` (EA-19b, spec
    #: §10.9). The edge refuses a box token while the flag is off, and the
    #: stamp of a box reads it.
    credential: str = "oauth"
    #: The connection's ``client_name``. For a box it is the box name, and
    #: the box stamp reads it (EA-19b).
    client_name: str = ""


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
    #: The left-out list (D-EA-12, spec §10.8, EA-17a). Branch 0 copies it
    #: into ``AgentScope``.
    left_out_projects: tuple[str, ...] = ()
    #: ``agent_connection.client_id`` (EA-13a). Branch 0 stamps
    #: ``agent_oauth.actor_via_for(client_origin, client_id=...)`` with it.
    client_id: str = ""
    #: ``oauth`` or ``box`` (EA-19b). Branch 0 passes it, with
    #: :attr:`client_name`, to ``agent_oauth.actor_via_for``.
    credential: str = "oauth"
    client_name: str = ""


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
        scopes, pinned_projects, include_personal, left_out_projects,
        requested_scopes, expires_at)
VALUES (CAST(:org AS UUID),
        CAST(:member AS TEXT),
        CAST(:client_id AS TEXT),
        CAST(:client_name AS TEXT),
        CAST(:client_origin AS TEXT),
        CAST(:scopes AS TEXT[]),
        CAST(:pins AS UUID[]),
        CAST(:personal AS BOOLEAN),
        CAST(:left_out AS UUID[]),
        CAST(:requested AS TEXT[]),
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
#: ⚠️ EA-19b: never a box connection. A box has a fixed expiry, and only a
#: rotation moves it (spec §10.9 question 4).
_SLIDE_SQL = """
UPDATE agent_connection
   SET last_used_at = now(),
       expires_at = now() + make_interval(secs => CAST(:idle_s AS DOUBLE PRECISION))
 WHERE id = CAST(:connection_id AS UUID)
   AND credential <> 'box'
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
#: ``pinned_projects`` and ``left_out_projects`` come back as ``text[]``, so
#: an id is a plain string and never an asyncpg ``UUID`` object.
#: ``organization_id`` comes back too: :func:`read_live_connection` reports
#: the ROW's organization, never the one it was asked for, so branch 0 can
#: compare the two.
_CONNECTION_LIVE_SQL = """
SELECT CAST(organization_id AS TEXT) AS organization_id,
       member_email, client_origin, client_id, scopes,
       CAST(pinned_projects AS TEXT[]) AS pinned_projects, include_personal,
       CAST(left_out_projects AS TEXT[]) AS left_out_projects,
       credential, client_name
  FROM agent_connection
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
   AND expires_at > now()
"""

_REVOKE_CONNECTION_SQL = """
UPDATE agent_connection
   SET revoked_at = now(),
       revoked_reason = CAST(:reason AS TEXT)
 WHERE id = CAST(:connection_id AS UUID)
   AND revoked_at IS NULL
"""

#: D-EA-11 (spec §10.7): revoke every connection of one member and one
#: client in the bound organization, before a first-party approval inserts
#: the new one. A row past ``expires_at`` goes too, so the list keeps one
#: row. The organization is in the WHERE as well as in the bound GUC, for
#: the reason of :data:`_CONNECTION_LIVE_SQL`. ⚠️ The lock order of rule 3
#: holds: this takes the connection rows first, and
#: :data:`_REVOKE_TOKENS_OF_SQL` takes their tokens after it.
_REPLACE_SAME_CLIENT_SQL = """
UPDATE agent_connection
   SET revoked_at = now(),
       revoked_reason = CAST(:reason AS TEXT)
 WHERE organization_id = CAST(:org AS UUID)
   AND member_email = CAST(:member AS TEXT)
   AND client_id = CAST(:client_id AS TEXT)
   AND revoked_at IS NULL
RETURNING CAST(id AS TEXT) AS id
"""

#: The tokens of the connections that :data:`_REPLACE_SAME_CLIENT_SQL`
#: revoked, in one statement.
_REVOKE_TOKENS_OF_SQL = """
UPDATE agent_token
   SET revoked_at = now()
 WHERE connection_id = ANY(CAST(:ids AS UUID[]))
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
"""

#: D-EA-11: the live connections of one member and one client in the bound
#: organization, newest first. The first-party path of ``check`` reads it to
#: find a connection whose scopes cover the request. The partial index
#: ``idx_agent_connection_member_live`` of migration 248 serves it.
_LIVE_OF_CLIENT_SQL = """
SELECT CAST(id AS TEXT) AS id, scopes
  FROM agent_connection
 WHERE organization_id = CAST(:org AS UUID)
   AND member_email = CAST(:member AS TEXT)
   AND client_id = CAST(:client_id AS TEXT)
   AND revoked_at IS NULL
   AND expires_at > now()
 ORDER BY created_at DESC, id
 LIMIT 20
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
    left_out_projects: Sequence[str] = (),
    requested_scopes: Sequence[str] | None = None,
    replace_same_client: bool = False,
) -> str:
    """Create a connection in the BOUND tenant and return its id.

    The organization is the tenant the request already bound. It is never a
    parameter, so no caller can pass one from a request (R11). With nothing
    bound, ``tenant_session`` raises ``TenantUnbound``.

    ``left_out_projects`` is the left-out list of D-EA-12 (spec §10.8). It
    can only narrow the reach. ``requested_scopes`` is what the agent asked
    for before the member cleared a box, the ceiling of Change access
    (D-EA-13). ``None`` stores NULL, as a connection from before EA-17a has.

    ⚠️ ``replace_same_client`` (D-EA-11, spec §10.7). True only for a
    first-party client. In the SAME transaction as the insert, the store
    first revokes every connection of this member and this client in the
    bound organization, and every token of each. So the member keeps one
    connection of the client, and the old tokens die with the old rows. The
    lock order of rule 3 holds: the connection rows, then the tokens. False,
    the default, adds a connection beside the others, as before.
    """
    from acb_common.db import current_tenant
    from sqlalchemy import text

    member = (member_email or "").strip().lower()
    if not member:
        raise ValueError("a connection needs the member's email")
    if not (client_id or "").strip():
        raise ValueError("a connection needs a client id")

    replaced: list[str] = []
    async with _tenant_session() as db:
        if replace_same_client:
            org = current_tenant()
            replaced = [
                str(r.id) for r in (
                    await db.execute(
                        text(_REPLACE_SAME_CLIENT_SQL),
                        {
                            "org": org,
                            "member": member,
                            "client_id": client_id,
                            "reason": REVOKE_REPLACED,
                        },
                    )
                ).all()
            ]
            if replaced:
                await db.execute(
                    text(_REVOKE_TOKENS_OF_SQL), {"ids": replaced, "org": org})
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
                    "left_out": [str(p) for p in left_out_projects],
                    "requested": (
                        None if requested_scopes is None
                        else [str(s) for s in requested_scopes]
                    ),
                    "idle_s": float(get_settings().agent_connection_idle_ttl_s),
                },
            )
        ).one()
    _log.info("agent_tokens.connection_created", client_id=client_id)
    for old in replaced:
        # Ids only. The new id names the row that took its place.
        _log.warning(
            "agent_tokens.connection_revoked",
            connection_id=old,
            reason=REVOKE_REPLACED,
            replaced_by=str(row.id),
        )
    return str(row.id)


@dataclass(frozen=True)
class ClientConnection:
    """One live connection of one member and one client (D-EA-11).

    It holds the id and the scopes, and nothing that the consent page shows.
    """

    id: str
    scopes: tuple[str, ...]


async def live_connections_of_client(
    member_email: str, client_id: str
) -> list[ClientConnection]:
    """The live connections of *member_email* and *client_id*, newest first.

    D-EA-11 (spec §10.7). The first-party path of ``check`` reads it. It runs
    in the BOUND tenant, as :func:`create_connection` does, so the
    organization is never a parameter (R11), and with nothing bound
    ``tenant_session`` raises ``TenantUnbound``. The caller passes the member
    of the session. An empty member or client id raises, because it would
    match no row, and a caller bug must not read as "no connection".
    """
    from acb_common.db import current_tenant
    from sqlalchemy import text

    member = (member_email or "").strip().lower()
    if not member:
        raise ValueError("a connection read needs the member's email")
    if not (client_id or "").strip():
        raise ValueError("a connection read needs a client id")
    async with _tenant_session() as db:
        rows = (
            await db.execute(
                text(_LIVE_OF_CLIENT_SQL),
                {"org": current_tenant(), "member": member, "client_id": client_id},
            )
        ).all()
    return [ClientConnection(id=row.id, scopes=tuple(row.scopes or ())) for row in rows]


async def _revoke_in(
    session: Any, organization_id: str, connection_id: str, *, reason: str
) -> None:
    """Revoke a connection and every token of it, on a BOUND *session*.

    The connection ``UPDATE`` comes first, so the row lock order is the same as
    in an issue: the connection, then the tokens. *reason* is one of
    :data:`REVOKE_REASONS`, and the row stores it (EA-19a).
    """
    from sqlalchemy import text

    stored = _reason(reason)
    params = {"connection_id": connection_id, "org": organization_id}
    await session.execute(
        text(_REVOKE_CONNECTION_SQL), {**params, "reason": stored})
    await session.execute(text(_REVOKE_TOKENS_SQL), params)


async def revoke_connection(
    organization_id: str, connection_id: str, *, reason: str
) -> None:
    """Revoke a connection and every token of it, in one transaction.

    The session is bound to *organization_id*, so the UPDATE of
    ``agent_connection`` passes RLS only for a connection of that tenant. The
    caller passes an organization it got from a token row or from the bound
    request, never from request input.

    *reason* is one of :data:`REVOKE_REASONS` (EA-19a). An unknown one raises
    ``ValueError`` before the session opens.

    The connection ``UPDATE`` waits for any issue that holds the connection
    lock. So the token ``UPDATE`` that follows sees the issued rows.
    """
    _reason(reason)
    async with _tenant_session(organization_id) as db:
        await _revoke_in(db, organization_id, connection_id, reason=reason)
    _log.warning(
        "agent_tokens.connection_revoked",
        connection_id=connection_id,
        reason=reason,
    )


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
    #: The left-out list of D-EA-12 (EA-17b). A screen that shows the reach
    #: of an agent needs it, or it claims more reach than the agent has.
    left_out_projects: tuple[str, ...] = ()
    #: What the agent asked for at the connect (EA-17a). ``None`` on a
    #: connection from before EA-17a. Change access never writes it.
    requested_scopes: tuple[str, ...] | None = None
    #: The resource scopes that a change may turn on (D-EA-13 rule 2,
    #: EA-17c-1): :func:`ceiling_of` the live tokens of the connection.
    #: Empty when no token is live.
    ceiling_scopes: tuple[str, ...] = ()
    #: EA-19c (spec §10.9): ``oauth`` or ``box``. The view shows a Box badge.
    credential: str = "oauth"
    #: EA-19c: the last client address of a box (:func:`touch_box_use`), and
    #: the time of its last new-address mark. ``None`` for an OAuth row.
    last_seen_addr: str | None = None
    addr_changed_at: datetime | None = None


#: The ``scope`` of each live token of the connection ``c``, as an array.
#: A code, an access token and a refresh token all count, because each one
#: is or becomes a token at its scope. The organization is in the WHERE, and
#: ``idx_agent_token_connection`` of migration 248 serves the read.
_LIVE_TOKEN_SCOPES_EXPR = """ARRAY(
           SELECT t.scope FROM agent_token t
            WHERE t.connection_id = c.id
              AND t.organization_id = c.organization_id
              AND t.used_at IS NULL
              AND t.revoked_at IS NULL
              AND t.expires_at > now())"""

#: The live connections of one organization, or of one member in it. Runs
#: bound to the tenant. The organization is in the WHERE as well as in the
#: GUC, for the reason of :data:`_CONNECTION_LIVE_SQL`. The partial index
#: ``idx_agent_connection_member_live`` of migration 248 serves both reads.
_LIST_CONNECTIONS_SQL = f"""
SELECT CAST(c.id AS TEXT) AS id, c.member_email, c.client_name,
       c.client_origin, c.scopes,
       CAST(c.pinned_projects AS TEXT[]) AS pinned_projects,
       c.include_personal, c.created_at, c.last_used_at, c.expires_at,
       CAST(c.left_out_projects AS TEXT[]) AS left_out_projects,
       c.requested_scopes,
       {_LIVE_TOKEN_SCOPES_EXPR} AS token_scopes,
       c.credential, c.last_seen_addr, c.addr_changed_at
  FROM agent_connection c
 WHERE c.organization_id = CAST(:org AS UUID)
   AND c.revoked_at IS NULL
   AND c.expires_at > now()
   AND (CAST(:member AS TEXT) IS NULL OR c.member_email = CAST(:member AS TEXT))
 ORDER BY c.created_at DESC, c.id
"""


def ceiling_of(token_scopes: Sequence[str | None]) -> tuple[str, ...]:
    """The resource scopes that every live token in *token_scopes* covers.

    D-EA-13 rule 2 (EA-17c-1). The edge takes the token scope intersected
    with the connection scopes, so a connection scope above the token scope
    is never live. Each token gives ``agent_oauth.covered_scopes`` of its
    scope, the cover rule of the edge, and two tokens that differ give their
    intersection. No token gives no scope. A token with no scope, or with a
    malformed one, gives no scope, as ``bearer.effective_scopes`` reads it.
    ``offline_access`` is never in the answer. The order is canonical.
    """
    ceiling: set[str] | None = None
    for raw in token_scopes:
        try:
            covered = agent_oauth.covered_scopes(agent_oauth.parse_scope(raw))
        except ValueError:
            covered = set()
        ceiling = covered if ceiling is None else ceiling & covered
    held = ceiling or set()
    return tuple(s for s in agent_oauth.SUPPORTED_SCOPES if s in held)


def _connection_row(row: Any) -> ConnectionRow:
    """The :class:`ConnectionRow` of a row of :data:`_LIST_CONNECTIONS_SQL`
    or :data:`_CHANGE_GRANT_SQL`. Both name the same columns."""
    return ConnectionRow(
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
        left_out_projects=tuple(str(p) for p in row.left_out_projects or ()),
        requested_scopes=(
            None if row.requested_scopes is None
            else tuple(row.requested_scopes)
        ),
        ceiling_scopes=ceiling_of(tuple(row.token_scopes or ())),
        credential=row.credential or "oauth",
        last_seen_addr=row.last_seen_addr,
        addr_changed_at=row.addr_changed_at,
    )

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
   SET revoked_at = now(),
       revoked_reason = CAST(:reason AS TEXT)
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
    return [_connection_row(row) for row in rows]


async def revoke_connection_scoped(
    organization_id: str,
    connection_id: str,
    *,
    reason: str,
    member_email: str | None = None,
) -> bool:
    """Revoke *connection_id* when the caller may, and say whether it did (EA-4b).

    *reason* is one of :data:`REVOKE_REASONS` (EA-19a): ``member`` from the
    member's route, ``admin`` from the admin's route. An unknown one raises
    ``ValueError`` before any SQL runs.

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

    _reason(reason)
    member = _member_filter(member_email)
    org = _canonical_uuid(str(organization_id))
    connection = _canonical_uuid(str(connection_id).lower())
    if org is None or connection is None:
        return False
    params = {"connection_id": connection, "org": org}
    async with _tenant_session(org) as db:
        row = (
            await db.execute(
                text(_REVOKE_CONNECTION_SCOPED_SQL),
                {**params, "member": member, "reason": reason},
            )
        ).one_or_none()
        if row is None:
            return False
        await db.execute(text(_REVOKE_TOKENS_SQL), params)
    _log.warning(
        "agent_tokens.connection_revoked",
        connection_id=connection,
        reason=reason,
    )
    return True


# ── Change access: the member changes the grant (EA-17c-1, D-EA-13) ─────────

#: Why :func:`update_connection_grant` changed nothing. The route maps
#: :data:`CHANGE_NOT_FOUND` to 404 and each other reason to 422.
#:
#: * :data:`CHANGE_NOT_FOUND`: no live connection of this member and this
#:   organization has the id. That covers an unknown id, a revoked or
#:   expired one, and one of another member or organization alike.
#: * :data:`CHANGE_NO_SCOPE`: the new grant holds no resource scope (rule 4).
#: * :data:`CHANGE_OFFLINE_ACCESS`: the new grant adds or drops
#:   ``offline_access`` (rule 3).
#: * :data:`CHANGE_ABOVE_CEILING`: the new grant turns on a resource scope
#:   that is outside :attr:`ConnectionRow.ceiling_scopes` (rule 2).
#: * :data:`CHANGE_PROJECT_NOT_SEEN`: the new grant adds a project that the
#:   member of the session does not see to one of its two lists.
CHANGE_NOT_FOUND = "not_found"
CHANGE_NO_SCOPE = "no_scope"
CHANGE_OFFLINE_ACCESS = "offline_access_fixed"
CHANGE_ABOVE_CEILING = "scope_above_ceiling"
CHANGE_PROJECT_NOT_SEEN = "project_not_seen"


@dataclass(frozen=True)
class ChangeRefused:
    """A change of a grant that did not happen, and why. Nothing was written.

    ``scopes`` holds the scopes above the ceiling for
    :data:`CHANGE_ABOVE_CEILING`, in the order of the request. ``projects``
    holds the new ids that the member does not see for
    :data:`CHANGE_PROJECT_NOT_SEEN`. Each is empty for each other reason.
    """

    reason: str
    scopes: tuple[str, ...] = ()
    projects: tuple[str, ...] = ()


@dataclass(frozen=True)
class GrantChange:
    """A change that the store accepted: the row before it and after it.

    ``before`` and ``after`` are equal when the new grant equals the row.
    Then the store wrote nothing, and :attr:`changed` is false.
    """

    before: ConnectionRow
    after: ConnectionRow

    @property
    def changed(self) -> bool:
        return self.after is not self.before


#: The columns of :data:`_LIST_CONNECTIONS_SQL`, for a row ``c``. The lock
#: read and the UPDATE give the row in the same shape, so
#: :func:`_connection_row` reads all three.
_CONNECTION_COLUMNS = f"""CAST(c.id AS TEXT) AS id, c.member_email, c.client_name,
       c.client_origin, c.scopes,
       CAST(c.pinned_projects AS TEXT[]) AS pinned_projects,
       c.include_personal, c.created_at, c.last_used_at, c.expires_at,
       CAST(c.left_out_projects AS TEXT[]) AS left_out_projects,
       c.requested_scopes,
       {_LIVE_TOKEN_SCOPES_EXPR} AS token_scopes,
       c.credential, c.last_seen_addr, c.addr_changed_at"""

#: Lock the ONE live connection of this member in this organization, and
#: read the row with the scope of each live token.
#: ⚠️ EA-19c: never a box. Change access does not apply to a box (spec
#: §10.9 question 2), so a box id reads as no row and the route answers 404.
#: ⚠️ The lock order of rule 3 holds: the connection row first
#: (``FOR NO KEY UPDATE``, as an issue takes it), and the tokens are only
#: read. While the lock holds, no issue and no rotation can add a token. A
#: code that ``mint_code`` adds meanwhile can only narrow the intersection.
_LOCK_MEMBER_CONNECTION_SQL = f"""
SELECT {_CONNECTION_COLUMNS}
  FROM agent_connection c
 WHERE c.id = CAST(:connection_id AS UUID)
   AND c.organization_id = CAST(:org AS UUID)
   AND c.member_email = CAST(:member AS TEXT)
   AND c.credential <> 'box'
   AND c.revoked_at IS NULL
   AND c.expires_at > now()
   FOR NO KEY UPDATE OF c
"""

#: Write the new grant. ⚠️ The WHERE names the id, the organization AND the
#: member, as :data:`_REVOKE_CONNECTION_SCOPED_SQL` does, with no NULL arm
#: for the member: only the member of the connection changes it (D-EA-13
#: rule 5). ``requested_scopes``, ``client_id`` and the idle window stay as
#: they are. ``RETURNING`` gives the row in the shape of the list.
_CHANGE_GRANT_SQL = f"""
UPDATE agent_connection c
   SET scopes = CAST(:scopes AS TEXT[]),
       pinned_projects = CAST(:pins AS UUID[]),
       left_out_projects = CAST(:left_out AS UUID[]),
       include_personal = CAST(:personal AS BOOLEAN)
 WHERE c.id = CAST(:connection_id AS UUID)
   AND c.organization_id = CAST(:org AS UUID)
   AND c.member_email = CAST(:member AS TEXT)
   AND c.credential <> 'box'
   AND c.revoked_at IS NULL
   AND c.expires_at > now()
RETURNING {_CONNECTION_COLUMNS}
"""


def _scope_refusal(
    asked: Sequence[str], held: Sequence[str], ceiling: Sequence[str]
) -> ChangeRefused | None:
    """The scope rules of D-EA-13 for a change from *held* to *asked*.

    A KNOWN resource scope is one of ``agent_oauth.RESOURCE_SCOPES``:
    ``SUPPORTED_SCOPES`` less ``offline_access``. Rules 4 and 2 count known
    resource scopes only.

    - Rule 4: *asked* holds one known resource scope at least. A body of
      only unknown scopes gets :data:`CHANGE_NO_SCOPE`, so the store never
      writes a grant with no real scope.
    - Rule 3: ``offline_access`` is in *asked* exactly when it is in *held*.
    - Rule 2: the ceiling binds a turn-on only. A known scope that a known
      held scope holds or covers (``agent_oauth.scope_holds``) adds no
      access at the edge, so it needs no check. Each other known scope must
      be in *ceiling*.
    - An unknown scope that *held* holds (a scope that left the server after
      the connect) drops out, because the store keeps known scopes only. An
      unknown scope that *held* does not hold is a turn-on that the server
      cannot grant, so it is above the ceiling.
    """
    offline = agent_oauth.OFFLINE_ACCESS
    known = set(agent_oauth.RESOURCE_SCOPES)
    asked_once = list(dict.fromkeys(asked))
    if not any(s in known for s in asked_once):
        return ChangeRefused(CHANGE_NO_SCOPE)
    if (offline in asked_once) != (offline in held):
        return ChangeRefused(CHANGE_OFFLINE_ACCESS)
    held_known = [h for h in held if h in known]
    allowed = set(ceiling)
    above = tuple(
        s for s in asked_once
        if s != offline and (
            (s in known and not agent_oauth.scope_holds(held_known, s)
             and s not in allowed)
            or (s not in known and s not in held)
        )
    )
    if above:
        return ChangeRefused(CHANGE_ABOVE_CEILING, above)
    return None


def _same_grant(
    row: ConnectionRow,
    scopes: Sequence[str],
    pins: Sequence[str],
    left_out: Sequence[str],
    personal: bool,
) -> bool:
    """True when the new grant reaches what *row* reaches now. The order of
    a list and a copy of an id change no reach."""
    return (
        set(scopes) == set(row.scopes)
        and set(pins) == set(row.pinned_projects)
        and set(left_out) == set(row.left_out_projects)
        and bool(personal) == row.include_personal
    )


async def update_connection_grant(
    organization_id: str,
    connection_id: str,
    *,
    member_email: str,
    scopes: Sequence[str],
    pinned_projects: Sequence[str],
    left_out_projects: Sequence[str],
    include_personal: bool,
    unseen_pins: Sequence[str] = (),
    unseen_left_out: Sequence[str] = (),
) -> GrantChange | ChangeRefused:
    """Change the grant of one connection of *member_email* (EA-17c-1).

    D-EA-13, spec §10.8. The caller passes the bound tenant and the member
    of the session, never request input (R11). In ONE transaction the store
    locks the live connection of this member in this organization, reads
    the scopes of its live tokens, and checks the rules in this order:

    1. The scope rules (:func:`_scope_refusal`). Rule 4 counts the known
       resource scopes only, so a grant never loses its last real scope.
       The ceiling is :func:`ceiling_of` the live tokens, read under the
       lock. It binds a turn-on only, so a narrowing passes with no live
       token too. The store writes the known scopes only, in the canonical
       order (``agent_oauth.scope_string``), and never an empty string.
    2. The marks. *unseen_pins* and *unseen_left_out* are the ids of the
       body that the member of the session does not see, as the caller
       found them. The store refuses such an id only when the locked row
       does not hold it in the same list. So a mark that the member no
       longer sees (a deleted, sealed or lost project) never blocks a save,
       and no change adds an unseen id.

    A new grant that equals the row writes nothing (:func:`_same_grant`).
    Else the store writes the scopes, the pins, the left-out list and the
    personal switch. It answers :class:`GrantChange`, or
    :class:`ChangeRefused` with nothing written.

    The edge and branch 0 read the row at each call, with no cache, so the
    next agent call sees the change. The tokens stay.

    An empty member raises, because it would match no row.
    """
    from sqlalchemy import text

    member = _member_filter(member_email)
    if member is None:
        raise ValueError("a change of a grant needs the member's email")
    org = _canonical_uuid(str(organization_id))
    connection = _canonical_uuid(str(connection_id).lower())
    if org is None or connection is None:
        return ChangeRefused(CHANGE_NOT_FOUND)
    where = {"connection_id": connection, "org": org, "member": member}
    new_scopes = [s for s in agent_oauth.scope_string(scopes).split(" ") if s]
    pins = [str(p) for p in pinned_projects]
    left_out = [str(p) for p in left_out_projects]
    async with _tenant_session(org) as db:
        locked = (
            await db.execute(text(_LOCK_MEMBER_CONNECTION_SQL), where)
        ).one_or_none()
        if locked is None:
            return ChangeRefused(CHANGE_NOT_FOUND)
        before = _connection_row(locked)
        refused = _scope_refusal(list(scopes), before.scopes, before.ceiling_scopes)
        if refused is not None:
            return refused
        if not any(s in agent_oauth.RESOURCE_SCOPES for s in new_scopes):
            # Rule 4 passed, so only a bug reaches this line. Write nothing.
            return ChangeRefused(CHANGE_NO_SCOPE)
        new_unseen = tuple(dict.fromkeys([
            *(p for p in unseen_pins if p not in before.pinned_projects),
            *(p for p in unseen_left_out if p not in before.left_out_projects),
        ]))
        if new_unseen:
            return ChangeRefused(CHANGE_PROJECT_NOT_SEEN, projects=new_unseen)
        if _same_grant(before, new_scopes, pins, left_out, include_personal):
            return GrantChange(before=before, after=before)
        row = (
            await db.execute(
                text(_CHANGE_GRANT_SQL),
                {
                    **where,
                    "scopes": new_scopes,
                    "pins": pins,
                    "left_out": left_out,
                    "personal": bool(include_personal),
                },
            )
        ).one_or_none()
    if row is None:
        # The lock held the row, so only a bug reaches this line.
        return ChangeRefused(CHANGE_NOT_FOUND)
    return GrantChange(before=before, after=_connection_row(row))


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
    connection. ⚠️ A first-party client (D-EA-11) can hold more codes on one
    connection, one for each sign-in with no click, so a replay ends the
    whole connection, the tokens of every device included. That fails
    closed. The token endpoint calls this function after
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
        await _revoke_in(
            session, organization_id, connection_id, reason=REVOKE_CODE_REPLAY)
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
            await _revoke_in(
                session, organization_id, connection_id,
                reason=REVOKE_REFRESH_REUSE)
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
            await _revoke_in(
                session, organization_id, connection_id,
                reason=REVOKE_REFRESH_REUSE)
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
        left_out_projects=tuple(str(p) for p in live.left_out_projects or ()),
        client_origin=live.client_origin or "",
        connection_client_id=live.client_id or "",
        credential=live.credential or "oauth",
        client_name=live.client_name or "",
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
    # EA-19b: a box connection is live only while the box flag is on. It is
    # read here and in the edge bearer check, never in :func:`lookup_token`.
    if row.credential == BOX and not agent_oauth.box_tokens_enabled():
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
        left_out_projects=tuple(str(p) for p in row.left_out_projects or ()),
        client_id=row.client_id or "",
        credential=row.credential or "oauth",
        client_name=row.client_name or "",
    )


# ── Box tokens (EA-19b, D-EA-5, spec §10.9) ─────────────────────────────────
#
# A box token is the ONE access token of a connection of credential ``box``.
# It has no refresh token, so nothing rotates in a race, and a stale session
# cannot sign out the others (rule 5 stays as it is). It acts as the member
# who made it. See rule 12 of the module docstring.

#: The start of each box token, so a scanner and a log filter find it.
BOX_TOKEN_PREFIX = "mbt_"
#: The lifetime of a box token in days: the default, and the most.
BOX_DAYS_DEFAULT = 90
BOX_DAYS_MAX = 365
#: The most live box connections of one member, and of one organization.
BOX_MEMBER_CAP = 20
BOX_ORG_CAP = 100
#: The longest box name.
BOX_NAME_MAX = 100
#: A plain rotation keeps the old token this long, so a box can swap it.
BOX_KEEP_OLD_S = 24 * 3600
#: The longest client address that :func:`touch_box_use` stores.
BOX_ADDR_MAX = 64
#: The audit action of a new address of a box token.
BOX_NEW_ADDRESS_ACTION = "box_token.new_address"

#: Why a box act changed nothing. :data:`ORG_NOT_ALLOWED` serves too.
#: ``box_tokens_off``: the flag is off. ``box_member_cap`` and
#: ``box_org_cap``: a cap is full. ``not_found``: no live box connection of
#: this member and this organization has the id.
BOX_OFF = "box_tokens_off"
BOX_MEMBER_FULL = "box_member_cap"
BOX_ORG_FULL = "box_org_cap"
BOX_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class BoxToken:
    """A new box token. The raw token lives only here, once.

    Its ``repr`` hides the token, as :class:`IssuedTokens` does, so a log
    line that prints the object does not leak it.
    """

    token: str = field(repr=False)
    connection_id: str
    organization_id: str
    scope: str
    expires_at: datetime


@dataclass(frozen=True)
class BoxRefused:
    """A box act that did not happen, and why. Nothing was written."""

    reason: str


def new_box_token() -> str:
    """``mbt_`` and 43 URL-safe characters: 256 random bits."""
    return BOX_TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


def _box_days(days: int) -> int:
    """*days* when it is a whole number from 1 to :data:`BOX_DAYS_MAX`."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValueError("days must be a whole number")
    if not 1 <= days <= BOX_DAYS_MAX:
        raise ValueError(f"days must be from 1 to {BOX_DAYS_MAX}")
    return days


def _box_name(name: str) -> str:
    """The box name, trimmed. Empty, too long or a control character raises."""
    clean = (name or "").strip()
    if not clean or len(clean) > BOX_NAME_MAX:
        raise ValueError(f"a box name has 1 to {BOX_NAME_MAX} characters")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in clean):
        raise ValueError("a box name holds a control character")
    return clean


def _box_scopes(scopes: Sequence[str]) -> tuple[str, ...]:
    """The scopes of a box token, by the consent rule (spec §10.9 q. 2).

    ``agent_oauth.parse_scope`` reads them, so an unknown scope drops out and
    a malformed one raises. ``offline_access`` drops out, because a box has
    no refresh token. No resource scope left raises.
    """
    parsed = agent_oauth.parse_scope(" ".join(str(s) for s in scopes))
    kept = tuple(s for s in parsed if s != agent_oauth.OFFLINE_ACCESS)
    if not kept:
        raise ValueError("a box token needs one scope or more")
    return kept


def _edge_origin() -> str:
    """The origin of ``agent_oauth.resource()``: the ``client_origin`` of a
    box, so branch 0 does not refuse it for an empty origin."""
    from urllib.parse import urlsplit

    parts = urlsplit(agent_oauth.resource())
    return f"{parts.scheme}://{parts.netloc}"


def _box_gate(organization_id: object) -> BoxRefused | None:
    """The flag, then the organization allowlist (rule 8)."""
    if not agent_oauth.box_tokens_enabled():
        return BoxRefused(BOX_OFF)
    if not agent_oauth.org_allowed(organization_id):
        return BoxRefused(ORG_NOT_ALLOWED)
    return None


#: ⚠️ One advisory lock for each key, held to the end of the transaction.
#: A mint takes the member key and then the organization key, BEFORE it
#: counts, so two mints at once cannot both pass a cap. The order is the
#: same on every mint, so two mints cannot deadlock.
_BOX_ADVISORY_LOCK_SQL = (
    "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS TEXT), 0))")

#: The live box connections of the organization, and of the member in it.
_BOX_COUNT_SQL = """
SELECT count(*) FILTER (WHERE member_email = CAST(:member AS TEXT)) AS mine,
       count(*) AS total
  FROM agent_connection
 WHERE organization_id = CAST(:org AS UUID)
   AND credential = 'box'
   AND revoked_at IS NULL
   AND expires_at > now()
"""

#: A box connection. ``expires_at`` is fixed: now plus the days of its
#: token, in the same transaction, so the two are equal.
_INSERT_BOX_CONNECTION_SQL = """
INSERT INTO agent_connection
       (organization_id, member_email, client_id, client_name, client_origin,
        scopes, pinned_projects, include_personal, left_out_projects,
        requested_scopes, credential, expires_at)
VALUES (CAST(:org AS UUID),
        CAST(:member AS TEXT),
        CAST(:client_id AS TEXT),
        CAST(:client_name AS TEXT),
        CAST(:client_origin AS TEXT),
        CAST(:scopes AS TEXT[]),
        CAST(:pins AS UUID[]),
        CAST(:personal AS BOOLEAN),
        CAST(:left_out AS UUID[]),
        NULL,
        'box',
        now() + make_interval(days => CAST(:days AS INTEGER)))
RETURNING CAST(id AS TEXT) AS id
"""

#: The one access token of a box. No refresh row exists.
_INSERT_BOX_TOKEN_SQL = """
INSERT INTO agent_token
       (organization_id, connection_id, kind, token_hash, expires_at,
        client_id, resource, scope)
VALUES (CAST(:org AS UUID), CAST(:connection_id AS UUID), 'access',
        CAST(:hash AS TEXT),
        now() + make_interval(days => CAST(:days AS INTEGER)),
        CAST(:client_id AS TEXT), CAST(:resource AS TEXT), CAST(:scope AS TEXT))
RETURNING expires_at
"""

#: ⚠️ Rule 3: lock the live box connection of this member FIRST, as an issue
#: does. A revoke that holds the row makes the rotation wait, and then the
#: rotation finds the row dead. The WHERE names the id, the organization,
#: the member and the credential.
_LOCK_BOX_SQL = """
SELECT scopes
  FROM agent_connection
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND member_email = CAST(:member AS TEXT)
   AND credential = 'box'
   AND revoked_at IS NULL
   AND expires_at > now()
   FOR NO KEY UPDATE
"""

#: A rotation first revokes every token of the connection except the newest
#: live one. So with the new token, the connection holds two live tokens at
#: most.
_BOX_KEEP_NEWEST_SQL = """
UPDATE agent_token
   SET revoked_at = now()
 WHERE connection_id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
   AND id IS DISTINCT FROM (
       SELECT t.id FROM agent_token t
        WHERE t.connection_id = CAST(:connection_id AS UUID)
          AND t.organization_id = CAST(:org AS UUID)
          AND t.kind = 'access'
          AND t.revoked_at IS NULL
          AND t.expires_at > now()
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 1)
"""

#: "Rotate now": the old token dies at once.
_BOX_OLD_NOW_SQL = """
UPDATE agent_token
   SET revoked_at = now()
 WHERE connection_id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
"""

#: "Rotate": the old token lives 24 hours more at most.
_BOX_OLD_KEEP_SQL = """
UPDATE agent_token
   SET expires_at = LEAST(
           expires_at,
           now() + make_interval(secs => CAST(:keep_s AS DOUBLE PRECISION)))
 WHERE connection_id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND revoked_at IS NULL
"""

#: A rotation is the ONE act that moves the expiry of a box connection. It
#: runs under the lock of :data:`_LOCK_BOX_SQL`.
_BOX_SET_EXPIRY_SQL = """
UPDATE agent_connection
   SET expires_at = now() + make_interval(days => CAST(:days AS INTEGER))
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND credential = 'box'
"""

#: ⚠️ The use stamp of a box (spec §10.9 question 5). At most once a minute:
#: the NULL branch stamps a new connection, which has no stamp yet. Only a
#: box: an OAuth connection keeps ``last_used_at`` as the last issue
#: (EA3-7). The address always moves to the new one. A change marks
#: ``addr_changed_at`` at most once an hour. A first address is no change.
#: ``expires_at`` is not in the SET: use never extends a box.
_TOUCH_BOX_SQL = """
UPDATE agent_connection
   SET last_used_at = now(),
       last_seen_addr = COALESCE(CAST(:addr AS TEXT), last_seen_addr),
       addr_changed_at = CASE
           WHEN CAST(:addr AS TEXT) IS NOT NULL
            AND last_seen_addr IS NOT NULL
            AND last_seen_addr <> CAST(:addr AS TEXT)
            AND (addr_changed_at IS NULL
                 OR addr_changed_at < now() - interval '1 hour')
           THEN now()
           ELSE addr_changed_at
       END
 WHERE id = CAST(:connection_id AS UUID)
   AND organization_id = CAST(:org AS UUID)
   AND credential = 'box'
   AND revoked_at IS NULL
   AND (last_used_at IS NULL OR last_used_at < now() - interval '60 seconds')
RETURNING addr_changed_at IS NOT DISTINCT FROM now() AS marked
"""


async def create_box_connection(
    member_email: str,
    name: str,
    *,
    scopes: Sequence[str] = (agent_oauth.PROJECTS_READ, agent_oauth.PROJECTS_WRITE),
    days: int = BOX_DAYS_DEFAULT,
    pinned_projects: Sequence[str] = (),
    include_personal: bool = False,
    left_out_projects: Sequence[str] = (),
) -> BoxToken | BoxRefused:
    """Make a box connection and its ONE token, in one transaction (EA-19b).

    The organization is the tenant the request already bound, as in
    :func:`create_connection`, so no caller can pass one (R11). With nothing
    bound, ``tenant_session`` raises ``TenantUnbound``. The caller passes
    the member of the session. The EA-19c route checks the ticket, the
    origin and the reach fields first.

    The token lives *days* days (1 to 365), and the connection expires with
    it. The scopes follow the consent rule (:func:`_box_scopes`). The store
    keeps only the hash, and the answer holds the raw token once.

    Refused with nothing written: the flag is off (:data:`BOX_OFF`), the
    organization is off the list (:data:`ORG_NOT_ALLOWED`), or a cap is
    full (:data:`BOX_MEMBER_FULL`, :data:`BOX_ORG_FULL`). A bad input raises
    ``ValueError`` before any SQL runs.
    """
    from acb_common.db import current_tenant
    from sqlalchemy import text

    member = _member_filter(member_email)
    if member is None:
        raise ValueError("a box token needs the member's email")
    box_name = _box_name(name)
    granted = _box_scopes(scopes)
    life = _box_days(days)
    if not agent_oauth.box_tokens_enabled():
        return BoxRefused(BOX_OFF)
    raw = new_box_token()
    scope = agent_oauth.scope_string(granted)
    async with _tenant_session() as db:
        org = str(current_tenant())
        if not agent_oauth.org_allowed(org):
            return BoxRefused(ORG_NOT_ALLOWED)
        for key in (f"agent_box_member:{org}:{member}", f"agent_box_org:{org}"):
            await db.execute(text(_BOX_ADVISORY_LOCK_SQL), {"key": key})
        counts = (
            await db.execute(text(_BOX_COUNT_SQL), {"org": org, "member": member})
        ).one()
        if counts.mine >= BOX_MEMBER_CAP:
            return BoxRefused(BOX_MEMBER_FULL)
        if counts.total >= BOX_ORG_CAP:
            return BoxRefused(BOX_ORG_FULL)
        connection_id = (
            await db.execute(
                text(_INSERT_BOX_CONNECTION_SQL),
                {
                    "org": org,
                    "member": member,
                    "client_id": agent_oauth.BOX_CLIENT_ID,
                    "client_name": box_name,
                    "client_origin": _edge_origin(),
                    "scopes": list(granted),
                    "pins": [str(p) for p in pinned_projects],
                    "personal": bool(include_personal),
                    "left_out": [str(p) for p in left_out_projects],
                    "days": life,
                },
            )
        ).one().id
        expires_at = (
            await db.execute(
                text(_INSERT_BOX_TOKEN_SQL),
                {
                    "org": org,
                    "connection_id": connection_id,
                    "hash": hash_token(raw),
                    "days": life,
                    "client_id": agent_oauth.BOX_CLIENT_ID,
                    "resource": agent_oauth.resource(),
                    "scope": scope,
                },
            )
        ).one().expires_at
    # Ids only. A token or its hash never goes into a log line (rule 7).
    _log.info("agent_tokens.box_connection_created", connection_id=connection_id)
    return BoxToken(
        token=raw,
        connection_id=connection_id,
        organization_id=org,
        scope=scope,
        expires_at=expires_at,
    )


async def rotate_box_token(
    organization_id: str,
    connection_id: str,
    *,
    member_email: str,
    now: bool = False,
    days: int = BOX_DAYS_DEFAULT,
) -> BoxToken | BoxRefused:
    """Give a box connection a new token, and say which (EA-19b).

    The caller passes the bound tenant and the member of the session, never
    request input (R11). In ONE transaction the store:

    1. locks the live box connection of this member FIRST (rule 3), else
       :data:`BOX_NOT_FOUND`;
    2. revokes every token of it except the newest live one;
    3. with *now*, revokes that one too. Else it keeps it for 24 hours at
       most (:data:`BOX_KEEP_OLD_S`);
    4. inserts the new token, which lives *days* days (1 to 365);
    5. sets ``expires_at`` of the connection to the expiry of the new token.

    So a connection holds two live tokens at most. The connection stays, so
    the stamp and the history stay. The flag and the allowlist refuse as in
    :func:`create_box_connection`.
    """
    from sqlalchemy import text

    member = _member_filter(member_email)
    if member is None:
        raise ValueError("a rotation needs the member's email")
    life = _box_days(days)
    org = _canonical_uuid(str(organization_id))
    connection = _canonical_uuid(str(connection_id).lower())
    if org is None or connection is None:
        return BoxRefused(BOX_NOT_FOUND)
    refused = _box_gate(org)
    if refused is not None:
        return refused
    where = {"connection_id": connection, "org": org}
    raw = new_box_token()
    async with _tenant_session(org) as db:
        locked = (
            await db.execute(text(_LOCK_BOX_SQL), {**where, "member": member})
        ).one_or_none()
        if locked is None:
            return BoxRefused(BOX_NOT_FOUND)
        scope = agent_oauth.scope_string(locked.scopes or ())
        await db.execute(text(_BOX_KEEP_NEWEST_SQL), where)
        if now:
            await db.execute(text(_BOX_OLD_NOW_SQL), where)
        else:
            await db.execute(
                text(_BOX_OLD_KEEP_SQL), {**where, "keep_s": float(BOX_KEEP_OLD_S)})
        expires_at = (
            await db.execute(
                text(_INSERT_BOX_TOKEN_SQL),
                {
                    **where,
                    "hash": hash_token(raw),
                    "days": life,
                    "client_id": agent_oauth.BOX_CLIENT_ID,
                    "resource": agent_oauth.resource(),
                    "scope": scope,
                },
            )
        ).one().expires_at
        await db.execute(text(_BOX_SET_EXPIRY_SQL), {**where, "days": life})
    _log.info("agent_tokens.box_token_rotated", connection_id=connection, now=now)
    return BoxToken(
        token=raw,
        connection_id=connection,
        organization_id=org,
        scope=scope,
        expires_at=expires_at,
    )


def _box_addr(addr: str | None) -> str | None:
    """The client address to store, or ``None`` for no address."""
    clean = (addr or "").strip()[:BOX_ADDR_MAX]
    if not clean or any(ord(c) < 0x20 or ord(c) == 0x7F for c in clean):
        return None
    return clean


async def touch_box_use(
    connection_id: str, organization_id: str, addr: str | None
) -> bool:
    """Stamp the use of a box connection, at most once a minute (EA-19b).

    The edge calls it after a successful bearer check of a box token, with
    the organization of the token row and ``request.client.host``. Nothing
    else calls it: ``/oauth/revoke`` and the refresh scope check do not.
    Only a box connection is stamped (:data:`_TOUCH_BOX_SQL`), and the
    expiry never moves.

    A new address sets ``addr_changed_at`` at most once an hour, and then
    this logs it. The audit row :data:`BOX_NEW_ADDRESS_ACTION` is written by
    the gateway list route (EA-19c), not by the edge. True when the
    row was stamped. The caller must not fail a request on an error here.
    """
    from sqlalchemy import text

    org = _canonical_uuid(str(organization_id))
    connection = _canonical_uuid(str(connection_id))
    if org is None or connection is None:
        return False
    stored = _box_addr(addr)

    async def _work(session: Any) -> Any:
        await _bind(session, org)
        return (
            await session.execute(
                text(_TOUCH_BOX_SQL),
                {"connection_id": connection, "org": org, "addr": stored},
            )
        ).one_or_none()

    row = await _run(_work)
    if row is None:
        return False
    if row.marked and stored is not None:
        # The edge writes NO audit row: the audit writer opens a sync engine
        # and has no tenant here. The mark is the evidence. The gateway list
        # route (EA-19c) reads a fresh mark and writes the row (review of
        # 2026-11-07).
        _log.warning("agent_tokens.box_new_address", connection_id=connection)
    return True


# ── The bulk revoke and the new-address mark (EA-19c, spec §10.9 q. 5) ──────

#: Revoke every box connection of one organization that is not revoked yet.
#: ⚠️ The lock order of rule 3 holds: this takes the connection rows first,
#: and :data:`_REVOKE_TOKENS_OF_SQL` takes their tokens after it. A row past
#: ``expires_at`` goes too, so a flag that comes back on revives nothing.
_REVOKE_BOX_CONNECTIONS_SQL = """
UPDATE agent_connection
   SET revoked_at = now(),
       revoked_reason = CAST(:reason AS TEXT)
 WHERE organization_id = CAST(:org AS UUID)
   AND credential = 'box'
   AND revoked_at IS NULL
RETURNING CAST(id AS TEXT) AS id
"""

#: The ids of the audit rows of *ids* that exist already. Runs bound to the
#: tenant, so it reads the rows of that organization only.
_AUDITED_SQL = """
SELECT CAST(id AS TEXT) AS id
  FROM audit_event
 WHERE id = ANY(CAST(:ids AS UUID[]))
"""

#: The namespace of the id of a new-address audit row. Fixed for good: a
#: change makes each old mark look new, and the list writes its row again.
_BOX_ADDRESS_EVENT_NS = uuid.UUID("5d0f3c51-8f8e-4a5c-9b7e-19c0ad0e5519")


async def revoke_box_connections(organization_id: str, *, reason: str) -> list[str]:
    """Revoke every box connection of *organization_id*, and every token of
    each, in one transaction. Answer the ids that it revoked (EA-19c).

    The admin's "Revoke all box tokens" calls it with :data:`REVOKE_FLAG_OFF`
    before an operator turns the flag on again (spec §10.9 question 5). The
    caller passes the bound tenant, never request input (R11). It does not
    read the flag, because a revoke only removes access. An OAuth
    connection never changes.
    """
    from sqlalchemy import text

    _reason(reason)
    org = _canonical_uuid(str(organization_id))
    if org is None:
        return []
    async with _tenant_session(org) as db:
        ids = [
            row.id for row in (
                await db.execute(
                    text(_REVOKE_BOX_CONNECTIONS_SQL), {"org": org, "reason": reason})
            ).all()
        ]
        if ids:
            await db.execute(text(_REVOKE_TOKENS_OF_SQL), {"ids": ids, "org": org})
    _log.warning("agent_tokens.box_connections_revoked", count=len(ids), reason=reason)
    return ids


def box_address_event_id(connection_id: str, marked_at: datetime) -> str:
    """The id of the ONE audit row of one new-address mark (EA-19c).

    The same connection and the same ``addr_changed_at`` give the same id,
    so the primary key of ``audit_event`` holds one row for each mark, also
    when two list reads race. A new mark has a new time, so a new id.
    """
    stamp = marked_at.isoformat()
    return str(uuid.uuid5(_BOX_ADDRESS_EVENT_NS, f"{str(connection_id).lower()}|{stamp}"))


async def audited_events(organization_id: str, event_ids: Sequence[str]) -> frozenset[str]:
    """The ids in *event_ids* that ``audit_event`` holds already (EA-19c).

    The gateway list route reads it before it writes the audit row of a new
    address, so a second list read writes nothing. The caller passes the
    bound tenant. An id that is not a UUID is never found.
    """
    from sqlalchemy import text

    org = _canonical_uuid(str(organization_id))
    ids = [i for i in (_canonical_uuid(str(e)) for e in event_ids) if i is not None]
    if org is None or not ids:
        return frozenset()
    async with _tenant_session(org) as db:
        rows = (await db.execute(text(_AUDITED_SQL), {"ids": ids})).all()
    return frozenset(row.id for row in rows)
