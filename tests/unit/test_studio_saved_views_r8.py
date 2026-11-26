"""WS-STU AF-10a — the saved views of a Studio app, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 AF-10a. Code:
``gateway/routes/studio/views.py``, ``gateway/studio/views_store.py`` and the
migration that adds ``studio_saved_views`` (441 at build). The hermetic twin
is ``tests/unit/test_studio_saved_views.py``.

The real package, install and view handlers run on a phase-4 promoted scratch
database, as the non-privileged app role, so FORCE RLS binds each statement.
Each call binds its org as the auth layer does. Org A has Owen (the owner, a
holder of ``studio:publish``), Ana and Ben in ``ops``, and Cat in ``eng``.
Org B has Zed. Owen installs his app for ``ops`` at version 1, then publishes
version 2 with the new field ``cost``. The group install stays at version 1,
so ``cost`` is a key that Ana and Ben may not read.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_saved_views_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**R7: red first, then mutate.** Each mutation ran on 2026-11-13, and each named test went red (1 failed).

* M1. ``views_store._LIST_SQL``: drop ``(shared OR owner_email = :me)``.
  ``test_r8_a_private_view_reads_for_its_owner_only`` goes red.
* M2. ``views.update_view``: drop the ``read_only`` check of a viewer.
  ``test_r8_only_the_owner_changes_a_shared_view`` goes red.
* M3. ``views.list_views``: return the views of a viewer whole.
  ``test_r8_a_viewer_reads_a_shared_view_with_the_hidden_parts_dropped``
  goes red.
* M4. ``members._PURGE_DELETES``: drop ``NOT`` from the clause of
  ``studio_private_views``. ``test_r8_a_member_purge_deletes_the_private_views_only``
  goes red.

Fix round 1 (2026-11-13). Each mutation below ran, and each named test went red.

* M1a. ``_LIST_SQL`` without the read filter: the private-view test and the
  owner-only test (2 failed). M1b. ``_GET_SQL`` without it: the 404 test of
  patch and delete (1 failed).
* M5. ``_GET_SQL`` without the package filter:
  ``test_r8_a_view_of_another_package_is_404_on_this_path`` (1 failed).
* M8. ``objects_store.query_hidden_keys`` returns ``frozenset()``: the roles
  probe here and two hermetic cases (3 failed).
* M9. The object check of a create removed: two cases here and one hermetic
  case (3 failed).
* M10 and M11 (hermetic). ``project_spec`` starts from ``dict(spec)``, and the
  audit of a change leaves the session: one hermetic test each.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from fastapi import HTTPException, Response
from gateway.routes.studio import packages, views
from gateway.studio import views_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@af10.test"
#: Mixed case in ``app_user`` and in the session, so each read folds it.
ANA = f"Ana.{TAG}@af10.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@af10.test"
CAT = f"cat.{TAG}@af10.test"
ZED = f"zed.{TAG}@af10.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_EMAIL_LIKE = f"%.{TAG}@af10.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"af10{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
NOT_FOUND = "Not found"
#: The value that the owner types about ``cost``, a key that a viewer of
#: version 1 may not read. It must never reach that viewer.
SECRET = 98765
_COST = {"key": "cost", "type": "number"}
#: An object of version 2 only. A viewer of version 1 does not read it.
_INVOICE = {"name": "invoice", "fields": [{"key": "amount", "type": "number"}]}


def _manifest(mid: str, cost: bool = False) -> dict[str, Any]:
    fields = [{"key": "site", "type": "text"}, {"key": "status", "type": "text"}]
    return {
        "studio": 1, "id": mid, "name": "Site visits", "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [*fields, *([_COST] if cost else [])]},
                    *([_INVOICE] if cost else [])],
        "actions": [{"id": "find", "uses": "object:visit.list"}],
    }


def _mid() -> str:
    return f"af10{TAG}-app{next(_COUNTER)}"


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    def __init__(self, payload: Any = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def stream(self) -> Any:
        yield self._raw


async def _bound(org: str, fn: Callable[[], Awaitable[Any]], ok: int = 200) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        return ok, await fn()
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _pkg(route: str, org: str, email: str, pid: str | None = None,
               payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package",
                 "delete_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route in ("install_package", "delete_package"):
        kwargs["response"] = response
    status, got = await _bound(org, lambda: handler(**kwargs))
    return (response.status_code or status) if status == 200 else status, got


async def _list(org: str, email: str, pid: str, obj: str = "visit") -> tuple[int, Any]:
    return await _bound(org, lambda: views.list_views(pid, user=_user(org, email),
                                                       object_name=obj))


async def _create(org: str, email: str, pid: str, body: dict[str, Any]) -> tuple[int, Any]:
    return await _bound(org, lambda: views.create_view(
        pid, request=_Request(body), user=_user(org, email)), ok=201)


async def _patch(org: str, email: str, pid: str, vid: str,
                 body: dict[str, Any]) -> tuple[int, Any]:
    return await _bound(org, lambda: views.update_view(
        pid, vid, request=_Request(body), user=_user(org, email)))


async def _delete(org: str, email: str, pid: str, vid: str) -> tuple[int, Any]:
    return await _bound(org, lambda: views.delete_view(pid, vid, user=_user(org, email)))


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _shared_spec() -> dict[str, Any]:
    return {"filter": [{"field": "site", "op": "eq", "value": "Plant 4"},
                       {"field": "cost", "op": "gt", "value": SECRET}],
            "sort": {"field": "cost", "dir": "desc"}, "layout": "table",
            "columns": ["site", "cost"]}


async def _app(world: Any) -> str:
    """A new app of Owen: v1 installed for ``ops``, then v2 with ``cost``."""
    mid = _mid()
    status, got = await _pkg("create_package", world.a, OWEN, payload=_manifest(mid))
    assert status == 200, got
    pid = str(got["package"]["id"])
    assert (await _pkg("publish", world.a, OWEN, pid, payload={}))[0] == 200
    status, got = await _pkg("install_package", world.a, OWEN, pid, payload={"scope": OPS},
                             grants=PUBLISHER)
    assert status == 200, got
    assert (await _pkg("save_draft", world.a, OWEN, pid,
                       payload=_manifest(mid, cost=True)))[0] == 200
    assert (await _pkg("publish", world.a, OWEN, pid, payload={}))[0] == 200
    return pid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B with Studio and the package host on, the members and
    the groups, and one app of Owen with one shared view."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    monkeypatch.setattr(get_settings(), "package_host_orgs", f"{p.org_a},{p.org_b}")
    with p.admin_engine.begin() as c:
        users: dict[str, str] = {}
        for email, org in ((OWEN, p.org_a), (ANA, p.org_a), (BEN, p.org_a), (CAT, p.org_a),
                           (ZED, p.org_b)):
            users[email] = str(c.execute(text(
                "INSERT INTO app_user (email, display_name, role, status, organization_id) "
                "VALUES (:e, :e, 'employee', 'active', :org) RETURNING id"),
                {"e": email, "org": org}).scalar_one())
        for group, org, members in ((OPS, p.org_a, (OWEN, ANA, BEN)), (ENG, p.org_a, (CAT,)),
                                    (f"group:ext{TAG}", p.org_b, (ZED,))):
            gid = str(c.execute(text(
                "INSERT INTO org_group (organization_id, slug, display_name) "
                "VALUES (:org, :s, :s) RETURNING id"),
                {"org": org, "s": group.removeprefix("group:")}).scalar_one())
            for email in members:
                c.execute(text(
                    "INSERT INTO org_group_member (group_id, user_id, organization_id) "
                    "VALUES (:g, :u, :org)"), {"g": gid, "u": users[email], "org": org})
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)

    async def setup() -> None:
        w.pid = await _app(w)
        status, got = await _create(w.a, OWEN, w.pid, {
            "object": "visit", "name": "Big visits", "shared": True, "spec": _shared_spec()})
        assert status == 201, got
        w.shared = got["view"]["id"]

    _run(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("studio_saved_views", "package_id"), ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _rows(world: Any, pid: str) -> list[dict[str, Any]]:
    """The view rows of *pid*, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, organization_id::text, owner_email, name, shared "
            "FROM studio_saved_views WHERE package_id = CAST(:p AS uuid) ORDER BY name"),
            {"p": pid}).all()
    return [{"id": r[0], "org": r[1], "owner": r[2], "name": r[3], "shared": r[4]}
            for r in rows]


def _names(got: Any) -> list[str]:
    return sorted(v["name"] for v in got["views"])


# ── The table ─────────────────────────────────────────────────────────────


def test_r8_the_table_forces_rls_and_takes_the_org_of_the_session(world) -> None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'studio_saved_views'::regclass")).one()
    assert tuple(row) == (True, True)
    [only] = _rows(world, world.pid)
    assert only["org"] == world.a and only["owner"] == OWEN and only["shared"] is True


def test_r8_a_view_of_org_a_never_reads_in_org_b(world) -> None:
    async def go() -> dict[str, Any]:
        async with tenant_session(world.b) as db:
            seen = int((await db.execute(text(
                "SELECT count(*) FROM studio_saved_views"))).scalar_one())
            got = await views_store.get_view(db, OWEN, package_id=world.pid,
                                             view_id=world.shared)
        return {"seen": seen, "got": got,
                "list": await _list(world.b, ZED, world.pid),
                "patch": await _patch(world.b, ZED, world.pid, world.shared, {"name": "x"}),
                "delete": await _delete(world.b, ZED, world.pid, world.shared)}

    got = _run(world, go)
    assert got["seen"] == 0 and got["got"] is None
    for case in ("list", "patch", "delete"):
        assert got[case] == (404, NOT_FOUND), case
    assert [r["name"] for r in _rows(world, world.pid)] == ["Big visits"]


def test_r8_a_member_with_no_install_gets_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _list(world.a, CAT, world.pid),
                await _create(world.a, CAT, world.pid, {"object": "visit", "name": "Mine"}),
                await _patch(world.a, CAT, world.pid, world.shared, {"name": "x"}),
                await _delete(world.a, CAT, world.pid, world.shared)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 4
    assert len(_rows(world, world.pid)) == 1


# ── Private views ─────────────────────────────────────────────────────────


def test_r8_a_private_view_reads_for_its_owner_only(world) -> None:
    async def go() -> dict[str, Any]:
        made = await _create(world.a, ANA, world.pid, {
            "object": "visit", "name": "My visits",
            "spec": {"filter": [{"field": "site", "op": "eq", "value": "Plant 9"}]}})
        mine = await _create(world.a, OWEN, world.pid, {"object": "visit", "name": "Owen's"})
        return {"made": made, "mine": mine, "ana": await _list(world.a, ANA, world.pid),
                "ben": await _list(world.a, BEN, world.pid),
                "owen": await _list(world.a, OWEN, world.pid)}

    got = _run(world, go)
    status, made = got["made"]
    assert status == 201 and made["view"]["shared"] is False and made["view"]["mine"] is True
    assert _names(got["ana"][1]) == ["Big visits", "My visits"]
    assert _names(got["ben"][1]) == ["Big visits"]
    assert _names(got["owen"][1]) == ["Big visits", "Owen's"]
    rows = {r["name"]: r for r in _rows(world, world.pid)}
    # The owner is the address of the session, in lower case.
    assert rows["My visits"]["owner"] == ANA_LOW and rows["My visits"]["org"] == world.a


def test_r8_another_member_s_private_view_is_404_on_patch_and_delete(world) -> None:
    async def go() -> dict[str, Any]:
        status, made = await _create(world.a, ANA, world.pid, {"object": "visit",
                                                               "name": "Ana's"})
        assert status == 201, made
        vid = made["view"]["id"]
        return {"vid": vid,
                "owen_patch": await _patch(world.a, OWEN, world.pid, vid, {"name": "Taken"}),
                "owen_delete": await _delete(world.a, OWEN, world.pid, vid),
                "ben_patch": await _patch(world.a, BEN, world.pid, vid, {"name": "Taken"}),
                "ben_delete": await _delete(world.a, BEN, world.pid, vid),
                "ana_patch": await _patch(world.a, ANA, world.pid, vid, {"name": "Ana's 2"}),
                "ana_delete": await _delete(world.a, ANA, world.pid, vid)}

    got = _run(world, go)
    for case in ("owen_patch", "owen_delete", "ben_patch", "ben_delete"):
        assert got[case] == (404, NOT_FOUND), case
    assert got["ana_patch"][0] == 200 and got["ana_patch"][1]["view"]["name"] == "Ana's 2"
    assert got["ana_delete"] == (200, {"deleted": True, "id": got["vid"]})
    assert [r["name"] for r in _rows(world, world.pid)] == ["Big visits"]


def test_r8_a_viewer_saves_a_private_view_and_cannot_share(world) -> None:
    async def go() -> dict[str, Any]:
        shared = await _create(world.a, ANA, world.pid, {"object": "visit", "name": "Team",
                                                         "shared": True})
        status, made = await _create(world.a, ANA, world.pid, {"object": "visit",
                                                               "name": "Mine"})
        assert status == 201, made
        share = await _patch(world.a, ANA, world.pid, made["view"]["id"], {"shared": True})
        return {"shared": shared, "share": share}

    got = _run(world, go)
    for case in ("shared", "share"):
        status, detail = got[case]
        assert status == 403 and detail["code"] == "read_only", (case, detail)
    rows = {r["name"]: r for r in _rows(world, world.pid)}
    assert set(rows) == {"Big visits", "Mine"} and rows["Mine"]["shared"] is False


# ── Shared views ──────────────────────────────────────────────────────────


def test_r8_a_viewer_reads_a_shared_view_with_the_hidden_parts_dropped(world) -> None:
    """``cost`` is a field of version 2, and the group install of Ana is at
    version 1. Ana reads the shared view with the condition on ``cost``, the
    sort and the column dropped, and ``dropped`` is 3. The value never reaches
    her. Owen reads the view whole."""
    async def go() -> dict[str, Any]:
        return {"ana": await _list(world.a, ANA, world.pid),
                "owen": await _list(world.a, OWEN, world.pid)}

    got = _run(world, go)
    status, ana = got["ana"]
    assert status == 200, ana
    [view] = ana["views"]
    assert view["shared"] is True and view["mine"] is False and view["dropped"] == 3
    assert view["spec"] == {"filter": [{"field": "site", "op": "eq", "value": "Plant 4"}],
                            "sort": None, "layout": "table", "columns": ["site"]}
    seen = json.dumps(ana)
    assert str(SECRET) not in seen and "cost" not in seen and OWEN not in seen
    status, owen = got["owen"]
    [whole] = owen["views"]
    assert whole["dropped"] == 0 and whole["spec"] == _shared_spec() and whole["mine"] is True


def test_r8_a_view_of_an_object_that_the_version_lacks_drops_everything(world) -> None:
    """``invoice`` is an object of version 2. Owen shares a view of it. Ana,
    at version 1, reads it with each part dropped, and cannot save a view of
    it herself."""
    async def go() -> dict[str, Any]:
        status, made = await _create(world.a, OWEN, world.pid, {
            "object": "invoice", "name": "Big invoices", "shared": True,
            "spec": {"filter": [{"field": "amount", "op": "gt", "value": SECRET}],
                     "columns": ["amount"]}})
        assert status == 201, made
        return {"list": await _list(world.a, ANA, world.pid, obj="invoice"),
                "save": await _create(world.a, ANA, world.pid, {"object": "invoice",
                                                                "name": "Mine"})}

    got = _run(world, go)
    status, listed = got["list"]
    assert status == 200
    [view] = listed["views"]
    assert view["dropped"] == 2 and view["spec"] == {"filter": [], "columns": []}
    assert str(SECRET) not in json.dumps(listed)
    status, detail = got["save"]
    assert status == 422 and detail["code"] == "view_invalid", detail


def test_r8_a_view_of_an_object_that_the_app_lacks_is_refused(world) -> None:
    """P1 of the review: each made-up name would open a new cap of 50."""
    async def go() -> list[tuple[int, Any]]:
        return [await _create(world.a, who, world.pid, {"object": "ghost", "name": "Ghost"})
                for who in (OWEN, ANA)]

    for status, detail in _run(world, go):
        assert status == 422 and detail["code"] == "view_invalid", detail
    assert [r["name"] for r in _rows(world, world.pid)] == ["Big visits"]


def test_r8_a_view_of_another_package_is_404_on_this_path(world) -> None:
    """A view of package Q never reads, changes or goes through the path of
    package P, also for the owner of both."""
    async def go() -> dict[str, Any]:
        other = await _app(world)
        status, made = await _create(world.a, OWEN, other, {
            "object": "visit", "name": "Other app", "shared": True})
        assert status == 201, made
        vid = made["view"]["id"]
        return {"other": other, "list": await _list(world.a, OWEN, world.pid),
                "patch": await _patch(world.a, OWEN, world.pid, vid, {"name": "Moved"}),
                "delete": await _delete(world.a, OWEN, world.pid, vid)}

    got = _run(world, go)
    assert _names(got["list"][1]) == ["Big visits"]
    assert got["patch"] == (404, NOT_FOUND) and got["delete"] == (404, NOT_FOUND)
    assert [r["name"] for r in _rows(world, got["other"])] == ["Other app"]


def test_r8_a_view_drops_the_keys_that_the_roles_of_a_viewer_hide(world) -> None:
    """The verifier's probe (M8). The object grants the role ``boss`` a read
    that hides ``status``. Ana holds no role, so her roles read no record of
    the object, and each part of the shared view is dropped. The value that
    Owen typed never reaches her."""
    mid = _mid()
    manifest = _manifest(mid)
    manifest["roles"] = ["boss"]
    manifest["objects"][0]["access"] = [{"role": "boss", "read": "all", "hide": ["status"]}]

    async def go() -> dict[str, Any]:
        status, got = await _pkg("create_package", world.a, OWEN, payload=manifest)
        assert status == 200, got
        pid = str(got["package"]["id"])
        assert (await _pkg("publish", world.a, OWEN, pid, payload={}))[0] == 200
        status, got = await _pkg("install_package", world.a, OWEN, pid,
                                 payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 200, got
        status, made = await _create(world.a, OWEN, pid, {
            "object": "visit", "name": "Secret site", "shared": True,
            "spec": {"filter": [{"field": "site", "op": "eq", "value": "SECRETSITE"}],
                     "sort": {"field": "status", "dir": "asc"},
                     "columns": ["site", "status"]}})
        assert status == 201, made
        return {"ana": await _list(world.a, ANA, pid), "owen": await _list(world.a, OWEN, pid)}

    got = _run(world, go)
    status, ana = got["ana"]
    assert status == 200, ana
    [view] = ana["views"]
    assert view["dropped"] == 4
    assert view["spec"] == {"filter": [], "sort": None, "columns": []}
    assert "SECRETSITE" not in json.dumps(ana)
    [whole] = got["owen"][1]["views"]
    assert whole["dropped"] == 0 and whole["spec"]["filter"][0]["value"] == "SECRETSITE"


def test_r8_only_the_owner_changes_a_shared_view(world) -> None:
    async def go() -> dict[str, Any]:
        out = {"ana_patch": await _patch(world.a, ANA, world.pid, world.shared, {"name": "x"}),
               "ana_unshare": await _patch(world.a, ANA, world.pid, world.shared,
                                           {"shared": False}),
               "ana_delete": await _delete(world.a, ANA, world.pid, world.shared),
               "owen_rename": await _patch(world.a, OWEN, world.pid, world.shared,
                                           {"name": "Large visits"}),
               "owen_unshare": await _patch(world.a, OWEN, world.pid, world.shared,
                                            {"shared": False})}
        out["ana_after"] = await _list(world.a, ANA, world.pid)
        out["owen_share"] = await _patch(world.a, OWEN, world.pid, world.shared,
                                         {"shared": True})
        out["owen_delete"] = await _delete(world.a, OWEN, world.pid, world.shared)
        return out

    got = _run(world, go)
    for case in ("ana_patch", "ana_unshare", "ana_delete"):
        status, detail = got[case]
        assert status == 403 and detail["code"] == "read_only", (case, detail)
    assert got["owen_rename"][0] == 200
    assert got["owen_rename"][1]["view"]["name"] == "Large visits"
    assert got["owen_unshare"][0] == 200 and got["owen_unshare"][1]["view"]["shared"] is False
    assert got["ana_after"] == (200, {"views": []})
    assert got["owen_share"][0] == 200 and got["owen_share"][1]["view"]["shared"] is True
    assert got["owen_delete"][0] == 200
    assert _rows(world, world.pid) == []
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT user_email, detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
            "AND detail->>'op' LIKE 'view_%' ORDER BY id"), {"p": world.pid}).all()
    details = [r[1] if isinstance(r[1], dict) else json.loads(r[1]) for r in rows]
    assert [d["op"] for d in details] == ["view_create", "view_update", "view_unshare",
                                          "view_share", "view_delete"]
    # Value free: the op and the share flag, no name, no filter and no value.
    assert all(set(d) == {"op", "shared"} for d in details), details
    assert {str(r[0]).lower() for r in rows} == {OWEN}


# ── The caps ──────────────────────────────────────────────────────────────


def test_r8_the_cap_refuses_view_51(world) -> None:
    async def go() -> dict[str, Any]:
        async with tenant_session(world.a) as db:
            for i in range(views_store.MAX_PRIVATE):
                await views_store.create_view(db, ANA, package_id=world.pid,
                                              object_name="visit", name=f"v{i}",
                                              shared=False, spec={})
        private = await _create(world.a, ANA, world.pid, {"object": "visit", "name": "v51"})
        other = await _create(world.a, BEN, world.pid, {"object": "visit", "name": "v0"})
        async with tenant_session(world.a) as db:
            for i in range(1, views_store.MAX_SHARED):
                await views_store.create_view(db, OWEN, package_id=world.pid,
                                              object_name="visit", name=f"s{i}",
                                              shared=True, spec={})
        shared = await _create(world.a, OWEN, world.pid, {"object": "visit", "name": "s51",
                                                          "shared": True})
        taken = await _create(world.a, BEN, world.pid, {"object": "visit", "name": "V0"})
        return {"private": private, "other": other, "shared": shared, "taken": taken}

    got = _run(world, go)
    for case in ("private", "shared"):
        status, detail = got[case]
        assert status == 409 and detail["code"] == "view_cap", (case, detail)
    assert got["other"][0] == 201
    status, detail = got["taken"]
    assert status == 409 and detail["code"] == "view_name_taken", detail
    rows = _rows(world, world.pid)
    assert sum(1 for r in rows if r["owner"] == ANA_LOW) == views_store.MAX_PRIVATE
    assert sum(1 for r in rows if r["shared"]) == views_store.MAX_SHARED


def test_r8_the_cap_of_the_app_holds_across_objects(world, monkeypatch) -> None:
    """The second wall: a cap for each member on the whole app, and one for
    the shared views of the app. The test lowers both to 2."""
    monkeypatch.setattr(views_store, "MAX_PACKAGE_PRIVATE", 2)
    monkeypatch.setattr(views_store, "MAX_PACKAGE_SHARED", 2)

    async def go() -> dict[str, Any]:
        out = {"p1": await _create(world.a, OWEN, world.pid, {"object": "visit", "name": "a"}),
               "p2": await _create(world.a, OWEN, world.pid, {"object": "invoice", "name": "b"}),
               "p3": await _create(world.a, OWEN, world.pid, {"object": "invoice", "name": "c"}),
               "s2": await _create(world.a, OWEN, world.pid, {"object": "invoice", "name": "d",
                                                               "shared": True}),
               "s3": await _create(world.a, OWEN, world.pid, {"object": "visit", "name": "e",
                                                               "shared": True})}
        return out

    got = _run(world, go)
    assert [got[k][0] for k in ("p1", "p2", "s2")] == [201, 201, 201]
    for case in ("p3", "s3"):
        status, detail = got[case]
        assert status == 409 and detail["code"] == "view_cap", (case, detail)
        assert "An app holds at most 2" in detail["message"], detail


# ── The package delete and the member purge ───────────────────────────────


def test_r8_a_package_delete_leaves_no_view(world) -> None:
    async def go() -> None:
        assert (await _create(world.a, ANA, world.pid, {"object": "visit",
                                                        "name": "Mine"}))[0] == 201
        status, got = await _pkg("delete_preview", world.a, OWEN, world.pid)
        assert status == 200, got
        status, got = await _pkg("delete_package", world.a, OWEN, world.pid,
                                 payload={"confirm": got["confirm"]})
        assert status == 200, got

    _run(world, go)
    assert _rows(world, world.pid) == []


def test_r8_a_member_purge_deletes_the_private_views_only(world) -> None:
    """The two clauses of the purge, run as the app role in org A: Ana's
    private view goes, the shared view of Owen stays when Owen is purged, and
    the private view of Ben stays."""
    from gateway.routes.admin.members import _PURGE_DELETES, _PURGE_KEEPS, _purge_params

    [gone] = [r for r in _PURGE_DELETES if r.table == "studio_saved_views"]
    [kept] = [r for r in _PURGE_KEEPS if r.table == "studio_saved_views"]

    async def go() -> dict[str, int]:
        for who, name in ((ANA, "Ana's"), (BEN, "Ben's"), (OWEN, "Owen's")):
            assert (await _create(world.a, who, world.pid, {"object": "visit",
                                                            "name": name}))[0] == 201
        out: dict[str, int] = {}
        async with tenant_session(world.a) as db:
            for who, key in ((ANA_LOW, "ana"), (OWEN, "owen")):
                params = _purge_params(gone, uid=str(uuid.uuid4()), email=who)
                out[f"{key}_kept"] = int((await db.execute(
                    text(kept.count_sql), params)).scalar_one())
                out[f"{key}_counted"] = int((await db.execute(
                    text(gone.count_sql), params)).scalar_one())
                out[f"{key}_deleted"] = int((await db.execute(
                    text(gone.delete_sql), params)).rowcount)
        return out

    got = _run(world, go)
    assert got == {"ana_kept": 0, "ana_counted": 1, "ana_deleted": 1,
                   "owen_kept": 1, "owen_counted": 1, "owen_deleted": 1}
    assert [r["name"] for r in _rows(world, world.pid)] == ["Ben's", "Big visits"]
