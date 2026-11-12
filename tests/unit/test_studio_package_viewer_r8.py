"""WS-STU J2a — a member reads an app that another member owns, on a real
database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 J2a and §19.7, and
``package_host.md`` §5.2 (who sees an install). Code:
``gateway/package_host/viewer.py``, ``resolve.visible_installs``,
``GET /studio/packages/{id}`` (``routes/studio/packages.py``) and the door
(``routes/studio/invoke.py``). The hermetic twin is
``tests/unit/test_studio_package_viewer.py``.

The real create, publish, install, package and door handlers run on a phase-4
promoted scratch database, as the non-privileged app role, so FORCE RLS binds
each statement. Each call binds its org as the auth layer does. Org A has
Owen (the owner, a holder of ``studio:publish``), Ana and Ben in ``ops``, and
Cat in ``eng``. Org B has Zed. Ana's address is in mixed case.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_package_viewer_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (J2a).**

a. Ana and Ben, in ``ops``, read Owen's app that he installed for ``ops``:
   200 with the INSTALLED version. A later draft save and a later publish do
   not reach them, because a group install stays at its version.
b. Cat (not in ``ops``), Zed (org B) and every caller while
   ``PACKAGE_HOST_ORGS`` is off get 404. The owner answer does not change,
   also with the switch off.
c. A viewer's object get and list give 200 on the declarations of the
   installed version, and each call writes one audit row that names the
   viewer and the version. A declaration of the draft only is 403.
d. A viewer's create, update and delete give 403 ``read_only``, and no
   record changes.
e. A draft-only install never reaches a non-owner, also the private one
   that names the non-owner, which the sidebar read shows to that member.
f. A paused, an archived and an expired install, and an archived app, give
   404 on the route and at the first check of the door. A pause during the
   body read gives 404 at the second check, and no record is read.
g. Past the cap of the reads of a viewer the door answers 503
   ``store_busy``. The owner read takes no slot.
h. Fix round 1 (P2). A newer version adds a field and a rule. A viewer of the
   older version sees neither, on the route or at the door: no field, no
   rule, no stored value and no filter or sort on it. A record that a viewer
   reads names no member who wrote it.

**R7: red first, then mutate.** Each mutation ran on 2026-11-11, on this
file and its hermetic twin together (60 tests).

* M1. ``viewer.installed``: drop the ``version is None`` check. Case e goes
  red (2 failed).
* M2. ``resolve.INSTALLS_SQL``: read ``apps.manifest`` (the draft) for each
  install. Case a goes red (1 failed).
* M3. ``invoke.refusal_for``: drop the ``read_only`` branch. Case d goes red
  (13 failed).
* M4. ``resolve.visible_installs``: pass ``groups=[]``. Cases a, c, d, f
  and g go red (6 failed).
* M5 (fix round 1). ``viewer.InstalledApp.project``: keep every field of the
  row. Case h goes red (5 failed of 67, with the hermetic twin).
* M6 (fix round 1). ``invoke._run_object_list``: drop the ``parse_query``
  of a viewer. Case h goes red, because a filter on ``cost`` runs (3 failed
  of 67).
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
from gateway.package_host import resolve, store
from gateway.routes.studio import invoke, packages
from gateway.studio import objects_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWEN = f"owen.{TAG}@j2a.test"
#: Mixed case in ``app_user`` and in the session, so each read folds it.
ANA = f"Ana.{TAG}@j2a.test"
ANA_LOW = ANA.lower()
BEN = f"ben.{TAG}@j2a.test"
CAT = f"cat.{TAG}@j2a.test"
ZED = f"zed.{TAG}@j2a.test"
OPS = f"group:ops{TAG}"
ENG = f"group:eng{TAG}"
_EMAIL_LIKE = f"%.{TAG}@j2a.test"
_GROUP_LIKE = f"%{TAG}"
_SLUG_LIKE = f"j2a{TAG}-%"
_COUNTER = iter(range(10_000))
STUDIO = ("feature:studio",)
PUBLISHER = ("feature:studio", "studio:publish")
NOT_FOUND = "Not found"
_DATA = {"site": "Plant 4, Hosur"}


def _manifest(mid: str, name: str = "Site visits",
              extra: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "studio": 1, "id": mid, "name": name, "purpose": "Track the visits to a site.",
        "objects": [{"name": "visit", "fields": [{"key": "site", "type": "text"}]}],
        "actions": [
            {"id": "peek", "uses": "object:visit.get"},
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create"},
            {"id": "change", "uses": "object:visit.update"},
            {"id": "remove", "uses": "object:visit.delete"},
            *(extra or []),
        ],
    }


def _mid() -> str:
    return f"j2a{TAG}-app{next(_COUNTER)}"


def _user(org: str, email: str, grants: tuple[str, ...] = STUDIO) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(list(grants)), organization_id=org)


class _Request:
    """The parts of a request that the handlers read: the body stream, the
    method, the path, the headers and the query. *on_read* runs at the read."""

    def __init__(self, payload: Any = None,
                 on_read: Callable[[], Awaitable[None]] | None = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.query_params: dict[str, str] = {}
        self._on_read = on_read
        self.reads = 0

    async def stream(self) -> Any:
        self.reads += 1
        if self._on_read is not None:
            await self._on_read()
        yield self._raw


async def _call(route: str, org: str, email: str, pid: str | None = None,
                payload: Any = None, grants: tuple[str, ...] = STUDIO) -> tuple[int, Any]:
    """Call the real package handler *route* with *org* bound."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email, grants)}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish", "install_package"):
        kwargs["request"] = _Request(payload)
    response = Response()
    response.status_code = None  # type: ignore[assignment]
    if route == "install_package":
        kwargs["response"] = response
    token = bind_tenant(org)
    try:
        got = await handler(**kwargs)
        default = 201 if route == "install_package" else 200
        return response.status_code or default, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


async def _door(org: str, email: str, pid: str, action: str, payload: Any = None,
                request: _Request | None = None) -> tuple[int, Any]:
    """Call the real door with *org* bound."""
    token = bind_tenant(org)
    try:
        got = await invoke.invoke_action(pid, action, request=request or _Request(payload),
                                         user=_user(org, email))
        return 200, got
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _live(org: str, mid: str, name: str = "Site visits") -> str:
    """A new package of Owen, published once."""
    status, got = await _call("create_package", org, OWEN, payload=_manifest(mid, name))
    assert status == 200, got
    pid = str(got["package"]["id"])
    status, got = await _call("publish", org, OWEN, pid, payload={})
    assert status == 200, got
    return pid


async def _record(org: str, pid: str) -> str:
    """One record of the object ``visit`` of *pid*, written by Owen."""
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, "visit", package_id=pid)
        assert obj is not None
        rec = await objects_store.create_record(db, OWEN, obj.id, _DATA, package_id=pid)
        assert rec is not None
    return rec.id


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, with Studio and the package host on for both. Owen
    publishes app Y with one record and installs it for ``ops`` at version 1.
    Then he saves a draft and publishes version 2, which the group install
    does not take."""
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
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, mid=_mid())

    async def setup() -> None:
        w.pid = await _live(w.a, w.mid)
        w.rec = await _record(w.a, w.pid)
        status, got = await _call("install_package", w.a, OWEN, w.pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        w.install = str(got["install"]["id"])
        draft = _manifest(w.mid, "Draft name",
                          [{"id": "draft_only", "uses": "object:visit.list"}])
        status, got = await _call("save_draft", w.a, OWEN, w.pid, payload=draft)
        assert status == 200, got
        status, got = await _call("publish", w.a, OWEN, w.pid, payload={})
        assert status == 200, got
        # The draft moves again after version 2, so the draft is not version 2.
        status, got = await _call("save_draft", w.a, OWEN, w.pid,
                                  payload=_manifest(w.mid, "Newest draft"))
        assert status == 200, got

    _run(w, setup)
    yield w
    invoke._READS_IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE}).all()]
        for table, col in (("app_audit", "app_id"), ("studio_objects", "package_id"),
                           ("app_versions", "app_id")):
            c.execute(text(f"DELETE FROM {table} WHERE {col} = ANY(CAST(:ids AS uuid[]))"),
                      {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": _SLUG_LIKE})
        c.execute(text("DELETE FROM org_group WHERE slug LIKE :s"), {"s": _GROUP_LIKE})
        c.execute(text("DELETE FROM app_user WHERE email LIKE :e"), {"e": _EMAIL_LIKE})


def _admin(world: Any, sql: str, **params: Any) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text(sql), params)


def _audits(world: Any, pid: str, email: str) -> list[dict[str, Any]]:
    """The door rows of *email* for *pid*, oldest first, read as the superuser."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) AND lower(user_email) = lower(:e) "
            "AND detail->>'action' IS NOT NULL "
            "ORDER BY id"), {"p": pid, "e": email}).all()
    return [{"org": r[0], **(r[1] if isinstance(r[1], dict) else json.loads(r[1]))}
            for r in rows]


def _record_data(world: Any, rid: str) -> Any:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                        {"r": rid}).first()
    return None if row is None else row[0]


def _records(world: Any, pid: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_records r JOIN studio_objects o ON o.id = r.object_id "
            "WHERE o.package_id = CAST(:p AS uuid)"), {"p": pid}).scalar_one())


# ── a. A member of the group reads the installed version ───────────────────


def test_r8_a_member_of_the_group_reads_the_installed_version(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call("get_package", world.a, who, world.pid) for who in (ANA, BEN)]

    for status, got in _run(world, go):
        assert status == 200, got
        pkg = got["package"]
        assert (pkg["access"], pkg["version"], pkg["name"]) == ("viewer", 1, "Site visits")
        assert pkg["manifest"] == _manifest(world.mid)
        assert [o["name"] for o in got["objects"]] == ["visit"]
        seen = json.dumps(got, default=str)
        for secret in ("Draft name", "Newest draft", "draft_only", OWEN):
            assert secret not in seen, secret


def test_r8_the_owner_answer_does_not_change(world, monkeypatch) -> None:
    """The owner reads the draft and the live version, as before, also with
    the switch of the package host off."""
    for value in (f"{world.a},{world.b}", ""):
        monkeypatch.setattr(get_settings(), "package_host_orgs", value)
        status, got = _run(world, lambda: _call("get_package", world.a, OWEN, world.pid))
        assert status == 200, got
        pkg = got["package"]
        assert "access" not in pkg and pkg["live_version"] == 2
        assert pkg["manifest"]["name"] == "Newest draft"


# ── b. Everyone else gets 404 ──────────────────────────────────────────────


def test_r8_a_member_outside_the_group_and_org_b_get_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call("get_package", world.a, CAT, world.pid),
                await _call("get_package", world.b, ZED, world.pid),
                await _door(world.a, CAT, world.pid, "find", {}),
                await _door(world.b, ZED, world.pid, "find", {}),
                await _door(world.b, ZED, world.pid, "peek", {"id": world.rec})]

    for status, got in _run(world, go):
        assert (status, got) == (404, NOT_FOUND)
    assert _audits(world, world.pid, CAT) == [] and _audits(world, world.pid, ZED) == []


def test_r8_the_switch_off_gives_each_viewer_404(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "package_host_orgs", "")

    async def go() -> list[tuple[int, Any]]:
        return [await _call("get_package", world.a, ANA, world.pid),
                await _door(world.a, ANA, world.pid, "find", {}),
                await _door(world.a, ANA, world.pid, "peek", {"id": world.rec})]

    for status, got in _run(world, go):
        assert (status, got) == (404, NOT_FOUND)
    assert _audits(world, world.pid, ANA) == []


# ── c. The door reads on the installed declarations ────────────────────────


def test_r8_a_viewer_gets_and_lists_the_records(world) -> None:
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        return (await _door(world.a, ANA, world.pid, "peek", {"id": world.rec}),
                await _door(world.a, ANA, world.pid, "find", {}))

    (s1, got), (s2, page) = _run(world, go)
    assert s1 == 200 and got["record"]["id"] == world.rec and got["record"]["data"] == _DATA
    assert s2 == 200 and [r["id"] for r in page["records"]] == [world.rec]
    rows = _audits(world, world.pid, ANA)
    assert [(r["action"], r["outcome"], r["viewer"], r["version"]) for r in rows] == [
        ("peek", "ran", True, 1), ("find", "ran", True, 1)]
    assert {r["org"] for r in rows} == {world.a}


def test_r8_a_declaration_of_the_draft_does_not_reach_a_viewer(world) -> None:
    """``draft_only`` is in version 2 and not in version 1, which the group
    install runs. The owner runs it, and a viewer gets 403."""
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        return (await _door(world.a, ANA, world.pid, "draft_only", {}),
                await _door(world.a, OWEN, world.pid, "find", {}))

    (status, got), (owner, _) = _run(world, go)
    assert status == 403 and got["code"] == "not_declared", got
    assert owner == 200


# ── d. A viewer writes nothing ─────────────────────────────────────────────


def test_r8_each_write_of_a_viewer_is_403_read_only_and_changes_nothing(world) -> None:
    before = _records(world, world.pid)
    request = _Request({"id": world.rec, "data": {"site": "changed"}})

    async def go() -> list[tuple[int, Any]]:
        return [await _door(world.a, ANA, world.pid, "add", {"data": {"site": "new"}}),
                await _door(world.a, ANA, world.pid, "change", request=request),
                await _door(world.a, ANA, world.pid, "remove", {"id": world.rec})]

    for status, got in _run(world, go):
        assert status == 403 and got["code"] == "read_only", got
    assert request.reads == 0, "a refused write read the body"
    assert _records(world, world.pid) == before
    assert _record_data(world, world.rec) == _DATA
    assert [r["reason"] for r in _audits(world, world.pid, ANA)] == ["read_only"] * 3


# ── e. A draft-only install never reaches a non-owner ──────────────────────


def test_r8_a_draft_only_install_never_reaches_a_non_owner(world) -> None:
    """Owen's second app has no published version. A private install that
    names Ana, with no version, is the one draft install that the resolve
    shows to a member who is not the owner: the sidebar read shows it to the
    member it names, with the DRAFT manifest. The viewer read refuses it, so
    Ana gets 404 on the route and at the door. The store refuses an org or a
    group install of a draft (``draft_not_private``)."""
    async def go() -> tuple[list[Any], list[tuple[int, Any]]]:
        status, made = await _call("create_package", world.a, OWEN, payload=_manifest(_mid()))
        assert status == 200, made
        draft = str(made["package"]["id"])
        async with tenant_session(world.a) as db:
            await store.install(db, app_id=draft, version=None, scope_subject=ANA_LOW,
                                scope_kind="private", installed_by=ANA_LOW, ends_at=None,
                                contributions=[])
        async with tenant_session(world.a) as db:
            seen = [r for r in await resolve.visible_installs(db, viewer=ANA)
                    if r.app_id == draft]
        answers = [await _call("get_package", world.a, who, draft) for who in (ANA, BEN)]
        return seen, [*answers, await _door(world.a, ANA, draft, "find", {})]

    seen, answers = _run(world, go)
    # The guard of viewer.py is load-bearing: the sidebar read shows the draft.
    assert [(r.scope_kind, r.version) for r in seen] == [("private", None)]
    for status, got in answers:
        assert (status, got) == (404, NOT_FOUND)


# ── f. A dead install gives 404 at both door checks ────────────────────────


@pytest.mark.parametrize("dead", ["paused", "archived", "expired", "app_archived"])
def test_r8_a_dead_install_gives_404_on_the_route_and_at_the_door(world, dead: str) -> None:
    if dead == "app_archived":
        _admin(world, "UPDATE apps SET status = 'archived' WHERE id = CAST(:p AS uuid)",
               p=world.pid)
    elif dead == "expired":
        _admin(world, "UPDATE package_install SET ends_at = now() - interval '1 minute' "
               "WHERE id = CAST(:i AS uuid)", i=world.install)
    elif dead == "archived":
        # The CHECK package_install_archived_at needs the date of an archive.
        _admin(world, "UPDATE package_install SET state = 'archived', archived_at = now() "
               "WHERE id = CAST(:i AS uuid)", i=world.install)
    else:
        _admin(world, "UPDATE package_install SET state = 'paused' WHERE id = CAST(:i AS uuid)",
               i=world.install)

    async def go() -> list[tuple[int, Any]]:
        return [await _call("get_package", world.a, ANA, world.pid),
                await _door(world.a, ANA, world.pid, "find", {}),
                await _door(world.a, ANA, world.pid, "peek", {"id": world.rec})]

    for status, got in _run(world, go):
        assert (status, got) == (404, NOT_FOUND), dead
    assert _audits(world, world.pid, ANA) == []


def test_r8_a_pause_during_the_read_gives_404_at_the_second_check(world) -> None:
    async def pause() -> None:
        async with tenant_session(world.a) as db:
            assert await store.pause_install(db, install_id=world.install) is True

    request = _Request({"id": world.rec}, on_read=pause)
    status, got = _run(world, lambda: _door(world.a, ANA, world.pid, "peek", request=request))
    assert (status, got) == (404, NOT_FOUND)
    assert request.reads == 1
    assert _audits(world, world.pid, ANA) == []


# ── h. A newer version adds a field that a pinned viewer never sees ────────

_COST = {"key": "cost", "type": "number", "hot": True}
_COST_RULE = {"name": "cost_ok", "kind": "range", "field": "cost", "min": 0}


def test_r8_a_field_of_a_newer_version_never_reaches_a_pinned_viewer(world) -> None:
    """Fix round 1 (P2). The group installs v1, with ``visit{site}``. Then the
    owner adds the hot field ``cost`` and a rule on it, and publishes v2. The
    sync adds ``cost`` to the object row, and the group install stays at v1.
    Ana reads v1: no ``cost`` in the fields, the rules, the records or a
    filter, and no address of the writer."""
    mid = _mid()

    async def go() -> dict[str, Any]:
        pid = await _live(world.a, mid)
        status, got = await _call("install_package", world.a, OWEN, pid,
                                  payload={"scope": OPS}, grants=PUBLISHER)
        assert status == 201, got
        v2 = _manifest(mid)
        v2["objects"][0]["fields"].append(_COST)
        v2["objects"][0]["rules"] = [_COST_RULE]
        assert (await _call("save_draft", world.a, OWEN, pid, payload=v2))[0] == 200
        assert (await _call("publish", world.a, OWEN, pid, payload={}))[0] == 200
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "visit", package_id=pid)
            assert obj is not None
            rec = await objects_store.create_record(
                db, OWEN, obj.id, {"site": "Plant 4, Hosur", "cost": 42}, package_id=pid)
            assert rec is not None
        return {
            "rid": rec.id,
            "viewer": await _call("get_package", world.a, ANA, pid),
            "owner": await _call("get_package", world.a, OWEN, pid),
            "find": await _door(world.a, ANA, pid, "find", {}),
            "peek": await _door(world.a, ANA, pid, "peek", {"id": rec.id}),
            "filter": await _door(world.a, ANA, pid, "find", {
                "filter": [{"field": "cost", "op": "gt", "value": 1}]}),
            "sort": await _door(world.a, ANA, pid, "find", {"sort": {"field": "cost"}}),
            "owner_find": await _door(world.a, OWEN, pid, "find", {}),
        }

    got = _run(world, go)
    status, viewer = got["viewer"]
    assert status == 200 and viewer["package"]["version"] == 1, viewer
    [obj] = viewer["objects"]
    assert [f["key"] for f in obj["fields"]] == ["site"] and obj["rules"] == []
    status, owner = got["owner"]
    assert status == 200 and [f["key"] for f in owner["objects"][0]["fields"]] == ["site", "cost"]
    assert [r["name"] for r in owner["objects"][0]["rules"]] == ["cost_ok"]
    status, page = got["find"]
    assert status == 200, page
    [row] = page["records"]
    assert row["data"] == {"site": "Plant 4, Hosur"}
    assert (row["created_by"], row["updated_by"]) == ("", "")
    status, one = got["peek"]
    assert status == 200 and one["record"]["data"] == {"site": "Plant 4, Hosur"}, one
    assert (one["record"]["created_by"], one["record"]["updated_by"]) == ("", "")
    for case in ("filter", "sort"):
        status, refused = got[case]
        assert status == 422 and refused["code"] == "query_field_unknown", (case, refused)
    seen = json.dumps([got["viewer"], got["find"], got["peek"]], default=str)
    assert "cost" not in seen and OWEN not in seen
    # The owner reads the newest version, as before.
    status, mine = got["owner_find"]
    assert status == 200 and mine["records"][0]["data"]["cost"] == 42


# ── g. The cap of the reads of a viewer ────────────────────────────────────


def test_r8_past_the_cap_a_viewer_gets_503_and_the_owner_reads(world) -> None:
    invoke._READS_IN_FLIGHT[world.a] = invoke.READERS_PER_ORG

    async def go() -> tuple[Any, tuple[int, Any]]:
        token = bind_tenant(world.a)
        try:
            with pytest.raises(HTTPException) as refused:
                await invoke.invoke_action(world.pid, "find", request=_Request({}),
                                           user=_user(world.a, ANA))
        finally:
            release_tenant(token)
        return refused.value, await _door(world.a, OWEN, world.pid, "find", {})

    refused, (owner, page) = _run(world, go)
    assert refused.status_code == 503
    assert refused.detail == {"code": "store_busy", "message": invoke.READ_BUSY_MESSAGE}
    assert (refused.headers or {}).get("Retry-After") == invoke.BUSY_RETRY_AFTER
    assert owner == 200 and [r["id"] for r in page["records"]] == [world.rec]
    assert {world.a: invoke.READERS_PER_ORG} == invoke._READS_IN_FLIGHT
