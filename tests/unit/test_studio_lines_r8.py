"""WS-STU C13: line items on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C13. Code: the lines relation in
``acb_common/studio_fields.py``, the door in ``gateway/routes/studio/invoke.py``
(``_split_lines``, ``_line_writes`` and ``_do_object_write``) and the store in
``gateway/studio/objects_store.py`` (``_find_lines``, ``_check_lines_fit``,
``_link`` and ``_check_line_parents``). The hermetic half is
``test_studio_lines.py``.

The real door handler and the store run on a phase-4 promoted scratch database,
as the app role with no BYPASSRLS, so FORCE RLS binds each statement. Two
organizations. An order holds its lines in ``order_line``, and a roll-up sums
their ``qty`` into ``total`` (C7b), with a range of 0 to 1,000 on it.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_lines_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (C13).**

a. A parent with 20 lines saves all of them or none. A failure on line 17
   writes nothing, and its refusal names line 17.
b. A save of 50 lines is refused, and writes nothing. A save of 49 lines runs.
c. A line cannot move to a parent of another package, and cannot move to a
   parent of another org (a second org). Each refusal writes nothing.
d. A roll-up over the lines changes in the same transaction: the parent, its
   roll-up and each line hold one transaction time, and a roll-up range that
   breaks refuses the whole save.
e. The door path (a direct write) and the chat path (an ask, the member's
   approval, then the resend) both hold.

**The mutations (R7).** Commit before each, and restore with git after. Each
result below was run on 2026-11-09, with ``test_studio_lines.py`` too.

* L1. ``objects_store._LINE_PARENTS_SQL``: drop the filters of the object and of
  the package (``_IN_PACKAGE``). ``test_r8_a_line_cannot_move_to_a_parent_of_another_package``
  and ``test_each_lines_statement_names_the_org_and_the_package`` go red. The
  org test stays green, because the org filter and RLS still hold.
* L2. ``objects_store._new_parents``: skip each mark (``continue`` first). The
  package test, ``test_r8_a_line_cannot_move_to_a_parent_of_another_org`` and
  ``test_r8_a_parent_id_that_is_not_a_uuid_is_refused`` go red.
* L3. ``invoke._do_object_write``: call ``apply_writes`` once for each write.
  ``test_r8_a_failure_on_line_17_writes_nothing``, the roll-up range test, the
  two tests of the shape of a save, and the hermetic one-batch fence go red.
* L4. ``objects_store._apply_write``: skip the check that a linked update is a
  line of its parent. ``test_r8_a_line_of_another_parent_is_refused_in_a_save``
  goes red.
* L5. ``invoke._split_lines``: compare the total with ``MAX_LINES_PER_SAVE + 1``.
  ``test_r8_fifty_lines_are_refused_and_forty_nine_are_saved``,
  ``test_r8_a_chat_ask_with_fifty_lines_opens_no_request`` and
  ``test_fifty_lines_are_refused_before_any_write`` go red.

**The mutations of fix round 1** (run on 2026-11-09):

* M2 of the verifier on the code before the fix: drop ``mark.before is None and``
  from the linked-create test of ``_new_parents``.
  ``test_r8_a_line_saved_with_its_parent_cannot_then_move_to_another_package``
  goes red: the batch commits. On the fixed code the same edit turns
  ``test_r8_a_line_saved_with_its_parent_may_then_move_inside_the_package`` red.
* F2. ``_new_parents``: ``continue`` for each mark whose first write is linked.
  The package test, the place test and the delete test go red.
* F3. ``_new_parents``: no refusal for a linked create whose parent is gone.
  ``test_r8_a_linked_line_of_a_parent_that_the_batch_deletes_is_refused`` goes red.
* F4. ``_new_parents``: name ``mark.index`` again.
  ``test_r8_the_refusal_of_a_move_names_the_write_that_moved_the_line`` goes red.
* F5. ``_line_moves``: no check of the one form. The ``capitals`` and ``hex32``
  cases go red. With the second check of ``_new_parents`` gone too, the ``urn``
  and ``uuid`` cases go red with the old DBAPIError, the 500 of the review.
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

import httpx
from acb_auth import UserContext, UserRole, build_access, direct_proof, get_current_user
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_fields import FieldError
from acb_skills import studio_tools as st
from fastapi import FastAPI, HTTPException
from fastapi import Request as _HttpRequest
from fastapi.responses import JSONResponse
from gateway import app_requests
from gateway.routes.studio import invoke
from gateway.routes.studio import router as studio_router
from gateway.studio import objects_store as store
from gateway.studio import packages_store
from gateway.studio.objects_store import BatchError, Write
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc13.test"
DIRECT_KEY = "r8-c13-direct-key-" + "k" * 30

ORDER_FIELDS: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "lines", "type": "relation", "to": "order_line", "lines": "order"},
    {"key": "total", "type": "number"},
]
LINE_FIELDS: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "item", "type": "text"},
    {"key": "qty", "type": "number", "hot": True},
]
RULES: list[dict[str, Any]] = [
    {"name": "qty_total", "kind": "rollup", "target": "total", "source": "order_line",
     "via": "order", "agg": "sum", "field": "qty"},
    {"name": "total_ok", "kind": "range", "field": "total", "min": 0, "max": 1000},
]
ACTIONS: list[dict[str, Any]] = [
    {"id": "add", "uses": "object:order.create"},
    {"id": "change", "uses": "object:order.update"},
    {"id": "add_line", "uses": "object:order_line.create"},
    {"id": "change_line", "uses": "object:order_line.update"},
]


def _manifest(mid: str) -> dict[str, Any]:
    return {"studio": 1, "id": mid, "name": "Orders", "purpose": "Take orders with lines.",
            "objects": [{"name": "order", "fields": ORDER_FIELDS, "rules": RULES},
                        {"name": "order_line", "fields": LINE_FIELDS}],
            "actions": ACTIONS}


def _user(org: str, email: str = ANA) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    """The parts of a request that the door reads: the body stream, the
    method, the path, the headers and the query."""

    def __init__(self, payload: Any, *, path: str, headers: dict[str, str] | None = None,
                 query: dict[str, str] | None = None) -> None:
        self._raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", dict(headers or {})
        self.url = SimpleNamespace(path=path)
        self.query_params = dict(query or {})

    async def stream(self) -> Any:
        yield self._raw


async def _direct(org: str, pid: str, action: str, payload: Any) -> tuple[int, Any]:
    """The real door handler, with a valid proof for Ana: a direct write."""
    path = f"/studio/apps/{pid}/actions/{action}"
    headers = {direct_proof.HEADER: direct_proof.sign(DIRECT_KEY, ANA, "POST", path)}
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, action, request=_Request(
            payload, path=path, headers=headers), user=_user(org))
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)
    if isinstance(found, JSONResponse):
        return found.status_code, json.loads(found.body)
    return 200, found


def _run(w: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(w.p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


async def _package(org: str, manifest: dict[str, Any]) -> str:
    async with tenant_session(org) as db:
        return (await packages_store.create_package(db, ANA, manifest)).id


async def _objects(org: str, pid: str) -> tuple[store.StudioObject, store.StudioObject]:
    async with tenant_session(org) as db:
        order = await store.define_object(db, ANA, package_id=pid, name="order",
                                          fields=ORDER_FIELDS)
        line = await store.define_object(db, ANA, package_id=pid, name="order_line",
                                         fields=LINE_FIELDS)
        assert order is not None and line is not None
        ruled = await store.set_constraints(db, order.id, RULES, package_id=pid)
    assert ruled is not None
    return ruled, line


@pytest.fixture
def world(promoted, monkeypatch):  # noqa: F811
    """Three packages of one manifest: two of org A and one of org B, each of
    Ana. Direct writes and delegated writes are on for each org."""
    p = promoted
    settings = get_settings()
    monkeypatch.setattr(settings, "studio_orgs", "*")
    monkeypatch.setattr(settings, "studio_direct_key", DIRECT_KEY)
    monkeypatch.setattr(settings, "app_requests_orgs", "*")
    invoke._IN_FLIGHT.clear()
    mid = f"lines-{TAG}-{uuid.uuid4().hex[:6]}"
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)

    async def setup() -> None:
        manifest = _manifest(mid)
        w.pkg = await _package(w.a, manifest)
        w.pkg2 = await _package(w.a, manifest)
        w.pkg_b = await _package(w.b, manifest)
        w.order, w.line = await _objects(w.a, w.pkg)
        w.order2, w.line2 = await _objects(w.a, w.pkg2)
        w.order_b, w.line_b = await _objects(w.b, w.pkg_b)

    _run(w, setup)
    yield w
    invoke._IN_FLIGHT.clear()
    with p.admin_engine.begin() as c:
        ids = [w.pkg, w.pkg2, w.pkg_b]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM pm_agent_runs WHERE app = 'studio' AND member_email = :a"),
                  {"a": ANA})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"{mid}-%"})


# -- reads as the superuser ----------------------------------------------------------


def _records(w: Any, oid: str) -> list[SimpleNamespace]:
    """Each record of the object *oid*: its id, data and the times."""
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT id::text, data, created_at, updated_at FROM studio_records "
            "WHERE object_id = CAST(:o AS uuid) ORDER BY created_at, id"), {"o": oid}).all()
    out = []
    for rid, data, made, changed in rows:
        out.append(SimpleNamespace(id=rid, data=data if isinstance(data, dict) else json.loads(
            data), created_at=made, updated_at=changed))
    return out


def _record(w: Any, rid: str) -> SimpleNamespace:
    with w.p.admin_engine.connect() as c:
        row = c.execute(text("SELECT id::text, data, created_at, updated_at FROM studio_records "
                             "WHERE id = CAST(:r AS uuid)"), {"r": rid}).one()
    data = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return SimpleNamespace(id=row[0], data=data, created_at=row[2], updated_at=row[3])


def _counts(w: Any) -> tuple[int, ...]:
    return tuple(len(_records(w, oid)) for oid in (
        w.order.id, w.line.id, w.order2.id, w.line2.id, w.order_b.id, w.line_b.id))


def _audits(w: Any, pid: str) -> list[dict[str, Any]]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
                              "ORDER BY id"), {"p": pid}).all()
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def _lines(n: int, qty: int | None = None) -> list[dict[str, Any]]:
    return [{"data": {"item": f"part {i}", "qty": i if qty is None else qty}}
            for i in range(1, n + 1)]


async def _order_with(w: Any, org: str, pid: str, lines: list[dict[str, Any]],
                      title: str = "Bike") -> dict[str, Any]:
    status, got = await _direct(org, pid, "add", {"data": {"title": title, "lines": lines}})
    assert status == 200, got
    return _flat(got)


def _flat(got: dict[str, Any]) -> dict[str, Any]:
    """The answer of a save, with the ids of the field `lines`. The answer gives
    the ids of each lines field under its key (fix round 1, P3)."""
    assert set(got["lines"]) == {"lines"}, got["lines"]
    return {**got, "lines": got["lines"]["lines"]}


# -- a. all or none --------------------------------------------------------------------


def test_r8_a_parent_with_twenty_lines_saves_them_all_with_its_rollup_in_one_transaction(
        world) -> None:
    w = world
    before = _counts(w)

    got = _run(w, lambda: _order_with(w, w.a, w.pkg, _lines(20)))
    rid = got["record"]["id"]
    # The answer reads the record after the roll-up of the batch: 1 + 2 + ... + 20.
    assert got["record"]["data"] == {"title": "Bike", "total": 210}
    assert len(got["lines"]) == 20 and len(set(got["lines"])) == 20
    rows = _records(w, w.line.id)
    assert {r.id for r in rows} == set(got["lines"])
    assert {r.data["order"]["id"] for r in rows} == {rid}
    assert sorted(r.data["qty"] for r in rows) == list(range(1, 21))
    parent = _record(w, rid)
    assert parent.data["total"] == 210
    # d. One transaction: now() is the time of the transaction, so the parent, the
    # derived write of its roll-up and each line hold one time.
    assert {r.created_at for r in rows} | {parent.created_at, parent.updated_at} == {
        parent.updated_at}
    assert _counts(w) == (before[0] + 1, before[1] + 20, *before[2:])
    row = _audits(w, w.pkg)[-1]
    assert (row["action"], row["outcome"], row["record_id"], row["lines"]) == (
        "add", "ran", rid, 20)
    assert "part" not in json.dumps(_audits(w, w.pkg))


def test_r8_a_failure_on_line_17_writes_nothing(world) -> None:
    w = world
    before = _counts(w)
    lines = _lines(20)
    lines[16] = {"data": {"item": "part 17", "qty": "seventeen"}}

    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add", {"data": {
        "title": "Bike", "lines": lines}}))
    assert status == 422, got
    assert got["code"] == "field_invalid"
    assert got["message"].startswith("Line 17 of `lines`: Field `qty`"), got["message"]
    assert got["fieldErrors"] == [{"field": "lines", "message": got["message"]}]
    assert _counts(w) == before, "no line and no parent"
    row = _audits(w, w.pkg)[-1]
    assert (row["outcome"], row["reason"]) == ("refused", "field_invalid")
    assert "record_id" not in row and "lines" not in row


def test_r8_the_store_writes_twenty_linked_lines_all_or_none(world) -> None:
    """The store half of (a): one batch of a create and 20 linked creates. A bad
    17th line rolls back the batch, and the caller's session stays usable."""
    w = world
    before = _counts(w)

    def batch(bad: bool) -> list[Write]:
        writes = [Write("create", w.order.id, data={"title": "Store"})]
        for i in range(1, 21):
            qty: Any = "x" if bad and i == 17 else 1
            writes.append(Write("create", w.line.id, data={"qty": qty}, parent=0))
        return writes

    async def go() -> tuple[BatchError, list[Any]]:
        async with tenant_session(w.a) as db:
            with pytest.raises(BatchError) as refused:
                await store.apply_writes(db, ANA, package_id=w.pkg, writes=batch(True))
            done = await store.apply_writes(db, ANA, package_id=w.pkg, writes=batch(False))
        return refused.value, done

    refused, done = _run(w, go)
    assert refused.index == 17 and isinstance(refused.cause, FieldError)
    assert _counts(w) == (before[0] + 1, before[1] + 20, *before[2:])
    parent = done[0].id
    assert _record(w, parent).data["total"] == 20
    assert all(r.data["order"] == {"id": parent} for r in _records(w, w.line.id))


# -- b. the cap ------------------------------------------------------------------------


def test_r8_fifty_lines_are_refused_and_forty_nine_are_saved(world) -> None:
    w = world
    before = _counts(w)

    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add", {"data": {
        "title": "Big", "lines": _lines(50, qty=1)}}))
    assert status == 422 and got["code"] == "too_many_lines", got
    assert "at most 49 lines" in got["message"] and "holds 50" in got["message"]
    assert _counts(w) == before
    assert _audits(w, w.pkg)[-1]["reason"] == "too_many_lines"

    got = _run(w, lambda: _order_with(w, w.a, w.pkg, _lines(49, qty=1)))
    assert len(got["lines"]) == 49 and got["record"]["data"]["total"] == 49
    assert _counts(w)[:2] == (before[0] + 1, before[1] + 49)


# -- d. the roll-up --------------------------------------------------------------------


def test_r8_a_rollup_range_that_breaks_refuses_the_whole_save(world) -> None:
    """The roll-up runs in the batch of the lines, so its rule refuses them."""
    w = world
    before = _counts(w)
    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add", {"data": {
        "title": "Heavy", "lines": _lines(20, qty=60)}}))
    assert status == 422 and got["code"] == "rule_refused" and got["rule"] == "total_ok", got
    assert _counts(w) == before


def test_r8_an_update_adds_and_changes_lines_and_the_rollup_follows(world) -> None:
    w = world

    async def go() -> tuple[dict[str, Any], tuple[int, Any]]:
        made = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 5}}, {"data": {"qty": 7}}])
        first = made["lines"][0]
        changed = await _direct(w.a, w.pkg, "change", {"id": made["record"]["id"], "data": {
            "title": "Bike 2", "lines": [{"data": {"qty": 3}},
                                          {"id": first, "data": {"qty": 10}}]}})
        return made, changed

    made, (status, got) = _run(w, go)
    assert status == 200, got
    got = _flat(got)
    rid = made["record"]["id"]
    assert got["record"]["data"] == {"title": "Bike 2", "total": 20}
    assert got["lines"][1] == made["lines"][0] and got["lines"][0] not in made["lines"]
    assert sorted(r.data["qty"] for r in _records(w, w.line.id)) == [3, 7, 10]
    parent = _record(w, rid)
    changed = {r.id: r for r in _records(w, w.line.id)}
    assert changed[made["lines"][0]].updated_at == parent.updated_at
    assert changed[got["lines"][0]].created_at == parent.updated_at


# -- the shape of a save ---------------------------------------------------------------


def test_r8_a_line_of_another_parent_is_refused_in_a_save(world) -> None:
    w = world

    async def go() -> tuple[str, str, tuple[int, Any]]:
        one = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}], "One")
        two = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 2}}], "Two")
        moved = await _direct(w.a, w.pkg, "change", {"id": one["record"]["id"], "data": {
            "lines": [{"id": two["lines"][0], "data": {"qty": 99}}]}})
        return one["record"]["id"], two["lines"][0], moved

    one, theirs, (status, got) = _run(w, go)
    assert status == 422 and got["code"] == "record_invalid", got
    assert got["message"].startswith("Line 1 of `lines`: A line of this save is not a line")
    assert _record(w, theirs).data["qty"] == 2
    assert _record(w, one).data["total"] == 1


def test_r8_a_line_id_that_names_nothing_and_a_parent_key_in_a_line_are_refused(world) -> None:
    w = world
    before = _counts(w)

    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        made = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}])
        ghost = await _direct(w.a, w.pkg, "change", {"id": made["record"]["id"], "data": {
            "lines": [{"id": str(uuid.uuid4()), "data": {"qty": 3}}]}})
        named = await _direct(w.a, w.pkg, "add", {"data": {"lines": [
            {"data": {"qty": 1, "order": {"id": made["record"]["id"]}}}]}})
        return ghost, named

    ghost, named = _run(w, go)
    assert ghost[0] == 422 and ghost[1]["code"] == "bad_lines", ghost
    assert "no such line" in ghost[1]["message"]
    assert named[0] == 422 and named[1]["code"] == "field_invalid", named
    assert "comes from the record that it is saved with" in named[1]["message"]
    assert _counts(w)[:2] == (before[0] + 1, before[1] + 1)


# -- c. a line stays in its package and its org -----------------------------------------


def test_r8_a_line_cannot_move_to_a_parent_of_another_package(world) -> None:
    w = world

    async def go() -> dict[str, Any]:
        mine = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 4}}])
        other = await _order_with(w, w.a, w.pkg2, [{"data": {"qty": 1}}])
        third = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}], "Third")
        line, theirs = mine["lines"][0], other["record"]["id"]
        got: dict[str, Any] = {"mine": mine, "other": other, "third": third}
        got["move"] = await _direct(w.a, w.pkg, "change_line", {
            "id": line, "data": {"order": {"id": theirs}}})
        got["create"] = await _direct(w.a, w.pkg, "add_line", {
            "data": {"order": {"id": theirs}, "qty": 9}})
        async with tenant_session(w.a) as db:
            with pytest.raises(BatchError) as refused:
                await store.apply_writes(db, ANA, package_id=w.pkg, writes=[Write(
                    "update", w.line.id, line, {"order": {"id": theirs.upper()}})])
        got["store"] = refused.value
        # A move inside the package runs, and both roll-ups follow.
        got["inside"] = await _direct(w.a, w.pkg, "change_line", {
            "id": line, "data": {"order": {"id": third["record"]["id"]}}})
        return got

    got = _run(w, go)
    for key in ("move", "create"):
        status, detail = got[key]
        assert status == 422 and detail["code"] == "field_invalid", (key, got[key])
        assert detail["fieldErrors"][0]["field"] == "order"
        assert "names no `order` record of this app" in detail["message"]
    assert isinstance(got["store"].cause, FieldError) and got["store"].cause.field == "order"
    assert got["inside"][0] == 200, got["inside"]
    line = got["mine"]["lines"][0]
    assert _record(w, line).data["order"] == {"id": got["third"]["record"]["id"]}
    assert _record(w, got["mine"]["record"]["id"]).data["total"] == 0
    assert _record(w, got["third"]["record"]["id"]).data["total"] == 5
    assert _record(w, got["other"]["record"]["id"]).data["total"] == 1
    assert len(_records(w, w.line.id)) == 2, "the refused create wrote no line"


def test_r8_a_line_cannot_move_to_a_parent_of_another_org(world) -> None:
    w = world

    async def go() -> dict[str, Any]:
        mine = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 4}}])
        theirs = await _order_with(w, w.b, w.pkg_b, [{"data": {"qty": 1}}])
        line, parent_b = mine["lines"][0], theirs["record"]["id"]
        got: dict[str, Any] = {"mine": mine, "parent_b": parent_b}
        got["move"] = await _direct(w.a, w.pkg, "change_line", {
            "id": line, "data": {"order": {"id": parent_b}}})
        got["create"] = await _direct(w.a, w.pkg, "add_line", {
            "data": {"order": {"id": parent_b}, "qty": 9}})
        async with tenant_session(w.a) as db:
            with pytest.raises(BatchError) as refused:
                await store.apply_writes(db, ANA, package_id=w.pkg, writes=[Write(
                    "create", w.line.id, data={"order": {"id": parent_b}, "qty": 2})])
        got["store"] = refused.value
        return got

    got = _run(w, go)
    for key in ("move", "create"):
        status, detail = got[key]
        assert status == 422 and detail["code"] == "field_invalid", (key, got[key])
        assert detail["fieldErrors"][0]["field"] == "order"
    assert isinstance(got["store"].cause, FieldError)
    line = got["mine"]["lines"][0]
    assert _record(w, line).data["order"] == {"id": got["mine"]["record"]["id"]}
    assert _record(w, got["mine"]["record"]["id"]).data["total"] == 4
    assert _record(w, got["parent_b"]).data["total"] == 1
    # Org B holds only the line of its own order, and org A only the line of Ana's.
    assert [r.data["order"]["id"] for r in _records(w, w.line_b.id)] == [got["parent_b"]]
    assert len(_records(w, w.line.id)) == 1


def test_r8_a_parent_id_that_is_not_a_uuid_is_refused(world) -> None:
    w = world
    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add_line", {
        "data": {"order": {"id": "not-a-record"}, "qty": 1}}))
    assert status == 422 and got["code"] == "field_invalid", got
    assert len(_records(w, w.line.id)) == 0


# -- fix round 1 -------------------------------------------------------------------------


def _state(w: Any) -> list[tuple[str, Any, Any]]:
    """Each record of the order and line objects of both packages of org A, with
    its data and its time, so a test sees that a refused batch wrote nothing."""
    return [(r.id, r.data, r.updated_at)
            for oid in (w.order.id, w.line.id, w.order2.id, w.line2.id)
            for r in _records(w, oid)]


async def _batch(w: Any, writes: list[Write]) -> BatchError | list[Any]:
    """One batch of the store in org A, package 1. A refusal is the answer."""
    async with tenant_session(w.a) as db:
        try:
            return await store.apply_writes(db, ANA, package_id=w.pkg, writes=writes)
        except BatchError as exc:
            return exc


@pytest.mark.parametrize("form", ["urn", "uuid", "braces", "capitals", "hex32"])
def test_r8_an_id_of_another_form_is_422_and_writes_nothing(world, form) -> None:
    """Fix round 1, P2. Python reads `urn:uuid:`, `uuid:`, braces, capitals and 32
    digits with no dash as a UUID, and Postgres refuses some of them. A parent id
    takes the one form only, so each other form is a 422 and never a 500."""
    w = world
    made = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}]))
    rid, line = made["record"]["id"], made["lines"][0]
    sent = {"urn": f"urn:uuid:{rid}", "uuid": f"uuid:{rid}", "braces": "{" + rid + "}",
            "capitals": rid.upper(), "hex32": rid.replace("-", "")}[form]
    before = _state(w)

    async def go() -> tuple[tuple[int, Any], tuple[int, Any]]:
        create = await _direct(w.a, w.pkg, "add_line", {"data": {"order": {"id": sent}, "qty": 2}})
        move = await _direct(w.a, w.pkg, "change_line", {"id": line, "data": {"order": {"id": sent}}})
        return create, move

    for status, got in _run(w, go):
        assert status == 422 and got["code"] == "field_invalid", (form, status, got)
        assert got["fieldErrors"][0]["field"] == "order"
        # A brace is no character of a relation id, so the value check of C1
        # refuses it first. Each other form reaches the check of one form.
        assert ("one form" in got["message"]) is (form != "braces"), got["message"]
    assert _state(w) == before, "a refused id wrote nothing"
    rows = [r for r in _audits(w, w.pkg) if r["action"] in ("add_line", "change_line")]
    assert [(r["outcome"], r["reason"]) for r in rows[-2:]] == [("refused", "field_invalid")] * 2


def test_r8_a_line_saved_with_its_parent_cannot_then_move_to_another_package(world) -> None:
    """Fix round 1, P3-1. A linked update of the line, then an unlinked update
    that moves it to an order of package 2, in one batch. The move is checked,
    so the batch is refused and writes nothing."""
    w = world
    made = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}]))
    theirs = _run(w, lambda: _order_with(w, w.a, w.pkg2, [{"data": {"qty": 1}}]))
    parent, line, other = made["record"]["id"], made["lines"][0], theirs["record"]["id"]
    before = _state(w)
    got = _run(w, lambda: _batch(w, [
        Write("update", w.order.id, parent, {"title": "P"}),
        Write("update", w.line.id, line, {"qty": 5}, parent=0),
        Write("update", w.line.id, line, {"order": {"id": other}})]))
    assert isinstance(got, BatchError), got
    assert isinstance(got.cause, FieldError) and got.cause.field == "order"
    assert _state(w) == before
    assert _record(w, line).data["order"] == {"id": parent}


def test_r8_the_refusal_of_a_move_names_the_write_that_moved_the_line(world) -> None:
    """Fix round 1, P3-2. The line is locked at write 1, and write 2 moves it."""
    w = world
    made = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}]))
    theirs = _run(w, lambda: _order_with(w, w.a, w.pkg2, [{"data": {"qty": 1}}]))
    got = _run(w, lambda: _batch(w, [
        Write("update", w.order.id, made["record"]["id"], {"title": "P"}),
        Write("update", w.line.id, made["lines"][0], {"qty": 5}, parent=0),
        Write("update", w.line.id, made["lines"][0], {"order": {"id": theirs["record"]["id"]}})]))
    assert isinstance(got, BatchError) and got.index == 2, got


def test_r8_a_line_saved_with_its_parent_may_then_move_inside_the_package(world) -> None:
    """The twin of the refused batch: the move goes to an order of this
    package, so the batch runs and both roll-ups follow."""
    w = world
    made = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 4}}]))
    other = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}], "Other"))
    parent, line = made["record"]["id"], made["lines"][0]
    got = _run(w, lambda: _batch(w, [
        Write("update", w.order.id, parent, {"title": "P"}),
        Write("update", w.line.id, line, {"qty": 5}, parent=0),
        Write("update", w.line.id, line, {"order": {"id": other["record"]["id"]}})]))
    assert not isinstance(got, BatchError), got
    assert _record(w, line).data["order"] == {"id": other["record"]["id"]}
    assert _record(w, parent).data["total"] == 0
    assert _record(w, other["record"]["id"]).data["total"] == 6


def test_r8_a_linked_line_of_a_parent_that_the_batch_deletes_is_refused(world) -> None:
    """Fix round 1, P3-3. The batch updates the parent, adds a linked line, and
    then deletes the parent. The new line would name a record that is gone, so
    the batch is refused and writes nothing."""
    w = world
    made = _run(w, lambda: _order_with(w, w.a, w.pkg, [{"data": {"qty": 1}}]))
    parent = made["record"]["id"]
    before = _state(w)
    got = _run(w, lambda: _batch(w, [
        Write("update", w.order.id, parent, {"title": "P"}),
        Write("create", w.line.id, data={"qty": 2}, parent=0),
        Write("delete", w.order.id, parent)]))
    assert isinstance(got, BatchError), got
    assert isinstance(got.cause, FieldError) and got.cause.field == "order"
    assert got.index == 1
    assert _state(w) == before


def test_r8_a_lines_relation_that_no_longer_fits_refuses_each_line_write(world) -> None:
    """Fail closed: a stored relation that a raw write broke refuses the write,
    so no line skips the check of its parent."""
    w = world
    with w.p.admin_engine.begin() as c:
        c.execute(text("UPDATE studio_object_fields SET spec = jsonb_set(spec, '{lines}', "
                       "'\"nope\"') WHERE object_id = CAST(:o AS uuid) AND key = 'lines'"),
                  {"o": w.order.id})
    status, got = _run(w, lambda: _direct(w.a, w.pkg, "add_line", {
        "data": {"qty": 1}}))
    assert status == 422 and got["code"] == "record_invalid", got
    assert "do not fit now" in got["message"]
    assert len(_records(w, w.line.id)) == 0


# -- e. the chat path ------------------------------------------------------------------


class _Seen(httpx.AsyncBaseTransport):
    """The ASGI transport into *app*, which records each request first."""

    def __init__(self, app: FastAPI, seen: list[tuple[str, str, str, bytes]]) -> None:
        self.inner = httpx.ASGITransport(app=app)
        self.seen = seen

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((request.method, request.url.path, request.url.query.decode(),
                          request.content))
        return await self.inner.handle_async_request(request)


@pytest.fixture
def chat(world, monkeypatch):
    """The chat tools of B2c-2b, wired to the real Studio router as Ana."""
    w = world
    app = FastAPI()
    app.include_router(studio_router)

    async def as_member(request: _HttpRequest) -> UserContext:
        return _user(w.a, request.headers.get("X-User-Email", ""))

    app.dependency_overrides[get_current_user] = as_member
    w.seen = []
    real = httpx.AsyncClient
    monkeypatch.setattr(st.httpx, "AsyncClient",
                        lambda **kw: real(transport=_Seen(app, w.seen), **kw))
    monkeypatch.setattr(st, "_current_user_email", lambda: ANA)
    monkeypatch.setattr(get_settings(), "agent_access_enabled", True)
    return w


def _chat(w: Any, fn: Callable[[], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        token = bind_tenant(w.a)
        try:
            return await fn()
        finally:
            release_tenant(token)
    return _run(w, go)


async def _approve(w: Any, rid: str) -> None:
    async with tenant_session(w.a) as db:
        await app_requests.decide(db, org=w.a, member=ANA, request_id=rid,
                                  decision="approve", idempotency_key=uuid.uuid4().hex)


def _runs(w: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM pm_agent_runs WHERE app = 'studio' "
                                  "AND member_email = :a"), {"a": ANA}).scalar_one())


def test_r8_a_chat_order_with_twenty_lines_asks_once_and_the_resend_saves_all(chat) -> None:
    w = chat
    before = _counts(w)
    data = {"title": "From chat", "lines": _lines(20)}

    async def go() -> dict[str, Any]:
        got: dict[str, Any] = {"asked": await st.studio_record_create(w.pkg, "order", data=data)}
        rid = got["asked"]["request_id"]
        got["after_ask"] = _counts(w)
        await _approve(w, rid)
        got["ran"] = await st.studio_record_create(w.pkg, "order", request_id=rid)
        return got

    got = _chat(w, go)
    asked = got["asked"]
    assert (asked["ok"], asked["saved"], asked["state"]) == (False, False, "awaiting"), asked
    assert got["after_ask"] == before, "an ask writes nothing"
    ran = got["ran"]
    assert (ran["ok"], ran["saved"]) == (True, True), ran
    parent = _record(w, ran["record_id"])
    assert parent.data == {"title": "From chat", "total": 210}
    rows = _records(w, w.line.id)
    assert len(rows) == 20 and {r.data["order"]["id"] for r in rows} == {parent.id}
    assert {r.created_at for r in rows} == {parent.updated_at}
    assert _counts(w) == (before[0] + 1, before[1] + 20, *before[2:])
    posts = [(path.rsplit("/", 1)[-1], q) for m, path, q, _b in w.seen if m == "POST"]
    assert posts == [("add", "ask=1"), ("add", f"request_id={asked['request_id']}")]


def test_r8_a_chat_ask_with_fifty_lines_opens_no_request(chat) -> None:
    w = chat
    runs, before = _runs(w), _counts(w)

    out = _chat(w, lambda: st.studio_record_create(w.pkg, "order", data={
        "title": "Too big", "lines": _lines(50, qty=1)}))
    assert (out["ok"], out["saved"], out["code"], out["status"]) == (
        False, False, "too_many_lines", 422), out
    assert "at most 49 lines" in out["errors"][0]["message"]
    assert _runs(w) == runs and _counts(w) == before


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """Its own removal from CI must fail here, in both lists."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for name in ("pr-check.yml", "deploy.yml"):
        workflow = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
        assert block, name
        assert re.search(r"^[ \t]+tests/unit/test_studio_lines_r8\.py(?: \\)?$",
                         block.group(1), re.M), name
