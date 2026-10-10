"""WS-55 EA-1 — the outside-agent token store, on a phase-4-promoted catalog.

Spec: ``project-docs/specs/external_agent_access.md`` §5.5, §7, §10.3 and §15
EA-1 · decision D97 · ``packages/acb_auth/acb_auth/agent_tokens.py`` · the
migration that creates ``agent_token`` (found by CONTENT, never by number, R1).

**What this file proves, as the NON-privileged role.** The store's own call
path runs against a dedicated database that carries the full ladder AND the
four ``generated/`` phases, so FORCE RLS is live on every scoped table. The
store's engine connects as a role that is not a superuser, not the owner and
has no BYPASSRLS. A superuser or the owner would escape RLS, and every
assertion below would then prove nothing. That is the shape of
``test_h3_rls_promotion_rehearsal.py``'s ``promoted`` fixture, and this file
builds its own copy on its own database name so that the two suites never
share a catalog.

The acceptance of EA-1, each one a test below:

* a lookup by hash works with NO tenant bound, and returns the connection id
  and the organization id;
* an unbound read of ``agent_connection`` returns zero rows;
* a reused refresh token revokes the connection and every token of it;
* a code works once;
* two concurrent refreshes revoke the connection;
* the table stores token hashes only.

Fix round 1 (the adversarial review, 2026-10-11) adds:

* ``TestIssueAndRevokeAreSerialized`` — a revoke that overlaps a rotation, in
  either order, leaves no live token. Driven by explicit transaction steps
  and a wait on ``pg_stat_activity``, never by event-loop luck;
* one test for each clause of the code spend, the refresh spend and the
  connection check, so deleting any clause turns one test red;
* the idle window: a refresh slides it, and a lookup refuses a token of a
  revoked or expired connection;
* a refresh token presented by another client, or for another resource,
  spends nothing and revokes nothing.

⚠️ **R8.** Every subject here is a SQL property: a policy, a single-row
``UPDATE ... WHERE used_at IS NULL``, a row lock under concurrency. A Python
fake agrees with whatever SQL it is handed. The gate is
``TENANT_LADDER_DATABASE_URL``, never ``DATABASE_URL``. A skip is not a pass.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_connection_tokens.py -v -rs
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import agent_tokens
from acb_auth.agent_tokens import (
    CONNECTION_DEAD,
    INVALID,
    REUSED,
    WRONG_CLIENT,
    IssuedTokens,
    Refused,
    consume_code,
    create_connection,
    hash_token,
    issue_tokens,
    lookup_token,
    mint_code,
    rotate_refresh,
)
from acb_common.db import (
    TenantUnbound,
    bind_tenant,
    current_tenant,
    release_tenant,
)
from acb_common.settings import Settings
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

_ROOT = Path(__file__).resolve().parents[2]
_LADDER = _ROOT / "infra" / "postgres"
_GENERATED = _LADDER / "generated"

_SNAPSHOT = os.environ.get("_ACB_TENANT_LADDER_URL_AT_LAUNCH")
_URL = (
    _SNAPSHOT
    if _SNAPSHOT is not None
    else os.environ.get("TENANT_LADDER_DATABASE_URL", "")
).strip()

_DB_GATE = pytest.mark.skipif(
    not _URL,
    reason=(
        "TENANT_LADDER_DATABASE_URL unset — R8 requires a REAL Postgres with "
        "pgvector (infra/postgres/01_schema.sql needs uuid-ossp AND vector). "
        "A skip here is not a pass; CI must set it. ⚠️ Do NOT export it as "
        "DATABASE_URL — see test_org_provisioning.py's docstring."
    ),
)

# The whole module needs the promoted catalog. A module-level gate keeps the
# module fixture from running (and erroring) on a box with no ladder database.
pytestmark = _DB_GATE

#: Suite-private role name, canonical privileges (the H3 rehearsal's reason: a
#: role is cluster-wide, so a shared name would clobber another suite's).
_APP_ROLE = "acb_app_agenttok"
_APP_PW = "agenttok_scratch_pw"

_CLIENT = "https://client.example/metadata.json"
_REDIRECT = "http://127.0.0.1:33418/callback"
_RESOURCE = "https://mcp.metorite.com/mcp"


def _migration() -> Path:
    """The migration that creates ``agent_token``, found by its content."""
    hits = [
        p for p in sorted(_LADDER.glob("[0-9]*_*.sql"))
        if "CREATE TABLE IF NOT EXISTS agent_token (" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one agent_token migration, found {hits}"
    return hits[0]


def _exec_sql_file(conn, sql: str) -> None:
    """One file, many statements, no client-side ``%`` pass (the ladder's way)."""
    with conn.connection.dbapi_connection.cursor() as cur:
        cur.execute(sql)


def _drop_database(admin_url, name: str) -> None:
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    maint.dispose()


def _create_database(admin_url, name: str) -> None:
    _drop_database(admin_url, name)
    maint = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    maint.dispose()


@pytest.fixture(scope="module")
def promoted():
    """A dedicated, fully phase-4-promoted catalog and a non-privileged role."""
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_agenttok"
    _create_database(admin_url, dedicated)

    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql",
                  "03_constraints.sql", "04_policies.sql"):
        with eng.begin() as conn:
            _exec_sql_file(conn, (_GENERATED / phase).read_text(encoding="utf-8"))

    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
            f"rolname='{_APP_ROLE}') THEN CREATE ROLE {_APP_ROLE} LOGIN; "
            f"END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
            f"NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public "
                       f"TO {_APP_ROLE}"))

    # Two organizations, seeded by the admin (the admin escapes RLS).
    org_a = str(uuid.uuid4())
    org_b = str(uuid.uuid4())
    with eng.begin() as conn:
        for oid, slug in ((org_a, "agenttok-a"), (org_b, "agenttok-b")):
            conn.execute(text(
                "INSERT INTO organization (id, slug, display_name) "
                "VALUES (:id, :slug, :name)"),
                {"id": oid, "slug": slug, "name": slug})

    class _NS:
        pass

    ns = _NS()
    ns.admin_engine = eng
    ns.app_url = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW)
    ns.app_dsn = ns.app_url.render_as_string(hide_password=False)
    ns.org_a = org_a
    ns.org_b = org_b
    ns.dbname = dedicated
    yield ns

    eng.dispose()
    _drop_database(admin_url, dedicated)


async def _connect(org: str, *, member: str = "dev@agenttok.test"):
    """The whole EA-1 flow for one member: connection, code, first tokens.

    The connection and the code are made under the BOUND tenant, as the
    consent route will. The code is spent and the tokens issued UNBOUND, as
    the public token endpoint will.
    """
    token = bind_tenant(org)
    try:
        connection_id = await create_connection(
            member, _CLIENT, client_name="Test client",
            client_origin="https://client.example",
            scopes=["projects:read", "offline_access"],
        )
        code = await mint_code(
            connection_id, client_id=_CLIENT, code_challenge="c" * 43,
            redirect_uri=_REDIRECT, resource=_RESOURCE, scope="projects:read",
        )
    finally:
        release_tenant(token)
    assert current_tenant() is None
    grant = await consume_code(code)
    assert grant is not None
    issued = await issue_tokens(grant, with_refresh=True)
    assert isinstance(issued, IssuedTokens), issued
    return connection_id, code, issued


async def _fresh_code(org: str, connection_id: str) -> str:
    """A new, unspent code for an existing connection."""
    token = bind_tenant(org)
    try:
        return await mint_code(
            connection_id, client_id=_CLIENT, code_challenge="c" * 43,
            redirect_uri=_REDIRECT, resource=_RESOURCE,
        )
    finally:
        release_tenant(token)


async def _rotate(raw: str, *, client_id: str = _CLIENT, resource: str | None = _RESOURCE):
    return await rotate_refresh(raw, client_id=client_id, resource=resource)


def _admin_one(promoted, sql: str, **params):
    with promoted.admin_engine.connect() as c:
        return c.execute(text(sql), params).one()


def _admin_exec(promoted, sql: str, **params) -> None:
    """A write as the admin, which escapes RLS. Used to put a row in a state."""
    with promoted.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _live_tokens(promoted, connection_id: str) -> int:
    return _admin_one(
        promoted,
        "SELECT count(*) FROM agent_token "
        " WHERE connection_id = :i AND revoked_at IS NULL",
        i=connection_id)[0]


def _connection_revoked(promoted, connection_id: str) -> bool:
    return _admin_one(
        promoted,
        "SELECT revoked_at IS NOT NULL FROM agent_connection WHERE id = :i",
        i=connection_id)[0]


async def _wait_for_a_lock_wait(promoted, *, timeout: float = 10.0) -> None:
    """Return once a session in the dedicated database waits on a lock.

    This is what makes the interleaving tests deterministic: the test does not
    guess with a sleep, it sees Postgres report the waiter.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        waiting = _admin_one(
            promoted,
            "SELECT count(*) FROM pg_stat_activity "
            " WHERE datname = :d AND wait_event_type = 'Lock'",
            d=promoted.dbname)[0]
        if waiting:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("no session waited on a lock — nothing serialized")


class TestTheCatalogIsPromoted:
    """Preconditions. If one fails, nothing else in this file proves anything."""

    async def test_the_store_engine_runs_as_the_non_privileged_role(self, promoted):
        """The engine the store opens is the role RLS binds."""
        from acb_common.db import get_session_factory

        async with tenant_engine_scope(promoted.app_dsn), get_session_factory()() as s:
            row = (await s.execute(text(
                "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
                " WHERE rolname = current_user"))).one()
        assert row[0] == _APP_ROLE
        assert not row[1], "the store connects as a SUPERUSER — RLS bypassed"
        assert not row[2], "the store role has BYPASSRLS — RLS bypassed"

    def test_agent_connection_is_forced_and_the_token_tables_are_not(self, promoted):
        with promoted.admin_engine.connect() as c:
            rows = {
                r[0]: (r[1], r[2], r[3])
                for r in c.execute(text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "       (SELECT count(*) FROM pg_policies p "
                    "         WHERE p.tablename = c.relname) "
                    "  FROM pg_class c "
                    " WHERE c.relname IN ('agent_connection', 'agent_token', "
                    "                     'agent_client', 'apps')"))
            }
        assert rows["agent_connection"][:2] == (True, True)
        assert rows["agent_connection"][2] >= 1
        for exempt in ("agent_token", "agent_client"):
            assert rows[exempt] == (False, False, 0), exempt
        assert rows["apps"][1], "the catalog is not phase-4 promoted"

    def test_the_migration_replays_cleanly(self, promoted):
        """The deploy replays the whole ladder on every push."""
        sql = _migration().read_text(encoding="utf-8")
        with promoted.admin_engine.begin() as conn:
            _exec_sql_file(conn, sql)


class TestTheLookupWorksUnbound:
    """The token lookup runs before the tenant is known (spec §10.3)."""

    async def test_a_lookup_by_hash_returns_the_connection_and_the_org(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, issued = await _connect(promoted.org_a)
            assert current_tenant() is None, "the lookup must run with NO tenant"
            found = await lookup_token(issued.access_token)
            refresh = await lookup_token(issued.refresh_token, kind="refresh")
        assert found is not None, "an UNBOUND lookup could not find the token"
        assert found.connection_id == connection_id
        assert found.organization_id == promoted.org_a
        assert found.kind == "access"
        assert found.resource == _RESOURCE
        assert refresh is not None and refresh.connection_id == connection_id

    async def test_an_unknown_or_expired_token_finds_nothing(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            _cid, _code, issued = await _connect(promoted.org_a)
            assert await lookup_token(agent_tokens.new_token()) is None
            # The access token is not a refresh token.
            assert await lookup_token(issued.access_token, kind="refresh") is None
            with promoted.admin_engine.begin() as c:
                c.execute(text(
                    "UPDATE agent_token SET expires_at = now() - interval '1 second' "
                    " WHERE token_hash = :h"), {"h": hash_token(issued.access_token)})
            assert await lookup_token(issued.access_token) is None

    async def test_an_unbound_read_of_agent_connection_returns_zero_rows(self, promoted):
        """The converse: the connection itself is tenant-scoped.

        ⚠️ The unbound read runs on a FRESH engine. A pooled connection that
        once ran ``set_config('app.tenant_id', ..., true)`` reads the setting
        back as ``''`` and not as NULL, so the policy's ``::uuid`` cast raises
        there instead of matching nothing. A fresh connection shows the
        fail-closed answer the policy gives.
        """
        from acb_common.db import get_session_factory, tenant_session

        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, _issued = await _connect(promoted.org_a)
        async with tenant_engine_scope(promoted.app_dsn):
            async with get_session_factory()() as s:
                unbound = (await s.execute(text(
                    "SELECT count(*) FROM agent_connection"))).scalar_one()
            async with tenant_session(promoted.org_b) as s:
                other = (await s.execute(text(
                    "SELECT count(*) FROM agent_connection WHERE id = :i"),
                    {"i": connection_id})).scalar_one()
            async with tenant_session(promoted.org_a) as s:
                own = (await s.execute(text(
                    "SELECT count(*) FROM agent_connection WHERE id = :i"),
                    {"i": connection_id})).scalar_one()
        everything = _admin_one(
            promoted, "SELECT count(*) FROM agent_connection")[0]
        assert everything >= 1
        assert unbound == 0, "an UNBOUND session read agent_connection rows"
        assert other == 0, "org B read org A's connection"
        assert own == 1

    async def test_a_connection_needs_a_bound_tenant(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            assert current_tenant() is None
            with pytest.raises(TenantUnbound):
                await create_connection("dev@agenttok.test", _CLIENT)

    async def test_another_tenant_cannot_mint_a_code_for_the_connection(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, _issued = await _connect(promoted.org_a)
            token = bind_tenant(promoted.org_b)
            try:
                with pytest.raises(LookupError):
                    await mint_code(
                        connection_id, client_id=_CLIENT, code_challenge="c" * 43,
                        redirect_uri=_REDIRECT, resource=_RESOURCE,
                    )
            finally:
                release_tenant(token)


class TestACode:
    """Single use, expiry and revocation: each clause of the code spend."""

    async def test_the_second_spend_of_a_code_returns_nothing(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            _cid, code, _issued = await _connect(promoted.org_a)
            again = await consume_code(code)
        assert again is None, "a spent code was accepted a second time"

    async def test_an_expired_code_returns_nothing(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, _issued = await _connect(promoted.org_a)
            code = await _fresh_code(promoted.org_a, cid)
            _admin_exec(
                promoted,
                "UPDATE agent_token SET expires_at = now() - interval '1 second' "
                " WHERE token_hash = :h", h=hash_token(code))
            assert await consume_code(code) is None

    async def test_a_revoked_code_returns_nothing(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, _issued = await _connect(promoted.org_a)
            code = await _fresh_code(promoted.org_a, cid)
            _admin_exec(
                promoted,
                "UPDATE agent_token SET revoked_at = now() WHERE token_hash = :h",
                h=hash_token(code))
            assert await consume_code(code) is None

    async def test_a_code_of_a_revoked_connection_issues_nothing(self, promoted):
        """The code survives a racing revoke, so the issue must check."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, _issued = await _connect(promoted.org_a)
            code = await _fresh_code(promoted.org_a, cid)
            _admin_exec(
                promoted,
                "UPDATE agent_connection SET revoked_at = now() WHERE id = :i",
                i=cid)
            grant = await consume_code(code)
            assert grant is not None
            assert await issue_tokens(grant, with_refresh=True) == Refused(
                CONNECTION_DEAD)


class TestRefreshRotation:
    """Rotation on every use, and reuse ends the connection (spec §10.2)."""

    async def test_a_refresh_issues_a_new_pair_and_spends_the_old(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, issued = await _connect(promoted.org_a)
            rotated = await _rotate(issued.refresh_token)
            assert isinstance(rotated, IssuedTokens), rotated
            assert rotated.refresh_token != issued.refresh_token
            assert rotated.connection_id == connection_id
            assert rotated.organization_id == promoted.org_a
            assert rotated.client_id == _CLIENT
            assert await lookup_token(rotated.access_token) is not None
            assert await lookup_token(issued.refresh_token, kind="refresh") is None

    async def test_the_issued_tokens_do_not_print(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            _cid, _code, issued = await _connect(promoted.org_a)
        shown = repr(issued)
        assert issued.access_token not in shown
        assert issued.refresh_token not in shown

    async def test_a_reused_refresh_token_revokes_the_connection(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, issued = await _connect(promoted.org_a)
            rotated = await _rotate(issued.refresh_token)
            assert isinstance(rotated, IssuedTokens)
            assert await _rotate(issued.refresh_token) == Refused(REUSED)
            # Every token of the connection is dead, the fresh pair included.
            assert await lookup_token(rotated.access_token) is None
            assert await lookup_token(rotated.refresh_token, kind="refresh") is None
            assert await lookup_token(issued.access_token) is None
        assert _connection_revoked(promoted, connection_id), (
            "the reuse did not revoke the connection")
        live = _live_tokens(promoted, connection_id)
        assert live == 0, f"{live} tokens of the revoked connection are still live"

    async def test_two_concurrent_refreshes_revoke_the_connection(self, promoted):
        """No grace window: the client must serialize its refreshes.

        Order-free twin of the deterministic test below. It holds under any
        order the two calls happen to take.
        """
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, _code, issued = await _connect(promoted.org_a)
            results = await asyncio.gather(
                _rotate(issued.refresh_token), _rotate(issued.refresh_token))
            winners = [r for r in results if isinstance(r, IssuedTokens)]
            assert len(winners) <= 1, "two callers spent one refresh token"
            for won in winners:
                assert await lookup_token(won.access_token) is None
        assert _connection_revoked(promoted, connection_id), (
            "two concurrent refreshes left the connection live")

    async def test_another_connection_is_untouched_by_a_reuse(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            _cid, _code, issued = await _connect(promoted.org_a)
            other_cid, _c2, other = await _connect(promoted.org_b)
            assert isinstance(await _rotate(issued.refresh_token), IssuedTokens)
            assert await _rotate(issued.refresh_token) == Refused(REUSED)
            found = await lookup_token(other.access_token)
        assert found is not None and found.connection_id == other_cid


class TestEachRefreshClauseRefuses:
    """Each clause of the spend and of the connection check, one test each."""

    async def test_a_revoked_refresh_token_is_refused(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_token SET revoked_at = now() WHERE token_hash = :h",
                h=hash_token(issued.refresh_token))
            assert await _rotate(issued.refresh_token) == Refused(INVALID)
        assert not _connection_revoked(promoted, cid)

    async def test_an_expired_refresh_token_is_refused(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_token SET expires_at = now() - interval '1 second' "
                " WHERE token_hash = :h", h=hash_token(issued.refresh_token))
            assert await _rotate(issued.refresh_token) == Refused(INVALID)
        assert not _connection_revoked(promoted, cid)

    async def test_a_revoked_connection_issues_nothing(self, promoted):
        """The connection row is revoked and its token rows were missed."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_connection SET revoked_at = now() WHERE id = :i",
                i=cid)
            assert await _rotate(issued.refresh_token) == Refused(CONNECTION_DEAD)

    async def test_an_expired_connection_issues_nothing(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_connection "
                "   SET expires_at = now() - interval '1 second' WHERE id = :i",
                i=cid)
            assert await _rotate(issued.refresh_token) == Refused(CONNECTION_DEAD)

    async def test_another_client_cannot_spend_or_kill_the_token(self, promoted):
        """A mismatch spends nothing and is NOT a reuse (FIX 4)."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            wrong = await _rotate(
                issued.refresh_token, client_id="https://evil.example/m.json")
            assert wrong == Refused(WRONG_CLIENT)
            # The real client still holds a working token.
            real = await _rotate(issued.refresh_token)
            assert isinstance(real, IssuedTokens), real
            # A spent token presented by another client is still not a reuse.
            late = await _rotate(
                issued.refresh_token, client_id="https://evil.example/m.json")
            assert late == Refused(WRONG_CLIENT)
        assert not _connection_revoked(promoted, cid)

    async def test_another_resource_cannot_spend_the_token(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            wrong = await _rotate(
                issued.refresh_token, resource="https://other.example/mcp")
            assert wrong == Refused(WRONG_CLIENT)
            assert isinstance(await _rotate(issued.refresh_token), IssuedTokens)
        assert not _connection_revoked(promoted, cid)


class TestTheIdleWindow:
    """A connection expires after its idle window, and use slides it (FIX 2)."""

    async def test_a_refresh_slides_the_idle_window(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_connection SET expires_at = now() + interval '1 day', "
                "       last_used_at = NULL WHERE id = :i", i=cid)
            assert isinstance(await _rotate(issued.refresh_token), IssuedTokens)
        slid, used = _admin_one(
            promoted,
            "SELECT expires_at > now() + interval '89 days', last_used_at IS NOT NULL "
            "  FROM agent_connection WHERE id = :i", i=cid)
        assert slid, "the refresh did not slide the idle window"
        assert used, "the refresh did not stamp last_used_at"

    async def test_a_lookup_refuses_a_token_of_a_revoked_connection(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_connection SET revoked_at = now() WHERE id = :i",
                i=cid)
            assert await lookup_token(issued.access_token) is None

    async def test_a_lookup_refuses_a_token_of_an_expired_connection(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            _admin_exec(
                promoted,
                "UPDATE agent_connection "
                "   SET expires_at = now() - interval '1 second' WHERE id = :i",
                i=cid)
            assert await lookup_token(issued.access_token) is None


class TestIssueAndRevokeAreSerialized:
    """Review finding P0: a revoke that overlaps an issue must still win.

    Each test drives the interleaving with explicit transaction steps and
    waits until Postgres reports the waiting session. Nothing depends on
    which coroutine the event loop happens to run first.
    """

    async def test_a_revoke_during_a_rotation_kills_the_new_pair(self, promoted):
        """A rotates and holds its transaction open. B revokes. A commits."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            a = await agent_tokens._session()
            try:
                await a.begin()
                rotated = await agent_tokens._rotate_in(
                    a, hash_token(issued.refresh_token), _CLIENT, _RESOURCE)
                assert isinstance(rotated, IssuedTokens), rotated
                revoke = asyncio.create_task(
                    agent_tokens.revoke_connection(promoted.org_a, cid))
                await _wait_for_a_lock_wait(promoted)
                assert not revoke.done(), "the revoke did not wait for the rotation"
                await a.commit()
            finally:
                await a.close()
            await asyncio.wait_for(revoke, 10)
            assert await lookup_token(rotated.access_token) is None
            assert await lookup_token(rotated.refresh_token, kind="refresh") is None
            again = await _rotate(rotated.refresh_token)
            assert isinstance(again, Refused), "a revoked connection renewed"
        assert _live_tokens(promoted, cid) == 0

    async def test_a_rotation_during_a_revoke_issues_nothing(self, promoted):
        """B revokes the connection row and holds. A rotates. B finishes.

        Without the connection lock the rotation spends the token, then waits
        on B, while B waits on the token row: a deadlock, which Postgres breaks
        by failing one of the two.
        """
        from acb_common.db import tenant_session

        params = {"connection_id": None, "org": promoted.org_a}
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            params["connection_id"] = cid
            async with tenant_session(promoted.org_a) as b:
                await b.execute(text(agent_tokens._REVOKE_CONNECTION_SQL), params)
                rotation = asyncio.create_task(_rotate(issued.refresh_token))
                await _wait_for_a_lock_wait(promoted)
                assert not rotation.done(), "the rotation did not wait for the revoke"
                await b.execute(text(agent_tokens._REVOKE_TOKENS_SQL), params)
            result = await asyncio.wait_for(rotation, 10)
        assert result == Refused(CONNECTION_DEAD)
        assert _live_tokens(promoted, cid) == 0

    async def test_the_second_of_two_refreshes_is_a_reuse(self, promoted):
        """Deterministic twin of the concurrent test: A rotates and holds, the
        second refresh waits, A commits, and the second spend matches nothing."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _code, issued = await _connect(promoted.org_a)
            a = await agent_tokens._session()
            try:
                await a.begin()
                first = await agent_tokens._rotate_in(
                    a, hash_token(issued.refresh_token), _CLIENT, _RESOURCE)
                assert isinstance(first, IssuedTokens), first
                second = asyncio.create_task(_rotate(issued.refresh_token))
                await _wait_for_a_lock_wait(promoted)
                assert not second.done()
                await a.commit()
            finally:
                await a.close()
            assert await asyncio.wait_for(second, 10) == Refused(REUSED)
            assert await lookup_token(first.access_token) is None
            assert await lookup_token(first.refresh_token, kind="refresh") is None
        assert _connection_revoked(promoted, cid)
        assert _live_tokens(promoted, cid) == 0


class TestOnlyTheHashIsStored:
    async def test_no_column_holds_a_raw_token(self, promoted):
        async with tenant_engine_scope(promoted.app_dsn):
            connection_id, code, issued = await _connect(promoted.org_a)
        with promoted.admin_engine.connect() as c:
            columns = {
                r[0] for r in c.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    " WHERE table_name = 'agent_token'"))
            }
            stored = [
                r[0] for r in c.execute(text(
                    "SELECT row_to_json(t)::text FROM agent_token t "
                    " WHERE connection_id = :i"), {"i": connection_id})
            ]
            hashes = {
                r[0] for r in c.execute(text(
                    "SELECT token_hash FROM agent_token WHERE connection_id = :i"),
                    {"i": connection_id})
            }
        assert "token" not in columns
        assert "token_hash" in columns
        assert len(stored) == 3  # the code, the access token, the refresh token
        for raw in (code, issued.access_token, issued.refresh_token):
            assert all(raw not in row for row in stored), "a raw token is stored"
        assert hashes == {
            hash_token(code),
            hash_token(issued.access_token),
            hash_token(issued.refresh_token),
        }


class TestTheLifetimesAndTheFlag:
    """Spec §5.5 names the lifetimes as Settings defaults so a test can see them."""

    def test_the_defaults(self):
        fields = Settings.model_fields
        assert fields["agent_access_enabled"].default is False
        assert fields["agent_access_token_ttl_s"].default == 3600
        assert fields["agent_refresh_token_ttl_s"].default == 30 * 24 * 3600
        assert fields["agent_code_ttl_s"].default == 60
        assert fields["agent_connection_idle_ttl_s"].default == 90 * 24 * 3600
        assert fields["agent_client_unused_ttl_s"].default == 24 * 3600


class TestTheMigrationRefusesAForeignTable:
    """A name clash fails the deploy instead of being adopted by IF NOT EXISTS.

    Runs on its OWN throwaway database: planting a stranger's ``agent_token``
    in a shared catalog would break every other suite.
    """

    def test_it_raises_on_a_stranger_named_agent_token(self):
        admin_url = make_url(_URL)
        name = f"{admin_url.database}_agenttok_guard"
        _create_database(admin_url, name)
        eng = create_engine(admin_url.set(database=name), future=True)
        try:
            with eng.begin() as conn:
                conn.execute(text("CREATE TABLE organization (id UUID PRIMARY KEY)"))
                conn.execute(text(
                    "CREATE TABLE agent_token (id TEXT PRIMARY KEY, other TEXT)"))
            with pytest.raises(Exception) as caught, eng.begin() as conn:
                _exec_sql_file(conn, _migration().read_text(encoding="utf-8"))
            assert "agent_token already exists and is NOT the WS-55 table" in str(
                caught.value)
        finally:
            eng.dispose()
            _drop_database(admin_url, name)
