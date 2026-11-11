"""WS-PKG PKG-14a — the host door of the extension values on a real database (R8).

Spec: ``project-docs/specs/package_host.md`` §8.3 and the PKG-14a row of §10.
Code: ``gateway/package_host/host_door.py``, ``routes/projects/package_fields.py``
and ``routes/crm/package_fields.py``. The hermetic twin is
``tests/unit/test_package_host_door.py``.

The REAL ``/projects`` and ``/crm`` routers run as the app role of the phase-4
rehearsal (``acb_app_h3rls``: no superuser, no BYPASSRLS), on a promoted
catalog with two organizations, so FORCE RLS binds each statement. The
installs go through ``store.install`` and the values through
``field_values.set_value``. Each check of rows reads as the superuser.

The world. Org A has the members Ana and Ben, and the group ``grp`` holds Ana
only. Org B has Zed. Org A has six Studio packages, and org B has one:

* ``org``: an org install. ``priv``: a private install of Ana. ``grp``: a
  group install of ``grp``.
* ``ended``: an org install past its ``ends_at``. ``paused``: a paused org
  install. ``gone``: an org install of an archived app.
* ``b``: an org install of org B.

Each install declares one field on ``task`` and on ``crm.deal``, and holds a
value on each record. Task T1 is in a project that Ana has a grant on. Task
T2 is in a project with no grant, and Ben is its assignee. Deal D1 is a
native deal, and deal D2 comes from Zoho.

Run (one R8 suite at a time, because the ``promoted`` fixture drops
``<db>_h3rls``)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_package_host_door_r8.py -v -rs -p no:xdist

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (PKG-14a).**

1. A value of a private install of another member, of a group that the
   viewer is not in, of an ended or paused install or of an archived app is
   never read. A write to it is 404 and writes nothing.
2. A task that the viewer cannot see is 404, also with an org install. Both
   arms of the task rule open it: the grant and the assignee.
3. A write to a visible task stores the value, and ``null`` deletes it. A bad
   value and an undeclared key are 422, and write nothing.
4. The CRM access modes: ``admins`` (also with no row), ``everyone`` and
   ``groups``. A member without CRM access gets 403, and writes nothing.
5. ``assert_writable`` refuses a write to a deal from Zoho (409).
6. Org B reads and writes none of the values of org A.
7. An unbound session reads and writes none.
8. Each route is 404 while ``PACKAGE_HOST_ORGS`` leaves the org out.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("gateway.routes.crm", reason="gateway not installed")

from acb_common import get_settings
from fastapi import Request
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@pkg14a.test"
BEN = f"ben.{TAG}@pkg14a.test"
ZED = f"zed.{TAG}@pkg14a.test"
BOSS = f"boss.{TAG}@pkg14a.test"
GRP = f"group:grp{TAG}"
_SLUG_LIKE = f"pkg14a-{TAG}-%"
_EMAIL_LIKE = f"%.{TAG}@pkg14a.test"
TASK, DEAL = "task", "crm.deal"
#: The installs of the world, in the order of the module docstring.
NAMES = ("org", "priv", "grp", "ended", "paused", "gone", "b")
DEAD = ("ended", "paused", "gone")


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _key(name: str) -> str:
    return f"{name}_{TAG}"


def _principal(email: str, org: str, *perms: str) -> Any:
    from acb_auth import UserContext, UserRole, build_access

    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(perms)), organization_id=org)


# ── The app ────────────────────────────────────────────────────────────────

_APP: dict[str, Any] = {}


def _app() -> tuple[Any, dict[str, Any]]:
    """One app with both routers. The caller picks the user by a header."""
    if not _APP:
        from acb_auth import get_current_user
        from acb_common.db import bind_tenant
        from fastapi import FastAPI
        from gateway.routes import crm as crm_package
        from gateway.routes.projects import router as projects_router

        users: dict[str, Any] = {}

        async def _caller(request: Request) -> Any:
            user = users[request.headers["x-test-as"]]
            bind_tenant(user.organization_id)
            return user

        app = FastAPI()
        app.include_router(projects_router)
        app.include_router(crm_package.router)
        app.dependency_overrides[get_current_user] = _caller
        _APP.update(app=app, users=users)
    return _APP["app"], _APP["users"]


async def _call(world: Any, user: Any, method: str, path: str,
                body: dict[str, Any] | None = None) -> httpx.Response:
    from acb_common.db import clear_tenant, release_tenant

    app, users = _app()
    key = str(id(user))
    users[key] = user
    scope = clear_tenant()
    try:
        async with (
            tenant_engine_scope(_dsn(world.p.app_url)),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                              base_url="http://pkg14a.test") as client,
        ):
            return await client.request(method, path, json=body,
                                        headers={"x-test-as": key})
    finally:
        release_tenant(scope)


def _do(world: Any, user: Any, method: str, path: str,
        body: dict[str, Any] | None = None) -> httpx.Response:
    return asyncio.run(_call(world, user, method, path, body))


def _task_path(task: str, install: str | None = None, key: str | None = None) -> str:
    base = f"/projects/tasks/{task}/package-fields"
    return base if install is None else f"{base}/{install}/{key}"


def _deal_path(deal: str, install: str | None = None, key: str | None = None) -> str:
    base = f"/crm/deals/{deal}/package-fields"
    return base if install is None else f"{base}/{install}/{key}"


# ── The world ──────────────────────────────────────────────────────────────


def _admin(world: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    with world.p.admin_engine.begin() as c:
        result = c.execute(text(sql), params or {})
        return list(result.all()) if result.returns_rows else []


def _values(world: Any) -> list[tuple[Any, ...]]:
    """Every value of the installs of this run, read as the superuser."""
    return [tuple(r) for r in _admin(
        world,
        "SELECT v.install_id::text, v.record_kind, v.record_id::text, v.field_key, "
        "v.value::text FROM package_field_value AS v JOIN package_install AS i "
        "ON i.id = v.install_id JOIN apps AS a ON a.id = i.app_id "
        "WHERE a.slug LIKE :s ORDER BY 1, 2, 3, 4", {"s": _SLUG_LIKE})]


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """The world of the module docstring. Teardown deletes what it made."""
    from acb_common.db import tenant_session
    from gateway.package_host import field_values as fv
    from gateway.package_host import store
    from gateway.package_host.store import Contribution

    from tests.unit._crm_fakes import crm_switch_on

    monkeypatch.setattr(get_settings(), "package_host_orgs", "*")
    crm_switch_on(monkeypatch, "*")
    p = promoted
    a, b = p.org_a, p.org_b
    w = SimpleNamespace(p=p, a=a, b=b, apps={}, ids={}, made=[])
    app_of = {"org": "a1", "priv": "a2", "grp": "a3", "ended": "a4", "paused": "a5",
              "gone": "a6", "b": "b1"}

    with p.admin_engine.begin() as c:
        for name, org in (*((f"a{i}", a) for i in range(1, 7)), ("b1", b)):
            man = json.dumps({"studio": 1, "id": f"r-{name}-{TAG}", "name": "P",
                              "purpose": "T"})
            w.apps[name] = str(c.execute(text(
                "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
                "format, status, manifest) VALUES (:s, 'pkg14a', :o, '', :org, 'manifest', "
                "'live', CAST(:m AS jsonb)) RETURNING id"),
                {"s": f"pkg14a-{TAG}-{name}", "o": ANA, "org": org, "m": man}).scalar_one())
            c.execute(text(
                "INSERT INTO app_versions (app_id, version, manifest, bundle_html, "
                "bundle_sha256, published_by, organization_id) VALUES "
                "(CAST(:i AS uuid), 1, CAST(:m AS jsonb), '', 'sha', :by, CAST(:o AS uuid))"),
                {"i": w.apps[name], "m": man, "by": ANA, "o": org})
        users = {}
        for email, org in ((ANA, a), (BEN, a), (ZED, b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        gid = str(c.execute(text(
            "INSERT INTO org_group (organization_id, slug, display_name) "
            "VALUES (:org, :s, :s) RETURNING id"),
            {"org": a, "s": GRP.removeprefix("group:")}).scalar_one())
        c.execute(text(
            "INSERT INTO org_group_member (group_id, user_id, organization_id) "
            "VALUES (:g, :u, :org)"), {"g": gid, "u": users[ANA], "org": a})
        w.group = gid
        # Two projects, one lane each, and one task in each.
        w.p1, w.p2, w.s1, w.s2 = (str(uuid.uuid4()) for _ in range(4))
        w.t1, w.t2 = str(uuid.uuid4()), str(uuid.uuid4())
        for pid, lane in ((w.p1, w.s1), (w.p2, w.s2)):
            c.execute(text(
                "INSERT INTO pm_projects (id, organization_id, name, source, created_by, "
                "owns_statuses) VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :n, 'manual', "
                "'seed@pkg14a.test', true)"), {"p": pid, "o": a, "n": f"pkg14a {TAG}"})
            c.execute(text(
                "INSERT INTO pm_task_statuses (id, project_id, name, position, category, "
                "is_default) VALUES (CAST(:s AS uuid), CAST(:p AS uuid), 'To do', 10, "
                "'todo', true)"), {"s": lane, "p": pid})
        c.execute(text(
            "INSERT INTO pm_project_grants (project_id, organization_id, subject, created_by) "
            "VALUES (CAST(:p AS uuid), CAST(:o AS uuid), :s, 'seed@pkg14a.test')"),
            {"p": w.p1, "o": a, "s": ANA})
        number = c.execute(text("SELECT coalesce(max(task_number), 0) FROM pm_tasks")).scalar_one()
        for i, (tid, pid, lane) in enumerate(((w.t1, w.p1, w.s1), (w.t2, w.p2, w.s2)), 1):
            c.execute(text(
                "INSERT INTO pm_tasks (id, title, project_id, root_project_id, status_id, "
                "created_by, organization_id, task_number) VALUES (CAST(:t AS uuid), :title, "
                "CAST(:p AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), 'seed@pkg14a.test', "
                "CAST(:o AS uuid), :n)"),
                {"t": tid, "title": f"T{i} {TAG}", "p": pid, "s": lane, "o": a,
                 "n": number + i})
        c.execute(text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by, organization_id) "
            "VALUES (CAST(:t AS uuid), :e, 'seed@pkg14a.test', CAST(:o AS uuid))"),
            {"t": w.t2, "e": BEN, "o": a})
        # Two deals: D1 is native, and D2 comes from Zoho.
        w.stage = str(c.execute(text(
            "INSERT INTO crm_deal_statuses (organization_id, name, position, type) "
            "VALUES (CAST(:o AS uuid), :n, 1, 'open') RETURNING id"),
            {"o": a, "n": f"pkg14a {TAG}"}).scalar_one())
        w.d1, w.d2 = (str(c.execute(text(
            "INSERT INTO crm_deals (organization_id, name, status_id, ext_source, ext_id) "
            "VALUES (CAST(:o AS uuid), :n, CAST(:s AS uuid), :src, :ext) RETURNING id"),
            {"o": a, "n": f"D{i} {TAG}", "s": w.stage, "src": src,
             "ext": None if src is None else f"z-{TAG}"}).scalar_one())
            for i, src in ((1, None), (2, "zoho")))

    async def seed() -> None:
        now = datetime.now(UTC)
        for name in NAMES:
            org = b if name == "b" else a
            scope = {"priv": (ANA, "private"), "grp": (GRP, "group")}.get(name, ("org", "org"))
            claims = [Contribution(point="fields", key=f"{kind}:{_key(name)}",
                                   payload={"type": "date", "label": name})
                      for kind in (TASK, DEAL)]
            async with tenant_session(org) as db:
                got = await store.install(
                    db, app_id=w.apps[app_of[name]], version=1, scope_subject=scope[0],
                    scope_kind=scope[1], installed_by=ANA,
                    ends_at=now + timedelta(days=1) if name == "ended" else None,
                    contributions=claims)
                w.ids[name] = got.id
            async with tenant_session(org) as db:
                for kind, record in ((TASK, w.t1), (TASK, w.t2), (DEAL, w.d1), (DEAL, w.d2)):
                    await fv.set_value(db, install_id=got.id, record_kind=kind,
                                       record_id=record, field_key=_key(name),
                                       value="2027-01-31", install_ids=[got.id])

    async def go() -> None:
        async with tenant_engine_scope(_dsn(p.app_url)):
            await seed()

    asyncio.run(go())
    _admin(w, "UPDATE package_install SET ends_at = now() - interval '1 minute' "
              "WHERE id = CAST(:i AS uuid)", {"i": w.ids["ended"]})
    _admin(w, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
           {"i": w.ids["paused"]})
    _admin(w, "UPDATE apps SET status = 'archived' WHERE id = CAST(:a AS uuid)",
           {"a": w.apps["a6"]})
    assert len(_values(w)) == 4 * len(NAMES)
    w.ana, w.ben = (_principal(e, a, "feature:projects", "feature:crm") for e in (ANA, BEN))
    w.zed = _principal(ZED, b, "feature:projects", "feature:crm")
    w.boss = _principal(BOSS, a, "feature:crm", "admin:access:manage")
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM pm_projects WHERE id = ANY(CAST(:p AS uuid[]))"),
                  {"p": [w.p1, w.p2]})
        c.execute(text("DELETE FROM crm_deals WHERE id = ANY(CAST(:d AS uuid[]))"),
                  {"d": [w.d1, w.d2]})
        c.execute(text("DELETE FROM crm_deal_statuses WHERE id = CAST(:s AS uuid)"),
                  {"s": w.stage})
        c.execute(text("DELETE FROM crm_settings WHERE organization_id = ANY("
                       "CAST(:o AS uuid[]))"), {"o": [a, b]})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :s"), {"s": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE id = CAST(:g AS uuid)"), {"g": gid})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _keys(res: httpx.Response) -> dict[str, Any]:
    """The answer of a read, by field key, with the install and the value."""
    assert res.status_code == 200, res.text
    return {f["key"]: (f["install_id"], f["value"]) for f in res.json()["fields"]}


def _want(world: Any, *names: str) -> dict[str, Any]:
    return {_key(n): (world.ids[n], "2027-01-31") for n in names}


def _set_access(world: Any, access: str, groups: list[str], org: str | None = None,
                mode: str | None = None) -> None:
    _admin(world, "INSERT INTO crm_settings (organization_id, access, access_groups, mode) "
                  "VALUES (CAST(:o AS uuid), :a, :g, :m) ON CONFLICT (organization_id) DO "
                  "UPDATE SET access = EXCLUDED.access, access_groups = EXCLUDED.access_groups, "
                  "mode = EXCLUDED.mode",
           {"o": org or world.a, "a": access, "g": groups, "m": mode})


# ── 1. Which installs the viewer sees ──────────────────────────────────────


def test_r8_the_door_runs_as_a_role_that_rls_binds(world, app_engine) -> None:  # noqa: F811
    with app_engine.connect() as c:
        role = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles "
                              "WHERE rolname = current_user")).first()
    assert role is not None and not role[0] and not role[1]


def test_r8_ana_sees_the_org_her_private_and_her_group_values_only(world) -> None:
    got = _keys(_do(world, world.ana, "GET", _task_path(world.t1)))
    assert got == _want(world, "org", "priv", "grp")


def test_r8_ben_sees_only_the_org_value_on_the_task_he_is_assigned(world) -> None:
    """Ana's private install and the group of Ana hold values on T2. Ben is in
    neither, so the door never reads them for him."""
    got = _keys(_do(world, world.ben, "GET", _task_path(world.t2)))
    assert got == _want(world, "org")


def test_r8_a_write_to_a_hidden_or_dead_install_is_404_and_writes_nothing(world) -> None:
    before = _values(world)
    for name in ("priv", "grp", *DEAD, "b"):
        for value in ("2030-01-01", None):
            res = _do(world, world.ben, "PUT",
                      _task_path(world.t2, world.ids[name], _key(name)), {"value": value})
            assert res.status_code == 404, (name, value, res.text)
            assert res.json()["detail"]["code"] == "install_not_found", name
    # Ana sees her own private install, and still no dead one.
    for name in DEAD:
        res = _do(world, world.ana, "PUT",
                  _task_path(world.t1, world.ids[name], _key(name)), {"value": "2030-01-01"})
        assert res.status_code == 404, (name, res.text)
    assert _values(world) == before


# ── 2. The task rule ───────────────────────────────────────────────────────


def test_r8_a_task_the_viewer_cannot_see_is_404_with_an_org_install(world) -> None:
    before = _values(world)
    org, key = world.ids["org"], _key("org")
    for who, task in ((world.ben, world.t1), (world.ana, world.t2)):
        res = _do(world, who, "GET", _task_path(task))
        assert (res.status_code, res.json()["detail"]) == (404, "Task not found")
        res = _do(world, who, "PUT", _task_path(task, org, key), {"value": "2030-01-01"})
        assert (res.status_code, res.json()["detail"]) == (404, "Task not found")
    # The grant arm opens T1 for Ana, and the assignee arm opens T2 for Ben.
    assert _do(world, world.ana, "GET", _task_path(world.t1)).status_code == 200
    assert _do(world, world.ben, "GET", _task_path(world.t2)).status_code == 200
    assert _values(world) == before


def test_r8_a_write_to_a_visible_task_stores_and_null_deletes(world) -> None:
    org, key = world.ids["org"], _key("org")
    path = _task_path(world.t2, org, key)
    res = _do(world, world.ben, "PUT", path, {"value": "2028-02-29"})
    assert res.status_code == 200, res.text
    assert res.json() == {"install_id": org, "key": key, "value": "2028-02-29"}
    assert _keys(_do(world, world.ben, "GET", _task_path(world.t2)))[key] == (org, "2028-02-29")
    # Ana's value on T1 for the same install did not move.
    assert _keys(_do(world, world.ana, "GET", _task_path(world.t1)))[key][1] == "2027-01-31"
    before = _values(world)
    res = _do(world, world.ben, "PUT", path, {"value": "31/01/2027"})
    assert res.status_code == 422 and res.json()["detail"]["field"] == key, res.text
    res = _do(world, world.ben, "PUT", _task_path(world.t2, org, _key("nope")),
              {"value": "2028-02-29"})
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "field_not_declared"
    assert _do(world, world.ben, "PUT", path, {"other": 1}).status_code == 422
    assert _values(world) == before
    res = _do(world, world.ben, "PUT", path, {"value": None})
    assert res.status_code == 200 and res.json()["value"] is None
    assert _keys(_do(world, world.ben, "GET", _task_path(world.t2)))[key] == (org, None)
    assert len(_values(world)) == len(before) - 1


# ── 3. The CRM access modes ────────────────────────────────────────────────


def test_r8_with_admins_a_member_gets_403_and_writes_nothing(world) -> None:
    before = _values(world)
    path = _deal_path(world.d1, world.ids["org"], _key("org"))
    for setting in (None, ("admins", [])):
        if setting is not None:
            _set_access(world, *setting)
        assert _do(world, world.ben, "GET", _deal_path(world.d1)).status_code == 403
        assert _do(world, world.ben, "PUT", path, {"value": "2030-01-01"}).status_code == 403
        # The admin passes, and sees the org install only: no private install
        # and no group of the admin.
        assert _keys(_do(world, world.boss, "GET", _deal_path(world.d1))) == _want(world, "org")
    assert _values(world) == before


def test_r8_with_everyone_each_member_reads_the_winners_of_their_own(world) -> None:
    _set_access(world, "everyone", [])
    assert _keys(_do(world, world.ben, "GET", _deal_path(world.d1))) == _want(world, "org")
    assert _keys(_do(world, world.ana, "GET", _deal_path(world.d1))) == _want(
        world, "org", "priv", "grp")
    res = _do(world, world.ben, "PUT", _deal_path(world.d1, world.ids["org"], _key("org")),
              {"value": "2029-03-03"})
    assert res.status_code == 200, res.text
    res = _do(world, world.ben, "PUT", _deal_path(world.d1, world.ids["priv"], _key("priv")),
              {"value": "2029-03-03"})
    assert res.status_code == 404, res.text


def test_r8_with_groups_only_a_member_of_the_group_passes(world) -> None:
    _set_access(world, "groups", [GRP])
    assert _keys(_do(world, world.ana, "GET", _deal_path(world.d1))) == _want(
        world, "org", "priv", "grp")
    assert _do(world, world.ben, "GET", _deal_path(world.d1)).status_code == 403


def test_r8_a_deal_from_zoho_refuses_a_write_in_mirror_mode(world) -> None:
    _set_access(world, "everyone", [], mode="mirror")
    before = _values(world)
    res = _do(world, world.ben, "PUT", _deal_path(world.d2, world.ids["org"], _key("org")),
              {"value": "2030-01-01"})
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "mirrored_record"
    assert _values(world) == before
    # A read still shows the value.
    assert _keys(_do(world, world.ben, "GET", _deal_path(world.d2))) == _want(world, "org")


def test_r8_a_missing_deal_is_404(world) -> None:
    _set_access(world, "everyone", [])
    res = _do(world, world.ben, "GET", _deal_path(str(uuid.uuid4())))
    assert (res.status_code, res.json()["detail"]) == (404, "Deal not found")


# ── 4. Org B and an unbound session ────────────────────────────────────────


def test_r8_org_b_reads_and_writes_none_of_org_a(world) -> None:
    _set_access(world, "everyone", [], org=world.b)
    before = _values(world)
    for path in (_task_path(world.t1), _deal_path(world.d1)):
        assert _do(world, world.zed, "GET", path).status_code == 404, path
    for name in ("org", "b"):
        for path in (_task_path(world.t1, world.ids[name], _key(name)),
                     _deal_path(world.d1, world.ids[name], _key(name))):
            res = _do(world, world.zed, "PUT", path, {"value": "2030-01-01"})
            assert res.status_code == 404, (path, res.text)
    # The value of org B on the same record id never shows in org A.
    assert _key("b") not in _keys(_do(world, world.ana, "GET", _task_path(world.t1)))
    assert _values(world) == before


def test_r8_an_unbound_session_reads_and_writes_none(world) -> None:
    from acb_common.db import get_session_factory
    from gateway.package_host import host_door
    from gateway.package_host.store import InstallNotFound

    before = _values(world)

    async def go() -> tuple[Any, Any]:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            session = get_session_factory()()
            try:
                read = await host_door.read_fields(session, world.ana, record_kind=TASK,
                                                   record_id=world.t1)
                try:
                    await host_door.write_field(
                        session, world.ana, record_kind=TASK, record_id=world.t1,
                        install_id=world.ids["org"], field_key=_key("org"), value="2030-01-01")
                    wrote: Any = "written"
                except InstallNotFound as exc:
                    wrote = exc
                return read, wrote
            finally:
                await session.rollback()
                await session.close()

    read, wrote = asyncio.run(go())
    assert read == []
    assert isinstance(wrote, InstallNotFound), wrote
    assert _values(world) == before


# ── 5. The switch ──────────────────────────────────────────────────────────


def test_r8_each_route_is_404_while_the_flag_leaves_the_org_out(world, monkeypatch) -> None:
    _set_access(world, "everyone", [])
    monkeypatch.setattr(get_settings(), "package_host_orgs", world.b)
    before = _values(world)
    org, key = world.ids["org"], _key("org")
    for who, method, path in (
        (world.ana, "GET", _task_path(world.t1)),
        (world.ana, "PUT", _task_path(world.t1, org, key)),
        (world.ben, "GET", _deal_path(world.d1)),
        (world.ben, "PUT", _deal_path(world.d1, org, key)),
    ):
        res = _do(world, who, method, path, {"value": "2030-01-01"} if method == "PUT" else None)
        assert (res.status_code, res.json()["detail"]) == (404, "Not found"), path
    assert _values(world) == before
