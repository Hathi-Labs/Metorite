"""STU-B2b-2b, R8 — the door op of the Studio action door, on a real database.

Spec: ``project-docs/specs/ai_studio.md`` §14 B2b-2. Code:
``gateway/routes/studio/invoke.py`` (the checks) and
``gateway/routes/studio/door_op.py`` (the call). The hermetic half is
``TestTheDoorOp`` in ``test_studio_action_door.py``.

The member calls the REAL gateway app through ``httpx.ASGITransport``, with
the internal bearer and the member's email (branch 1a), as the BFF does. The
door then calls the same app in the process, with the inbound headers, so
the REAL Knowledge router and its one rule of who sees what run. The suite
runs on a private, phase-4-promoted catalog, as a NON-privileged role (no
superuser, no BYPASSRLS).

What it proves:

a. The owner of a package reads ``knowledge.get_page`` on a page that the
   owner can see. The answer is ``{result, truncated}``.
b. A page in a space with no grant for the caller gives the 404 of
   Knowledge, unchanged.
c. ``list_maintenance`` without ``knowledge:manage`` gives the 403 of
   Knowledge, and a holder of the verb reads the list.
d. A member with a deny override of ``feature:knowledge`` gets the 403 of
   the Knowledge router.
e. A member of org B gets 404 for the package of org A, and the 404 of
   Knowledge for a page of org A through a package of org B.
f. Each call writes one ``app_audit`` row in the bound org, with the status
   of the app and no data of the page.
g. A paused package gives 423 ``package_paused``, and nothing is called.
h. With ``STUDIO_DOOR_OPS_ORGS`` empty, the door op is 403
   ``source_not_open``, as before B2b-2b.
i. No connection of the app role is in a transaction while the app runs the
   call.

**The mutations (R7).** Commit before each, and restore with git after.

* R1. ``door_op.forwarded``: forward no header. Each read of a and c goes
  red, because the app then answers 401.
* R2. ``invoke._run_executor``: run the call inside the session of the
  second check. ``test_no_connection_is_in_a_transaction_during_the_call``.
* R3. ``invoke._audit_detail``: drop ``status``.
  ``test_each_call_writes_one_audit_row_with_no_page_data``.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_door_op_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import access as access_module
from acb_auth.access import invalidate as invalidate_access
from acb_common import get_settings
from acb_common.db import tenant_session
from gateway.routes.studio import door_op
from gateway.studio import packages_store
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import _clone_promoted_database
from tests.unit.test_mcp_edge_writes import _DB_GATE, _URL, _drop_database

pytestmark = _DB_GATE

INTERNAL = "studio-door-op-internal-token"
DOMAIN = "stub2b2b.test"
OWNER = f"owner@{DOMAIN}"
MANAGER = f"manager@{DOMAIN}"
DENIED = f"denied@{DOMAIN}"
STRANGER = f"stranger@{DOMAIN}"
AUTHOR = f"author@{DOMAIN}"

_APP_ROLE = "acb_app_stub2b2b"
_APP_PW = "stub2b2b_scratch_pw"

MANIFEST: dict[str, Any] = {
    "studio": 1, "id": "kbreader", "name": "Knowledge reader",
    "purpose": "Read pages of Knowledge.",
    "objects": [],
    "actions": [
        {"id": "page", "uses": "knowledge.get_page"},
        {"id": "maint", "uses": "knowledge.list_maintenance"},
        {"id": "propose", "uses": "knowledge.propose"},
        {"id": "search", "uses": "knowledge.search"},
    ],
}


class _Seeder:
    """Admin writes with the tenant GUC set, as ``test_mcp_edge_app_door_r8.py``."""

    def __init__(self, engine, org: str) -> None:
        self.engine = engine
        self.org = org

    def one(self, sql: str, **params) -> Any:
        with self.engine.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self.org})
            row = c.execute(text(sql), params).first()
        return row[0] if row is not None else None

    def member(self, email: str, permissions: list[str],
               deny: tuple[str, ...] = ()) -> None:
        identity = self.one(
            "INSERT INTO user_identity (email, display_name) VALUES (:e, :e)"
            " ON CONFLICT DO NOTHING RETURNING id", e=email)
        if identity is None:
            identity = self.one("SELECT id FROM user_identity WHERE email = :e", e=email)
        self.one("INSERT INTO org_membership (organization_id, user_id, status)"
                 " VALUES (CAST(:o AS uuid), CAST(:u AS uuid), 'active') RETURNING user_id",
                 o=self.org, u=str(identity))
        app_user = self.one(
            "INSERT INTO app_user (email, display_name, organization_id, status)"
            " VALUES (:e, :e, CAST(:o AS uuid), 'active') RETURNING id", e=email, o=self.org)
        role = self.one(
            "INSERT INTO org_role (organization_id, slug, display_name)"
            " VALUES (CAST(:o AS uuid), :s, :s) RETURNING id",
            o=self.org, s="door-" + email.split("@")[0])
        for permission in permissions:
            self.one("INSERT INTO org_role_permission (role_id, permission)"
                     " VALUES (CAST(:r AS uuid), :p) RETURNING role_id",
                     r=str(role), p=permission)
        self.one("INSERT INTO user_role (user_id, role_id) VALUES (CAST(:u AS uuid),"
                 " CAST(:r AS uuid)) RETURNING user_id", u=str(app_user), r=str(role))
        for permission in deny:
            self.one("INSERT INTO user_permission_override (user_id, permission, effect,"
                     " reason, set_by, organization_id) VALUES (CAST(:u AS uuid), :p, 'deny',"
                     " 'B2b-2b R8', :by, CAST(:o AS uuid)) RETURNING user_id",
                     u=str(app_user), p=permission, by=AUTHOR, o=self.org)

    def space(self, slug: str, grants: dict[str, str]) -> str:
        sid = str(self.one(
            "INSERT INTO kb_spaces (organization_id, slug, name, kind, owner_email)"
            " VALUES (CAST(:o AS uuid), :s, :n, 'team', :owner) RETURNING id",
            o=self.org, s=slug, n=slug.title(), owner=AUTHOR))
        for subject, level in grants.items():
            self.one("INSERT INTO kb_space_grants (organization_id, space_id, subject, level,"
                     " created_by) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :sub, :lvl,"
                     " :by) RETURNING space_id",
                     o=self.org, s=sid, sub=subject, lvl=level, by=AUTHOR)
        return sid

    def page(self, space: str, title: str, body: str) -> str:
        pid = str(self.one(
            "INSERT INTO kb_pages (organization_id, space_id, title, body_md, state,"
            " author_email, published_at) VALUES (CAST(:o AS uuid), CAST(:s AS uuid), :t,"
            " :b, 'published', :a, now()) RETURNING id",
            o=self.org, s=space, t=title, b=body, a=AUTHOR))
        self.one("INSERT INTO kb_page_versions (organization_id, page_id, version, body_md,"
                 " title, author_email, reason) VALUES (CAST(:o AS uuid), CAST(:p AS uuid),"
                 " 1, :b, :t, :a, 'create') RETURNING id",
                 o=self.org, p=pid, b=body, t=title, a=AUTHOR)
        return pid


@pytest.fixture(scope="module")
def world():
    """A private promoted catalog. Org A: an owner, a manager, a member with a
    deny of ``feature:knowledge``, two spaces and two pages. Org B: one member
    and no page."""
    if not _URL:
        pytest.skip("TENANT_LADDER_DATABASE_URL unset")
    admin_url = make_url(_URL)
    dedicated = f"{admin_url.database}_stub2b2b"
    _clone_promoted_database(admin_url, dedicated)
    ded_admin_url = admin_url.set(database=dedicated)
    eng = create_engine(ded_admin_url, future=True)
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
        c.execute(text(f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO {_APP_ROLE}"))

    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())
    with eng.begin() as c:
        for org, slug in ((org_a, "stub2b2ba"), (org_b, "stub2b2bb")):
            c.execute(text("INSERT INTO organization (id, slug, display_name) "
                           "VALUES (:id, :s, :s)"), {"id": org, "s": slug})
    a, b = _Seeder(eng, org_a), _Seeder(eng, org_b)
    reader = ["feature:studio", "feature:knowledge"]
    a.member(OWNER, reader)
    a.member(MANAGER, [*reader, "knowledge:manage"])
    a.member(DENIED, reader, deny=("feature:knowledge",))
    b.member(STRANGER, reader)
    team = a.space("team", {OWNER: "read", MANAGER: "read", DENIED: "read"})
    other = a.space("other", {MANAGER: "read"})
    w = SimpleNamespace(
        admin=eng, a=org_a, b=org_b, dedicated=dedicated,
        page=a.page(team, "Team plan", "# Plan for the line"),
        hidden=a.page(other, "Other plan", "# Secret plan"))
    w.app_dsn = admin_url.set(
        database=dedicated, username=_APP_ROLE, password=_APP_PW,
    ).render_as_string(hide_password=False)

    async def _setup() -> None:
        async with tenant_engine_scope(w.app_dsn):
            for name, org, member in (("pkg", org_a, OWNER), ("pkg_manager", org_a, MANAGER),
                                      ("pkg_denied", org_a, DENIED),
                                      ("pkg_paused", org_a, OWNER),
                                      ("pkg_b", org_b, STRANGER)):
                async with tenant_session(org) as db:
                    made = await packages_store.create_package(db, member, MANIFEST)
                setattr(w, name, made.id)
            async with tenant_session(org_a) as db:
                assert await packages_store.pause_package(db, OWNER, w.pkg_paused) is not None

    asyncio.run(_setup())
    yield w

    eng.dispose()
    _drop_database(admin_url, dedicated)


@pytest.fixture
def live(monkeypatch, world):
    """The real gateway app, Studio and its door op on, and Knowledge on."""
    settings = get_settings()
    for name, value in (("studio_orgs", "*"), ("studio_door_ops_orgs", "*"),
                        ("knowledge_enabled", True), ("knowledge_orgs", "*"),
                        ("package_host_orgs", "")):
        monkeypatch.setattr(settings, name, value, raising=False)
    monkeypatch.setenv("GATEWAY_INTERNAL_TOKEN", INTERNAL)
    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.setenv("ALLOWED_EMAIL_DOMAIN", DOMAIN)
    monkeypatch.delenv("GATEWAY_TRUST_UNVERIFIED_SSO_HEADERS", raising=False)
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_module, "_tables_missing", False)
    invalidate_access()
    try:
        yield world
    finally:
        invalidate_access()


async def _in_scope(world, work) -> Any:
    """Run *work* in its own task, on the suite's non-privileged DSN."""
    async with tenant_engine_scope(world.app_dsn):
        return await asyncio.create_task(work())


async def _send(world, who: str, method: str, path: str, body: Any = None) -> httpx.Response:
    """One request as the member *who*: the internal bearer and the member's
    email (branch 1a), as the BFF sends it."""
    from gateway.main import app as gateway_app

    async def go() -> httpx.Response:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway_app),
                                     base_url="http://gateway.test") as client:
            return await client.request(
                method, path, json=body,
                headers={"Authorization": f"Bearer {INTERNAL}", "X-User-Email": who})
    return await _in_scope(world, go)


async def _door(world, who: str, pid: str, action: str, args: Any = None) -> httpx.Response:
    return await _send(world, who, "POST", f"/studio/apps/{pid}/actions/{action}",
                       {} if args is None else args)


def _audits(world, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    """The audit rows of *pid*, read as the superuser, oldest first."""
    with world.admin.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, user_email, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND kind = 'action' ORDER BY id"),
            {"p": pid}).all()
    return [(org, email, d if isinstance(d, dict) else json.loads(d)) for org, email, d in rows]


class TestTheDoorOp:

    async def test_the_owner_reads_a_page_that_the_owner_can_see(self, live):
        r = await _door(live, OWNER, live.pkg, "page", {"page_id": live.page})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["truncated"] is False
        page = got["result"]
        assert (page["id"], page["title"], page["body_md"]) == (
            live.page, "Team plan", "# Plan for the line")
        assert page["versions"][0]["version"] == 1

    async def test_a_page_in_a_space_with_no_grant_is_the_404_of_knowledge(self, live):
        direct = await _send(live, OWNER, "GET", f"/knowledge/pages/{live.hidden}")
        r = await _door(live, OWNER, live.pkg, "page", {"page_id": live.hidden})
        assert direct.status_code == 404
        assert (r.status_code, r.json()) == (404, direct.json())
        assert "Secret" not in r.text

    async def test_the_maintenance_list_needs_knowledge_manage(self, live):
        direct = await _send(live, OWNER, "GET", "/knowledge/maintenance")
        r = await _door(live, OWNER, live.pkg, "maint")
        assert direct.status_code == 403
        assert (r.status_code, r.json()) == (403, direct.json())
        ok = await _door(live, MANAGER, live.pkg_manager, "maint", {"reason": "all"})
        assert ok.status_code == 200, ok.text
        assert set(ok.json()["result"]) >= {"pages", "counts"}

    async def test_a_feature_deny_on_knowledge_is_the_403_of_knowledge(self, live):
        direct = await _send(live, DENIED, "GET", f"/knowledge/pages/{live.page}")
        r = await _door(live, DENIED, live.pkg_denied, "page", {"page_id": live.page})
        assert direct.status_code == 403
        assert (r.status_code, r.json()) == (403, direct.json())
        assert "Plan" not in r.text

    async def test_org_b_gets_404_for_the_package_and_the_page_of_org_a(self, live):
        r = await _door(live, STRANGER, live.pkg, "page", {"page_id": live.page})
        assert (r.status_code, r.json()) == (404, {"detail": "Not found"})
        r = await _door(live, STRANGER, live.pkg_b, "page", {"page_id": live.page})
        assert r.status_code == 404 and "Plan" not in r.text
        # The 404 at the package names nothing, so no row of org A names org B.
        assert all(email != STRANGER for _, email, _ in _audits(live, live.pkg))
        [(org, email, detail)] = _audits(live, live.pkg_b)[-1:]
        assert (org, email, detail["status"]) == (live.b, STRANGER, 404)

    async def test_each_call_writes_one_audit_row_with_no_page_data(self, live):
        before = len(_audits(live, live.pkg))
        await _door(live, OWNER, live.pkg, "page", {"page_id": live.page})
        await _door(live, OWNER, live.pkg, "page", {"page_id": live.hidden})
        await _door(live, OWNER, live.pkg, "page", {"page_id": "not-a-uuid"})
        rows = _audits(live, live.pkg)[before:]
        assert len(rows) == 3
        assert all(org == live.a and email == OWNER for org, email, _ in rows)
        details = [d for _, _, d in rows]
        assert [(d["outcome"], d.get("status"), d.get("reason")) for d in details] == [
            ("ran", 200, None), ("refused", 404, "app_refused"), ("refused", None, "bad_args")]
        for d in details:
            assert d["uses"] == "knowledge.get_page" and d["class"] == "A"
        assert "Plan" not in json.dumps(details)

    async def test_a_class_b_door_op_and_an_off_door_row_run_nothing(self, live):
        r = await _door(live, OWNER, live.pkg, "propose", {"kind": "page"})
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "approval_required")
        r = await _door(live, OWNER, live.pkg, "search", {"q": "plan"})
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "not_in_catalog")

    async def test_a_paused_package_is_423_and_calls_nothing(self, live, monkeypatch):
        calls: list[Any] = []

        async def _never(*a: Any, **k: Any) -> Any:
            calls.append(a)
            raise AssertionError("a paused package called the app")

        monkeypatch.setattr(door_op, "_send", _never)
        r = await _door(live, OWNER, live.pkg_paused, "page", {"page_id": live.page})
        assert (r.status_code, r.json()["detail"]["code"]) == (423, "package_paused")
        assert calls == []
        [(_, _, d)] = _audits(live, live.pkg_paused)[-1:]
        assert d["reason"] == "package_paused"

    async def test_with_the_switch_off_the_door_op_is_source_not_open(self, live, monkeypatch):
        monkeypatch.setattr(get_settings(), "studio_door_ops_orgs", "")
        r = await _door(live, OWNER, live.pkg, "page", {"page_id": live.page})
        assert (r.status_code, r.json()["detail"]["code"]) == (403, "source_not_open")

    async def test_no_connection_is_in_a_transaction_during_the_call(self, live, monkeypatch):
        """While the app runs the call, the door holds no session. The probe
        reads ``pg_stat_activity`` as the superuser, at the start of the call."""
        seen: list[int] = []
        real = door_op._send

        async def _probe(app: Any, call: Any, headers: Any) -> Any:
            with live.admin.connect() as c:
                seen.append(int(c.execute(text(
                    "SELECT count(*) FROM pg_stat_activity WHERE usename = :u "
                    "AND datname = :d AND xact_start IS NOT NULL"),
                    {"u": _APP_ROLE, "d": live.dedicated}).scalar_one()))
            return await real(app, call, headers)

        monkeypatch.setattr(door_op, "_send", _probe)
        r = await _door(live, OWNER, live.pkg, "page", {"page_id": live.page})
        assert r.status_code == 200, r.text
        assert seen == [0]
