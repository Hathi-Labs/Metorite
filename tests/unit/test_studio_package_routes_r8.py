"""The package routes of AI Studio on a real database (STU-A5, R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 A5. Code:
``gateway/routes/studio/packages.py`` over ``gateway/studio/packages_store.py``
(A3 and A4) and ``gateway/studio/objects_store.py`` (C1 and the sync). The
hermetic half is ``test_studio_package_routes.py``.

The real handlers run on a phase-4 promoted scratch database, as the
non-privileged app role, so FORCE RLS binds. Each call binds its org as the
auth layer does, and the handler opens ``tenant_session()`` with no org.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    eval "$(bash scripts/dev_db.sh --export)"
    export TENANT_LADDER_DATABASE_URL="${TENANT_LADDER_DATABASE_URL%/*}/<private db>"
    uv run pytest tests/unit/test_studio_package_routes_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (A5).**

a. A member creates a package whose manifest names an object, publishes it,
   and the object rows exist. The door lists the empty object (no
   ``object_missing``).
b. The owner succeeds on each route. Another member, org B with the same
   email, and an App Workshop app get 404 ``Not found`` on each route, and
   write no row.
c. A publish makes the object and field rows. A publish with no change makes
   none, and moves no row (``updated_at`` stays).
d. Each refusal of the sync (``field_removed``, ``field_changed``,
   ``object_removed``, ``required_over_records`` and ``rules_refused``) is 409
   and leaves no version row, no new object row, and no audit row.
e. The rules (the drift of C6 and C7a): a publish that adds a state machine
   stores it. A publish that adds a formula to an object with records is
   refused. A rules list that equals the stored one writes nothing. The phases
   of the sync let a rule and the ``hot`` flag of its field change together.
f. Two publishes of one package give N and N+1, and the rows are made once.
   Two publishes in one org, in opposite order, never deadlock (20 rounds).
   The org lock waits 1 s at most, so a racer may get 503 ``store_busy`` with
   ``Retry-After``. No racer raises (40P01) or is 500, a 503 leaves no
   version row, and each round has a success. A publish racing a draft save
   finishes, and the rows equal the objects of the version.
g. A catalog that fails is 503 and writes nothing. The switch off is 404 on
   each route and writes nothing. A body past the cap is 413.
h. The audit: a create writes one ``action`` row, a publish its own ``publish``
   row, and a sync that changed rows an ``action`` row. A draft save, a 404
   and a refusal write none.
i. The store: ``list_objects`` reads one package, and ``sync_objects`` writes in
   a SAVEPOINT, so a refusal of a late phase undoes an early phase.
j. The caps: a swap of hot fields at the cap of an object or of the org
   passes, also when another member fills the org, because a cold flip that
   no stored rule reads runs first. A refusal of a field step names the
   field. The org cap of objects is 409, and its problem names a next step.
k. The versions list holds 100, newest first, and its cursor reads the rest.
l. Roll-ups across objects (C7b): the rules phase runs in two passes, so a
   roll-up that moves to a new reader, and a reader that drops its roll-up as
   its source gains one, pass in either object order. A roll-up of a roll-up
   is refused in either order. A publish that adds a roll-up stores it, and
   a roll-up on an object with records is refused with no version row. A
   field that a dropped roll-up read can turn cold in the same publish.
m. A rule and a hot swap at a cap: rules pass 1 runs before the cold flips,
   so a rule that moves or goes with a swap passes at the object cap and at
   the org cap, and so does a roll-up that goes with a swap of its read
   field. A rule that goes with a new hot field passes too. A refusal of
   pass 2 rolls back an earlier cold flip and its hot rows.
n. The expected hash of a draft (G1c review round 1): a publish with a stale
   ``based_on`` is 409 ``draft_changed`` and writes nothing. A draft save with
   a stale ``based_on`` is 409 and keeps the draft. Two saves with one hash at
   once give one 200 and one 409. With no hash, each route behaves as before.
   A value that is not a hash is 422.

**The mutations (R7).** Each was run on 2026-11-03. The red tests are in the
build report. M2: drop the sync. M3: sync before the publish. M4: ignore a
removed field. M5: swallow ``ObjectsOutOfSync``. M6: pass an empty catalog.
M7: use ``record_app_audit``. M9: take the owner from the body. M10: skip
``set_constraints`` in the sync.
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
from acb_common.studio_manifest import manifest_hash
from fastapi import HTTPException
from gateway.routes.studio import actions, invoke, packages
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
ANA = f"ana.{TAG}@stua5.test"
BEN = f"ben.{TAG}@stua5.test"
NOT_FOUND = "Not found"

_FIELDS: list[dict[str, Any]] = [
    {"key": "site", "type": "text"},
    {"key": "status", "type": "select", "options": ["planned", "done"], "hot": True},
    {"key": "qty", "type": "number"},
    {"key": "price", "type": "number"},
    {"key": "total", "type": "number", "hot": True},
]
_TOTAL: dict[str, Any] = {
    "name": "line_total", "kind": "formula", "target": "total", "rounding": "half_up",
    "scale": 2, "expr": {"fn": "mul", "args": [{"ref": "qty"}, {"ref": "price"}]},
}
_FLOW: dict[str, Any] = {
    "name": "visit_flow", "kind": "state_machine", "field": "status", "initial": "planned",
    "moves": [{"from": "planned", "to": "done"}],
}
_COUNTER = iter(range(10_000))


def _visit(fields: list[dict[str, Any]] | None = None, rules: list[dict[str, Any]] | None = None,
           name: str = "visit") -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "fields": list(_FIELDS if fields is None else fields)}
    if rules is not None:
        out["rules"] = rules
    return out


def _manifest(mid: str, objects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    objs = [_visit()] if objects is None else objects
    return {
        "studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
        "objects": objs,
        "actions": [{"id": "find", "uses": f"object:{objs[0]['name']}.list"}] if objs else [],
    }


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:workflows"]), organization_id=org)


class _Request:
    """The parts of a request that a handler reads: the stream of the body,
    and, for the action door, the method, the path and the headers of the
    direct proof (B2c-1). It carries no proof, so a door call is not direct."""

    def __init__(self, payload: Any = None, raw: bytes | None = None) -> None:
        self._raw = raw if raw is not None else (
            b"" if payload is None else json.dumps(payload).encode("utf-8"))
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/y")
        self.reads = 0

    async def stream(self) -> Any:
        self.reads += 1
        yield self._raw


async def _call_full(route: str, org: str, email: str, pid: str | None = None,
                     payload: Any = None, raw: bytes | None = None,
                     **query: Any) -> tuple[int, Any, dict[str, str]]:
    """Call the real handler *route* with *org* bound. Answers (200, answer,
    {}) or the status, the detail and the headers of a refusal."""
    handler = getattr(packages, route)
    kwargs: dict[str, Any] = {"user": _user(org, email), **query}
    if pid is not None:
        kwargs["package_id"] = pid
    if route in ("create_package", "save_draft", "publish"):
        kwargs["request"] = _Request(payload, raw)
    token = bind_tenant(org)
    try:
        return 200, await handler(**kwargs), {}
    except HTTPException as exc:
        return exc.status_code, exc.detail, dict(exc.headers or {})
    finally:
        release_tenant(token)


async def _call(route: str, org: str, email: str, pid: str | None = None, payload: Any = None,
                raw: bytes | None = None, **query: Any) -> tuple[int, Any]:
    """:func:`_call_full` with no headers."""
    status, detail, _ = await _call_full(route, org, email, pid, payload, raw, **query)
    return status, detail


def _run(world: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(world.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


def _mid(label: str = "app") -> str:
    return f"a5{TAG}-{label}{next(_COUNTER)}"


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Org A and org B, an App Workshop app of Ana in org A. Every package that
    a test makes has a slug that starts with ``a5<tag>``, and the teardown
    removes it."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    run = uuid.uuid4().hex[:6]
    with p.admin_engine.begin() as c:
        code_app = str(c.execute(text(
            "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id) "
            "VALUES (:s, 'stua5', :oe, '/tmp/stua5', CAST(:o AS uuid)) RETURNING id"),
            {"s": f"a5{TAG}-code-{run}", "oe": ANA, "o": p.org_a}).scalar_one())
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, code_app=code_app)
    yield w
    with p.admin_engine.begin() as c:
        ids = [str(r[0]) for r in c.execute(text(
            "SELECT id FROM apps WHERE slug LIKE :t"), {"t": f"a5{TAG}-%"}).all()]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM app_versions WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"a5{TAG}-%"})


# ── Readers (as the superuser, so RLS hides nothing) ───────────────────────


def _versions(world: Any, pid: str) -> list[int]:
    with world.p.admin_engine.connect() as c:
        return [int(r[0]) for r in c.execute(text(
            "SELECT version FROM app_versions WHERE app_id = CAST(:p AS uuid) ORDER BY version"),
            {"p": pid}).all()]


def _objects(world: Any, pid: str) -> list[tuple[str, str, Any, Any]]:
    """(name, id, updated_at, constraints) of each object row of *pid*."""
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT name, id::text, updated_at, constraints FROM studio_objects "
            "WHERE package_id = CAST(:p AS uuid) ORDER BY name"), {"p": pid}).all()
    return [(r[0], r[1], r[2], r[3] if not isinstance(r[3], str) else json.loads(r[3]))
            for r in rows]


def _fields(world: Any, oid: str) -> list[tuple[str, str, bool, int]]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(
            "SELECT key, type, hot, position FROM studio_object_fields "
            "WHERE object_id = CAST(:o AS uuid) ORDER BY position, key"), {"o": oid}).all()]


def _audits(world: Any, pid: str) -> list[tuple[str, str, dict[str, Any]]]:
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT organization_id::text, kind, detail FROM app_audit "
            "WHERE app_id = CAST(:p AS uuid) ORDER BY id"), {"p": pid}).all()
    return [(r[0], r[1], r[2] if isinstance(r[2], dict) else json.loads(r[2])) for r in rows]


def _app(world: Any, pid: str) -> tuple[Any, ...]:
    with world.p.admin_engine.connect() as c:
        return tuple(c.execute(text(
            "SELECT status, live_version, owner_email, organization_id::text, format, manifest "
            "FROM apps WHERE id = CAST(:p AS uuid)"), {"p": pid}).one())


def _snapshot(world: Any, pid: str) -> dict[str, Any]:
    """Everything that a refused publish must leave as it was."""
    objs = _objects(world, pid)
    app = _app(world, pid)
    return {"versions": _versions(world, pid), "objects": objs,
            "fields": {o[0]: _fields(world, o[1]) for o in objs},
            "status": (app[0], app[1]), "audits": [a[1:] for a in _audits(world, pid)]}


async def _new(org: str, email: str, manifest: dict[str, Any]) -> str:
    status, got = await _call("create_package", org, email, payload=manifest)
    assert status == 200, got
    return str(got["package"]["id"])


async def _publish(org: str, email: str, pid: str, notes: str = "") -> tuple[int, Any]:
    return await _call("publish", org, email, pid, payload={"notes": notes})


async def _save(org: str, email: str, pid: str, manifest: dict[str, Any]) -> tuple[int, Any]:
    return await _call("save_draft", org, email, pid, payload=manifest)


async def _live(org: str, email: str, manifest: dict[str, Any]) -> str:
    """A package of *email*, published once."""
    pid = await _new(org, email, manifest)
    status, got = await _publish(org, email, pid)
    assert status == 200, got
    return pid


async def _record(org: str, pid: str, name: str = "visit", **data: Any) -> str:
    async with tenant_session(org) as db:
        obj = await objects_store.get_object_by_name(db, name, package_id=pid)
        assert obj is not None
        rec = await objects_store.create_record(
            db, ANA, obj.id, data or {"site": "Plant 4"}, package_id=pid)
        assert rec is not None
    return rec.id


# ── a. The first app ───────────────────────────────────────────────────────


def test_r8_a_member_creates_publishes_and_the_object_rows_exist(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(mid))
        return pid, await _publish(world.a, ANA, pid, "first")

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["version"]["version"] == 1 and got["version"]["notes"] == "first"
    assert got["sync"]["objects_created"] == ["visit"]
    rows = _objects(world, pid)
    assert [r[0] for r in rows] == ["visit"]
    assert _fields(world, rows[0][1]) == [
        (f["key"], f["type"], bool(f.get("hot")), i) for i, f in enumerate(_FIELDS)]
    app = _app(world, pid)
    assert (app[0], app[1], app[2], app[3], app[4]) == ("live", 1, ANA, world.a, "manifest")
    assert [o["name"] for o in got["objects"]] == ["visit"]


def test_r8_the_door_lists_the_object_after_the_publish(world) -> None:
    """The live failure that A5 closes: a manifest that names an object with no
    row answered 404 ``object_missing``."""
    async def door(pid: str) -> tuple[int, Any]:
        token = bind_tenant(world.a)
        try:
            out = await invoke.invoke_action(
                pid, "find", request=_Request({}), user=_user(world.a, ANA))
            return 200, out
        except HTTPException as exc:
            return exc.status_code, exc.detail
        finally:
            release_tenant(token)

    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        before = await door(pid)
        await _publish(world.a, ANA, pid)
        return before, await door(pid)

    before, after = _run(world, go)
    assert before == (404, {"code": "object_missing", "message": invoke.NO_OBJECT})
    assert "publish the package" in invoke.NO_OBJECT
    assert after[0] == 200, after


# ── b. The walls ───────────────────────────────────────────────────────────


def _every_route(world: Any, org: str, email: str, pid: str) -> Any:
    async def go() -> list[tuple[int, Any]]:
        return [
            await _call("list_packages", org, email),
            await _call("get_package", org, email, pid),
            await _save(org, email, pid, _manifest("x")),
            await _publish(org, email, pid),
            await _call("list_versions", org, email, pid),
        ]
    return go


def test_r8_the_owner_succeeds_on_each_route(world) -> None:
    mid = _mid()

    async def go() -> list[tuple[int, Any]]:
        out = [await _call("create_package", world.a, ANA, payload=_manifest(mid))]
        pid = out[0][1]["package"]["id"]
        out.append(await _call("list_packages", world.a, ANA))
        out.append(await _call("get_package", world.a, ANA, pid))
        out.append(await _save(world.a, ANA, pid, _manifest(mid)))
        out.append(await _publish(world.a, ANA, pid))
        out.append(await _call("list_versions", world.a, ANA, pid))
        return out

    got = _run(world, go)
    assert [s for s, _ in got] == [200] * 6
    assert [p["manifest_id"] for p in got[1][1]["packages"]] == [mid]
    assert "manifest" not in got[1][1]["packages"][0]
    assert got[2][1]["package"]["manifest"]["id"] == mid and got[2][1]["objects"] == []
    assert [v["version"] for v in got[5][1]["versions"]] == [1]


def test_r8_another_member_of_the_org_gets_404_on_each_route(world) -> None:
    pid = _run(world, lambda: _new(world.a, ANA, _manifest(_mid())))
    before = _snapshot(world, pid)
    got = _run(world, _every_route(world, world.a, BEN, pid))
    # The list is the caller's own, so it is empty and not a 404.
    assert got[0] == (200, {"packages": []})
    assert got[1:] == [(404, NOT_FOUND)] * 4
    assert _snapshot(world, pid) == before


def test_r8_org_b_gets_404_on_each_route_also_with_the_same_email(world) -> None:
    pid = _run(world, lambda: _new(world.a, ANA, _manifest(_mid())))
    before = _snapshot(world, pid)
    got = _run(world, _every_route(world, world.b, ANA, pid))
    assert got[0] == (200, {"packages": []})
    assert got[1:] == [(404, NOT_FOUND)] * 4
    assert _snapshot(world, pid) == before
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM app_audit WHERE organization_id = CAST(:b AS uuid) "
            "AND app_id = CAST(:p AS uuid)"), {"b": world.b, "p": pid}).scalar_one() == 0


def test_r8_an_app_workshop_app_gets_404_on_each_route(world) -> None:
    got = _run(world, _every_route(world, world.a, ANA, world.code_app))
    assert got[0][0] == 200 and world.code_app not in json.dumps(got[0][1], default=str)
    assert got[1:] == [(404, NOT_FOUND)] * 4
    assert _versions(world, world.code_app) == []
    assert _objects(world, world.code_app) == []


def test_r8_a_non_owner_parses_no_byte_and_a_bad_body_does_not_tell_it_the_package_exists(
        world) -> None:
    pid = _run(world, lambda: _new(world.a, ANA, _manifest(_mid())))

    async def go() -> list[tuple[int, Any]]:
        return [await _call("save_draft", world.a, BEN, pid, raw=b"{not json"),
                await _call("publish", world.a, BEN, pid, raw=b"{not json"),
                await _call("save_draft", world.a, ANA, pid, raw=b"{not json"),
                await _call("publish", world.a, ANA, pid, raw=b"{not json")]

    got = _run(world, go)
    assert got[0] == got[1] == (404, NOT_FOUND)
    assert got[2][0] == got[3][0] == 422


def test_r8_a_bad_id_is_404(world) -> None:
    async def go() -> list[tuple[int, Any]]:
        return [await _call("get_package", world.a, ANA, "not-a-uuid"),
                await _call("list_versions", world.a, ANA, "123"),
                await _call("publish", world.a, ANA, "x", payload={}),
                await _call("save_draft", world.a, ANA, "y", payload=_manifest("z"))]

    assert _run(world, go) == [(404, NOT_FOUND)] * 4


def test_r8_the_list_holds_the_callers_packages_only(world) -> None:
    async def go() -> tuple[str, str, str, str, tuple[int, Any], tuple[int, Any]]:
        a = await _new(world.a, ANA, _manifest(_mid()))
        b = await _new(world.a, BEN, _manifest(_mid()))
        o = await _new(world.b, ANA, _manifest(_mid()))
        return (a, b, o, world.code_app, await _call("list_packages", world.a, ANA),
                await _call("list_packages", world.b, ANA))

    a, b, o, code, mine, theirs = _run(world, go)
    assert [p["id"] for p in mine[1]["packages"]] == [a]
    assert [p["id"] for p in theirs[1]["packages"]] == [o]
    assert b not in json.dumps(mine, default=str) and code not in json.dumps(mine, default=str)


def test_r8_a_body_that_names_another_owner_or_org_changes_nothing(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, BEN, _manifest(_mid()))
        return pid, await _call("publish", world.a, BEN, pid, payload={
            "notes": "n", "owner_email": ANA, "organization_id": world.b, "published_by": ANA})

    pid, (status, got) = _run(world, go)
    assert status == 200 and got["version"]["published_by"] == BEN
    app = _app(world, pid)
    assert (app[2], app[3]) == (BEN, world.a)
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT published_by FROM app_versions WHERE app_id = CAST(:p AS uuid)"),
            {"p": pid}).scalar_one() == BEN
    assert {o[1] for o in _objects(world, pid)}
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT created_by, organization_id::text FROM studio_objects "
            "WHERE package_id = CAST(:p AS uuid)"), {"p": pid}).one() == (BEN, world.a)


# ── c. The rows follow the publish ─────────────────────────────────────────


def test_r8_a_publish_with_no_change_makes_and_moves_no_row(world) -> None:
    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(_mid(), [_visit(rules=[_FLOW])]))
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200 and got["version"]["version"] == 2
    assert got["sync"] == {"objects_created": [], "fields_added": [], "hot_changed": [],
                           "rules_set": []}
    assert _versions(world, pid) == [1, 2]
    assert len(_objects(world, pid)) == 1


def test_r8_a_publish_with_no_change_leaves_every_object_row_as_it_was(world) -> None:
    async def first() -> str:
        return await _live(world.a, ANA, _manifest(_mid(), [_visit(rules=[_FLOW])]))

    pid = _run(world, first)
    before = _snapshot(world, pid)
    _run(world, lambda: _publish(world.a, ANA, pid))
    after = _snapshot(world, pid)
    # The object row keeps its updated_at: no field, no hot flip and no rule wrote it.
    assert after["objects"] == before["objects"]
    assert after["fields"] == before["fields"]
    assert after["versions"] == [1, 2]


def test_r8_a_new_field_is_added_and_the_old_rows_stay(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        more = [*_FIELDS, {"key": "note", "type": "text"},
                {"key": "owner", "type": "text", "hot": True}]
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(more)])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["fields_added"] == [["visit", "note"], ["visit", "owner"]]
    rows = _objects(world, pid)
    assert len(rows) == 1
    assert [f[0] for f in _fields(world, rows[0][1])] == [
        "site", "status", "qty", "price", "total", "note", "owner"]
    assert [f[2] for f in _fields(world, rows[0][1])][-2:] == [False, True]


def test_r8_a_new_object_and_a_change_of_hot_come_with_the_publish(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        turned = [{**f, "hot": not f.get("hot", False)} if f["key"] in ("site", "status") else f
                  for f in _FIELDS]
        extra = _visit([{"key": "name", "type": "text", "hot": True}], name="extra")
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(turned), extra])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["objects_created"] == ["extra"]
    assert sorted((h[1], h[2]) for h in got["sync"]["hot_changed"]) == [
        ("site", True), ("status", False)]
    rows = {r[0]: r for r in _objects(world, pid)}
    assert sorted(rows) == ["extra", "visit"]
    hot = {f[0]: f[2] for f in _fields(world, rows["visit"][1])}
    assert (hot["site"], hot["status"]) == (True, False)


def test_r8_a_draft_save_writes_no_object_row(world) -> None:
    mid = _mid()

    async def go() -> str:
        pid = await _new(world.a, ANA, _manifest(mid))
        await _save(world.a, ANA, pid, _manifest(mid, [_visit(), _visit(name="second")]))
        return pid

    pid = _run(world, go)
    assert _objects(world, pid) == [] and _versions(world, pid) == []
    assert _app(world, pid)[:2] == ("draft", None)


def test_r8_a_draft_with_a_new_manifest_id_is_422_and_changes_nothing(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(mid))
        return pid, await _save(world.a, ANA, pid, _manifest(_mid()))

    pid, (status, got) = _run(world, go)
    assert status == 422 and got["errors"][0]["path"] == "$.id"
    assert _app(world, pid)[5]["id"] == mid


# ── d. A refusal of the sync leaves nothing ────────────────────────────────


def _change_site_type(m: dict[str, Any]) -> None:
    m["objects"][0]["fields"][0] = {"key": "site", "type": "number"}


def _drop_site(m: dict[str, Any]) -> None:
    del m["objects"][0]["fields"][0]


def _drop_visit(m: dict[str, Any]) -> None:
    m["objects"] = [_visit([{"key": "name", "type": "text"}], name="other")]
    m["actions"] = []


def _need_a_field(m: dict[str, Any]) -> None:
    m["objects"][0]["fields"].append({"key": "need", "type": "text", "required": True})


def _add_a_formula(m: dict[str, Any]) -> None:
    m["objects"][0]["rules"] = [_TOTAL]


_REFUSALS = [
    ("field_changed", _change_site_type, None),
    ("field_removed", _drop_site, None),
    ("object_removed", _drop_visit, None),
    ("required_over_records", _need_a_field, None),
    ("rules_refused", _add_a_formula, "line_total"),
]


@pytest.mark.parametrize("kind,change,rule", _REFUSALS, ids=[r[0] for r in _REFUSALS])
def test_r8_each_refusal_is_409_and_leaves_no_version_and_no_object_row(
        world, kind, change, rule) -> None:
    mid = _mid()

    async def setup() -> str:
        pid = await _live(world.a, ANA, _manifest(mid))
        await _record(world.a, pid, site="Plant 4")
        return pid

    pid = _run(world, setup)
    before = _snapshot(world, pid)
    bad = _manifest(mid)
    change(bad)
    # A new object in the same draft must not stay when the publish is refused.
    if bad["objects"][0]["name"] == "visit":
        bad["objects"].append(_visit([{"key": "name", "type": "text"}], name="extra"))
    status, got = _run(world, lambda: _save(world.a, ANA, pid, bad))
    assert status == 200, got
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 409, got
    assert got["code"] == "objects_out_of_sync"
    assert kind in {p["kind"] for p in got["problems"]}
    if rule is not None:
        assert [p["rule"] for p in got["problems"] if p["kind"] == kind] == [rule]
    after = _snapshot(world, pid)
    assert after == before, "a refused publish changed a row"
    assert [o[0] for o in _objects(world, pid)] == ["visit"]


def test_r8_a_refused_publish_leaves_the_record_and_the_live_version(world) -> None:
    mid = _mid()

    async def setup() -> tuple[str, str]:
        pid = await _live(world.a, ANA, _manifest(mid))
        return pid, await _record(world.a, pid, site="Plant 4")

    pid, rid = _run(world, setup)
    bad = _manifest(mid)
    _drop_site(bad)
    _run(world, lambda: _save(world.a, ANA, pid, bad))
    status, _ = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 409
    with world.p.admin_engine.connect() as c:
        data = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                         {"r": rid}).scalar_one()
    assert (data if isinstance(data, dict) else json.loads(data)) == {"site": "Plant 4"}
    app = _app(world, pid)
    assert (app[0], app[1]) == ("live", 1)
    # The draft keeps the edit, so the member can fix it.
    assert app[5]["objects"][0]["fields"][0]["key"] == "status"


def test_r8_a_refusal_names_each_problem_with_its_object_and_field(world) -> None:
    mid = _mid()
    pid = _run(world, lambda: _live(world.a, ANA, _manifest(mid)))
    bad = _manifest(mid)
    bad["objects"][0]["fields"] = [{"key": "site", "type": "number"},
                                   {"key": "status", "type": "text"}]
    _run(world, lambda: _save(world.a, ANA, pid, bad))
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 409
    seen = sorted((p["kind"], p["object"], p["field"]) for p in got["problems"])
    assert ("field_changed", "visit", "site") in seen
    assert ("field_changed", "visit", "status") in seen
    assert ("field_removed", "visit", "qty") in seen


# ── e. The rules ───────────────────────────────────────────────────────────


def test_r8_a_publish_that_adds_a_state_machine_stores_it(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(rules=[_FLOW])])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["rules_set"] == ["visit"]
    stored = _objects(world, pid)[0][3]
    assert [r["kind"] for r in stored] == ["state_machine"] and stored[0]["name"] == "visit_flow"
    assert got["objects"][0]["rules"][0]["moves"] == [{"from": "planned", "to": "done"}]
    # The sync wrote an audit row that names the rules.
    syncs = [a for a in _audits(world, pid) if a[2].get("op") == "package.objects_sync"]
    assert syncs[-1][2]["rules"] == ["visit"]


def test_r8_a_new_object_with_rules_stores_them_with_its_fields(world) -> None:
    pid = _run(world, lambda: _live(world.a, ANA, _manifest(_mid(), [_visit(rules=[_FLOW])])))
    stored = _objects(world, pid)[0][3]
    assert [r["name"] for r in stored] == ["visit_flow"]


def test_r8_a_publish_that_adds_a_formula_to_an_object_with_records_is_refused(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        await _record(world.a, pid, site="Plant 4")
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(rules=[_TOTAL])])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 409, got
    problem = got["problems"][0]
    assert (problem["kind"], problem["object"], problem["rule"]) == (
        "rules_refused", "visit", "line_total")
    assert "declare before the first record" in problem["message"]
    assert _versions(world, pid) == [1]
    assert _objects(world, pid)[0][3] == []


def test_r8_a_publish_that_adds_a_formula_to_an_object_with_no_record_passes(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(rules=[_TOTAL])])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert [r["kind"] for r in _objects(world, pid)[0][3]] == ["formula"]
    # A formula computes on write: the record gets its total.
    rid = _run(world, lambda: _record(world.a, pid, site="x", qty=3, price=4))
    with world.p.admin_engine.connect() as c:
        data = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                         {"r": rid}).scalar_one()
    assert float((data if isinstance(data, dict) else json.loads(data))["total"]) == 12.0


def test_r8_a_rules_list_that_equals_the_stored_one_writes_nothing(world) -> None:
    mid = _mid()
    manifest = _manifest(mid, [_visit(rules=[_FLOW, _TOTAL])])
    pid = _run(world, lambda: _live(world.a, ANA, manifest))
    before = _objects(world, pid)
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 200 and got["sync"]["rules_set"] == []
    assert _objects(world, pid) == before


def test_r8_a_formula_that_stays_passes_on_an_object_with_records(world) -> None:
    mid = _mid()
    manifest = _manifest(mid, [_visit(rules=[_TOTAL])])

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, manifest)
        await _record(world.a, pid, site="x", qty=1, price=2)
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200 and got["sync"]["rules_set"] == []
    assert _versions(world, pid) == [1, 2]


def test_r8_a_publish_that_removes_the_rules_clears_them(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_visit(rules=[_FLOW])]))
        assert (await _save(world.a, ANA, pid, _manifest(mid)))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200 and got["sync"]["rules_set"] == ["visit"]
    assert _objects(world, pid)[0][3] == []


_SKU = [{"key": "sku", "type": "text", "hot": True}, {"key": "name", "type": "text"}]
_UNIQUE = {"name": "one_sku", "kind": "unique", "fields": ["sku"]}


def test_r8_a_rule_and_the_hot_flag_of_its_field_can_leave_together(world) -> None:
    """The rule goes and its field turns cold in one publish. The rule must go
    first, or the store refuses the cold field."""
    mid = _mid()
    cold = [{"key": "sku", "type": "text"}, {"key": "name", "type": "text"}]

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_visit(_SKU, [_UNIQUE])]))
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(cold)])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["hot_changed"] == [["visit", "sku", False]]
    assert _objects(world, pid)[0][3] == []


def test_r8_a_rule_and_the_hot_flag_of_its_field_can_arrive_together(world) -> None:
    """The field turns hot and a rule reads it in one publish. The field must
    turn hot first."""
    mid = _mid()
    cold = [{"key": "sku", "type": "text"}, {"key": "name", "type": "text"}]

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_visit(cold)]))
        assert (await _save(world.a, ANA, pid, _manifest(mid, [_visit(_SKU, [_UNIQUE])])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["hot_changed"] == [["visit", "sku", True]]
    assert [r["name"] for r in _objects(world, pid)[0][3]] == ["one_sku"]


# ── f. Two writers ─────────────────────────────────────────────────────────


def test_r8_two_publishes_of_one_package_give_n_and_n_plus_1_with_rows_made_once(world) -> None:
    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        return pid, list(await asyncio.gather(
            _publish(world.a, ANA, pid, "one"), _publish(world.a, ANA, pid, "two")))

    pid, got = _run(world, go)
    assert [s for s, _ in got] == [200, 200], got
    assert sorted(g["version"]["version"] for _, g in got) == [1, 2]
    assert _versions(world, pid) == [1, 2]
    rows = _objects(world, pid)
    assert len(rows) == 1 and len(_fields(world, rows[0][1])) == len(_FIELDS)
    # Exactly one of the two syncs wrote rows.
    assert sorted(len(g["sync"]["objects_created"]) for _, g in got) == [0, 1]


def _ok_or_busy(results: list[Any]) -> None:
    """Each answer of a race is 200, or 503 ``store_busy`` with ``Retry-After``:
    the org lock waits at most 1 s, and under load a racer can pass that
    bound. A retry fixes a 503. A deadlock (40P01) or any other error escapes
    the handler, and the app would answer 500, so it fails here."""
    for got in results:
        if isinstance(got, BaseException):
            state = getattr(getattr(got, "orig", None), "sqlstate", None)
            raise AssertionError(f"a racer raised {type(got).__name__} (sqlstate {state})")
        status, detail, headers = got
        assert status in (200, 503), got
        if status == 503:
            assert detail["code"] == "store_busy", got
            assert headers.get("Retry-After") == packages.RETRY_AFTER, got


def _dense(world: Any, pid: str, got: tuple[int, Any, dict[str, str]]) -> None:
    """One publish of a new package: a success is version 1, and a 503 leaves
    no version row and no object row, so the numbers keep no gap."""
    status, detail, _ = got
    if status == 200:
        assert detail["version"]["version"] == 1, got
        assert _versions(world, pid) == [1]
    else:
        assert _versions(world, pid) == [] and _objects(world, pid) == []


def test_r8_two_publishes_in_one_org_never_deadlock_in_either_order(world) -> None:
    """Two real connections, 20 rounds, two packages of one org with new
    objects each. The starting order flips each round. A lock taken in the
    other order (the org lock before the ``apps`` row) deadlocks here. The
    racer that takes the org lock first never waits, so each round has a
    success."""
    def manifest() -> dict[str, Any]:
        # Cold fields: 20 rounds of hot fields would pass the hot budget of the org.
        plain = [{"key": "name", "type": "text"}, {"key": "note", "type": "text"}]
        return _manifest(_mid("race"), [_visit(plain, name="first"), _visit(plain, name="second")])

    async def go() -> list[list[tuple[str, Any]]]:
        rounds = []
        for i in range(20):
            a = await _new(world.a, ANA, manifest())
            b = await _new(world.a, BEN, manifest())
            pairs = [(a, ANA), (b, BEN)]
            if i % 2:
                pairs.reverse()
            got = await asyncio.gather(
                *(_call_full("publish", world.a, who, pid, payload={}) for pid, who in pairs),
                return_exceptions=True)
            rounds.append([(pid, g) for (pid, _), g in zip(pairs, got, strict=True)])
        return rounds

    for got in _run(world, go):
        _ok_or_busy([g for _, g in got])
        assert any(g[0] == 200 for _, g in got), got
        assert all(len(g[1]["objects"]) == 2 for _, g in got if g[0] == 200)
        for pid, g in got:
            _dense(world, pid, g)


def test_r8_a_publish_racing_a_draft_save_finishes_and_the_rows_equal_the_version(world) -> None:
    mid = _mid()
    more = [*_FIELDS, {"key": "note", "type": "text"}]

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _live(world.a, ANA, _manifest(mid))
        return pid, list(await asyncio.gather(
            _publish(world.a, ANA, pid), _save(world.a, ANA, pid, _manifest(mid, [_visit(more)]))))

    pid, got = _run(world, go)
    assert [s for s, _ in got] == [200, 200], got
    with world.p.admin_engine.connect() as c:
        manifest = c.execute(text(
            "SELECT manifest FROM app_versions WHERE app_id = CAST(:p AS uuid) "
            "ORDER BY version DESC LIMIT 1"), {"p": pid}).scalar_one()
    manifest = manifest if isinstance(manifest, dict) else json.loads(manifest)
    want = [f["key"] for f in manifest["objects"][0]["fields"]]
    rows = _objects(world, pid)
    assert [f[0] for f in _fields(world, rows[0][1])] == want


def test_r8_a_publish_and_a_draft_save_of_many_packages_never_deadlock(world) -> None:
    """Six publishes and six draft saves at once. A save takes no org lock, so
    it is always 200. A publish is 200, or 503 when the org lock does not come
    in 1 s."""
    async def go() -> tuple[list[str], list[Any]]:
        mids = [_mid("many") for _ in range(6)]
        pids = [await _new(world.a, ANA, _manifest(m)) for m in mids]
        calls: list[Awaitable[tuple[int, Any, dict[str, str]]]] = []
        for pid, m in zip(pids, mids, strict=True):
            calls.append(_call_full("publish", world.a, ANA, pid, payload={}))
            calls.append(_call_full("save_draft", world.a, ANA, pid,
                                    payload=_manifest(m, [_visit(), _visit(name="two")])))
        return pids, list(await asyncio.gather(*calls, return_exceptions=True))

    pids, got = _run(world, go)
    _ok_or_busy(got)
    publishes, saves = got[0::2], got[1::2]
    assert [g[0] for g in saves] == [200] * len(pids), saves
    assert any(g[0] == 200 for g in publishes), publishes
    for pid, g in zip(pids, publishes, strict=True):
        _dense(world, pid, g)


# ── g. Waits, the switch and the cap ───────────────────────────────────────


def test_r8_a_catalog_that_fails_is_503_and_writes_nothing(world, monkeypatch) -> None:
    async def down() -> list[dict[str, Any]]:
        raise RuntimeError("the catalog is down")

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        monkeypatch.setattr(actions, "catalog_rows", down)
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert (status, got["code"]) == (503, "catalog_unavailable")
    assert _versions(world, pid) == [] and _objects(world, pid) == []
    assert [a[1] for a in _audits(world, pid)] == ["action"]  # the create only


def test_r8_a_manifest_that_a2_refuses_with_the_catalog_is_422_and_writes_nothing(world) -> None:
    mid = _mid()
    bad = _manifest(mid)
    bad["actions"] = [{"id": "ghost", "uses": "knowledge.no_such_op"}]

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, bad)
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 422 and got["errors"]
    assert _versions(world, pid) == [] and _objects(world, pid) == []


def test_r8_with_the_switch_off_each_route_is_404_and_writes_nothing(world, monkeypatch) -> None:
    pid = _run(world, lambda: _new(world.a, ANA, _manifest(_mid())))
    before = _snapshot(world, pid)
    monkeypatch.setattr(get_settings(), "studio_orgs", "")

    async def go() -> list[tuple[int, Any]]:
        return [await _call("create_package", world.a, ANA, raw=b"{not json"),
                await _call("list_packages", world.a, ANA),
                await _call("get_package", world.a, ANA, pid),
                await _call("save_draft", world.a, ANA, pid, raw=b"{not json"),
                await _call("publish", world.a, ANA, pid, raw=b"{not json"),
                await _call("list_versions", world.a, ANA, pid)]

    assert _run(world, go) == [(404, NOT_FOUND)] * 6
    assert _snapshot(world, pid) == before


def test_r8_a_body_past_the_cap_is_413_and_writes_nothing(world) -> None:
    big = b"x" * (packages.MAX_BODY_BYTES + 1)

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        return pid, [await _call("create_package", world.a, ANA, raw=big),
                     await _call("save_draft", world.a, ANA, pid, raw=big),
                     await _call("publish", world.a, ANA, pid, raw=big)]

    pid, got = _run(world, go)
    assert [(s, d["code"]) for s, d in got] == [(413, "body_too_large")] * 3
    assert _versions(world, pid) == []


def test_r8_a_second_create_with_the_same_manifest_id_is_409_with_the_first_id(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        first = await _new(world.a, ANA, _manifest(mid))
        again = await _call("create_package", world.a, ANA, payload=_manifest(mid))
        # Another member and another org may use the id.
        other = await _call("create_package", world.a, BEN, payload=_manifest(mid))
        return first, again, other

    first, again, other = _run(world, go)
    assert again[0] == 409 and again[1]["code"] == "package_exists"
    assert again[1]["package_id"] == first
    assert other[0] == 200
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM apps WHERE owner_email = :o AND slug LIKE :s"),
            {"o": ANA, "s": f"{mid}-%"}).scalar_one() == 1


def test_r8_a_member_holds_at_most_100_packages(world) -> None:
    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        async with tenant_session(world.a) as db:
            for _ in range(packages.MAX_PACKAGES_PER_MEMBER):
                await packages_store.create_package(db, ANA, _manifest(_mid("cap")))
        full = await _call("create_package", world.a, ANA, payload=_manifest(_mid("cap")))
        other = await _call("create_package", world.a, BEN, payload=_manifest(_mid("cap")))
        return full, other

    full, other = _run(world, go)
    assert (full[0], full[1]["code"]) == (409, "package_limit")
    assert other[0] == 200
    with world.p.admin_engine.connect() as c:
        assert c.execute(text(
            "SELECT count(*) FROM apps WHERE owner_email = :o AND slug LIKE :s"),
            {"o": ANA, "s": f"a5{TAG}-cap%"}).scalar_one() == 100


# ── h. The audit ───────────────────────────────────────────────────────────


def test_r8_the_audit_rows_of_a_create_a_publish_and_a_publish_with_no_change(world) -> None:
    mid = _mid()

    async def go() -> str:
        pid = await _new(world.a, ANA, _manifest(mid))
        # A draft save writes no audit row.
        await _save(world.a, ANA, pid, _manifest(mid, [_visit(), _visit(name="second")]))
        await _publish(world.a, ANA, pid)
        await _publish(world.a, ANA, pid)
        return pid

    pid = _run(world, go)
    rows = _audits(world, pid)
    assert {r[0] for r in rows} == {world.a}
    summary = [(r[1], r[2].get("op")) for r in rows]
    assert summary == [
        ("action", "package.create"),
        ("publish", None), ("action", "package.objects_sync"),
        ("publish", None),
    ]
    assert len(rows[0][2]["sha256"]) == 64 and rows[0][2]["bytes"] > 100
    assert rows[2][2]["objects"] == ["visit", "second"] and rows[2][2]["version"] == 1
    # No audit row holds the manifest text.
    assert "Track install visits" not in json.dumps(rows, default=str)


def test_r8_a_404_and_a_refusal_write_no_audit_row(world) -> None:
    mid = _mid()

    async def go() -> str:
        pid = await _live(world.a, ANA, _manifest(mid))
        await _publish(world.a, BEN, pid)
        await _publish(world.b, ANA, pid)
        bad = _manifest(mid)
        _drop_site(bad)
        await _save(world.a, ANA, pid, bad)
        await _publish(world.a, ANA, pid)
        return pid

    pid = _run(world, go)
    assert [(r[1], r[2].get("op")) for r in _audits(world, pid)] == [
        ("action", "package.create"), ("publish", None), ("action", "package.objects_sync")]


# ── i. The store ───────────────────────────────────────────────────────────


def test_r8_list_objects_reads_one_package_with_fields_and_rules(world) -> None:
    mid = _mid()

    async def go() -> tuple[Any, Any, Any]:
        pid = await _live(world.a, ANA, _manifest(mid, [
            _visit(rules=[_FLOW]), _visit([{"key": "name", "type": "text"}], name="second")]))
        other = await _live(world.a, ANA, _manifest(_mid(), [_visit(name="elsewhere")]))
        async with tenant_session(world.a) as db:
            mine = await objects_store.list_objects(db, pid)
            theirs = await objects_store.list_objects(db, other)
        async with tenant_session(world.b) as db:
            wrong_org = await objects_store.list_objects(db, pid)
        return mine, theirs, wrong_org

    mine, theirs, wrong_org = _run(world, go)
    assert [o.name for o in mine] == ["second", "visit"] or [o.name for o in mine] == [
        "visit", "second"]
    visit = next(o for o in mine if o.name == "visit")
    assert [f.key for f in visit.fields] == [f["key"] for f in _FIELDS]
    assert [(f.key, f.hot) for f in visit.fields if f.hot] == [("status", True), ("total", True)]
    assert [r.name for r in visit.constraints] == ["visit_flow"]
    assert [o.name for o in theirs] == ["elsewhere"]
    assert wrong_org == []


def test_r8_a_sync_for_an_app_workshop_app_or_another_org_answers_none(world) -> None:
    async def go() -> tuple[Any, Any]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        async with tenant_session(world.a) as db:
            code = await objects_store.sync_objects(
                db, ANA, package_id=world.code_app, objects=[_visit()])
        async with tenant_session(world.b) as db:
            wrong = await objects_store.sync_objects(db, ANA, package_id=pid, objects=[_visit()])
        return code, wrong

    assert _run(world, go) == (None, None)
    assert _objects(world, world.code_app) == []


def test_r8_a_refusal_of_a_late_phase_undoes_an_early_phase(world) -> None:
    """The write phase runs in a SAVEPOINT. A formula is refused in the rules
    phase, after the new object was made. The caller commits, and the new
    object must not stay."""
    mid = _mid()

    async def go() -> tuple[str, Any]:
        pid = await _live(world.a, ANA, _manifest(mid))
        await _record(world.a, pid, site="x")
        wanted = [_visit(rules=[_TOTAL]), _visit([{"key": "name", "type": "text"}], name="zeta")]
        raised = None
        async with tenant_session(world.a) as db:
            try:
                await objects_store.sync_objects(db, ANA, package_id=pid, objects=wanted)
            except objects_store.ObjectsOutOfSync as exc:
                raised = exc
        return pid, raised

    pid, raised = _run(world, go)
    assert raised is not None
    assert [(p.kind, p.object, p.rule) for p in raised.problems] == [
        ("rules_refused", "visit", "line_total")]
    assert [o[0] for o in _objects(world, pid)] == ["visit"], "the early phase stayed"


def test_r8_a_plan_problem_writes_nothing_and_lists_every_problem(world) -> None:
    mid = _mid()

    async def go() -> tuple[str, Any]:
        pid = await _live(world.a, ANA, _manifest(mid))
        wanted = [_visit([{"key": "site", "type": "number"}]), _visit(name="zeta")]
        async with tenant_session(world.a) as db:
            try:
                await objects_store.sync_objects(db, ANA, package_id=pid, objects=wanted)
            except objects_store.ObjectsOutOfSync as exc:
                return pid, exc
        return pid, None

    pid, raised = _run(world, go)
    assert raised is not None
    kinds = sorted(p.kind for p in raised.problems)
    assert kinds == ["field_changed", "field_removed", "field_removed", "field_removed",
                     "field_removed"]
    assert [o[0] for o in _objects(world, pid)] == ["visit"]


def test_r8_pages_stay_in_the_manifest_and_make_no_row(world) -> None:
    """``pages`` are manifest only (D11a). The sync takes the objects and
    projects nothing else."""
    mid = _mid()
    manifest = _manifest(mid)
    manifest["views"] = [{"id": "intro", "block": "markdown", "text": "Welcome."}]
    manifest["pages"] = [{"id": "home", "title": "Home", "cells": [{"view": "intro"}]}]

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _new(world.a, ANA, manifest)
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert [o[0] for o in _objects(world, pid)] == ["visit"]
    assert _app(world, pid)[5]["pages"][0]["id"] == "home"


# ── j. The caps ────────────────────────────────────────────────────────────


def _hot_obj(name: str, hot: int, cold: int = 0) -> dict[str, Any]:
    """An object with *hot* hot fields ``h1``.. and *cold* cold fields ``c1``.."""
    fields = [{"key": f"h{i}", "type": "text", "hot": True} for i in range(1, hot + 1)]
    fields += [{"key": f"c{i}", "type": "text"} for i in range(1, cold + 1)]
    return {"name": name, "fields": fields}


def _heat(obj: dict[str, Any], **hot: bool) -> dict[str, Any]:
    """A copy of *obj* with the ``hot`` flag of each named field changed."""
    out = {**obj, "fields": [dict(f) for f in obj["fields"]]}
    for f in out["fields"]:
        if f["key"] in hot:
            f["hot"] = hot[f["key"]]
    return out


def _hot_map(world: Any, pid: str, name: str) -> dict[str, bool]:
    oid = next(o[1] for o in _objects(world, pid) if o[0] == name)
    return {f[0]: f[2] for f in _fields(world, oid)}


def _org_hot(world: Any) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_object_fields "
            "WHERE organization_id = CAST(:o AS uuid) AND hot"), {"o": world.a}).scalar_one())


async def _fill_hot(world: Any, who: str, count: int) -> None:
    """Publish packages of *who* that hold *count* hot fields in all."""
    while count > 0:
        n = min(count, objects_store.MAX_HOT_PER_OBJECT)
        await _live(world.a, who, _manifest(_mid("fill"), [_hot_obj("thing", n)]))
        count -= n


def test_r8_a_hot_swap_at_the_object_cap_passes(world) -> None:
    """Eight hot fields: ``h8`` turns cold and ``c1`` turns hot in one publish.
    The cold flip runs first, so the hot budget of the object holds."""
    mid = _mid("swap")
    full = _hot_obj("inv", objects_store.MAX_HOT_PER_OBJECT, cold=1)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [full]))
        assert (await _save(world.a, ANA, pid, _manifest(
            mid, [_heat(full, h8=False, c1=True)])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert sorted(map(tuple, got["sync"]["hot_changed"])) == [
        ("inv", "c1", True), ("inv", "h8", False)]
    hot = _hot_map(world, pid, "inv")
    assert (hot["h8"], hot["c1"], sum(hot.values())) == (False, True, 8)
    assert _versions(world, pid) == [1, 2]


def test_r8_a_new_hot_field_and_a_cold_flip_at_the_object_cap_pass(world) -> None:
    mid = _mid("swapnew")
    full = _hot_obj("inv", objects_store.MAX_HOT_PER_OBJECT)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [full]))
        more = _heat(full, h8=False)
        more["fields"].append({"key": "b", "type": "text", "hot": True})
        assert (await _save(world.a, ANA, pid, _manifest(mid, [more])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["fields_added"] == [["inv", "b"]]
    assert got["sync"]["hot_changed"] == [["inv", "h8", False]]
    hot = _hot_map(world, pid, "inv")
    assert (hot["h8"], hot["b"], sum(hot.values())) == (False, True, 8)


def test_r8_a_hot_swap_at_the_org_cap_passes_when_another_member_fills_it(world) -> None:
    """Ben fills the org up to four hot fields short of the cap, and Ana's
    package takes the last four. Ana then swaps one hot field for another."""
    mid = _mid("orgswap")
    mine = _hot_obj("inv", 4, cold=1)
    room = objects_store.MAX_HOT_PER_ORG - _org_hot(world) - 4
    assert room >= 0, "the org holds too many hot fields for this test"

    async def go() -> tuple[str, tuple[int, Any]]:
        await _fill_hot(world, BEN, room)
        pid = await _live(world.a, ANA, _manifest(mid, [mine]))
        assert (await _save(world.a, ANA, pid, _manifest(
            mid, [_heat(mine, h4=False, c1=True)])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert _org_hot(world) == objects_store.MAX_HOT_PER_ORG
    assert status == 200, got
    hot = _hot_map(world, pid, "inv")
    assert (hot["h4"], hot["c1"]) == (False, True)


def test_r8_a_refusal_of_the_write_phase_names_the_field(world) -> None:
    """At the org cap, one more hot field is refused. The problem names the
    field: a field that turns hot, and a new hot field."""
    mid = _mid("orgfull")
    mine = _hot_obj("inv", 4, cold=1)
    room = objects_store.MAX_HOT_PER_ORG - _org_hot(world) - 4
    assert room >= 0, "the org holds too many hot fields for this test"

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        await _fill_hot(world, BEN, room)
        pid = await _live(world.a, ANA, _manifest(mid, [mine]))
        await _save(world.a, ANA, pid, _manifest(mid, [_heat(mine, c1=True)]))
        flip = await _publish(world.a, ANA, pid)
        more = {**mine, "fields": [*mine["fields"], {"key": "b", "type": "text", "hot": True}]}
        await _save(world.a, ANA, pid, _manifest(mid, [more]))
        return pid, flip, await _publish(world.a, ANA, pid)

    pid, flip, new = _run(world, go)
    for (status, got), key in ((flip, "c1"), (new, "b")):
        assert status == 409, got
        assert [(p["kind"], p["object"], p["field"]) for p in got["problems"]] == [
            ("store_refused", "inv", key)]
        message = got["problems"][0]["message"]
        assert f"at most {objects_store.MAX_HOT_PER_ORG} hot fields" in message
        # One sentence that names a next step.
        assert message.endswith("so turn a hot field cold first.") and ". " not in message
    assert _versions(world, pid) == [1]


def _org_objects(world: Any) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_objects WHERE organization_id = CAST(:o AS uuid)"),
            {"o": world.a}).scalar_one())


def test_r8_the_org_object_cap_names_a_next_step(world) -> None:
    """One member can fill the org cap of objects, and no route deletes a
    package yet (A5c). So the refusal of another member names a next step."""
    cap, per = objects_store.MAX_OBJECTS_PER_ORG, objects_store.MAX_OBJECTS_PER_PACKAGE
    room = cap - _org_objects(world)
    assert room >= 0, "the org holds too many objects for this test"

    async def go() -> tuple[str, tuple[int, Any]]:
        left = room
        while left > 0:
            n = min(left, per)
            objs = [{"name": f"o{i}", "fields": [{"key": "a", "type": "text"}]} for i in range(n)]
            await _live(world.a, BEN, _manifest(_mid("big"), objs))
            left -= n
        pid = await _new(world.a, ANA, _manifest(_mid("late")))
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert _org_objects(world) == cap
    assert status == 409, got
    [problem] = got["problems"]
    assert (problem["kind"], problem["object"]) == ("store_refused", "visit")
    assert f"at most {cap} objects" in problem["message"]
    assert "ask an admin to remove a package" in problem["message"]
    assert _versions(world, pid) == [] and _objects(world, pid) == []


# ── k. The page of versions ────────────────────────────────────────────────


def test_r8_the_versions_list_holds_at_most_100_newest_first_with_a_cursor(world) -> None:
    """101 versions. The first page holds the newest 100 and the cursor of the
    next page. The cursor reads the rest, and a stranger with a cursor gets
    404."""
    page = packages.VERSIONS_PAGE

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, _manifest(_mid("many")))
        async with tenant_session(world.a) as db:
            for _ in range(page + 1):
                await packages_store.publish_version(db, ANA, pid, "", catalog={})
        return pid, [
            await _call("list_versions", world.a, ANA, pid),
            await _call("list_versions", world.a, ANA, pid, before="2"),
            await _call("list_versions", world.a, ANA, pid, before="50"),
            await _call("list_versions", world.a, BEN, pid, before="50"),
            await _call("list_versions", world.b, ANA, pid, before="50"),
            await _call("list_versions", world.a, ANA, world.code_app, before="50"),
            await _call("list_versions", world.a, ANA, pid, before="0"),
        ]

    pid, (first, last, middle, ben, org_b, code, bad) = _run(world, go)
    assert _versions(world, pid) == list(range(1, page + 2))
    assert [v["version"] for v in first[1]["versions"]] == list(range(page + 1, 1, -1))
    assert first[1]["next_before"] == 2
    assert last == (200, {"versions": [last[1]["versions"][0]], "next_before": None})
    assert last[1]["versions"][0]["version"] == 1
    assert [v["version"] for v in middle[1]["versions"]] == list(range(49, 0, -1))
    assert middle[1]["next_before"] is None
    assert ben == org_b == code == (404, NOT_FOUND)
    assert bad[0] == 422 and bad[1]["errors"][0]["path"] == "before"


def test_r8_the_store_refuses_a_page_past_the_limit_or_a_bad_cursor(world) -> None:
    async def go() -> list[Any]:
        pid = await _new(world.a, ANA, _manifest(_mid()))
        out: list[Any] = []
        async with tenant_session(world.a) as db:
            for kwargs in ({"limit": packages_store.MAX_VERSIONS_PAGE + 1}, {"limit": 0},
                           {"limit": True}, {"before": 0}, {"before": "5"}):
                try:
                    await packages_store.list_versions(db, ANA, pid, **kwargs)
                    out.append(None)
                except packages_store.PackageStoreError as exc:
                    out.append(exc.paths)
        return out

    assert all(p is not None for p in _run(world, go))


# ── l. Roll-ups across objects (C7b) ───────────────────────────────────────

_ORDER_TOTAL: dict[str, Any] = {
    "name": "order_total", "kind": "rollup", "target": "total", "source": "line",
    "via": "order_ref", "agg": "sum", "field": "amount"}
_CUST_SPENT: dict[str, Any] = {
    "name": "cust_spent", "kind": "rollup", "target": "spent", "source": "order",
    "via": "cust_ref", "agg": "sum", "field": "total"}


def _shop(mid: str, objects: list[dict[str, Any]]) -> dict[str, Any]:
    return {"studio": 1, "id": mid, "name": "Shop", "purpose": "Track orders.",
            "objects": objects, "actions": []}


def _line(amount_hot: bool = True) -> dict[str, Any]:
    return {"name": "line", "fields": [
        {"key": "order_ref", "type": "relation", "to": "order", "hot": True},
        {"key": "amount", "type": "number", "hot": amount_hot}]}


def _order(rules: list[dict[str, Any]] | None = None, cust: bool = False) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [{"key": "code", "type": "text"},
                                    {"key": "total", "type": "number", "hot": True}]
    if cust:
        fields.append({"key": "cust_ref", "type": "relation", "to": "cust", "hot": True})
    return {"name": "order", "fields": fields, **({"rules": rules} if rules else {})}


def _cust(rules: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"name": "cust", "fields": [{"key": "spent", "type": "number"}],
            **({"rules": rules} if rules else {})}


def _in_order(objs: dict[str, dict[str, Any]], names: str) -> list[dict[str, Any]]:
    return [objs[n] for n in names.split(",")]


def _rule_names(world: Any, pid: str) -> dict[str, list[str]]:
    return {o[0]: [r["name"] for r in o[3]] for o in _objects(world, pid)}


async def _republish(world: Any, mid: str, pid: str,
                     objects: list[dict[str, Any]]) -> tuple[int, Any]:
    status, got = await _save(world.a, ANA, pid, _shop(mid, objects))
    assert status == 200, got
    return await _publish(world.a, ANA, pid)


@pytest.mark.parametrize("names", ["cust,order,line", "order,line,cust"])
def test_r8_a_roll_up_that_moves_to_a_new_reader_passes_in_either_order(world, names) -> None:
    """v1: ``order.total`` is a roll-up of ``line.amount``. v2 drops it, and a
    new object ``cust`` rolls up ``order.total``. The rules phase removes the
    old roll-up in its first pass, so the order of the objects does not count."""
    mid = _mid("move")
    v2 = {"cust": _cust([_CUST_SPENT]), "order": _order(cust=True), "line": _line()}

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _shop(mid, [_order([_ORDER_TOTAL]), _line()]))
        return pid, await _republish(world, mid, pid, _in_order(v2, names))

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert sorted(got["sync"]["rules_set"]) == ["cust", "order"]
    assert _rule_names(world, pid) == {"cust": ["cust_spent"], "line": [], "order": []}
    assert _versions(world, pid) == [1, 2]


@pytest.mark.parametrize("names", ["order,cust,line", "cust,order,line"])
def test_r8_a_reader_that_drops_its_roll_up_as_its_source_gains_one_passes(world, names) -> None:
    """v1: ``cust.spent`` rolls up ``order.total``. v2 drops that roll-up and
    makes ``order.total`` a roll-up of ``line.amount``. The reader loses its
    rule in the first pass, so the source can gain its rule in either order."""
    mid = _mid("swaprole")
    v1 = [_order(cust=True), _cust([_CUST_SPENT]), _line()]
    v2 = {"order": _order([_ORDER_TOTAL], cust=True), "cust": _cust(), "line": _line()}

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _shop(mid, v1))
        return pid, await _republish(world, mid, pid, _in_order(v2, names))

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert _rule_names(world, pid) == {"cust": [], "line": [], "order": ["order_total"]}


@pytest.mark.parametrize("names", ["order,cust,line", "cust,order,line"])
def test_r8_a_roll_up_of_a_roll_up_is_refused_in_either_order(world, names) -> None:
    """``order.total`` becomes a roll-up while ``cust.spent`` still reads it. A2
    refuses the draft, and the sync refuses the same objects on its own, with
    nothing written."""
    mid = _mid("chain")
    v1 = [_order(cust=True), _cust([_CUST_SPENT]), _line()]
    bad = {"order": _order([_ORDER_TOTAL], cust=True), "cust": _cust([_CUST_SPENT]),
           "line": _line()}

    async def go() -> tuple[str, tuple[int, Any], Any]:
        pid = await _live(world.a, ANA, _shop(mid, v1))
        draft = await _save(world.a, ANA, pid, _shop(mid, _in_order(bad, names)))
        raised = None
        async with tenant_session(world.a) as db:
            try:
                await objects_store.sync_objects(
                    db, ANA, package_id=pid, objects=_in_order(bad, names))
            except objects_store.ObjectsOutOfSync as exc:
                raised = exc
        return pid, draft, raised

    pid, draft, raised = _run(world, go)
    assert draft[0] == 422, draft
    assert raised is not None, "the sync took a roll-up of a roll-up"
    assert [p.kind for p in raised.problems] == ["rules_refused"]
    assert "cannot read a roll-up" in raised.problems[0].message
    assert _rule_names(world, pid) == {"cust": ["cust_spent"], "line": [], "order": []}
    assert _versions(world, pid) == [1]


def test_r8_a_republish_with_roll_ups_writes_no_rules(world) -> None:
    mid = _mid("same")
    objs = [_cust([_CUST_SPENT]), _order(cust=True), _line()]
    pid = _run(world, lambda: _live(world.a, ANA, _shop(mid, objs)))
    before = _objects(world, pid)
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 200 and got["sync"]["rules_set"] == [], got
    assert _objects(world, pid) == before


def test_r8_a_publish_that_adds_a_roll_up_stores_it_and_it_computes(world) -> None:
    mid = _mid("addroll")

    async def go() -> tuple[str, tuple[int, Any], Any]:
        pid = await _live(world.a, ANA, _shop(mid, [_order(), _line()]))
        got = await _republish(world, mid, pid, [_order([_ORDER_TOTAL]), _line()])
        order = await _record(world.a, pid, "order", code="A")
        await _record(world.a, pid, "line", order_ref={"id": order}, amount=7)
        async with tenant_session(world.a) as db:
            obj = await objects_store.get_object_by_name(db, "order", package_id=pid)
            assert obj is not None
            rec = await objects_store.get_record(db, obj.id, order, package_id=pid)
        return pid, got, rec

    pid, (status, got), rec = _run(world, go)
    assert status == 200, got
    assert got["sync"]["rules_set"] == ["order"]
    assert [r["kind"] for r in dict((o[0], o[3]) for o in _objects(world, pid))["order"]] == [
        "rollup"]
    assert rec is not None and float(rec.data["total"]) == 7.0


def test_r8_a_roll_up_on_an_object_with_records_is_refused_with_no_version_row(world) -> None:
    mid = _mid("lateroll")

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _shop(mid, [_order(), _line()]))
        await _record(world.a, pid, "order", code="A")
        return pid, await _republish(world, mid, pid, [_order([_ORDER_TOTAL]), _line()])

    pid, (status, got) = _run(world, go)
    assert status == 409, got
    [problem] = got["problems"]
    assert (problem["kind"], problem["object"], problem["rule"]) == (
        "rules_refused", "order", "order_total")
    assert "declare before the first record" in problem["message"]
    assert _versions(world, pid) == [1]
    assert _rule_names(world, pid) == {"line": [], "order": []}


def test_r8_a_dropped_roll_up_lets_its_read_field_turn_cold(world) -> None:
    """v2 drops the roll-up of ``order`` and turns ``line.amount`` cold. A
    stored roll-up of another object reads the field, so its cold flip waits
    until the rules phase removes that roll-up."""
    mid = _mid("coldread")

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _shop(mid, [_order([_ORDER_TOTAL]), _line()]))
        return pid, await _republish(world, mid, pid, [_order(), _line(amount_hot=False)])

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["hot_changed"] == [["line", "amount", False]]
    assert _hot_map(world, pid, "line")["amount"] is False
    assert _rule_names(world, pid) == {"line": [], "order": []}


# ── m. A rule and a hot swap at a cap ──────────────────────────────────────


def _unique(key: str) -> dict[str, Any]:
    return {"name": f"one_{key}", "kind": "unique", "fields": [key]}


def _ruled(obj: dict[str, Any], *rules: dict[str, Any]) -> dict[str, Any]:
    return {**obj, "rules": list(rules)} if rules else obj


def test_r8_a_rule_that_moves_with_a_hot_swap_at_the_object_cap_passes(world) -> None:
    """Eight hot fields, and ``unique`` reads ``h8``. The manifest moves the
    rule to ``c1``, turns ``c1`` hot and ``h8`` cold, so the object ends with 8.
    Pass 1 of the rules drops the old rule before the swap."""
    mid = _mid("moverule")
    full = _hot_obj("inv", objects_store.MAX_HOT_PER_OBJECT, cold=1)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_ruled(full, _unique("h8"))]))
        assert (await _save(world.a, ANA, pid, _manifest(
            mid, [_ruled(_heat(full, h8=False, c1=True), _unique("c1"))])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    hot = _hot_map(world, pid, "inv")
    assert (hot["h8"], hot["c1"], sum(hot.values())) == (False, True, 8)
    assert [r["name"] for r in _objects(world, pid)[0][3]] == ["one_c1"]
    assert _versions(world, pid) == [1, 2]


def test_r8_a_rule_that_moves_with_a_hot_swap_at_the_org_cap_passes(world) -> None:
    """The org form: Ben fills the org up to four hot fields short of the cap,
    and Ana's object takes the last four. Her rule moves from ``h4`` to ``c1``
    with the swap."""
    mid = _mid("orgmoverule")
    mine = _hot_obj("inv", 4, cold=1)
    room = objects_store.MAX_HOT_PER_ORG - _org_hot(world) - 4
    assert room >= 0, "the org holds too many hot fields for this test"

    async def go() -> tuple[str, tuple[int, Any]]:
        await _fill_hot(world, BEN, room)
        pid = await _live(world.a, ANA, _manifest(mid, [_ruled(mine, _unique("h4"))]))
        assert (await _save(world.a, ANA, pid, _manifest(
            mid, [_ruled(_heat(mine, h4=False, c1=True), _unique("c1"))])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert _org_hot(world) == objects_store.MAX_HOT_PER_ORG
    assert status == 200, got
    hot = _hot_map(world, pid, "inv")
    assert (hot["h4"], hot["c1"]) == (False, True)


def test_r8_a_rule_that_goes_with_a_hot_swap_at_the_object_cap_passes(world) -> None:
    mid = _mid("droprule")
    full = _hot_obj("inv", objects_store.MAX_HOT_PER_OBJECT, cold=1)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_ruled(full, _unique("h8"))]))
        assert (await _save(world.a, ANA, pid, _manifest(
            mid, [_heat(full, h8=False, c1=True)])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    hot = _hot_map(world, pid, "inv")
    assert (hot["h8"], hot["c1"], sum(hot.values())) == (False, True, 8)
    assert _objects(world, pid)[0][3] == []


def test_r8_a_rule_that_goes_with_a_new_hot_field_at_the_object_cap_passes(world) -> None:
    """The rule on ``h8`` goes, ``h8`` turns cold, and a NEW hot field ``b``
    comes. The new field waits until the cold flip frees its room."""
    mid = _mid("droprulenew")
    full = _hot_obj("inv", objects_store.MAX_HOT_PER_OBJECT)

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _manifest(mid, [_ruled(full, _unique("h8"))]))
        more = _heat(full, h8=False)
        more["fields"].append({"key": "b", "type": "text", "hot": True})
        assert (await _save(world.a, ANA, pid, _manifest(mid, [more])))[0] == 200
        return pid, await _publish(world.a, ANA, pid)

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    assert got["sync"]["fields_added"] == [["inv", "b"]]
    hot = _hot_map(world, pid, "inv")
    assert (hot["h8"], hot["b"], sum(hot.values())) == (False, True, 8)


def _line8(amount_hot: bool = True, c1_hot: bool = False) -> dict[str, Any]:
    """A ``line`` with 8 hot fields: the relation, ``amount`` and ``h3`` to
    ``h8``. ``c1`` is cold."""
    fields: list[dict[str, Any]] = [
        {"key": "order_ref", "type": "relation", "to": "order", "hot": True},
        {"key": "amount", "type": "number", "hot": amount_hot}]
    fields += [{"key": f"h{i}", "type": "text", "hot": True} for i in range(3, 9)]
    fields.append({"key": "c1", "type": "text", "hot": c1_hot})
    return {"name": "line", "fields": fields}


def test_r8_a_roll_up_that_goes_with_a_swap_of_its_read_field_at_the_cap_passes(world) -> None:
    """``order`` drops its roll-up of ``line.amount``, and ``line`` swaps
    ``amount`` for ``c1`` at its cap of 8 hot fields."""
    mid = _mid("droproll")

    async def go() -> tuple[str, tuple[int, Any]]:
        pid = await _live(world.a, ANA, _shop(mid, [_order([_ORDER_TOTAL]), _line8()]))
        return pid, await _republish(
            world, mid, pid, [_order(), _line8(amount_hot=False, c1_hot=True)])

    pid, (status, got) = _run(world, go)
    assert status == 200, got
    hot = _hot_map(world, pid, "line")
    assert (hot["amount"], hot["c1"], sum(hot.values())) == (False, True, 8)
    assert _rule_names(world, pid) == {"line": [], "order": []}


def _hot_rows(world: Any, pid: str, name: str, key: str) -> int:
    oid = next(o[1] for o in _objects(world, pid) if o[0] == name)
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            "SELECT count(*) FROM studio_record_hot WHERE object_id = CAST(:o AS uuid) "
            "AND field_key = :k"), {"o": oid, "k": key}).scalar_one())


def test_r8_a_cold_flip_rolls_back_when_the_second_rules_pass_refuses(world) -> None:
    """``status`` turns cold, and a new formula on an object with records is
    refused in the second rules pass. The SAVEPOINT brings back the flag and
    the hot rows."""
    mid = _mid("coldback")

    async def go() -> str:
        pid = await _live(world.a, ANA, _manifest(mid))
        await _record(world.a, pid, site="x", status="planned")
        return pid

    pid = _run(world, go)
    before = _hot_rows(world, pid, "visit", "status")
    bad = _ruled(_heat(_visit(), status=False), _TOTAL)
    assert _run(world, lambda: _save(world.a, ANA, pid, _manifest(mid, [bad])))[0] == 200
    status, got = _run(world, lambda: _publish(world.a, ANA, pid))
    assert status == 409, got
    assert [(p["kind"], p["rule"]) for p in got["problems"]] == [("rules_refused", "line_total")]
    assert before == 1 and _hot_rows(world, pid, "visit", "status") == 1
    assert _hot_map(world, pid, "visit")["status"] is True
    assert _versions(world, pid) == [1]


# ── n. The expected hash of a draft (G1c review round 1, P1) ───────────────
#
# A chat tool reads the draft, checks it, and then writes. A second write can
# land between the read and the write. So the publish body and the draft save
# take an optional ``based_on``, and the store compares it with the hash of
# the stored draft under its row lock. With no ``based_on`` a route behaves as
# before.


def _second(mid: str) -> dict[str, Any]:
    """The same app with one more field."""
    return _manifest(mid, [_visit(fields=[*_FIELDS, {"key": "note", "type": "text"}])])


def test_r8_a_publish_with_a_stale_based_on_is_409_and_writes_nothing(world) -> None:
    """Mutation: drop the compare from ``publish_version`` -> a version row -> red."""
    mid = _mid("stale")
    first, second = _manifest(mid), _second(mid)

    async def setup() -> str:
        pid = await _new(world.a, ANA, first)
        assert (await _save(world.a, ANA, pid, second))[0] == 200
        return pid

    pid = _run(world, setup)
    before = _snapshot(world, pid)
    status, got = _run(world, lambda: _call(
        "publish", world.a, ANA, pid, payload={"notes": "", "based_on": manifest_hash(first)}))
    assert (status, got["code"]) == (409, packages.DRAFT_CHANGED), got
    assert _snapshot(world, pid) == before and _versions(world, pid) == []
    status, got = _run(world, lambda: _call(
        "publish", world.a, ANA, pid, payload={"notes": "", "based_on": manifest_hash(second)}))
    assert status == 200 and got["version"]["version"] == 1, got
    assert [f[0] for f in _fields(world, _objects(world, pid)[0][1])] == [
        *(f["key"] for f in _FIELDS), "note"]


def test_r8_a_draft_save_with_a_stale_hash_is_409_and_keeps_the_draft(world) -> None:
    """Mutation: drop the compare from ``update_manifest`` -> the third draft
    replaces the second -> red."""
    mid = _mid("stalesave")
    first, second = _manifest(mid), _second(mid)
    third = _manifest(mid, [_visit(fields=_FIELDS[:2])])

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        pid = await _new(world.a, ANA, first)
        ok = await _call("save_draft", world.a, ANA, pid, payload=second,
                         based_on=manifest_hash(first))
        stale = await _call("save_draft", world.a, ANA, pid, payload=third,
                            based_on=manifest_hash(first))
        return pid, ok, stale

    pid, ok, stale = _run(world, go)
    assert ok[0] == 200, ok
    assert (stale[0], stale[1]["code"]) == (409, packages.DRAFT_CHANGED), stale
    assert _app(world, pid)[5] == second


def test_r8_two_saves_with_one_hash_at_once_give_one_save(world) -> None:
    """The lost update of two saves at once: both read the same draft. The row
    lock lets one through, and the other gets 409."""
    mid = _mid("twosaves")
    first = _manifest(mid)
    a, b = _second(mid), _manifest(mid, [_visit(fields=_FIELDS[:2])])

    async def go() -> tuple[str, list[tuple[int, Any]]]:
        pid = await _new(world.a, ANA, first)
        h = manifest_hash(first)
        got = await asyncio.gather(
            _call("save_draft", world.a, ANA, pid, payload=a, based_on=h),
            _call("save_draft", world.a, ANA, pid, payload=b, based_on=h))
        return pid, list(got)

    pid, got = _run(world, go)
    assert sorted(s for s, _ in got) == [200, 409], got
    winner = a if got[0][0] == 200 else b
    assert _app(world, pid)[5] == winner


def test_r8_a_publish_and_a_save_with_no_hash_behave_as_before(world) -> None:
    mid = _mid("nohash")
    first, second = _manifest(mid), _second(mid)

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        pid = await _new(world.a, ANA, first)
        return pid, await _save(world.a, ANA, pid, second), await _publish(world.a, ANA, pid)

    pid, saved, published = _run(world, go)
    assert saved[0] == 200 and published[0] == 200, (saved, published)
    assert _versions(world, pid) == [1] and _app(world, pid)[5] == second


@pytest.mark.parametrize("bad", ["", "abc", "g" * 64, 5, ["x"], "a" * 63])
def test_r8_a_based_on_that_is_not_a_hash_is_422_and_writes_nothing(world, bad) -> None:
    mid = _mid("badhash")

    async def go() -> tuple[str, tuple[int, Any], tuple[int, Any]]:
        pid = await _new(world.a, ANA, _manifest(mid))
        put = await _call("save_draft", world.a, ANA, pid, payload=_second(mid),
                          based_on=bad if isinstance(bad, str) else str(bad))
        pub = await _call("publish", world.a, ANA, pid, payload={"notes": "", "based_on": bad})
        return pid, put, pub

    pid, put, pub = _run(world, go)
    assert put[0] == 422 and pub[0] == 422, (put, pub)
    assert _versions(world, pid) == [] and _app(world, pid)[5] == _manifest(mid)
