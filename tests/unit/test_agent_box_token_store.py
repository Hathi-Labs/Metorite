"""WS-55 EA-19b — the box token store and the box stamp.

Spec: ``project-docs/specs/external_agent_access.md`` §10.9 (questions 1, 4,
5, 6 and 8) and the EA-19b row of §15 · D-EA-5 · Metorite task 1839.

**What this file proves.** A box token is the ONE access token of a
connection of credential ``box``. It has no refresh token. Its expiry is
fixed, and only a rotation moves it. The stamp is
``mcp:box:<slug>-<8 hex>``. The flag ``agent_box_tokens_enabled`` is a
pause: the edge and branch 0 refuse a box while it is off, and a revoke
still works.

Two halves.

**Database-free (always runs).** The stamp, the label, the token shape, the
``repr``, the days, the scope parity with the consent rule, and the source
fences of the touch.

**R8 (a real database, as a NON-privileged role, on a phase-4-promoted
catalog of its own).** The store calls run as that role under
``tenant_engine_scope``. The edge calls the REAL gateway app through
``httpx.ASGITransport`` (EA3-20), as in ``test_mcp_edge_claim.py``. A few
cross-organization reads run as the catalog's superuser too, which bypasses
RLS, so only the organization predicate keeps them apart.

Run::

    eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_agent_box_token_store.py -v -rs

**The named mutations of §10.9 (R7).** Each one turned the named test red
on 2026-11-06. ``A`` is ``packages/acb_auth/acb_auth`` and ``E`` is
``apps/services/agent_edge/agent_edge``.

* B1. A bearer check that ignores the flag: ``E/bearer.py`` drops the
  ``box_tokens_enabled`` check.
  ``TestTheFlag::test_flag_off_a_box_token_gets_401_at_the_edge``.
* B2. An expiry that slides on use: ``A/agent_tokens.py``
  ``_TOUCH_BOX_SQL`` also sets ``expires_at = now() + interval '90 days'``.
  ``TestUseNeverMovesTheExpiry::test_calls_at_the_edge_never_move_the_expiry``.
  And ``_SLIDE_SQL`` without ``AND credential <> 'box'``:
  ``TestUseNeverMovesTheExpiry::test_the_idle_slide_skips_a_box``.
* B3. A rotation that skips the connection lock: ``_LOCK_BOX_SQL`` without
  ``FOR NO KEY UPDATE``.
  ``TestTheRotation::test_a_rotation_waits_for_a_revoke_and_writes_nothing``.
* B4. A third live token: ``rotate_box_token`` skips
  ``_BOX_KEEP_NEWEST_SQL``.
  ``TestTheRotation::test_a_second_rotation_leaves_two_live_tokens``.
* B5. A token found across organizations: ``_LOCK_BOX_SQL`` and
  ``_TOUCH_BOX_SQL`` without ``AND organization_id = CAST(:org AS UUID)``.
  ``TestAcrossOrganizations::test_another_org_rotates_nothing_even_past_rls``
  and ``test_another_org_touches_nothing_even_past_rls``.
* B6. A stamp query with no NULL branch: ``_TOUCH_BOX_SQL`` without
  ``last_used_at IS NULL OR``.
  ``TestTheTouch::test_a_new_connection_gets_stamped``.

Two more mutations of the caps:

* B7. ``create_box_connection`` takes no advisory lock.
  ``TestTheCaps::test_concurrent_mints_pass_the_member_cap_once`` and
  ``test_concurrent_mints_pass_the_org_cap_once``.
* B8. ``read_live_connection`` drops the box flag check.
  ``TestTheFlag::test_flag_off_branch_0_reads_no_box_connection``.

The gate is ``TENANT_LADDER_DATABASE_URL``. A skip is not a pass.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import random
import re
import string
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

import acb_audit
import structlog
from acb_auth import agent_oauth, agent_signing, agent_tokens
from acb_common import get_settings
from acb_common.db import TenantUnbound, bind_tenant, release_tenant
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit import test_mcp_edge_task_writes as tw
from tests.unit import test_mcp_edge_writes as w
from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope

# The fixtures of the EA-6d suite: the fake Redis under the real wrapper, and
# the real gateway app behind the edge.
redis_fake = tw.redis_fake
live_app = tw.live_app
live = tw.live

_DB_GATE = w._DB_GATE
KEY = w.KEY
MEMBER = w.MEMBER
INTERNAL = w.INTERNAL
RESOURCE = w.RESOURCE
R8_VIA = w.R8_VIA

_ROOT = Path(__file__).resolve().parents[2]
_EDGE_DIR = _ROOT / "apps" / "services" / "agent_edge" / "agent_edge"

CID = "0a1b2c3d-1111-4222-8333-444455556666"
TOKEN_SHAPE = re.compile(r"^mbt_[A-Za-z0-9_-]{43}$")
BOX_ADDR = "203.0.113.7"


# ═══════════════════════════════════════════════════════════════════════════
# Database-free: the stamp and the label
# ═══════════════════════════════════════════════════════════════════════════


def _box_stamp(name: str | None, cid: str = CID) -> str:
    return agent_oauth.actor_via_for(
        None, client_id=agent_oauth.BOX_CLIENT_ID, credential="box",
        connection_id=cid, client_name=name)


class TestTheStamp:
    def test_a_box_stamps_its_slug_and_the_start_of_its_id(self):
        assert _box_stamp("metorite-home") == "mcp:box:metorite-home-0a1b2c3d"

    def test_each_run_of_other_characters_is_one_dash(self):
        assert _box_stamp("  Vijay's PC #2 (WSL) ") == "mcp:box:vijay-s-pc-2-wsl-0a1b2c3d"

    def test_the_slug_keeps_40_characters_at_most(self):
        stamp = _box_stamp("a" * 60)
        assert stamp == "mcp:box:" + "a" * 40 + "-0a1b2c3d"
        assert agent_oauth.ACTOR_VIA_PATTERN.fullmatch(stamp)

    def test_a_name_with_no_slug_gives_the_short_stamp(self):
        assert _box_stamp("!!!") == "mcp:box:0a1b2c3d"
        assert _box_stamp("") == "mcp:box:0a1b2c3d"
        assert _box_stamp(None) == "mcp:box:0a1b2c3d"

    def test_a_connection_id_that_is_not_a_uuid_gives_no_stamp(self):
        assert _box_stamp("home", cid="not-a-uuid") == ""
        assert _box_stamp("home", cid="") == ""

    def test_an_upper_case_id_gives_the_canonical_start(self):
        assert _box_stamp("home", cid=CID.upper()) == "mcp:box:home-0a1b2c3d"

    def test_every_stamp_of_a_random_name_fits_the_pattern(self):
        rng = random.Random(19)
        alphabet = string.printable + "ÄéßЖ漢"
        for _ in range(500):
            name = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
            stamp = _box_stamp(name)
            assert stamp.startswith(agent_oauth.BOX_STAMP_PREFIX), name
            assert agent_oauth.ACTOR_VIA_PATTERN.fullmatch(stamp), (name, stamp)

    def test_the_defaults_keep_each_old_stamp(self):
        claude = "https://claude.ai/oauth/claude-code-client-metadata"
        assert agent_oauth.actor_via_for(
            "https://claude.ai", client_id=claude) == "mcp:claude.ai:claude-code"
        assert agent_oauth.actor_via_for(
            "https://claude.ai", client_id=claude, credential="oauth",
            connection_id=CID, client_name="home") == "mcp:claude.ai:claude-code"
        assert agent_oauth.actor_via_for("", client_id=None) == ""

    def test_an_oauth_host_named_box_never_reads_as_a_box(self):
        stamp = agent_oauth.actor_via_for(
            "https://box", client_id="https://box/home-client-metadata")
        assert stamp.startswith("mcp:") and not stamp.startswith("mcp:box")

    def test_the_label_and_the_host(self):
        assert agent_oauth.client_label("mcp:box:metorite-home-0a1b2c3d") == "Box metorite-home"
        assert agent_oauth.client_label("mcp:box:0a1b2c3d") == "Box"
        assert agent_oauth.client_host("mcp:box:metorite-home-0a1b2c3d") == "box"

    def test_the_flag_defaults_off_and_has_one_reader(self, monkeypatch):
        assert type(get_settings()).model_fields["agent_box_tokens_enabled"].default is False
        monkeypatch.setattr(get_settings(), "agent_box_tokens_enabled", True)
        assert agent_oauth.box_tokens_enabled() is True
        monkeypatch.setattr(get_settings(), "agent_box_tokens_enabled", False)
        assert agent_oauth.box_tokens_enabled() is False


class TestTheTokenShape:
    def test_a_token_is_mbt_and_43_url_safe_characters(self):
        tokens = {agent_tokens.new_box_token() for _ in range(50)}
        assert len(tokens) == 50
        assert all(TOKEN_SHAPE.fullmatch(t) for t in tokens)

    def test_the_repr_hides_the_token(self):
        raw = agent_tokens.new_box_token()
        token = agent_tokens.BoxToken(
            token=raw, connection_id=CID, organization_id=CID, scope="projects:read",
            expires_at=None)  # type: ignore[arg-type]
        assert raw not in repr(token) and raw not in str(token)

    @pytest.mark.parametrize("days", [0, 366, -1, True, "90", 1.5, None])
    def test_a_bad_days_raises(self, days):
        with pytest.raises(ValueError):
            agent_tokens._box_days(days)  # type: ignore[arg-type]

    @pytest.mark.parametrize("days", [1, 90, 365])
    def test_a_good_days_passes(self, days):
        assert agent_tokens._box_days(days) == days

    @pytest.mark.parametrize("name", ["", "   ", "a\x00b", "tab\there", "x" * 101])
    def test_a_bad_name_raises(self, name):
        with pytest.raises(ValueError):
            agent_tokens._box_name(name)

    @pytest.mark.parametrize("asked", [
        "projects:read",
        "projects:read projects:write",
        "projects:write offline_access",
        "context:read bogus:scope projects:read",
        "knowledge:read projects:read",
        " projects:read  projects:read ",
    ])
    def test_the_scopes_are_those_of_the_consent_rule(self, asked):
        """Scope parity: the consent validator, less ``offline_access``."""
        expected = tuple(
            s for s in agent_oauth.parse_scope(asked) if s != agent_oauth.OFFLINE_ACCESS)
        assert agent_tokens._box_scopes(asked.split(" ")) == expected

    @pytest.mark.parametrize("asked", ["offline_access", "", "bogus:scope", 'bad"token'])
    def test_no_resource_scope_or_a_bad_token_raises(self, asked):
        with pytest.raises(ValueError):
            agent_tokens._box_scopes([asked])


class TestTheSourceFences:
    def test_only_the_bearer_check_touches(self):
        """``/oauth/revoke`` and the refresh scope check never call the touch."""
        callers = [p.name for p in sorted(_EDGE_DIR.glob("*.py"))
                   if "touch_box_use" in p.read_text(encoding="utf-8")]
        assert callers == ["bearer.py"]

    def test_the_touch_comes_after_every_check(self):
        source = inspect.getsource(
            __import__("agent_edge.bearer", fromlist=["x"]).authenticate)
        touch = source.index("_touch_box(")
        for check in ("org_allowed", "box_tokens_enabled", "agent_oauth.resource()"):
            assert source.index(check) < touch, check

    def test_no_edge_module_reads_x_real_ip(self):
        """No string constant of the edge names the header."""
        for path in _EDGE_DIR.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            assert not [
                n for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value.strip().lower() == "x-real-ip"
            ], path.name

    def test_the_lookup_does_not_read_the_flag(self):
        """Rule 8: ``/oauth/revoke`` needs the lookup to find a box token."""
        assert "box_tokens_enabled" not in inspect.getsource(agent_tokens.lookup_token)

    def test_no_box_sql_sets_the_expiry_except_the_rotation(self):
        tree = ast.parse(inspect.getsource(agent_tokens))
        setters = []
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                sql = " ".join(node.value.value.split())
                if "UPDATE agent_connection" in sql and "expires_at =" in sql:
                    setters.append(node.targets[0].id)  # type: ignore[attr-defined]
        assert sorted(setters) == ["_BOX_SET_EXPIRY_SQL", "_SLIDE_SQL"]
        assert "credential <> 'box'" in agent_tokens._SLIDE_SQL


# ═══════════════════════════════════════════════════════════════════════════
# R8: a catalog of its own
# ═══════════════════════════════════════════════════════════════════════════

_APP_ROLE = "acb_app_boxtoken"
_APP_PW = "boxtoken_scratch_pw"
OTHER = "other-box@mcpwrites.test"
B_MEMBER = "b-member@mcpwrites.test"


@pytest.fixture(scope="module")
def world():
    """A promoted catalog: org A with the member, a project and an OAuth
    connection, and org B with one member."""
    if not w._URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(w._URL)
    dedicated = f"{admin_url.database}_boxtoken"
    w._drop_database(admin_url, dedicated)
    with create_engine(admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(f'CREATE DATABASE "{dedicated}"'))
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    for phase in ("01_add_columns.sql", "02_backfill.sql", "03_constraints.sql",
                  "04_policies.sql"):
        with eng.begin() as conn, open(os.path.join(w._GENERATED, phase),
                                       encoding="utf-8") as fh:
            w._exec_sql_file(conn, fh.read())
    with create_engine(ded_admin_url, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{_APP_ROLE}')"
            f" THEN CREATE ROLE {_APP_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(f"ALTER ROLE {_APP_ROLE} WITH LOGIN PASSWORD '{_APP_PW}' "
                       "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{dedicated}" TO {_APP_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"))
        c.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                       f"IN SCHEMA public TO {_APP_ROLE}"))
        c.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"))

    org, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for oid, slug in ((org, "boxtoken-a"), (org_b, "boxtoken-b")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": oid, "s": slug})
    a = w._Seeder(eng, org)
    a.number = 400_000
    a.member(MEMBER, ["feature:projects"])
    team = a.project("Team", in_space=True)
    s = SimpleNamespace(todo=a.status(team, "To do", "todo", 0),
                        doing=a.status(team, "Doing", "in_progress", 1),
                        done=a.status(team, "Done", "done", 2))
    b = w._Seeder(eng, org_b)
    b.member(B_MEMBER, ["feature:projects"])
    world_ = SimpleNamespace(admin=eng, org=org, org_b=org_b, seeder=a, team=team, s=s,
                             c={}, tok={})
    world_.c["write"], world_.tok["write"] = a.connection(scopes=w.WRITE_SCOPES)
    world_.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)
    #: The catalog's superuser, which bypasses RLS (B5).
    world_.admin_dsn = ded_admin_url.render_as_string(hide_password=False)
    yield world_

    eng.dispose()
    w._drop_database(admin_url, dedicated)


@pytest.fixture
def box_on(monkeypatch, request):
    """The access flags and the box flag ON, for every organization.

    Each test starts with no live box connection, so the caps of one test
    never meet the boxes of another.
    """
    settings = get_settings()
    for name, value in (("agent_access_enabled", True), ("agent_access_orgs", "*"),
                        ("agent_box_tokens_enabled", True)):
        monkeypatch.setattr(settings, name, value)
    if "world" in request.fixturenames:
        with request.getfixturevalue("world").admin.begin() as c:
            c.execute(text("UPDATE agent_connection SET revoked_at = now(),"
                           " revoked_reason = 'admin' WHERE credential = 'box'"
                           " AND revoked_at IS NULL"))


@pytest.fixture
def audits(monkeypatch) -> list:
    """The audit rows the store writes, kept in memory."""
    seen: list = []
    monkeypatch.setattr(acb_audit, "record", seen.append)
    return seen


def _box_flag(monkeypatch, on: bool) -> None:
    monkeypatch.setattr(get_settings(), "agent_box_tokens_enabled", on)


async def _as(dsn: str, work, org: str | None = None) -> Any:
    """Run *work* as the role of *dsn*, in its own task, with *org* bound."""
    async with tenant_engine_scope(dsn):
        async def go() -> Any:
            token = bind_tenant(org) if org is not None else None
            try:
                return await work()
            finally:
                if token is not None:
                    release_tenant(token)
        return await asyncio.create_task(go())


async def _mint(world, *, member: str = MEMBER, name: str = "metorite-home",
                org: str | None = None, **kw) -> agent_tokens.BoxToken:
    made = await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
        member, name, **kw), org or world.org)
    assert isinstance(made, agent_tokens.BoxToken), made
    return made


def _admin(world, sql: str, **params) -> Any:
    with world.admin.begin() as c:
        return c.execute(text(sql), params).first()


def _admin_all(world, sql: str, **params) -> list[Any]:
    with world.admin.begin() as c:
        return list(c.execute(text(sql), params))


def _conn(world, cid: str) -> Any:
    return _admin(world, "SELECT * FROM agent_connection WHERE id = CAST(:i AS uuid)", i=cid)


def _tokens(world, cid: str) -> list[Any]:
    return _admin_all(world, "SELECT * FROM agent_token WHERE connection_id = CAST(:i AS uuid)"
                             " ORDER BY created_at, id", i=cid)


def _live_tokens(world, cid: str) -> int:
    return int(_admin(world, "SELECT count(*) FROM agent_token WHERE connection_id ="
                             " CAST(:i AS uuid) AND revoked_at IS NULL AND expires_at > now()",
                      i=cid)[0])


def _age(world, cid: str, seconds: int = 120) -> None:
    """Move ``last_used_at`` back, so the next touch is past the minute."""
    _admin(world, "UPDATE agent_connection SET last_used_at = now() - make_interval("
                  "secs => :s) WHERE id = CAST(:i AS uuid) RETURNING id", s=seconds, i=cid)


async def _edge_post(token: str, message: dict[str, Any], *,
                     client: tuple[str, int] = (BOX_ADDR, 4321),
                     headers: dict[str, str] | None = None) -> httpx.Response:
    """One ``/mcp`` request. The caller holds the engine scope."""
    from agent_edge import app as edge_app_module

    sent = {"authorization": f"Bearer {token}", "content-type": "application/json",
            "mcp-protocol-version": "2025-11-25", **(headers or {})}
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app(), client=client),
            base_url=w.ISSUER) as c:
        return await c.post("/mcp", content=json.dumps(message), headers=sent)


async def _edge(world, token: str, message: dict[str, Any] | None = None,
                **kw) -> httpx.Response:
    message = message or w._rpc("tools/list", {})
    return await _as(world.app_dsn, lambda: _edge_post(token, message, **kw))


def _expected_stamp(made: agent_tokens.BoxToken, name: str = "metorite-home") -> str:
    return _box_stamp(name, made.connection_id)


# ── The mint ────────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheMint:
    async def test_one_connection_and_one_access_token(self, world, box_on):
        made = await _mint(world, name="mint-shape", days=30)
        assert TOKEN_SHAPE.fullmatch(made.token)
        row = _conn(world, made.connection_id)
        assert (row.credential, row.client_id, row.client_name, row.client_origin) == (
            "box", "urn:metorite:box", "mint-shape", "https://mcp.metorite.com")
        assert list(row.scopes) == ["projects:read", "projects:write"]
        assert row.requested_scopes is None and row.last_used_at is None
        [tok] = _tokens(world, made.connection_id)
        assert (tok.kind, tok.client_id, tok.resource, tok.scope) == (
            "access", "urn:metorite:box", RESOURCE, "projects:read projects:write")
        # Rule 7: only the hash. The expiry of the connection is the token's.
        assert tok.token_hash == agent_tokens.hash_token(made.token)
        assert made.token not in json.dumps([str(v) for v in tok]) + json.dumps(
            [str(v) for v in row])
        assert row.expires_at == tok.expires_at == made.expires_at
        days = (row.expires_at - row.created_at).total_seconds() / 86400
        assert days == pytest.approx(30, abs=0.01)

    async def test_the_most_days_is_365(self, world, box_on):
        made = await _mint(world, name="year", days=365)
        row = _conn(world, made.connection_id)
        assert (row.expires_at - row.created_at).days == 365
        with pytest.raises(ValueError):
            await _mint(world, name="too long", days=366)

    async def test_the_scopes_follow_the_consent_rule(self, world, box_on):
        made = await _mint(world, name="scoped",
                           scopes=["projects:read", "offline_access", "bogus:scope"])
        assert made.scope == "projects:read"
        assert list(_conn(world, made.connection_id).scopes) == ["projects:read"]

    async def test_flag_off_or_org_off_writes_nothing(self, world, box_on, monkeypatch):
        before = int(_admin(world, "SELECT count(*) FROM agent_connection")[0])
        _box_flag(monkeypatch, False)
        off = await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
            MEMBER, "off"), world.org)
        assert off == agent_tokens.BoxRefused(agent_tokens.BOX_OFF)
        _box_flag(monkeypatch, True)
        monkeypatch.setattr(get_settings(), "agent_access_orgs", world.org_b)
        listed = await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
            MEMBER, "off"), world.org)
        assert listed == agent_tokens.BoxRefused(agent_tokens.ORG_NOT_ALLOWED)
        assert int(_admin(world, "SELECT count(*) FROM agent_connection")[0]) == before

    async def test_an_unbound_mint_raises(self, world, box_on):
        with pytest.raises(TenantUnbound):
            await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
                MEMBER, "unbound"))


# ── The flag (a pause) ──────────────────────────────────────────────────────


@_DB_GATE
class TestTheFlag:
    async def test_flag_off_a_box_token_gets_401_at_the_edge(self, live, box_on, monkeypatch):
        """B1: the bearer check reads the flag."""
        made = await _mint(live, name="flag-edge")
        on = await _edge(live, made.token)
        assert on.status_code == 200, on.text
        _box_flag(monkeypatch, False)
        off = await _edge(live, made.token)
        assert off.status_code == 401
        assert 'error="invalid_token"' in off.headers.get("www-authenticate", "")
        # An OAuth token is not touched by the box flag.
        oauth = await _edge(live, live.tok["write"])
        assert oauth.status_code == 200, oauth.text

    async def test_flag_off_branch_0_reads_no_box_connection(self, world, box_on, monkeypatch):
        """B8: the connection read of branch 0 reads the flag too."""
        made = await _mint(world, name="flag-branch0")
        found = await _as(world.app_dsn, lambda: agent_tokens.read_live_connection(
            world.org, made.connection_id))
        assert found is not None
        assert (found.credential, found.client_name) == ("box", "flag-branch0")
        _box_flag(monkeypatch, False)
        assert await _as(world.app_dsn, lambda: agent_tokens.read_live_connection(
            world.org, made.connection_id)) is None
        oauth = await _as(world.app_dsn, lambda: agent_tokens.read_live_connection(
            world.org, world.c["write"]))
        assert oauth is not None and oauth.credential == "oauth"

    async def test_the_lookup_finds_a_box_token_with_the_flag_off(self, world, box_on,
                                                                  monkeypatch):
        made = await _mint(world, name="flag-lookup")
        _box_flag(monkeypatch, False)
        found = await _as(world.app_dsn, lambda: agent_tokens.lookup_token(made.token))
        assert found is not None
        assert (found.credential, found.client_name, found.organization_id) == (
            "box", "flag-lookup", world.org)


# ── The stamp at the gateway ────────────────────────────────────────────────


@_DB_GATE
class TestTheStampAtTheGateway:
    async def test_a_box_claims_a_task_and_the_rows_carry_its_stamp(self, live, box_on):
        made = await _mint(live, name="metorite-home")
        live.tok["box"] = made.token
        task = live.seeder.task(live.team, live.s.todo, "box claim")
        r = await tw._send(live, "GET", f"/projects/tasks/{task}")
        assert r.status_code == 200, r.text
        result = await tw._mcp(live, "box", "claim_task", {
            "task_id": task, "expected_version": r.json()["updated_at"]})
        assert result["isError"] is False, result
        stamp = _expected_stamp(made)
        assert re.fullmatch(r"mcp:box:metorite-home-[0-9a-f]{8}", stamp)
        vias = {row[0] for row in _admin_all(
            live, "SELECT meta->>'via' FROM pm_activities WHERE task_id = CAST(:t AS uuid)",
            t=task)}
        assert vias == {stamp}

    async def test_a_person_with_a_forged_box_stamp_gets_401(self, live, box_on):
        ok = await tw._send(live, "GET", "/projects/tree", signed=None)
        assert ok.status_code == 200, ok.text
        forged = await tw._send(live, "GET", "/projects/tree", signed=None,
                                headers={"X-Actor-Via": "mcp:box:x"})
        assert forged.status_code == 401

    async def test_a_signed_call_with_another_box_stamp_gets_401(self, live, box_on):
        made = await _mint(live, name="signed-box")

        async def send(via: str) -> httpx.Response:
            from gateway.main import app as gateway_app

            headers = agent_signing.signed_headers(
                KEY, organization_id=live.org, connection_id=made.connection_id,
                actor_via=via, method="GET", path="/projects/tree", query="", body=b"")

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                         base_url="http://gateway.test") as c:
                return await c.get("/projects/tree", headers=headers)

        right = await _as(live.app_dsn, lambda: send(_expected_stamp(made, "signed-box")))
        assert right.status_code == 200, right.text
        other = await _as(live.app_dsn, lambda: send("mcp:box:signed-box-ffffffff"))
        assert other.status_code == 401


# ── Use never moves the expiry ──────────────────────────────────────────────


@_DB_GATE
class TestUseNeverMovesTheExpiry:
    async def test_calls_at_the_edge_never_move_the_expiry(self, live, box_on):
        """B2."""
        made = await _mint(live, name="no-slide", days=10)
        before = _conn(live, made.connection_id)
        for _ in range(3):
            r = await _edge(live, made.token)
            assert r.status_code == 200, r.text
            _age(live, made.connection_id)
        after = _conn(live, made.connection_id)
        assert after.last_used_at is not None
        assert after.expires_at == before.expires_at
        [tok] = _tokens(live, made.connection_id)
        assert tok.expires_at == made.expires_at

    async def test_the_idle_slide_skips_a_box(self, world, box_on):
        """B2: the slide of an OAuth issue never reaches a box row."""
        made = await _mint(world, name="no-idle-slide", days=5)

        async def slide() -> None:
            from acb_common.db import tenant_session

            async with tenant_session(world.org) as db:
                await db.execute(text(agent_tokens._SLIDE_SQL), {
                    "connection_id": made.connection_id, "idle_s": 90 * 86400.0})

        await _as(world.app_dsn, slide)
        row = _conn(world, made.connection_id)
        assert row.expires_at == made.expires_at and row.last_used_at is None


# ── The touch ───────────────────────────────────────────────────────────────


@_DB_GATE
class TestTheTouch:
    async def test_a_new_connection_gets_stamped(self, world, box_on):
        """B6: the NULL branch."""
        made = await _mint(world, name="touch-new")
        assert _conn(world, made.connection_id).last_used_at is None
        touched = await _as(world.app_dsn, lambda: agent_tokens.touch_box_use(
            made.connection_id, world.org, BOX_ADDR))
        assert touched is True
        row = _conn(world, made.connection_id)
        assert row.last_used_at is not None and row.last_seen_addr == BOX_ADDR
        assert row.addr_changed_at is None

    async def test_a_second_touch_in_the_minute_writes_nothing(self, world, box_on):
        made = await _mint(world, name="touch-twice")
        touch = lambda: agent_tokens.touch_box_use(  # noqa: E731
            made.connection_id, world.org, BOX_ADDR)
        assert await _as(world.app_dsn, touch) is True
        first = _conn(world, made.connection_id).last_used_at
        assert await _as(world.app_dsn, touch) is False
        assert _conn(world, made.connection_id).last_used_at == first

    async def test_only_a_box_is_touched(self, live, box_on):
        oauth = live.c["write"]
        before = _conn(live, oauth)
        touched = await _as(live.app_dsn, lambda: agent_tokens.touch_box_use(
            oauth, live.org, BOX_ADDR))
        assert touched is False
        r = await _edge(live, live.tok["write"])
        assert r.status_code == 200, r.text
        after = _conn(live, oauth)
        assert (after.last_used_at, after.last_seen_addr, after.expires_at) == (
            before.last_used_at, before.last_seen_addr, before.expires_at)

    async def test_a_new_address_sets_one_mark_an_hour_and_the_edge_writes_no_audit(
            self, world, box_on, audits):
        made = await _mint(world, name="touch-addr")
        cid = made.connection_id

        async def touch(addr: str) -> bool:
            _age(world, cid)
            return await _as(world.app_dsn, lambda: agent_tokens.touch_box_use(
                cid, world.org, addr))

        assert await touch("198.51.100.1") is True  # the first address
        assert _conn(world, cid).addr_changed_at is None
        assert await touch("198.51.100.2") is True  # a change: the mark
        row = _conn(world, cid)
        assert row.addr_changed_at is not None and row.last_seen_addr == "198.51.100.2"
        marked = row.addr_changed_at
        assert await touch("198.51.100.1") is True  # in the hour: no second mark
        row = _conn(world, cid)
        # The address that set the mark stays for the hour (review of 2026-11-07):
        # the box that calls a minute later must not hide the one that raised it.
        assert row.addr_changed_at == marked and row.last_seen_addr == "198.51.100.2"
        _admin(world, "UPDATE agent_connection SET addr_changed_at = now() - interval"
                      " '2 hours' WHERE id = CAST(:i AS uuid) RETURNING id", i=cid)
        assert await touch("198.51.100.3") is True  # past the hour: a new mark
        row = _conn(world, cid)
        assert row.addr_changed_at > marked and row.last_seen_addr == "198.51.100.3"
        # The audit writer opens a sync engine with no tenant. The edge never calls it.
        assert audits == []

    def test_the_edge_path_never_reaches_the_audit_writer(self):
        import inspect

        src = inspect.getsource(agent_tokens)
        assert "acb_audit" not in src.split("BOX_NEW_ADDRESS_ACTION", 1)[1] or "import acb_audit" not in src
        assert "_audit_new_address" not in src
        assert "from acb_audit" not in src

    async def test_the_edge_stores_the_client_address_never_x_real_ip(self, live, box_on):
        made = await _mint(live, name="edge-addr")
        r = await _edge(live, made.token, headers={"X-Real-IP": "198.51.100.66",
                                                   "X-Forwarded-For": "198.51.100.67"})
        assert r.status_code == 200, r.text
        row = _conn(live, made.connection_id)
        assert row.last_seen_addr == BOX_ADDR and row.last_used_at is not None

    async def test_a_failed_touch_never_fails_the_request(self, live, box_on, monkeypatch):
        made = await _mint(live, name="touch-fails")

        async def broken(*a, **k):
            raise RuntimeError("touch failed")

        monkeypatch.setattr(agent_tokens, "touch_box_use", broken)
        r = await _edge(live, made.token)
        assert r.status_code == 200, r.text

    async def test_a_revoke_at_the_edge_does_not_touch(self, live, box_on):
        made = await _mint(live, name="revoke-no-touch")

        async def revoke() -> httpx.Response:
            return await _token_endpoint("/oauth/revoke", {
                "token": made.token, "client_id": agent_oauth.BOX_CLIENT_ID,
                "token_type_hint": "access_token"})

        r = await _as(live.app_dsn, revoke)
        assert r.status_code == 200
        row = _conn(live, made.connection_id)
        assert row.last_used_at is None and row.revoked_reason == "oauth_revoke"


# ── The rotation ────────────────────────────────────────────────────────────


async def _rotate(world, made: agent_tokens.BoxToken, *, member: str = MEMBER,
                  org: str | None = None, dsn: str | None = None, **kw) -> Any:
    return await _as(dsn or world.app_dsn, lambda: agent_tokens.rotate_box_token(
        org or world.org, made.connection_id, member_email=member, **kw))


async def _found(world, raw: str) -> bool:
    return await _as(world.app_dsn, lambda: agent_tokens.lookup_token(raw)) is not None


@_DB_GATE
class TestTheRotation:
    async def test_a_rotation_keeps_the_old_token_a_day(self, world, box_on):
        made = await _mint(world, name="rotate-keep", days=90)
        new = await _rotate(world, made, days=30)
        assert isinstance(new, agent_tokens.BoxToken) and new.token != made.token
        assert new.connection_id == made.connection_id and new.scope == made.scope
        assert await _found(world, made.token) and await _found(world, new.token)
        assert _live_tokens(world, made.connection_id) == 2
        old, fresh = _tokens(world, made.connection_id)
        assert (old.expires_at - fresh.created_at).total_seconds() <= 24 * 3600 + 1
        row = _conn(world, made.connection_id)
        assert row.expires_at == fresh.expires_at == new.expires_at
        assert (row.expires_at - fresh.created_at).days == 30
        assert row.revoked_at is None

    async def test_a_second_rotation_leaves_two_live_tokens(self, world, box_on):
        """B4: no third live token."""
        made = await _mint(world, name="rotate-twice")
        second = await _rotate(world, made)
        third = await _rotate(world, made)
        assert _live_tokens(world, made.connection_id) == 2
        assert not await _found(world, made.token)
        assert await _found(world, second.token) and await _found(world, third.token)

    async def test_rotate_now_kills_the_old_token_at_once(self, world, box_on):
        made = await _mint(world, name="rotate-now")
        new = await _rotate(world, made, now=True)
        assert _live_tokens(world, made.connection_id) == 1
        assert not await _found(world, made.token) and await _found(world, new.token)

    async def test_another_member_or_an_oauth_connection_rotates_nothing(self, world, box_on):
        made = await _mint(world, name="rotate-other")
        refused = agent_tokens.BoxRefused(agent_tokens.BOX_NOT_FOUND)
        assert await _rotate(world, made, member=OTHER) == refused
        oauth = SimpleNamespace(connection_id=world.c["write"])
        assert await _rotate(world, oauth) == refused
        assert _live_tokens(world, made.connection_id) == 1

    async def test_flag_off_rotates_nothing(self, world, box_on, monkeypatch):
        made = await _mint(world, name="rotate-off")
        _box_flag(monkeypatch, False)
        assert await _rotate(world, made) == agent_tokens.BoxRefused(agent_tokens.BOX_OFF)
        assert _live_tokens(world, made.connection_id) == 1

    async def test_a_rotation_waits_for_a_revoke_and_writes_nothing(self, world, box_on):
        """B3: rule 3. A revoke holds the row, and the rotation waits for it.

        The revoke runs in an open admin transaction. The rotation starts, and
        then the revoke commits. With the lock the rotation re-reads the row,
        finds it revoked and writes nothing. Without the lock it inserts a
        live token on the revoked connection.
        """
        made = await _mint(world, name="rotate-race")
        cid = made.connection_id
        with world.admin.connect() as admin:
            tx = admin.begin()
            admin.execute(text("UPDATE agent_connection SET revoked_at = now(),"
                               " revoked_reason = 'member' WHERE id = CAST(:i AS uuid)"),
                          {"i": cid})
            admin.execute(text("UPDATE agent_token SET revoked_at = now()"
                               " WHERE connection_id = CAST(:i AS uuid)"), {"i": cid})
            async with tenant_engine_scope(world.app_dsn):
                rotation = asyncio.create_task(agent_tokens.rotate_box_token(
                    world.org, cid, member_email=MEMBER))
                await asyncio.sleep(1.0)
                assert not rotation.done(), "the rotation did not wait for the revoke"
                tx.commit()
                result = await rotation
        assert result == agent_tokens.BoxRefused(agent_tokens.BOX_NOT_FOUND)
        assert _live_tokens(world, cid) == 0
        assert len(_tokens(world, cid)) == 1


# ── The caps ────────────────────────────────────────────────────────────────


def _box_count(world, org: str, member: str | None = None) -> int:
    return int(_admin(
        world, "SELECT count(*) FROM agent_connection WHERE organization_id ="
               " CAST(:o AS uuid) AND credential = 'box' AND revoked_at IS NULL"
               " AND expires_at > now() AND (CAST(:m AS text) IS NULL OR member_email ="
               " CAST(:m AS text))", o=org, m=member)[0])


async def _mint_many(world, members: list[str]) -> list[Any]:
    async def go() -> list[Any]:
        async def one(member: str) -> Any:
            token = bind_tenant(world.org)
            try:
                return await agent_tokens.create_box_connection(member, "race")
            finally:
                release_tenant(token)
        return list(await asyncio.gather(*(asyncio.create_task(one(m)) for m in members)))
    return await _as(world.app_dsn, go)


@_DB_GATE
class TestTheCaps:
    def test_the_caps_are_20_and_100(self):
        assert (agent_tokens.BOX_MEMBER_CAP, agent_tokens.BOX_ORG_CAP) == (20, 100)

    async def test_the_member_cap_refuses_and_a_revoke_frees_a_slot(self, world, box_on,
                                                                     monkeypatch):
        member = f"cap-{uuid.uuid4().hex[:8]}@mcpwrites.test"
        monkeypatch.setattr(agent_tokens, "BOX_MEMBER_CAP", 2)
        first = await _mint(world, member=member, name="one")
        await _mint(world, member=member, name="two")
        full = await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
            member, "three"), world.org)
        assert full == agent_tokens.BoxRefused(agent_tokens.BOX_MEMBER_FULL)
        await _as(world.app_dsn, lambda: agent_tokens.revoke_connection_scoped(
            world.org, first.connection_id, member_email=member,
            reason=agent_tokens.REVOKE_MEMBER))
        await _mint(world, member=member, name="three")
        assert _box_count(world, world.org, member) == 2

    async def test_the_org_cap_refuses(self, world, box_on, monkeypatch):
        monkeypatch.setattr(agent_tokens, "BOX_ORG_CAP", _box_count(world, world.org) + 1)
        await _mint(world, member=f"org-{uuid.uuid4().hex[:8]}@mcpwrites.test")
        full = await _as(world.app_dsn, lambda: agent_tokens.create_box_connection(
            f"org-{uuid.uuid4().hex[:8]}@mcpwrites.test", "over"), world.org)
        assert full == agent_tokens.BoxRefused(agent_tokens.BOX_ORG_FULL)

    async def test_concurrent_mints_pass_the_member_cap_once(self, world, box_on, monkeypatch):
        """B7: the advisory lock comes before the count."""
        member = f"race-{uuid.uuid4().hex[:8]}@mcpwrites.test"
        monkeypatch.setattr(agent_tokens, "BOX_MEMBER_CAP", 1)
        results = await _mint_many(world, [member] * 6)
        made = [r for r in results if isinstance(r, agent_tokens.BoxToken)]
        assert len(made) == 1, results
        assert _box_count(world, world.org, member) == 1

    async def test_concurrent_mints_pass_the_org_cap_once(self, world, box_on, monkeypatch):
        monkeypatch.setattr(agent_tokens, "BOX_ORG_CAP", _box_count(world, world.org) + 1)
        members = [f"orgrace-{i}-{uuid.uuid4().hex[:6]}@mcpwrites.test" for i in range(6)]
        results = await _mint_many(world, members)
        made = [r for r in results if isinstance(r, agent_tokens.BoxToken)]
        assert len(made) == 1, results


# ── Across organizations ────────────────────────────────────────────────────


@_DB_GATE
class TestAcrossOrganizations:
    async def test_the_lookup_names_the_org_of_the_row(self, world, box_on):
        made = await _mint(world, name="cross-lookup")
        found = await _as(world.app_dsn, lambda: agent_tokens.lookup_token(made.token))
        assert found.organization_id == world.org

    async def test_another_org_reads_no_box_connection(self, world, box_on):
        made = await _mint(world, name="cross-read")
        for dsn in (world.app_dsn, world.admin_dsn):
            assert await _as(dsn, lambda: agent_tokens.read_live_connection(
                world.org_b, made.connection_id)) is None

    async def test_another_org_rotates_nothing_even_past_rls(self, world, box_on):
        """B5: the superuser bypasses RLS, so only the predicate holds."""
        made = await _mint(world, name="cross-rotate")
        refused = agent_tokens.BoxRefused(agent_tokens.BOX_NOT_FOUND)
        for dsn in (world.app_dsn, world.admin_dsn):
            assert await _rotate(world, made, org=world.org_b, dsn=dsn) == refused
        assert _live_tokens(world, made.connection_id) == 1

    async def test_another_org_touches_nothing_even_past_rls(self, world, box_on):
        made = await _mint(world, name="cross-touch")
        for dsn in (world.app_dsn, world.admin_dsn):
            assert await _as(dsn, lambda: agent_tokens.touch_box_use(
                made.connection_id, world.org_b, BOX_ADDR)) is False
        assert _conn(world, made.connection_id).last_used_at is None

    async def test_a_member_of_org_b_mints_in_org_b_only(self, world, box_on):
        made = await _mint(world, member=B_MEMBER, name="b-box", org=world.org_b)
        assert made.organization_id == world.org_b
        row = _conn(world, made.connection_id)
        assert str(row.organization_id) == world.org_b


# ── Refresh and revoke ──────────────────────────────────────────────────────


async def _token_endpoint(path: str, form: dict[str, str]) -> httpx.Response:
    from agent_edge import app as edge_app_module

    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=edge_app_module.create_app()),
            base_url=w.ISSUER) as c:
        return await c.post(path, data=form)


@_DB_GATE
class TestRefreshAndRevoke:
    async def test_a_box_token_is_no_refresh_token(self, live, box_on):
        made = await _mint(live, name="no-refresh")
        client = "https://client.example/meta.json"
        _admin(live, "INSERT INTO agent_client (client_id, registered_via, metadata,"
                     " fetched_at, expires_at) VALUES (:c, 'cimd', '{}'::jsonb, now(),"
                     " now() + interval '1 day') ON CONFLICT DO NOTHING RETURNING client_id",
               c=client)
        for client_id in (agent_oauth.BOX_CLIENT_ID, client):
            r = await _as(live.app_dsn, lambda cid=client_id: _token_endpoint(
                "/oauth/token", {"grant_type": "refresh_token", "client_id": cid,
                                 "refresh_token": made.token}))
            assert r.status_code in (400, 401), r.text
            assert "access_token" not in r.text
        refused = await _as(live.app_dsn, lambda: agent_tokens.rotate_refresh(
            made.token, client_id=agent_oauth.BOX_CLIENT_ID))
        assert refused == agent_tokens.Refused(agent_tokens.INVALID)
        assert await _as(live.app_dsn, lambda: agent_tokens.lookup_token(
            made.token, kind=agent_tokens.REFRESH)) is None
        # Nothing was revoked.
        assert _conn(live, made.connection_id).revoked_at is None
        assert _live_tokens(live, made.connection_id) == 1

    async def test_oauth_revoke_works_with_the_flag_off(self, live, box_on, monkeypatch):
        made = await _mint(live, name="revoke-off")
        _box_flag(monkeypatch, False)
        r = await _as(live.app_dsn, lambda: _token_endpoint("/oauth/revoke", {
            "token": made.token, "client_id": agent_oauth.BOX_CLIENT_ID}))
        assert r.status_code == 200
        row = _conn(live, made.connection_id)
        assert row.revoked_at is not None and row.revoked_reason == "oauth_revoke"
        assert _live_tokens(live, made.connection_id) == 0
        _box_flag(monkeypatch, True)
        assert (await _edge(live, made.token)).status_code == 401

    async def test_the_member_revoke_works_with_the_flag_off(self, world, box_on, monkeypatch):
        made = await _mint(world, name="member-revoke-off")
        _box_flag(monkeypatch, False)
        revoked = await _as(world.app_dsn, lambda: agent_tokens.revoke_connection_scoped(
            world.org, made.connection_id, member_email=MEMBER,
            reason=agent_tokens.REVOKE_MEMBER))
        assert revoked is True
        assert _conn(world, made.connection_id).revoked_reason == "member"


# ── No token in a log line ──────────────────────────────────────────────────


@_DB_GATE
class TestNoTokenInTheLog:
    async def test_the_mint_the_rotation_and_a_call_log_no_token(self, live, box_on,
                                                                 audits, caplog):
        caplog.set_level("DEBUG")
        with structlog.testing.capture_logs() as logs:
            made = await _mint(live, name="quiet")
            new = await _rotate(live, made)
            _age(live, made.connection_id)
            r = await _edge(live, new.token, client=("198.51.100.200", 1))
            assert r.status_code == 200, r.text
            await _edge(live, made.token, client=("198.51.100.201", 1))
        seen = json.dumps([str(e) for e in logs]) + caplog.text + repr(made) + repr(new)
        seen += json.dumps([str(a) for a in audits])
        for raw in (made.token, new.token):
            assert raw not in seen
            assert agent_tokens.hash_token(raw) not in seen
        assert any(e.get("event") == "agent_tokens.box_connection_created" for e in logs)
