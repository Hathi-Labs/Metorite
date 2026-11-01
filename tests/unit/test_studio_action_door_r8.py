"""The action door of AI Studio on a real database (STU-B2a, R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.2 and §14 B2a. Code:
``gateway/routes/studio/invoke.py`` over ``gateway/studio/packages_store.py``
(A3) and ``gateway/studio/objects_store.py`` (C1). The hermetic half is
``test_studio_action_door.py``.

The real handler runs on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org. The
packages are made by the A3 store, and the objects and records by the C1
store, each as the app role.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_action_door_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (B2a).**

a. The owner of a package runs its ``object:<name>.get`` action, and gets the
   record as C1 returns it. One ``app_audit`` row lands in the caller's org.
b. Another member of the org, and org B with the same email, get 404
   ``Not found`` on each action. No audit row names the package.
c. An action that the manifest does not declare gets 403 ``not_declared``.
d. Class B and class C get 403 ``approval_required``, and nothing changes.
   A workflow of another org is not in the catalog of this org.
e. A class A action with no executor gets 403 ``source_not_open``.
f. An App Workshop app is not a package, so its id is 404.
g. A record of another package of the same member is 404.
h. A body that names another org or another package changes nothing.
i. With the switch off, each call is 404 and writes no audit row.
j. An action that is not an A2 action id is 404, and writes no row.
k. An object that the package lacks is 404 ``object_missing``, and a record
   id that names nothing is 404 ``Not found``. The audit row tells them apart.
l. A catalog that fails is 503 ``catalog_unavailable``, and the row lands.

**The mutations (R7).** Commit before each, and restore with git after.
Each result below was run on 2026-11-01, with both B2a suites.

* M1. ``invoke.invoke_action``: drop the switch check.
  ``test_r8_the_switch_off_is_404_and_writes_no_row`` and three switch tests
  of the hermetic half go red.
* M2. ``packages_store._GET_SQL``: drop ``AND owner_email = :m``.
  ``test_r8_another_member_of_the_org_gets_404`` goes red. The fake session
  of the hermetic half filters on the member itself, so only R8 sees it.
* M3. ``invoke._declared``: answer the first entry for each id, so the
  declaration check passes each action.
  ``test_r8_an_undeclared_action_is_403_and_audited`` and
  ``test_an_undeclared_action_is_403_not_declared`` go red.
* M4. ``invoke.refusal_for``: drop the class X refusal.
  ``test_a_class_x_row_is_403_class_x`` of the hermetic half goes red. No
  real catalog row is class X, so no R8 test can see it.
* M5. ``invoke.refusal_for``: drop the class B and C refusal.
  ``test_r8_each_write_verb_writes_nothing``,
  ``test_r8_a_source_that_is_not_an_object_never_runs`` and eight hermetic
  tests go red.
* M6a. ``invoke.invoke_action``: open ``_tenant_session`` with the
  ``organization_id`` of the body.
  ``test_r8_a_body_that_names_another_org_changes_nothing`` and
  ``test_the_session_takes_no_org_from_the_request`` go red.
* M6b. ``invoke.invoke_action``: open an unbound ``get_db()`` session. Seven
  tests of this suite go red, the owner test first. (The hermetic half
  hangs on a real engine, so run this one on R8 only.)
* M7a. ``objects_store._GET_RECORD_SQL``: drop ``AND {_IN_PACKAGE}``. No
  test of the door goes red, because the door reads the object by its name
  in the package first. C1's
  ``test_r8_a_record_call_names_its_package_and_its_object`` goes red.
* M7b. ``invoke._run_object_get``: take the package id of the object read
  and the record read from the body key ``package_id``, the id of ANOTHER
  package of the same member. ``test_r8_a_record_of_another_package_is_404``
  and ``test_a_get_reads_with_the_package_id`` go red: the door reads the
  record of the other package.
* M10 (fix round 1). ``invoke.invoke_action``: parse the body before the
  switch check. ``test_a_malformed_body_from_an_org_outside_the_switch_is_404``
  of the hermetic half goes red.
* M11 (fix round 1). ``invoke.invoke_action``: drop the ``try`` around the
  catalog read. ``test_r8_a_catalog_that_fails_is_503_and_audited`` and
  ``test_a_catalog_that_fails_is_503_and_audited`` go red.
* M8. ``invoke.invoke_action``: answer 403 for a package that
  ``get_package`` does not find. ``test_r8_another_member_of_the_org_gets_404``,
  ``test_r8_org_b_gets_404_also_with_the_same_email``,
  ``test_r8_a_code_app_is_404`` and four hermetic tests go red.
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
from fastapi import HTTPException
from gateway.routes.studio import invoke
from gateway.studio import objects_store, packages_store
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stub2a.test"
BEN = f"ben.{TAG}@stub2a.test"
NOT_FOUND = "Not found"

_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
    {"key": "notes", "type": "text"},
]
_DATA = {"site": "Plant 4, Hosur", "status": "planned", "notes": "Bring the nozzle kit"}


def _manifest(mid: str, wf_a: str, wf_b: str) -> dict[str, Any]:
    return {
        "studio": 1,
        "id": mid,
        "name": "Visits",
        "purpose": "Track install visits.",
        "objects": [{"name": "visit", "fields": _FIELDS}],
        "actions": [
            {"id": "peek", "uses": "object:visit.get"},
            {"id": "find", "uses": "object:visit.list"},
            {"id": "add", "uses": "object:visit.create", "set": {"status": "planned"}},
            {"id": "change", "uses": "object:visit.update", "set": {"status": "done"}},
            {"id": "remove", "uses": "object:visit.delete"},
            {"id": "flow", "uses": f"workflow:{wf_a}"},
            {"id": "flow_b", "uses": f"workflow:{wf_b}"},
            {"id": "pages", "uses": "knowledge.list_pages"},
            {"id": "propose", "uses": "knowledge.propose"},
        ],
    }


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:workflows"]), organization_id=org)


class _Request:
    """The one part of a request that the handler reads: the body bytes."""

    def __init__(self, payload: Any) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")

    async def body(self) -> bytes:
        return self._raw


async def _call(org: str, email: str, pid: str, action: str,
                payload: Any = None) -> tuple[int, Any]:
    """Call the real handler with *org* bound, as the auth layer binds it.
    Answers the status and the answer, or the detail of a refusal."""
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=_Request(payload),
                                           user=_user(org, email))
        return 200, found
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


async def _package(org: str, member: str, manifest: dict[str, Any]) -> str:
    async with tenant_session(org) as db:
        made = await packages_store.create_package(db, member, manifest)
    return made.id


async def _object_with_record(org: str, pid: str) -> tuple[str, str]:
    async with tenant_session(org) as db:
        obj = await objects_store.define_object(db, ANA, package_id=pid, name="visit",
                                                fields=_FIELDS)
        assert obj is not None
        rec = await objects_store.create_record(db, ANA, obj.id, _DATA, package_id=pid)
        assert rec is not None
    return obj.id, rec.id


def _seed_workflow(p: Any, org: str) -> str:
    wid = str(uuid.uuid4())
    with p.admin_engine.begin() as c:
        c.execute(text(
            "INSERT INTO workflows (id, name, owner_email, status, organization_id) "
            "VALUES (:id, :n, :o, 'published', :org)"),
            {"id": wid, "n": f"Flow {TAG}", "o": ANA, "org": org})
    return wid


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Four packages and one code app. Org A: two packages of Ana, one of Ben,
    and a code app of Ana. Org B: one package of a member with Ana's email.
    A published workflow in each org."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    run = uuid.uuid4().hex[:6]
    mid = f"door-{TAG}-{run}"
    wf_a, wf_b = _seed_workflow(p, p.org_a), _seed_workflow(p, p.org_b)
    manifest = _manifest(mid, wf_a, wf_b)
    with p.admin_engine.begin() as c:
        code_app = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id) "
            "VALUES (:s, 'stub2a', :oe, '/tmp/stub2a', CAST(:o AS uuid)) RETURNING id"),
            {"s": f"{mid}-code", "oe": ANA, "o": p.org_a}).scalar_one())
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, code_app=code_app, wf_a=wf_a, wf_b=wf_b)

    async def setup() -> None:
        w.pkg = await _package(w.a, ANA, manifest)
        w.pkg2 = await _package(w.a, ANA, manifest)
        w.pkg_ben = await _package(w.a, BEN, manifest)
        w.pkg_b = await _package(w.b, ANA, manifest)
        # A package of Ana with no object in the registry.
        w.pkg_empty = await _package(w.a, ANA, manifest)
        w.obj, w.rec = await _object_with_record(w.a, w.pkg)
        w.obj2, w.rec2 = await _object_with_record(w.a, w.pkg2)
        w.obj_ben, w.rec_ben = await _object_with_record(w.a, w.pkg_ben)
        w.obj_b, w.rec_b = await _object_with_record(w.b, w.pkg_b)

    _run(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        ids = [w.pkg, w.pkg2, w.pkg_ben, w.pkg_b, w.pkg_empty, w.code_app]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{mid}-%"})
        c.execute(text("DELETE FROM workflows WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": wf_a, "b": wf_b})


def _audits(world: Any, pid: str) -> list[tuple[str, str, str, dict[str, Any]]]:
    """The audit rows of *pid*, read as the superuser, oldest first."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, user_email, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY id"), {"p": pid}).all()
    out = []
    for org, email, kind, detail in rows:
        out.append((org, email, kind, detail if isinstance(detail, dict) else json.loads(detail)))
    return out


def _record_row(world: Any, rid: str) -> tuple[Any, ...] | None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT data, updated_by, updated_at FROM studio_records "
            "WHERE id = CAST(:r AS uuid)"), {"r": rid}).first()
    return None if row is None else tuple(row)


def _record_count(world: Any, oid: str) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_records WHERE object_id = CAST(:o AS uuid)"),
            {"o": oid}).scalar_one())


_EVERY_ACTION = ("peek", "find", "add", "change", "remove", "flow", "flow_b", "pages",
                 "propose", "no_such_action")


# ── a. The owner runs a get ────────────────────────────────────────────────


def test_r8_the_owner_gets_the_record_and_one_audit_row(world) -> None:
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "peek", {"id": world.rec}))
    assert status == 200, got
    rec = got["record"]
    assert (rec["id"], rec["object_id"], rec["data"]) == (world.rec, world.obj, _DATA)
    assert (rec["created_by"], rec["updated_by"]) == (ANA, ANA)
    rows = _audits(world, world.pkg)
    assert rows == [(world.a, ANA, "action", {
        "action": "peek", "uses": "object:visit.get", "class": "A", "outcome": "ran"})]
    # The row holds no record data.
    for value in _DATA.values():
        assert value not in json.dumps(rows[0][3])


# ── b. The walls of the member and the org ─────────────────────────────────


def test_r8_another_member_of_the_org_gets_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, BEN, world.pkg, a, {"id": world.rec})
                for a in _EVERY_ACTION]

    assert _run(world, go) == [(404, NOT_FOUND)] * len(_EVERY_ACTION)
    assert _audits(world, world.pkg) == []
    # Ben's own package answers Ben, so the wall is the owner, not the org.
    status, got = _run(world, lambda: _call(world.a, BEN, world.pkg_ben, "peek",
                                            {"id": world.rec_ben}))
    assert status == 200 and got["record"]["id"] == world.rec_ben


def test_r8_org_b_gets_404_also_with_the_same_email(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        out = [await _call(world.b, ANA, world.pkg, a, {"id": world.rec})
               for a in _EVERY_ACTION]
        # Org B's own record by its own id, through org A's package.
        out.append(await _call(world.b, ANA, world.pkg, "peek", {"id": world.rec_b}))
        return out

    assert _run(world, go) == [(404, NOT_FOUND)] * (len(_EVERY_ACTION) + 1)
    assert _audits(world, world.pkg) == []
    # An audit row of org B never names org A's package.
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM app_audit WHERE organization_id = CAST(:b AS uuid) "
            "AND app_id = CAST(:p AS uuid)"), {"b": world.b, "p": world.pkg}).scalar_one() == 0


# ── c. The declaration ─────────────────────────────────────────────────────


def test_r8_an_undeclared_action_is_403_and_audited(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("peek_all", "peek2")]

    for status, detail in _run(world, go):
        assert (status, detail["code"]) == (403, "not_declared")
    rows = _audits(world, world.pkg)
    assert [r[3]["reason"] for r in rows] == ["not_declared", "not_declared"]
    assert all(r[3]["outcome"] == "refused" and r[3]["class"] is None for r in rows)


# ── d. Class B and C write nothing ─────────────────────────────────────────


def test_r8_each_write_verb_writes_nothing(world) -> None:
    before = _record_row(world, world.rec)
    count = _record_count(world, world.obj)
    body = {"id": world.rec, "site": "Changed", "status": "done"}

    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, body)
                for a in ("add", "change", "remove")]

    for status, detail in _run(world, go):
        assert (status, detail["code"]) == (403, "approval_required")
    assert _record_row(world, world.rec) == before
    assert _record_count(world, world.obj) == count
    rows = _audits(world, world.pkg)
    assert [(r[3]["action"], r[3]["class"], r[3]["reason"]) for r in rows] == [
        ("add", "B", "approval_required"), ("change", "B", "approval_required"),
        ("remove", "C", "approval_required")]


# ── d and e. A source that is not an object ────────────────────────────────


def test_r8_a_source_that_is_not_an_object_never_runs(world) -> None:
    async def go() -> dict[str, tuple[int, Any]]:
        return {a: await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("flow", "flow_b", "propose", "pages", "find")}

    got = {a: (s, d["code"]) for a, (s, d) in _run(world, go).items()}
    assert got == {
        "flow": (403, "approval_required"),
        # A published workflow of org B is not in the catalog of org A.
        "flow_b": (403, "not_in_catalog"),
        "propose": (403, "approval_required"),
        "pages": (403, "source_not_open"),
        "find": (403, "source_not_open"),
    }
    classes = {r[3]["action"]: r[3]["class"] for r in _audits(world, world.pkg)}
    assert classes == {"flow": "B", "flow_b": None, "propose": "B", "pages": "A", "find": "A"}


# ── f, g and h. The package, the record and the body ───────────────────────


def test_r8_an_action_that_is_not_an_id_is_404_and_writes_no_row(world) -> None:
    """An action of 20,000 characters, or a `uses` value in the path, names no
    A2 action id. The door reads nothing for it and writes no audit row."""
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("a" * 20000, "object:visit.get", "a" * 49)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 3
    assert _audits(world, world.pkg) == []


def test_r8_an_object_that_the_package_lacks_is_object_missing(world) -> None:
    """The manifest declares `visit`, and the registry of this package holds no
    such object. A record id that names nothing is a plain 404."""
    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg_empty, "peek", {"id": world.rec}),
                await _call(world.a, ANA, world.pkg, "peek", {"id": str(uuid.uuid4())})]

    no_object, no_record = _run(world, go)
    assert no_object == (404, {"code": "object_missing", "message": invoke.NO_OBJECT})
    assert no_record == (404, NOT_FOUND)
    assert [r[3] for r in _audits(world, world.pkg_empty)] == [{
        "action": "peek", "uses": "object:visit.get", "class": "A",
        "outcome": "not_found", "reason": "object_missing"}]
    assert [r[3] for r in _audits(world, world.pkg)] == [{
        "action": "peek", "uses": "object:visit.get", "class": "A",
        "outcome": "not_found"}]


def test_r8_a_catalog_that_fails_is_503_and_audited(world, monkeypatch) -> None:
    async def _fails() -> list[dict[str, Any]]:
        raise RuntimeError("the catalog session failed")

    monkeypatch.setattr(invoke.actions, "catalog_rows", _fails)
    status, detail = _run(world, lambda: _call(world.a, ANA, world.pkg, "flow",
                                               {"id": world.rec}))
    assert (status, detail) == (503, {"code": "catalog_unavailable",
                                      "message": invoke.CATALOG_DOWN})
    assert _audits(world, world.pkg) == [(world.a, ANA, "action", {
        "action": "flow", "uses": f"workflow:{world.wf_a}", "class": None,
        "outcome": "failed", "reason": "catalog_unavailable"})]


def test_r8_a_code_app_is_404(world) -> None:
    status, detail = _run(world, lambda: _call(world.a, ANA, world.code_app, "peek",
                                               {"id": world.rec}))
    assert (status, detail) == (404, NOT_FOUND)
    assert _audits(world, world.code_app) == []


def test_r8_a_record_of_another_package_is_404(world) -> None:
    """Both packages of Ana hold an object `visit`. The door reads the object
    of the package in the path, so a record of the other package is 404."""
    async def go() -> list[tuple[int, Any]]:
        # The second body names the other package. The door reads the package
        # of the path only, so the key changes nothing (M7b).
        return [await _call(world.a, ANA, world.pkg, "peek", body)
                for body in ({"id": world.rec2},
                             {"id": world.rec2, "package_id": world.pkg2})]

    assert _run(world, go) == [(404, NOT_FOUND)] * 2
    assert [r[3]["outcome"] for r in _audits(world, world.pkg)] == ["not_found"] * 2


def test_r8_a_body_that_names_another_org_changes_nothing(world) -> None:
    body = {"id": world.rec, "organization_id": world.b, "owner_email": BEN}
    status, got = _run(world, lambda: _call(world.a, ANA, world.pkg, "peek", body))
    assert status == 200, got
    assert got["record"]["id"] == world.rec
    rows = _audits(world, world.pkg)
    assert [(r[0], r[1]) for r in rows] == [(world.a, ANA)]


# ── i. The switch ──────────────────────────────────────────────────────────


def test_r8_the_switch_off_is_404_and_writes_no_row(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "studio_orgs", world.b)

    async def go() -> list[tuple[int, Any]]:
        return [await _call(world.a, ANA, world.pkg, a, {"id": world.rec})
                for a in ("peek", "add", "no_such_action")]

    assert _run(world, go) == [(404, NOT_FOUND)] * 3
    assert _audits(world, world.pkg) == []
