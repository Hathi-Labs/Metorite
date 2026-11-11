"""WS-55 EA-19a — each revoke of an agent connection stores its reason.

Spec: ``project-docs/specs/external_agent_access.md`` §10.9 question 6 and the
EA-19a row of §15 · Metorite task 1838 · the migration that adds
``agent_connection.revoked_reason`` (found by CONTENT, never by number, R1).

**What this file proves.** Each writer that revokes a connection stores one
reason of ``agent_tokens.REVOKE_REASONS`` in ``revoked_reason``. Each case
drives the REAL caller where one exists: the edge's ``/oauth/token`` and
``/oauth/revoke``, the gateway's ``approve``, and the two revoke routes. The
store runs as a NON-privileged role on a phase-4-promoted catalog, the
``promoted`` fixture of ``test_agent_oauth_server.py`` (R8).

* one case for each reason that a path writes;
* ``REVOKE_REASONS`` equals the reasons that the code passes. EA-19c
  writes ``flag_off``;
* an unknown reason raises ``ValueError`` before any SQL runs;
* a row that an old writer revoked keeps NULL, and the migration is
  idempotent.

Named mutations, each one turns a case red:

(a) a revoke path that stores no reason — drop ``revoked_reason`` from one
    revoke statement. ``TestEachPathStoresItsReason`` and
    ``test_each_connection_revoke_statement_sets_the_reason`` go red.
(b) a reason outside ``REVOKE_REASONS`` — a caller that passes another
    string. The route case raises, and
    ``test_the_set_is_the_reasons_the_code_passes`` goes red.
(c) a ``_rotate_in`` reuse site that stays silent — delete its
    ``_revoke_in``. ``test_a_reuse_after_the_org_left_stores_refresh_reuse``
    or ``test_a_reused_refresh_token_stores_refresh_reuse`` goes red.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass. Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_revoke_reason.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import uuid
from pathlib import Path
from uuid import UUID

import pytest

pytest.importorskip("sqlalchemy")

import acb_audit
from acb_auth import agent_tokens
from acb_auth.permissions import EffectiveAccess
from acb_auth.roles import UserContext, UserRole
from acb_common.db import bind_tenant, release_tenant
from gateway.routes import agent_access, integrations_agents
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope

# The fixtures and helpers of the EA-2a suite. ``_flag_on`` and ``redis`` are
# autouse there, and the import makes them autouse here too.
from tests.unit.test_agent_oauth_server import (  # noqa: F401
    _DB_GATE,
    CLIENT,
    MEMBER,
    _admin_one,
    _approve,
    _code,
    _connection_id_of_code,
    _edge_client,
    _exchange,
    _flag_on,
    _orgs,
    _token,
    promoted,
    redis,
    web,
)

_ROOT = Path(__file__).resolve().parents[2]
_LADDER = _ROOT / "infra" / "postgres"
_STORE = _ROOT / "packages" / "acb_auth" / "acb_auth" / "agent_tokens.py"
_CALLERS = (
    _ROOT / "apps" / "services" / "agent_edge" / "agent_edge" / "token_endpoint.py",
    _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes" / "agent_access.py",
    _ROOT / "apps" / "services" / "gateway" / "gateway" / "routes"
    / "integrations_agents.py",
    _STORE,
)

#: The writers that revoke a connection. Each call of one must pass ``reason``.
_WRITERS = {"_revoke_in", "revoke_connection", "revoke_connection_scoped",
            "revoke_box_connections"}


def _migration() -> Path:
    """The migration that adds ``revoked_reason``, found by its content."""
    hits = [
        p for p in sorted(_LADDER.glob("[0-9]*_*.sql"))
        if "ADD COLUMN IF NOT EXISTS revoked_reason" in p.read_text(encoding="utf-8")
    ]
    assert len(hits) == 1, f"expected one revoked_reason migration, found {hits}"
    return hits[0]


def _row(promoted, connection_id: str):  # noqa: F811
    return _admin_one(
        promoted,
        "SELECT revoked_at IS NOT NULL AS revoked, revoked_reason, credential,"
        "       last_seen_addr, addr_changed_at"
        "  FROM agent_connection WHERE id = CAST(:i AS uuid)",
        i=connection_id)


def _latest_connection(promoted, member: str) -> str:  # noqa: F811
    return str(_admin_one(
        promoted,
        "SELECT id FROM agent_connection WHERE member_email = :m"
        " ORDER BY created_at DESC LIMIT 1", m=member)[0])


def _refresh_form(refresh_token: str) -> dict:
    return {"grant_type": "refresh_token", "client_id": CLIENT,
            "refresh_token": refresh_token}


async def _issued(promoted) -> tuple[str, dict]:  # noqa: F811
    """A connection of the full flow, and its first token answer."""
    code = await _code(promoted)
    r = await _token(_exchange(code))
    assert r.status_code == 200, r.text
    return _connection_id_of_code(promoted, code), r.json()


def _member_ctx(org: str, email: str) -> UserContext:
    return UserContext(
        email=email, role=UserRole.EMPLOYEE, organization_id=org,
        access=EffectiveAccess(is_active=True),
    )


async def _bound(org: str, work):
    """Run *work* in its own task with *org* bound, as a request does."""
    async def go():
        token = bind_tenant(org)
        try:
            return await work()
        finally:
            release_tenant(token)
    return await asyncio.create_task(go())


@pytest.fixture
def audits(monkeypatch) -> list:
    """The revoke routes write one audit row. Keep it in memory."""
    seen: list = []
    monkeypatch.setattr(acb_audit, "record", seen.append)
    return seen


# ═══════════════════════════════════════════════════════════════════════════
# The migration
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestTheMigration:
    def test_the_four_columns_and_the_named_check(self, promoted):  # noqa: F811
        cols = {
            r.column_name: r for r in promoted.admin_engine.connect().execute(text(
                "SELECT column_name, data_type, is_nullable, column_default"
                "  FROM information_schema.columns"
                " WHERE table_name = 'agent_connection'"
                "   AND column_name IN ('credential', 'revoked_reason',"
                "                       'last_seen_addr', 'addr_changed_at')"))
        }
        assert set(cols) == {
            "credential", "revoked_reason", "last_seen_addr", "addr_changed_at"}
        assert cols["credential"].is_nullable == "NO"
        assert cols["credential"].column_default.startswith("'oauth'")
        assert cols["revoked_reason"].is_nullable == "YES"
        assert cols["addr_changed_at"].data_type == "timestamp with time zone"
        checks = {
            r[0]: r[1] for r in promoted.admin_engine.connect().execute(text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = 'agent_connection'::regclass AND contype = 'c'"))
        }
        assert "agent_connection_credential" in checks
        assert "'oauth'" in checks["agent_connection_credential"]
        assert "'box'" in checks["agent_connection_credential"]
        assert not any("revoked_reason" in d for d in checks.values()), (
            "revoked_reason has a CHECK: a wrong string would roll back a revoke")

    def test_the_file_sets_lock_timeout(self):
        assert "SET lock_timeout" in _migration().read_text(encoding="utf-8")

    async def test_an_old_revoke_keeps_null_and_a_replay_changes_nothing(
            self, promoted, web):  # noqa: F811
        """A revoke by the code of before EA-19a sets ``revoked_at`` only.
        Its row keeps NULL, also after the file runs again."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
        with promoted.admin_engine.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now()"
                           " WHERE id = CAST(:i AS uuid)"), {"i": cid})
        with (promoted.admin_engine.begin() as c,
              c.connection.dbapi_connection.cursor() as cur):
            cur.execute(_migration().read_text(encoding="utf-8"))
        row = _row(promoted, cid)
        assert row.revoked is True
        assert row.revoked_reason is None
        assert row.credential == "oauth"
        assert (row.last_seen_addr, row.addr_changed_at) == (None, None)


# ═══════════════════════════════════════════════════════════════════════════
# One case for each path that revokes a connection
# ═══════════════════════════════════════════════════════════════════════════


@_DB_GATE
class TestEachPathStoresItsReason:
    async def test_a_live_connection_has_no_reason(self, promoted, web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
        row = _row(promoted, cid)
        assert (row.revoked, row.revoked_reason, row.credential) == (
            False, None, "oauth")

    async def test_oauth_revoke_stores_oauth_revoke(self, promoted, web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, issued = await _issued(promoted)
            async with _edge_client() as edge:
                r = await edge.post("/oauth/revoke", data={
                    "token": issued["refresh_token"], "client_id": CLIENT,
                    "token_type_hint": "refresh_token"})
        assert r.status_code == 200
        assert _row(promoted, cid).revoked_reason == agent_tokens.REVOKE_OAUTH_REVOKE

    async def test_a_replayed_code_stores_code_replay(self, promoted, web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            code = await _code(promoted)
            first = await _token(_exchange(code))
            second = await _token(_exchange(code))
        assert first.status_code == 200, first.text
        assert second.status_code == 400
        cid = _connection_id_of_code(promoted, code)
        row = _row(promoted, cid)
        assert row.revoked is True
        assert row.revoked_reason == agent_tokens.REVOKE_CODE_REPLAY

    async def test_a_reused_refresh_token_stores_refresh_reuse(self, promoted, web):  # noqa: F811
        """The reuse site after the spend in ``_rotate_in``."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, issued = await _issued(promoted)
            first = await _token(_refresh_form(issued["refresh_token"]))
            again = await _token(_refresh_form(issued["refresh_token"]))
        assert first.status_code == 200, first.text
        assert again.status_code == 400
        row = _row(promoted, cid)
        assert row.revoked is True
        assert row.revoked_reason == agent_tokens.REVOKE_REFRESH_REUSE

    async def test_a_reuse_after_the_org_left_stores_refresh_reuse(
            self, promoted, web, monkeypatch):  # noqa: F811
        """The reuse site before the spend in ``_rotate_in`` (rule 8)."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, issued = await _issued(promoted)
            first = await _token(_refresh_form(issued["refresh_token"]))
            assert first.status_code == 200, first.text
            _orgs(monkeypatch, promoted.org_b)
            again = await _token(_refresh_form(issued["refresh_token"]))
        assert again.status_code == 400
        row = _row(promoted, cid)
        assert row.revoked is True, "a reuse off the list did not revoke"
        assert row.revoked_reason == agent_tokens.REVOKE_REFRESH_REUSE

    async def test_a_failed_mint_in_approve_stores_approve_rollback(
            self, promoted, web, monkeypatch):  # noqa: F811
        member = f"rollback-{uuid.uuid4().hex[:8]}@agentoauth.test"

        async def _no_code(*a, **k):
            raise RuntimeError("mint failed")

        monkeypatch.setattr(agent_tokens, "mint_code", _no_code)
        async with tenant_engine_scope(promoted.app_dsn):
            with pytest.raises(RuntimeError, match="mint failed"):
                await _approve(promoted, session_email=member)
        row = _row(promoted, _latest_connection(promoted, member))
        assert row.revoked is True
        assert row.revoked_reason == agent_tokens.REVOKE_APPROVE_ROLLBACK

    async def test_a_first_party_replace_stores_replaced(self, promoted):  # noqa: F811
        member = f"replace-{uuid.uuid4().hex[:8]}@agentoauth.test"
        async with tenant_engine_scope(promoted.app_dsn):
            old = await _bound(promoted.org_a, lambda: agent_tokens.create_connection(
                member, CLIENT, scopes=["projects:read"]))
            new = await _bound(promoted.org_a, lambda: agent_tokens.create_connection(
                member, CLIENT, scopes=["projects:read"], replace_same_client=True))
        assert _row(promoted, old).revoked_reason == agent_tokens.REVOKE_REPLACED
        assert _row(promoted, new).revoked is False

    async def test_the_members_route_stores_member(self, promoted, web, audits):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
            await _bound(promoted.org_a, lambda: agent_access.revoke_my_connection(
                UUID(cid), _member_ctx(promoted.org_a, MEMBER)))
        assert _row(promoted, cid).revoked_reason == agent_tokens.REVOKE_MEMBER
        assert len(audits) == 1

    async def test_the_admins_route_stores_admin(self, promoted, web, audits):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
            await _bound(
                promoted.org_a,
                lambda: integrations_agents.revoke_org_agent_connection(
                    UUID(cid), _member_ctx(promoted.org_a, "admin@agentoauth.test")))
        assert _row(promoted, cid).revoked_reason == agent_tokens.REVOKE_ADMIN
        assert len(audits) == 1

    async def test_a_second_revoke_keeps_the_first_reason(self, promoted, web, audits):  # noqa: F811
        """A revoke of a revoked row matches nothing, so the first reason stays."""
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
            await agent_tokens.revoke_connection(
                promoted.org_a, cid, reason=agent_tokens.REVOKE_OAUTH_REVOKE)
            await agent_tokens.revoke_connection(
                promoted.org_a, cid, reason=agent_tokens.REVOKE_MEMBER)
        assert _row(promoted, cid).revoked_reason == agent_tokens.REVOKE_OAUTH_REVOKE


# ═══════════════════════════════════════════════════════════════════════════
# An unknown reason raises before any SQL
# ═══════════════════════════════════════════════════════════════════════════


class _NoSession:
    """A session that fails the test when a statement reaches it."""

    async def execute(self, *a, **k):
        raise AssertionError("a statement ran before the reason check")


@_DB_GATE
class TestAnUnknownReasonRaises:
    async def test_revoke_connection_raises_and_changes_nothing(self, promoted, web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
            with pytest.raises(ValueError, match="unknown revoke reason"):
                await agent_tokens.revoke_connection(
                    promoted.org_a, cid, reason="seat_lost")
        assert _row(promoted, cid).revoked is False

    async def test_revoke_connection_scoped_raises_and_changes_nothing(
            self, promoted, web):  # noqa: F811
        async with tenant_engine_scope(promoted.app_dsn):
            cid, _ = await _issued(promoted)
            with pytest.raises(ValueError, match="unknown revoke reason"):
                await agent_tokens.revoke_connection_scoped(
                    promoted.org_a, cid, reason="box_rotated")
        assert _row(promoted, cid).revoked is False

    async def test_revoke_in_raises_before_its_first_statement(self):
        with pytest.raises(ValueError, match="unknown revoke reason"):
            await agent_tokens._revoke_in(
                _NoSession(), str(uuid.uuid4()), str(uuid.uuid4()), reason="")

    @pytest.mark.parametrize("writer", sorted(_WRITERS))
    def test_the_reason_is_a_required_keyword(self, writer):
        import inspect

        param = inspect.signature(getattr(agent_tokens, writer)).parameters["reason"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty


# ═══════════════════════════════════════════════════════════════════════════
# The set is the reasons that the code passes
# ═══════════════════════════════════════════════════════════════════════════


def _reason_constants() -> dict[str, str]:
    """``REVOKE_*`` name to value, read from the store's own source."""
    out: dict[str, str] = {}
    for node in ast.parse(_STORE.read_text(encoding="utf-8")).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.startswith("REVOKE_")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            out[node.targets[0].id] = node.value.value
    return out


def _passed_reasons() -> tuple[set[str], list[str]]:
    """Each reason that a revoke path passes, and each defect found.

    A defect is a writer call with no ``reason``, or a ``reason`` that is not
    a ``REVOKE_*`` constant (a literal, or a name the store does not define).
    The ``reason`` parameter of a writer that passes it on counts as no value.
    """
    constants = _reason_constants()
    passed: set[str] = set()
    defects: list[str] = []
    for path in _CALLERS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else func.id if isinstance(func, ast.Name) else None)
            sql = None
            if name == "text" and node.args and isinstance(node.args[0], ast.Name):
                sql = node.args[0].id
            if name not in _WRITERS and sql != "_REPLACE_SAME_CLIENT_SQL":
                continue
            if sql is not None:
                continue  # the dict beside it is checked below
            kw = {k.arg: k.value for k in node.keywords}
            where = f"{path.name}:{node.lineno} {name}"
            if "reason" not in kw:
                defects.append(f"{where} passes no reason")
                continue
            value = kw["reason"]
            ref = (value.attr if isinstance(value, ast.Attribute)
                   else value.id if isinstance(value, ast.Name) else None)
            if ref == "reason":
                continue  # a writer passes its own parameter on
            if ref not in constants:
                defects.append(f"{where} passes {ast.unparse(value)}")
                continue
            passed.add(constants[ref])
        # _REPLACE_SAME_CLIENT_SQL takes its reason in its parameter dict.
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
                if "reason" in keys and "client_id" in keys:
                    value = node.values[keys.index("reason")]
                    ref = value.id if isinstance(value, ast.Name) else None
                    if ref not in constants:
                        defects.append(
                            f"{path.name}:{node.lineno} replace passes "
                            f"{ast.unparse(value)}")
                    else:
                        passed.add(constants[ref])
    return passed, defects


class TestTheSetIsTheReasonsTheCodePasses:
    def test_each_constant_is_in_the_set_and_the_set_holds_nothing_else(self):
        assert set(_reason_constants().values()) == set(agent_tokens.REVOKE_REASONS)

    def test_the_set_is_the_reasons_the_code_passes(self):
        passed, defects = _passed_reasons()
        assert defects == []
        # EA-19c writes flag_off: the revoke of all box tokens (spec §10.9
        # question 5).
        assert passed == set(agent_tokens.REVOKE_REASONS)
        spec = frozenset({
            "member", "admin", "replaced", "refresh_reuse", "code_replay",
            "oauth_revoke", "approve_rollback", "flag_off"})
        assert spec == agent_tokens.REVOKE_REASONS, "the set of §10.9 changed"

    def test_each_connection_revoke_statement_sets_the_reason(self):
        """Every SQL string of the store that revokes a connection row also
        writes ``revoked_reason`` (mutation a, as source)."""
        tree = ast.parse(_STORE.read_text(encoding="utf-8"))
        found = 0
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                sql = " ".join(node.value.value.split())
                if "UPDATE agent_connection SET revoked_at" in sql:
                    found += 1
                    name = node.targets[0].id
                    assert "revoked_reason = CAST(:reason AS TEXT)" in sql, name
        # EA-19c adds the revoke of all box connections of an organization.
        assert found == 4, f"expected 4 connection revoke statements, found {found}"
