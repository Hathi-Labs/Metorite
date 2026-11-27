"""The seed of ``studio:publish`` onto ``admin`` on a real database (STU-J1-2, R8).

Spec: ``project-docs/specs/ai_studio.md`` §9 item 3 and §14 J1, and the
owner's answer STU-Q4 (D180): "Owners and admins publish and can grant
publish." Metorite task 1707. The owner allowed the write to the role rows of
each live org on 2026-11-10. Code: the migration that seeds the capability,
found by CONTENT and never by number (R1), the real ``provision_org_roles``,
the real ``resolve_access``, the real override editor
(``admin/members.set_member_overrides``) and the real install of PKG-8b
(``studio/packages.install_package``), the one act that reads the verb today.

The checks run on the promoted phase-4 catalog of
``test_h3_rls_promotion_rehearsal``, as its NOSUPERUSER NOBYPASSRLS app role
with the org bound, so FORCE RLS binds. One check replays the plain ladder,
which has no tenancy column, so the other arm of the migration runs too.

**Acceptance (J1-2).**

a. The admin of each org holds ``studio:publish`` after the migration. The
   world takes the rows of the seed away first, as an org that was made
   before the migration has none, and then runs the file. The owner holds it
   through ``*``. A manager, a member and a guest do not.
b. Only the ``admin`` role of an org holds a ``studio:publish`` row, and each
   row carries the org of its role.
c. A new org from ``provision_org_roles`` gives the verb to its admin only.
d. The file runs twice with no second row, and the function holds the
   element once.
e. On the replayed ladder (no tenancy column), the admin of the first org
   holds one row, and a new org gets it too.
f. A table owner that is not a superuser runs the file under FORCE RLS. The
   tenancy arm binds each org in turn, so the WITH CHECK passes.
g. An owner grants the verb to one member through the override editor. The
   editor lists the verb before and after the grant. The member gets 403 on
   an org install before the grant and 201 after it.
h. An admin installs an app for the org with 201, and a manager gets 403.

**The mutations (R7).** Each ran on 2026-11-11, and each test named here
went red.

* M1: drop part 1 of the migration (the grant to each org). Six went red:
  ``test_the_admin_of_each_org_holds_the_share_verb``,
  ``test_only_the_admin_role_holds_a_row_of_the_verb``,
  ``test_the_file_runs_twice_and_grants_nothing_twice``,
  ``test_the_seed_on_the_replayed_ladder``,
  ``test_a_table_owner_under_force_rls_runs_the_file`` and
  ``test_an_admin_installs_for_the_org_and_a_manager_gets_403``.
* M2: drop the ``EXECUTE out;`` of part 2 (the rewrite of the function).
  Three went red: ``test_a_new_org_gets_the_verb_on_its_admin_only``,
  ``test_the_file_runs_twice_and_grants_nothing_twice`` and
  ``test_the_seed_on_the_replayed_ladder``.
* M3: drop the ``set_config`` of the tenancy arm. One went red, with "new row
  violates row-level security policy":
  ``test_a_table_owner_under_force_rls_runs_the_file``.

Each mutation ran on a NEW ladder database. The ``replayed`` fixture applies
the ladder over the database of ``TENANT_LADDER_DATABASE_URL``, so a row
from an earlier run there stays.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_publish_seed_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant
from fastapi import HTTPException, Response
from gateway.routes.admin import members
from gateway.routes.studio import packages
from sqlalchemy import create_engine, text

from tests.unit._studio_approval import approve_live
from tests.unit._tenant_ladder import apply_ladder, tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    _URL,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"

VERB = "studio:publish"
TAG = uuid.uuid4().hex[:8]
_EMAIL_LIKE = f"%.{TAG}@stuj12.test"
_SLUG_LIKE = f"j12{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)

#: The people of the two orgs: a key, the org ("a" or "b") and a system role.
PEOPLE: dict[str, tuple[str, str]] = {
    "owner": ("a", "owner"),
    "admin": ("a", "admin"),
    "manager": ("a", "manager"),
    "member": ("a", "member"),
    "guest": ("a", "guest"),
    "grantee": ("a", "member"),
    "admin_b": ("b", "admin"),
    "member_b": ("b", "member"),
}

#: A table owner that is not a superuser, for check f. Cluster-wide, so the
#: name is private to this suite. It gets rights in the module database only,
#: and the fixture drops that database.
_OWNER_ROLE = "stuj12_seed_owner"
_OWNER_PW = "stuj12_scratch_pw"


#: The first org of the ladder. 130 seeds its roles, not the function.
_DEFAULT_ORG_SQL = "SELECT id::text FROM organization WHERE slug = 'default'"


def _email(key: str) -> str:
    return f"{key}.{TAG}@stuj12.test"


def _migration() -> Path:
    """The J1-2 migration, found by content (R1)."""
    found = [
        p for p in sorted(_MIGRATIONS.glob("*.sql"))
        if p.name != "schema.generated.sql"
        and "the seed of `studio:publish` onto `admin`" in p.read_text(encoding="utf-8")
    ]
    assert len(found) == 1, [p.name for p in found]
    return found[0]


def _run_file(engine: Any) -> None:
    """Run the migration file verbatim, as the runner of the box does."""
    sql = _migration().read_text(encoding="utf-8")
    with engine.connect() as c:
        with c.connection.dbapi_connection.cursor() as cur:
            cur.execute(sql)
        c.connection.dbapi_connection.commit()


def _take_the_seed_away(engine: Any, orgs: list[str]) -> None:
    """The state of an org that was made before the migration."""
    with engine.begin() as c:
        c.execute(text(
            "DELETE FROM org_role_permission p USING org_role r "
            "WHERE p.role_id = r.id AND p.permission = :v "
            "AND r.organization_id = ANY(CAST(:o AS uuid[]))"), {"v": VERB, "o": orgs})


def _held(conn: Any, org: str) -> dict[str, set[str]]:
    return {
        row[0]: set(row[1] or [])
        for row in conn.execute(
            text("SELECT r.slug, array_agg(p.permission) FROM org_role r "
                 "LEFT JOIN org_role_permission p ON p.role_id = r.id "
                 "WHERE r.organization_id = CAST(:o AS uuid) GROUP BY r.slug"),
            {"o": org},
        )
    }


def _assert_admin_only(held: dict[str, set[str]]) -> None:
    assert VERB in held["admin"], f"admin does not hold {VERB}"
    for role in ("manager", "member", "guest", "agent_service"):
        assert VERB not in held.get(role, set()), f"{role} holds {VERB}"
    assert held["owner"] == {"*"}


def _function_source(conn: Any) -> str:
    return str(conn.execute(text(
        "SELECT pg_get_functiondef(p.oid) FROM pg_proc p "
        "WHERE p.proname = 'provision_org_roles' LIMIT 1")).scalar())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811 — a fixture
    """Org A and org B with the real system roles. The fixture takes the rows
    of the seed away and runs the migration file, so check a reads the work
    of part 1 and not of the function."""
    p = promoted
    orgs = {"a": p.org_a, "b": p.org_b}
    with p.admin_engine.begin() as c:
        for org in orgs.values():
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            c.execute(text("SELECT provision_org_roles(:o)"), {"o": org})
    _take_the_seed_away(p.admin_engine, list(orgs.values()))
    with p.admin_engine.connect() as c:
        for org in orgs.values():
            assert VERB not in _held(c, org)["admin"]
    _run_file(p.admin_engine)
    with p.admin_engine.begin() as c:
        roles = {(str(org), str(slug)): str(rid) for org, slug, rid in c.execute(text(
            "SELECT organization_id, slug, id FROM org_role "
            "WHERE organization_id = ANY(CAST(:o AS uuid[]))"),
            {"o": list(orgs.values())}).all()}
        for key, (org_key, role) in PEOPLE.items():
            org = orgs[org_key]
            uid = c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', CAST(:o AS uuid)) RETURNING id"),
                {"e": _email(key), "o": org}).scalar()
            c.execute(text(
                "INSERT INTO user_role (user_id, role_id, organization_id) "
                "VALUES (:u, CAST(:r AS uuid), CAST(:o AS uuid))"),
                {"u": uid, "r": roles[(org, role)], "o": org})
    yield SimpleNamespace(p=p, orgs=orgs, a=p.org_a, b=p.org_b)
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


@pytest.fixture(autouse=True)
def _resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The role leg reads ``app_user`` on a bound session (the cutover path),
    and an earlier suite cannot leave the resolver degraded."""
    import acb_auth.access as access_mod

    monkeypatch.setenv("IDENTITY_CUTOVER", "1")
    monkeypatch.delenv("ACCESS_LEGACY_FALLBACK", raising=False)
    monkeypatch.setattr(access_mod, "_tables_missing", False)
    access_mod.invalidate()


def _run(world: Any, fn: Callable[[Any], Awaitable[Any]]) -> Any:
    """Run ``fn(resolve)`` on the app role's engine. ``resolve(key)`` gives
    the member of *key* through the real ``resolve_access``."""
    import acb_auth.access as access_mod

    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            async def resolve(key: str) -> UserContext:
                org = world.orgs[PEOPLE[key][0]]
                token = bind_tenant(org)
                try:
                    access = await access_mod.resolve_access(_email(key), use_cache=False)
                finally:
                    release_tenant(token)
                return UserContext(email=_email(key), role=UserRole.EMPLOYEE,
                                   access=access, organization_id=org)

            return await fn(resolve)

    return asyncio.run(go())


# ── a, b. The grant to each org that exists ───────────────────────────────


def test_the_admin_of_each_org_holds_the_share_verb(world) -> None:
    async def fn(resolve: Any) -> dict[str, tuple[bool, bool]]:
        out: dict[str, tuple[bool, bool]] = {}
        for key in PEOPLE:
            user = await resolve(key)
            out[key] = (user.access.is_active, user.has_permission(VERB))
        return out

    got = _run(world, fn)
    assert got == {
        "owner": (True, True), "admin": (True, True), "manager": (True, False),
        "member": (True, False), "guest": (True, False), "grantee": (True, False),
        "admin_b": (True, True), "member_b": (True, False),
    }, got


def test_only_the_admin_role_holds_a_row_of_the_verb(world) -> None:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT r.organization_id::text, r.slug, p.organization_id::text "
            "FROM org_role_permission p JOIN org_role r ON r.id = p.role_id "
            "WHERE p.permission = :v"), {"v": VERB}).all()
        first = c.execute(text(_DEFAULT_ORG_SQL)).scalar()
    by_org = {(str(o), str(s)) for o, s, _ in rows}
    assert {(world.a, "admin"), (world.b, "admin")} <= by_org, rows
    assert {s for _, s, _ in rows} == {"admin"}, rows
    assert all(role_org == row_org for role_org, _, row_org in rows), rows
    assert len(rows) == len(by_org), "an admin role holds the verb twice"
    # 130 seeded the first org of the ladder, not the function, so only part 1
    # gives its admin the row.
    assert (str(first), "admin") in by_org, rows


# ── c, d. A new org, and a second run ─────────────────────────────────────


def test_a_new_org_gets_the_verb_on_its_admin_only(world) -> None:
    with world.p.admin_engine.connect() as c:
        trans = c.begin()
        try:
            org = c.execute(text(
                "SELECT provision_organization('stuj12-new', 'New Org', "
                "'chief@stuj12-new.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()


def test_the_file_runs_twice_and_grants_nothing_twice(world) -> None:
    for _ in range(2):
        _run_file(world.p.admin_engine)
    with world.p.admin_engine.connect() as c:
        counts = c.execute(text(
            "SELECT r.organization_id::text, count(*) FROM org_role_permission p "
            "JOIN org_role r ON r.id = p.role_id "
            "WHERE p.permission = :v GROUP BY r.organization_id"), {"v": VERB}).all()
        src = _function_source(c)
    assert counts and all(n == 1 for _, n in counts), counts
    assert src.count(f"'{VERB}'") == 1


# ── e. The plain ladder, the arm with no tenancy column ───────────────────


@pytest.fixture(scope="module")
def replayed():
    """The plain ladder, applied twice: no tenancy column, the other arm."""
    eng = create_engine(_URL, future=True)
    with eng.begin() as conn:
        apply_ladder(conn)
    with eng.begin() as conn:
        apply_ladder(conn)
    yield eng
    eng.dispose()


def test_the_seed_on_the_replayed_ladder(replayed) -> None:
    with replayed.connect() as c:
        trans = c.begin()
        try:
            first = c.execute(text(_DEFAULT_ORG_SQL)).scalar()
            _assert_admin_only(_held(c, first))
            rows = c.execute(text(
                "SELECT count(*) FROM org_role_permission p "
                "JOIN org_role r ON r.id = p.role_id WHERE r.slug = 'admin' "
                "AND p.permission = :v AND r.organization_id = CAST(:o AS uuid)"),
                {"v": VERB, "o": first}).scalar()
            assert rows == 1, "a replay granted the capability twice"
            org = c.execute(text(
                "SELECT provision_organization('stuj12-ladder', 'Ladder Org', "
                "'chief@stuj12-ladder.test')")).scalar_one()
            _assert_admin_only(_held(c, str(org)))
        finally:
            trans.rollback()


# ── f. A table owner that is not a superuser ──────────────────────────────


def test_a_table_owner_under_force_rls_runs_the_file(world) -> None:
    """The tenancy arm binds each org in turn. Without the bind, the WITH
    CHECK of ``org_role_permission`` refuses the first row for this owner."""
    p = world.p
    with p.admin_engine.connect() as c:
        was = str(c.execute(text(
            "SELECT tableowner FROM pg_tables WHERE tablename = 'org_role_permission'")).scalar())
        forced = c.execute(text(
            "SELECT relforcerowsecurity FROM pg_class "
            "WHERE relname = 'org_role_permission'")).scalar()
        database = str(c.execute(text("SELECT current_database()")).scalar())
    assert forced is True
    with p.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
        c.execute(text(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = "
            f"'{_OWNER_ROLE}') THEN CREATE ROLE {_OWNER_ROLE} LOGIN; END IF; END $$;"))
        c.execute(text(
            f"ALTER ROLE {_OWNER_ROLE} WITH LOGIN PASSWORD '{_OWNER_PW}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"))
        c.execute(text(f'GRANT CONNECT ON DATABASE "{database}" TO {_OWNER_ROLE}'))
        c.execute(text(f"GRANT USAGE ON SCHEMA public TO {_OWNER_ROLE}"))
        c.execute(text(f"GRANT SELECT ON organization, org_role TO {_OWNER_ROLE}"))
        c.execute(text(f"ALTER TABLE org_role_permission OWNER TO {_OWNER_ROLE}"))
    _take_the_seed_away(p.admin_engine, [world.a, world.b])
    owner = create_engine(p.app_url.set(username=_OWNER_ROLE, password=_OWNER_PW),
                          future=True)
    try:
        with owner.connect() as c:
            role = c.execute(text(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user")).first()
        assert role is not None and not role[0] and not role[1]
        _run_file(owner)
        with p.admin_engine.connect() as c:
            held = {org: _held(c, org) for org in (world.a, world.b)}
    finally:
        owner.dispose()
        with p.admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as c:
            c.execute(text(f'ALTER TABLE org_role_permission OWNER TO "{was}"'))
        # The later checks read the seed, so put it back whatever happened.
        _run_file(p.admin_engine)
    for org in (world.a, world.b):
        _assert_admin_only(held[org])


# ── g, h. The grant path and the act that reads the verb ──────────────────


class _Request:
    """The body stream that a package handler reads."""

    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


def _manifest() -> dict[str, Any]:
    return {
        "studio": 1, "id": f"j12{TAG}-app{next(_COUNTER)}", "name": "Site visits",
        "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


async def _handler(route: str, user: UserContext, pid: str | None = None,
                   payload: Any = None) -> tuple[int, Any]:
    """Call the real package handler *route* as *user*, with the org bound."""
    kwargs: dict[str, Any] = {"user": user, "request": _Request(payload)}
    if pid is not None:
        kwargs["package_id"] = pid
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(user.organization_id)
    try:
        got = await getattr(packages, route)(**kwargs)
        default = 201 if route == "install_package" else 200
        return response.status_code or default, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _live_package(user: UserContext) -> str:
    """A new package of *user*, published once. The build needs
    ``feature:studio`` and ownership only (STU-Q4b)."""
    builder = UserContext(email=user.email, role=UserRole.EMPLOYEE,
                          access=build_access(list(STUDIO)),
                          organization_id=user.organization_id)
    status, got = await _handler("create_package", builder, payload=_manifest())
    assert status == 200, got
    pid = str(got["package"]["id"])
    status, got = await _handler("publish", builder, pid, payload={})
    assert status == 200, got
    return pid


async def _install_for_the_org(user: UserContext) -> tuple[int, Any]:
    pid = await _live_package(user)
    # WS-STU J2b-1 (path A): an org install needs the live version approved by
    # another holder. The ask and the approval go through the one writer.
    await approve_live(user.organization_id, user.email, pid, f"approver.{pid}@j2b1.test")
    return await _handler("install_package", user, pid, payload={"scope": "org"})


@pytest.fixture
def _flags(monkeypatch: pytest.MonkeyPatch, world) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", world.a)


def _verb_row(access_out: dict[str, Any]) -> dict[str, Any]:
    rows = [c for c in access_out["capabilities"] if c["permission"] == VERB]
    assert len(rows) == 1, access_out["capabilities"]
    return rows[0]


def test_an_owner_grants_the_verb_to_a_member_through_the_override_editor(
        world, _flags, monkeypatch: pytest.MonkeyPatch) -> None:
    audit: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(members, "record_admin_change",
                        lambda actor, action, target, **kw: audit.append((action, kw)))

    async def fn(resolve: Any) -> dict[str, Any]:
        owner = await resolve("owner")
        out: dict[str, Any] = {}
        token = bind_tenant(world.a)
        try:
            out["listed_before"] = _verb_row(await members.get_member_access(
                _email("grantee"), owner))
        finally:
            release_tenant(token)
        out["install_before"] = await _install_for_the_org(await resolve("grantee"))
        token = bind_tenant(world.a)
        try:
            out["after_write"] = _verb_row(await members.set_member_overrides(
                _email("grantee"),
                members.OverrideRequest(overrides=[members.OverrideEntry(
                    permission=VERB, effect="allow", reason="J1-2 R8")]),
                admin=owner))
        finally:
            release_tenant(token)
        grantee = await resolve("grantee")
        out["holds"] = grantee.has_permission(VERB)
        out["install_after"] = await _install_for_the_org(grantee)
        out["member_holds"] = (await resolve("member")).has_permission(VERB)
        return out

    got = _run(world, fn)
    assert got["listed_before"]["allowed"] is False, got
    status, detail = got["install_before"]
    assert status == 403 and detail["code"] == packages.FORBIDDEN, got
    assert got["after_write"]["allowed"] is True, got
    assert got["after_write"]["source"] == "allow-override", got
    assert got["holds"] is True, got
    status, body = got["install_after"]
    assert status == 201 and body["install"]["scope_kind"] == "org", got
    assert got["member_holds"] is False, got
    assert audit == [("org.member_overrides_set", {"overrides": [
        {"permission": VERB, "effect": "allow", "reason": "J1-2 R8"}]})], audit


def test_an_admin_installs_for_the_org_and_a_manager_gets_403(world, _flags) -> None:
    async def fn(resolve: Any) -> dict[str, Any]:
        owner = await resolve("owner")
        token = bind_tenant(world.a)
        try:
            listed = _verb_row(await members.get_member_access(_email("admin"), owner))
        finally:
            release_tenant(token)
        return {"listed": listed,
                "admin": await _install_for_the_org(await resolve("admin")),
                "manager": await _install_for_the_org(await resolve("manager"))}

    got = _run(world, fn)
    assert got["listed"]["allowed"] is True, got
    assert (got["listed"]["source"], got["listed"]["via_role"]) == ("role", "admin"), got
    status, body = got["admin"]
    assert status == 201 and body["install"]["scope_kind"] == "org", got
    status, detail = got["manager"]
    assert status == 403 and detail["code"] == packages.FORBIDDEN, got
