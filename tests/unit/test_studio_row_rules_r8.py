"""WS-STU C8a: app roles and row rules on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §19.7 and the §14 C8a row. Code:
``acb_common/studio_access.py`` (the grammar and the scope) and
``gateway/studio/objects_store.py`` (the one reader and the one writer, with
``studio_objects.access`` of the migration ``studio_row_rules``). The pure half
and the one-reader fence are ``test_studio_row_rules.py``.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations. A viewer holds
its roles directly: C8b gives a member the roles of the role map of an install.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_row_rules_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**The world.** A ``slip`` names an ``employee`` and a ``manager`` (hot person
fields) and a ``team``. ``hr`` reads and writes all. ``manager`` reads the
slips where ``manager = $me``. ``employee`` reads and writes the slips where
``employee = $me``, and ``bank_account`` and ``bonus`` are hidden from it. The
formula ``total`` reads ``bonus``, so it is hidden too. A ``team`` is open, and
its roll-up ``headcount`` counts the slips of the team. A ``claim`` is a line
of a slip (C13).

**Acceptance (C8a).** An employee gets only ``employee = me`` records from a
get and a list, a manager only ``manager = me``, and a holder of ``hr`` gets
all. A get of another person's record answers None, and an update or a delete
of it answers "not found". A hidden key is absent from the get, the list and
each write answer.

**The mutations (R7).** Commit before each, and restore with git after. Each
result below was run on 2026-11-11.

* M1. ``objects_store._q_where``: drop the ``_q_access`` part. The list tests of
  the employee and the manager go red.
* M2. ``objects_store._lock_records``: lock with ``_LOCK_RECORD_SQL`` always.
  ``test_r8_an_update_or_a_delete_of_another_persons_record_answers_not_found``
  goes red.
* M3. ``objects_store._View.shown``: return the record as it is. The hidden-key
  tests go red.
* M4. ``objects_store._check_sees``: return at once.
  ``test_r8_a_create_or_a_move_into_the_rows_of_another_person_is_refused`` goes red.
* M5. ``objects_store._check_line_parents``: use ``_LINE_PARENTS_SQL`` always.
  ``test_r8_a_line_cannot_name_a_parent_that_the_viewer_cannot_read`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from acb_common.studio_access import AccessError, Viewer
from acb_common.studio_fields import FieldError
from acb_common.studio_query import QueryError
from acb_common.studio_rules import ConstraintViolation
from gateway.studio import objects_store as store
from gateway.studio.objects_store import (
    AccessDenied,
    BatchError,
    RecordNotFound,
    Write,
)
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
OWNER = f"owner.{TAG}@stuc8a.test"
E1, E2, E3 = (f"e{i}.{TAG}@stuc8a.test" for i in (1, 2, 3))
M1, M2 = (f"m{i}.{TAG}@stuc8a.test" for i in (1, 2))
HR = f"hr.{TAG}@stuc8a.test"

AS_OWNER = Viewer(OWNER)
AS_E1 = Viewer(E1, frozenset({"employee"}))
AS_M1 = Viewer(M1, frozenset({"manager"}))
AS_M2 = Viewer(M2, frozenset({"manager"}))
AS_HR = Viewer(HR, frozenset({"hr"}))
AS_STRANGER = Viewer(f"x.{TAG}@stuc8a.test", frozenset({"intern"}))
# Fix round 1: two roles of one member.
AS_M1_BOTH = Viewer(M1, frozenset({"manager", "employee"}))
AS_E1_AUDIT = Viewer(E1, frozenset({"auditor", "employee"}))
# Fix round 2: a clerk grant reads and writes all, and hides one key.
CEO = f"ceo.{TAG}@stuc8a.test"
X1 = f"x1.{TAG}@stuc8a.test"
AS_CLERK_EMP = Viewer(X1, frozenset({"clerk", "employee"}))
AS_CLERK = Viewer(f"clerk.{TAG}@stuc8a.test", frozenset({"clerk"}))
AS_MOVER = Viewer(M1, frozenset({"mover"}))

TEAM_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "headcount", "type": "number"},
    {"key": "lead", "type": "person", "hot": True},
]
#: Fix round 1 (P1-3): a team is restricted. An employee reads and changes only the
#: team that they lead.
TEAM_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all", "write": "all"},
    {"role": "employee", "read": {"where": "lead = $me"}, "write": "all"},
]
SLIP_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "manager", "type": "person", "hot": True},
    {"key": "team", "type": "relation", "to": "team", "hot": True},
    {"key": "month", "type": "text", "hot": True},
    {"key": "amount", "type": "number"},
    {"key": "bonus", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "bank_account", "type": "text"},
    {"key": "approver", "type": "person"},
    {"key": "claims", "type": "relation", "to": "claim", "lines": "slip"},
]
CLAIM_FIELDS: list[dict[str, Any]] = [
    {"key": "slip", "type": "relation", "to": "slip", "hot": True},
    {"key": "what", "type": "text"},
]
SLIP_RULES: list[dict[str, Any]] = [
    {"name": "pay", "kind": "formula", "target": "total", "rounding": "half_up",
     "expr": {"fn": "add", "args": [{"ref": "amount"}, {"ref": "bonus"}]}},
    {"name": "total_ok", "kind": "range", "field": "total", "max": 1_000_000},
    {"name": "slip_once", "kind": "unique", "fields": ["month"]},
]
TEAM_RULES: list[dict[str, Any]] = [
    {"name": "heads", "kind": "rollup", "target": "headcount", "source": "slip",
     "via": "team", "agg": "count"},
]
SLIP_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all", "write": "all"},
    {"role": "manager", "read": {"where": "manager = $me"}},
    {"role": "employee", "read": {"where": "employee = $me"}, "write": "all",
     "hide": ["bank_account", "bonus"]},
    # Fix round 1 (P1-1): reads every record, and writes none.
    {"role": "auditor", "read": "all"},
]
_HIDDEN = {"bank_account", "bonus", "total"}

# ── The fix round 2 world: a second package of org A ─────────────────────────
#: P1-A. A clerk reads and writes all, and `salary` is hidden from it. An
#: employee reads the own record with no key hidden.
STAFF_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "salary", "type": "number"},
    {"key": "note", "type": "text"},
]
STAFF_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["salary"]},
    {"role": "employee", "read": {"where": "employee = $me"}},
]
#: P1-B. `net = gross - tax`, and `gross` is hidden from a clerk. A crew is
#: open, and its roll-up `net_total` adds the `net` of its paychecks.
CREW_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "net_total", "type": "number"},
]
CREW_RULES: list[dict[str, Any]] = [
    {"name": "net_sum", "kind": "rollup", "target": "net_total", "source": "paycheck",
     "via": "crew", "agg": "sum", "field": "net"},
]
PAYCHECK_FIELDS: list[dict[str, Any]] = [
    {"key": "crew", "type": "relation", "to": "crew", "hot": True},
    {"key": "gross", "type": "number"},
    {"key": "tax", "type": "number"},
    {"key": "net", "type": "number", "hot": True},
]
PAYCHECK_RULES: list[dict[str, Any]] = [
    {"name": "net_of", "kind": "formula", "target": "net", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "gross"}, {"ref": "tax"}]}},
]
PAYCHECK_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["gross"]},
]
#: P1-C. A stock that never goes below 0, and a mover who reads and writes the
#: own movements only.
MOVEMENT_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "sku", "type": "text", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
]
MOVEMENT_RULES: list[dict[str, Any]] = [
    {"name": "stock", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0},
]
MOVEMENT_ACCESS: list[dict[str, Any]] = [
    {"role": "mover", "read": {"where": "by = $me"}, "write": "all"},
]
#: P1-D. `margin = total - cost`, at most 100, and `cost` is hidden from a
#: clerk. `total` adds the `amount` of the lines, and a line is open.
ORDER_FIELDS: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "cost", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "margin", "type": "number"},
]
ORDER_RULES: list[dict[str, Any]] = [
    {"name": "line_sum", "kind": "rollup", "target": "total", "source": "line",
     "via": "order", "agg": "sum", "field": "amount"},
    {"name": "margin_of", "kind": "formula", "target": "margin", "rounding": "half_up",
     "expr": {"fn": "sub", "args": [{"ref": "total"}, {"ref": "cost"}]}},
    {"name": "margin_ok", "kind": "range", "field": "margin", "max": 100},
]
ORDER_ACCESS: list[dict[str, Any]] = [
    {"role": "clerk", "read": "all", "write": "all", "hide": ["cost"]},
]
LINE_FIELDS: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "amount", "type": "number", "hot": True},
]
#: P2. A guard of the machine reads `hr_note`, which is hidden from an employee.
REQUEST_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "status", "type": "select", "options": ["draft", "approved"]},
    {"key": "hr_note", "type": "text"},
]
REQUEST_RULES: list[dict[str, Any]] = [
    {"name": "flow", "kind": "state_machine", "field": "status", "initial": "draft",
     "moves": [{"from": "draft", "to": "approved",
                "guard": [{"field": "hr_note", "op": "not_empty"}]}]},
]
REQUEST_ACCESS: list[dict[str, Any]] = [
    {"role": "employee", "read": {"where": "by = $me"}, "write": "all", "hide": ["hr_note"]},
]


async def _define(db: Any, pkg: str, name: str, fields: Any, rules: Any = None,
                  access: Any = None) -> Any:
    made = await store.define_object(db, OWNER, package_id=pkg, name=name, fields=fields)
    assert made is not None
    if rules:
        assert await store.set_constraints(db, made.id, rules, package_id=pkg)
    if access:
        assert await store.set_access(db, made.id, access, package_id=pkg)
    return made


async def _build_round_two(db: Any, pkg: str) -> dict[str, Any]:
    """The objects and the records of fix round 2, in the package *pkg*."""
    staff = await _define(db, pkg, "staff", STAFF_FIELDS, access=STAFF_ACCESS)
    crew = await _define(db, pkg, "crew", CREW_FIELDS)
    paycheck = await _define(db, pkg, "paycheck", PAYCHECK_FIELDS, PAYCHECK_RULES, PAYCHECK_ACCESS)
    assert await store.set_constraints(db, crew.id, CREW_RULES, package_id=pkg)
    movement = await _define(db, pkg, "movement", MOVEMENT_FIELDS, MOVEMENT_RULES,
                             MOVEMENT_ACCESS)
    order = await _define(db, pkg, "order", ORDER_FIELDS)
    line = await _define(db, pkg, "line", LINE_FIELDS)
    assert await store.set_constraints(db, order.id, ORDER_RULES, package_id=pkg)
    assert await store.set_access(db, order.id, ORDER_ACCESS, package_id=pkg)
    request = await _define(db, pkg, "request", REQUEST_FIELDS, REQUEST_RULES, REQUEST_ACCESS)

    async def put(*writes: Write) -> list[Any]:
        return await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                        writes=list(writes))

    ceo = (await put(Write("create", staff.id, data={"employee": CEO, "salary": 999_000})))[0]
    c1 = (await put(Write("create", crew.id, data={"name": "Crew 1"})))[0]
    await put(Write("create", paycheck.id, data={"crew": {"id": c1.id}, "gross": 1000, "tax": 200}))
    mv1, mv2, mv3 = await put(
        Write("create", movement.id, data={"by": M1, "sku": "A", "qty": 5}),
        Write("create", movement.id, data={"by": M2, "sku": "A", "qty": -3}),
        Write("create", movement.id, data={"by": M1, "sku": "B", "qty": 1}))
    o1 = (await put(Write("create", order.id, data={"name": "O1", "cost": 50})))[0]
    l0 = (await put(Write("create", line.id, data={"order": {"id": o1.id}, "amount": 10})))[0]
    return {"pkg2": pkg, "staff": staff, "crew": crew, "paycheck": paycheck,
            "movement": movement, "order": order, "line": line, "request": request,
            "ceo": ceo.id, "c1": c1.id, "mv1": mv1.id, "mv2": mv2.id, "mv3": mv3.id,
            "o1": o1.id, "l0": l0.id}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc8a', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc8a", "name": "stuc8a", "purpose": "The C8a R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _run(p: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(p.app_url)):
            return await fn()
    return asyncio.run(go())


def _slip(employee: str, manager: str, team: str, n: int) -> dict[str, Any]:
    return {"employee": employee, "manager": manager, "team": {"id": team},
            "month": f"2026-{n:02d}", "amount": 100 * n, "bonus": 10 * n,
            "bank_account": f"AC-{n}"}


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """One package of org A, owned by OWNER, with a team, its slips and a
    claim object, and one package of org B."""
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b), ("a2", p.org_a)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc8a-{TAG}-{key}", "oe": OWNER, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def build() -> SimpleNamespace:
        pkg = ids["a"]
        async with tenant_session(p.org_a) as db:
            claim = await store.define_object(db, OWNER, package_id=pkg, name="claim",
                                              fields=CLAIM_FIELDS)
            team = await store.define_object(db, OWNER, package_id=pkg, name="team",
                                             fields=TEAM_FIELDS)
            slip = await store.define_object(db, OWNER, package_id=pkg, name="slip",
                                             fields=SLIP_FIELDS)
            assert claim and team and slip
            await store.set_constraints(db, slip.id, SLIP_RULES, package_id=pkg)
            await store.set_constraints(db, team.id, TEAM_RULES, package_id=pkg)
            done = await store.set_access(db, slip.id, SLIP_ACCESS, package_id=pkg)
            assert done is not None and done.access is not None
            assert await store.set_access(db, team.id, TEAM_ACCESS, package_id=pkg)
            t1, t2 = await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", team.id, data={"name": "Plant 4", "lead": E1}),
                Write("create", team.id, data={"name": "Plant 9", "lead": E3})])
            rows = [_slip(E1, M1, t1.id, 1), _slip(E2, M1, t1.id, 2), _slip(E3, M2, t1.id, 3),
                    _slip(M1, M2, t1.id, 4)]
            made = await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                            writes=[Write("create", slip.id, data=r) for r in rows])
            two = await _build_round_two(db, ids["a2"])
        return SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg=pkg, pkg_b=ids["b"], slip=slip,
                               team=team, claim=claim, t1=t1.id, t2=t2.id,
                               s1=made[0].id, s2=made[1].id, s3=made[2].id, s4=made[3].id,
                               **two)

    w = _run(p, build)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc8a-{TAG}-%"})


def _list(w: Any, viewer: Viewer, **query: Any) -> store.RecordPage:
    async def go() -> store.RecordPage:
        async with tenant_session(w.a) as db:
            page = await store.query_records(db, package_id=w.pkg, object_id=w.slip.id,
                                             viewer=viewer, limit=100, **query)
        assert page is not None
        return page
    return _run(w.p, go)


def _get(w: Any, viewer: Viewer, rid: str, oid: str | None = None) -> store.Record | None:
    async def go() -> store.Record | None:
        async with tenant_session(w.a) as db:
            return await store.get_record(db, oid or w.slip.id, rid, package_id=w.pkg,
                                          viewer=viewer)
    return _run(w.p, go)


def _write(w: Any, viewer: Viewer, *writes: Write) -> list[Any]:
    async def go() -> list[Any]:
        async with tenant_session(w.a) as db:
            return await store.apply_writes(db, viewer.email, package_id=w.pkg, viewer=viewer,
                                            writes=list(writes))
    return _run(w.p, go)


def _refused(w: Any, viewer: Viewer, *writes: Write) -> Exception:
    with pytest.raises(BatchError) as caught:
        _write(w, viewer, *writes)
    return caught.value.cause


def _stored(w: Any, rid: str) -> dict[str, Any]:
    with w.p.admin_engine.connect() as c:
        return dict(c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                              {"r": rid}).scalar_one())


def _employees(page: store.RecordPage) -> set[str]:
    return {r.data["employee"] for r in page.records}


# ── The reads ───────────────────────────────────────────────────────────────


def test_r8_an_employee_lists_and_gets_only_their_own_records(world) -> None:
    page = _list(world, AS_E1)
    assert [r.id for r in page.records] == [world.s1] and page.total == 1
    own = _get(world, AS_E1, world.s1)
    assert own is not None and own.data["employee"] == E1


def test_r8_a_manager_reads_only_the_records_of_their_team(world) -> None:
    assert _employees(_list(world, AS_M1)) == {E1, E2}
    assert _list(world, AS_M1).total == 2
    assert _employees(_list(world, AS_M2)) == {E3, M1}
    assert _get(world, AS_M1, world.s3) is None


def test_r8_a_holder_of_hr_and_the_owner_read_every_record_and_every_key(world) -> None:
    for viewer in (AS_HR, AS_OWNER):
        page = _list(world, viewer)
        assert _employees(page) == {E1, E2, E3, M1} and page.total == 4
        assert all(set(r.data) >= _HIDDEN for r in page.records)
        got = _get(world, viewer, world.s2)
        assert got is not None and got.data["bank_account"] == "AC-2" and got.data["total"] == 220


def test_r8_many_roles_read_the_union_of_their_rows(world) -> None:
    both = Viewer(M1, frozenset({"manager", "employee"}))
    page = _list(world, both)
    assert _employees(page) == {E1, E2, M1} and page.total == 3
    # The own slip hides the keys of the employee grant. A slip of the team shows them.
    by = {r.data["employee"]: r for r in page.records}
    assert not _HIDDEN & set(by[M1].data)
    assert set(by[E1].data) >= _HIDDEN


def test_r8_a_get_of_another_persons_record_answers_none(world) -> None:
    assert _get(world, AS_E1, world.s2) is None
    assert _get(world, AS_E1, world.s3) is None
    assert _get(world, AS_E1, str(uuid.uuid4())) is None


def test_r8_a_viewer_with_no_grant_reads_nothing_and_writes_nothing(world) -> None:
    page = _list(world, AS_STRANGER)
    assert page.records == () and page.total == 0
    assert _get(world, AS_STRANGER, world.s1) is None
    cause = _refused(world, AS_STRANGER, Write("update", world.slip.id, world.s1, {"month": "x"}))
    assert isinstance(cause, AccessDenied)


def test_r8_a_filter_on_a_person_field_stays_inside_the_row_rule(world) -> None:
    mine = _list(world, AS_M1, filter=[{"field": "employee", "op": "eq", "value": E2}])
    assert [r.id for r in mine.records] == [world.s2]
    other = _list(world, AS_M1, filter=[{"field": "employee", "op": "eq", "value": E3}])
    assert other.records == () and other.total == 0
    some = _list(world, AS_M1, filter=[{"field": "employee", "op": "in", "value": [E1, E3]}])
    assert [r.id for r in some.records] == [world.s1]


def test_r8_each_op_of_a_person_field_on_a_hot_field_and_on_a_cold_one(world) -> None:
    """C8a gives `person` the ops eq, ne, in and is_empty. `manager` is hot, and
    `approver` is cold, so the query reads `data` for it."""
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s3, {"approver": M2}))

    def ids(field: str, op: str, value: Any = None) -> set[str]:
        item: dict[str, Any] = {"field": field, "op": op}
        if value is not None:
            item["value"] = value
        return {r.id for r in _list(world, AS_OWNER, filter=[item]).records}

    assert ids("manager", "eq", M1) == {world.s1, world.s2}
    assert ids("manager", "ne", M1) == {world.s3, world.s4}
    assert ids("manager", "in", [M2]) == {world.s3, world.s4}
    assert ids("manager", "is_empty") == set()
    assert ids("approver", "eq", M2) == {world.s3}
    assert ids("approver", "ne", M2) == set()
    assert ids("approver", "in", [M2, M1]) == {world.s3}
    assert ids("approver", "is_empty") == {world.s1, world.s2, world.s4}
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s3, {"approver": None}))


def test_r8_another_org_reads_nothing_of_the_package(world) -> None:
    async def go() -> tuple[Any, Any]:
        async with tenant_session(world.b) as db:
            got = await store.get_record(db, world.slip.id, world.s1, package_id=world.pkg,
                                         viewer=AS_OWNER)
            page = await store.query_records(db, package_id=world.pkg, object_id=world.slip.id,
                                             viewer=AS_OWNER)
        return got, page

    assert _run(world.p, go) == (None, None)


# ── The hidden keys ─────────────────────────────────────────────────────────


def test_r8_a_hidden_key_leaves_the_get_the_list_and_the_write_answers(world) -> None:
    got = _get(world, AS_E1, world.s1)
    assert got is not None and not _HIDDEN & set(got.data) and got.data["amount"] == 100
    listed = _list(world, AS_E1).records[0]
    assert not _HIDDEN & set(listed.data)
    made, changed = _write(world, AS_E1,
                           Write("create", world.slip.id, data={"employee": E1, "month": "x"}),
                           Write("update", world.slip.id, world.s1, {"month": "2026-01b"}))
    for answer in (made, changed):
        assert isinstance(answer, store.Record) and not _HIDDEN & set(answer.data)
    # The store keeps the keys. Only the answer leaves them out.
    assert _stored(world, world.s1)["bank_account"] == "AC-1"
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))


def test_r8_a_write_that_names_a_hidden_key_is_refused(world) -> None:
    for data in ({"bank_account": "AC-9"}, {"bonus": 5}):
        cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, data))
        assert isinstance(cause, FieldError) and cause.field in data
    assert _stored(world, world.s1)["bank_account"] == "AC-1"


def test_r8_a_filter_or_a_sort_on_a_hidden_key_is_refused(world) -> None:
    for query in ({"filter": [{"field": "bank_account", "op": "eq", "value": "AC-1"}]},
                  {"filter": [{"field": "total", "op": "gt", "value": 0}]},
                  {"sort": {"field": "bonus"}}):
        with pytest.raises(QueryError) as refused:
            _list(world, AS_E1, **query)
        assert refused.value.code == "query_op_not_allowed" and "hidden" in refused.value.message
    assert _list(world, AS_HR, filter=[{"field": "bank_account", "op": "eq",
                                         "value": "AC-2"}]).total == 1


def test_r8_a_roll_up_over_rows_that_the_viewer_cannot_read_is_hidden(world) -> None:
    hidden = _get(world, AS_E1, world.t1, world.team.id)
    assert hidden is not None and "headcount" not in hidden.data and hidden.data["name"] == "Plant 4"
    for viewer in (AS_HR, AS_OWNER):
        shown = _get(world, viewer, world.t1, world.team.id)
        assert shown is not None and shown.data["headcount"] >= 4


def test_r8_a_bisection_of_a_hidden_key_gets_one_refusal_for_every_amount(world) -> None:
    """Fix round 1 (P1-4). `total = amount + bonus` holds a range, and `bonus` is
    hidden from an employee. A refusal that follows the amount would show `bonus`
    by bisection, so the store refuses each write of `amount` by the employee,
    with one text that names no hidden key."""
    said: set[str] = set()
    for amount in (1, 500_000, 999_989, 999_990, 999_991, 2_000_000):
        cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, {"amount": amount}))
        assert isinstance(cause, AccessDenied), (amount, cause)
        said.add(str(cause))
    assert len(said) == 1
    named = set(re.findall(r"`(\w+)`", said.pop()))
    assert "amount" in named and not named & _HIDDEN
    assert _stored(world, world.s1)["amount"] == 100
    full = _refused(world, AS_OWNER, Write("update", world.slip.id, world.s1, {"amount": 2_000_000}))
    assert "`total`" in full.message


def test_r8_a_key_rule_over_rows_that_the_viewer_cannot_read_names_no_field(world) -> None:
    """A `unique` refusal still tells a viewer that some record holds the key: the
    one bit that the spec lists. Its text names the rule and the object only."""
    taken = Write("create", world.slip.id, data={"employee": E1, "month": "2026-02"})
    masked = _refused(world, AS_E1, taken)
    assert isinstance(masked, ConstraintViolation) and masked.rule == "slip_once"
    assert "`month`" not in masked.message
    full = _refused(world, AS_OWNER, taken)
    assert "`month`" in full.message


# ── Fix round 1 ─────────────────────────────────────────────────────────────


def _team_heads(world: Any, rid: str) -> Any:
    return _stored(world, rid).get("headcount")


def test_r8_two_roles_write_only_the_rows_that_a_write_grant_reads(world) -> None:
    """P1-1. The write right is decided for each record. The manager grant of M1
    reads s1 and writes nothing, so M1 cannot change or delete s1, also with an
    employee grant that writes. An auditor grant reads all and writes nothing, so
    E1 still writes only the slips of E1."""
    before = {rid: _stored(world, rid) for rid in (world.s1, world.s2)}
    for viewer, rid in ((AS_M1_BOTH, world.s1), (AS_E1_AUDIT, world.s2)):
        for write in (Write("update", world.slip.id, rid, {"month": "taken"}),
                      Write("delete", world.slip.id, rid)):
            cause = _refused(world, viewer, write)
            assert isinstance(cause, AccessDenied), (viewer, write.op, cause)
    assert {rid: _stored(world, rid) for rid in (world.s1, world.s2)} == before
    # The own slip of each still takes a write.
    _write(world, AS_M1_BOTH, Write("update", world.slip.id, world.s4, {"month": "2026-04b"}))
    _write(world, AS_E1_AUDIT, Write("update", world.slip.id, world.s1, {"month": "2026-01c"}))
    _write(world, AS_OWNER, Write("update", world.slip.id, world.s4, {"month": "2026-04"}))


def test_r8_a_write_cannot_widen_what_the_viewer_reads(world) -> None:
    """P1-2. M1 holds the manager and the employee grants. On the own slip s4
    (employee M1, manager M2) the employee grant hides `bank_account`. A write of
    `manager = M1` would make the manager grant read s4 too, and show the key. A
    viewer with no grant that writes all cannot set a person field that a `where`
    of the object reads, unless a write grant of the viewer reads that field."""
    cause = _refused(world, AS_M1_BOTH, Write("update", world.slip.id, world.s4, {"manager": M1}))
    assert isinstance(cause, AccessDenied) and "`manager`" in str(cause)
    assert _stored(world, world.s4)["manager"] == M2
    own = _get(world, AS_M1_BOTH, world.s4)
    assert own is not None and "bank_account" not in own.data
    cause = _refused(world, AS_M1_BOTH,
                     Write("create", world.slip.id, data={"employee": M1, "manager": M1}))
    assert isinstance(cause, AccessDenied)
    made = _write(world, AS_M1_BOTH, Write("create", world.slip.id, data={"employee": M1}))[0]
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))


def test_r8_a_child_cannot_change_a_roll_up_parent_that_the_viewer_cannot_read(world) -> None:
    """P1-3. E1 reads every slip (the auditor grant) and writes the own slips. E1
    leads t1 and cannot read t2. A slip of t2 would change the head count of t2,
    so the store refuses it, with the text of a team that is not there."""
    before = _team_heads(world, world.t2)
    cause = _refused(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": world.t2}}))
    gone = _refused(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": str(uuid.uuid4())}}))
    assert isinstance(cause, FieldError) and cause.field == "team"
    assert isinstance(gone, FieldError) and gone.message == cause.message
    assert _team_heads(world, world.t2) == before
    moved = _refused(world, AS_E1_AUDIT, Write("update", world.slip.id, world.s1,
                                                {"team": {"id": world.t2}}))
    assert isinstance(moved, FieldError) and moved.message == cause.message
    made = _write(world, AS_E1_AUDIT, Write("create", world.slip.id, data={
        "employee": E1, "team": {"id": world.t1}}))[0]
    _write(world, AS_OWNER, Write("delete", world.slip.id, made.id))
    assert _team_heads(world, world.t2) == before


def test_r8_a_filter_or_a_sort_on_a_field_that_is_not_text_is_refused(world) -> None:
    """P2. A field name that is a list is refused with `query_field_unknown`, for a
    viewer with hidden keys too. It never reaches a set test (a TypeError, a 500)."""
    for query in ({"filter": [{"field": ["bank_account"], "op": "eq", "value": "x"}]},
                  {"sort": {"field": ["bank_account"]}}):
        with pytest.raises(QueryError) as refused:
            _list(world, AS_E1, **query)
        assert refused.value.code == "query_field_unknown"


# ── The writes ──────────────────────────────────────────────────────────────


def test_r8_an_update_or_a_delete_of_another_persons_record_answers_not_found(world) -> None:
    async def go() -> tuple[Any, Any]:
        async with tenant_session(world.a) as db:
            changed = await store.update_record(db, E1, world.slip.id, world.s2, {"month": "x"},
                                                package_id=world.pkg, viewer=AS_E1)
            gone = await store.delete_record(db, E1, world.slip.id, world.s2,
                                             package_id=world.pkg, viewer=AS_E1)
        return changed, gone

    assert _run(world.p, go) == (None, False)
    cause = _refused(world, AS_E1, Write("delete", world.slip.id, world.s3))
    assert isinstance(cause, RecordNotFound)
    assert _stored(world, world.s2)["month"] == "2026-02"
    assert _get(world, AS_OWNER, world.s3) is not None


def test_r8_a_create_or_a_move_into_the_rows_of_another_person_is_refused(world) -> None:
    cause = _refused(world, AS_E1, Write("create", world.slip.id, data={"employee": E2}))
    assert isinstance(cause, AccessDenied) and "employee" in str(cause)
    cause = _refused(world, AS_E1, Write("update", world.slip.id, world.s1, {"employee": E2}))
    assert isinstance(cause, AccessDenied)
    assert _stored(world, world.s1)["employee"] == E1


def test_r8_a_role_with_no_write_cannot_change_a_record_that_it_reads(world) -> None:
    cause = _refused(world, AS_M1, Write("update", world.slip.id, world.s1, {"month": "x"}))
    assert isinstance(cause, AccessDenied)
    done = _write(world, AS_HR, Write("update", world.slip.id, world.s3, {"month": "2026-03"}))
    assert isinstance(done[0], store.Record)


def test_r8_a_line_cannot_name_a_parent_that_the_viewer_cannot_read(world) -> None:
    cause = _refused(world, AS_E1, Write("create", world.claim.id,
                                         data={"slip": {"id": world.s2}, "what": "taxi"}))
    assert isinstance(cause, FieldError) and cause.field == "slip"
    missing = _refused(world, AS_E1, Write("create", world.claim.id,
                                           data={"slip": {"id": str(uuid.uuid4())}, "what": "x"}))
    assert isinstance(missing, FieldError) and missing.message == cause.message
    made = _write(world, AS_E1, Write("create", world.claim.id,
                                      data={"slip": {"id": world.s1}, "what": "taxi"}))[0]
    _write(world, AS_OWNER, Write("delete", world.claim.id, made.id))


# ── The declaration ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("where", "said"), [
    ("month", "not one"), ("approver", "must be hot"), ("team", "not one")])
def test_r8_a_where_on_a_cold_field_or_a_field_that_is_not_a_person_is_refused(
        world, where: str, said: str) -> None:
    async def go() -> str:
        async with tenant_session(world.a) as db:
            with pytest.raises(AccessError) as bad:
                await store.set_access(db, world.slip.id, [
                    {"role": "hr", "read": {"where": f"{where} = $me"}}], package_id=world.pkg)
        return bad.value.message

    assert said in _run(world.p, go)
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
    assert kept == SLIP_ACCESS


def test_r8_a_person_field_that_a_row_rule_reads_cannot_turn_cold(world) -> None:
    async def go() -> str:
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as bad:
                await store.set_field_hot(db, world.slip.id, "employee", False)
        return str(bad.value)

    assert "row rule" in _run(world.p, go)


def test_r8_the_sync_of_a_publish_stores_the_access_and_a_bad_one_writes_nothing(world) -> None:
    def manifest(access: Any) -> list[dict[str, Any]]:
        return [{"name": "claim", "fields": CLAIM_FIELDS},
                {"name": "team", "fields": TEAM_FIELDS, "rules": TEAM_RULES,
                 "access": TEAM_ACCESS},
                {"name": "slip", "fields": SLIP_FIELDS, "rules": SLIP_RULES, "access": access}]

    narrow = [{"role": "hr", "read": "all", "write": "all"}]

    async def go() -> tuple[Any, str]:
        async with tenant_session(world.a) as db:
            done = await store.sync_objects(db, OWNER, package_id=world.pkg,
                                            objects=manifest(narrow))
        bad_access = [{"role": "hr", "read": {"where": "approver = $me"}}]
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectsOutOfSync) as refused:
                await store.sync_objects(db, OWNER, package_id=world.pkg,
                                         objects=manifest(bad_access))
            kind = refused.value.problems[0].kind
        async with tenant_session(world.a) as db:
            await store.sync_objects(db, OWNER, package_id=world.pkg,
                                     objects=manifest(SLIP_ACCESS))
        return done, kind

    done, kind = _run(world.p, go)
    assert done is not None and done.access_set == ("slip",) and done.changed
    assert kind == store.ACCESS_REFUSED
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
    assert kept == SLIP_ACCESS


# ── The migration ───────────────────────────────────────────────────────────


def _migration() -> Path:
    files = [p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
             if "studio_objects ADD COLUMN IF NOT EXISTS access" in p.read_text(encoding="utf-8")]
    assert len(files) == 1
    return files[0]


def test_r8_the_checks_hold_a_person_and_the_shape_of_the_access(world) -> None:
    with world.p.admin_engine.connect() as c:
        hot = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'studio_object_fields_hot_type'")).scalar_one()
    assert "person" in hot and "relation" in hot
    for bad in ('"all"', "[]", json.dumps([{"role": "r"}] * 17)):
        with pytest.raises(Exception, match="studio_objects_access_shape"), \
                world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_objects SET access = CAST(:a AS jsonb) "
                           "WHERE id = CAST(:o AS uuid)"), {"a": bad, "o": world.team.id})


def test_r8_the_migration_is_idempotent_and_a_replay_changes_nothing(world) -> None:
    sql = _migration().read_text(encoding="utf-8")
    raw = world.p.admin_engine.raw_connection()
    try:
        conn = raw.driver_connection
        conn.autocommit = True
        for _ in range(2):
            conn.execute(sql)
        conn.autocommit = False
    finally:
        raw.close()
    with world.p.admin_engine.connect() as c:
        kept = c.execute(text("SELECT access FROM studio_objects WHERE id = CAST(:o AS uuid)"),
                         {"o": world.slip.id}).scalar_one()
        count = c.execute(text(
            "SELECT count(*) FROM pg_constraint WHERE conname = 'studio_objects_access_shape'")
        ).scalar_one()
    assert kept == SLIP_ACCESS and count == 1


# ── Fix round 2 ─────────────────────────────────────────────────────────────


def _write2(world: Any, viewer: Viewer, *writes: Write) -> list[Any]:
    async def go() -> list[Any]:
        async with tenant_session(world.a) as db:
            return await store.apply_writes(db, viewer.email, package_id=world.pkg2,
                                            viewer=viewer, writes=list(writes))
    return _run(world.p, go)


def _refused2(world: Any, viewer: Viewer, *writes: Write) -> Exception:
    with pytest.raises(BatchError) as caught:
        _write2(world, viewer, *writes)
    return caught.value.cause


def _get2(world: Any, viewer: Viewer, oid: str, rid: str) -> store.Record | None:
    async def go() -> store.Record | None:
        async with tenant_session(world.a) as db:
            return await store.get_record(db, oid, rid, package_id=world.pkg2, viewer=viewer)
    return _run(world.p, go)


def test_r8_a_write_all_grant_cannot_move_a_record_into_a_grant_that_hides_less(world) -> None:
    """P1-A. X1 holds a clerk grant (reads and writes all, `salary` hidden) and an
    employee grant (the own record, nothing hidden). A write of `employee = X1` on
    the CEO's record would show `salary`. Each write of a viewer that a rule binds
    must not shrink the hidden keys of the record, also on a create."""
    cause = _refused2(world, AS_CLERK_EMP, Write("update", world.staff.id, world.ceo,
                                                 {"employee": X1}))
    assert isinstance(cause, AccessDenied), cause
    assert _stored(world, world.ceo)["employee"] == CEO
    seen = _get2(world, AS_CLERK_EMP, world.staff.id, world.ceo)
    assert seen is not None and "salary" not in seen.data
    made = _refused2(world, AS_CLERK_EMP, Write("create", world.staff.id, data={"employee": X1}))
    assert isinstance(made, AccessDenied), made
    # A change that hides no less still passes.
    done = _write2(world, AS_CLERK_EMP, Write("update", world.staff.id, world.ceo, {"note": "ok"}))
    assert isinstance(done[0], store.Record) and "salary" not in done[0].data


def test_r8_a_roll_up_over_a_formula_of_a_hidden_key_is_hidden(world) -> None:
    """P1-B. `net = gross - tax` and `gross` is hidden from a clerk, so `net` is
    hidden too. The crew roll-up `net_total` adds `net`, so it would show `gross`
    for a crew of one. The source view of a roll-up runs the formulas."""
    seen = _get2(world, AS_CLERK, world.crew.id, world.c1)
    assert seen is not None and "net_total" not in seen.data, seen
    full = _get2(world, AS_OWNER, world.crew.id, world.c1)
    assert full is not None and full.data["net_total"] == 800


def test_r8_a_delete_cannot_drive_a_rule_over_rows_that_the_viewer_cannot_read(world) -> None:
    """P1-C. The stock of sku A is 5 - 3 = 2, and M1 reads only the own movements.
    A delete of the +5 would break the floor, and a delete of the +1 of sku B
    would not. Each refusal is the same, so a delete tells M1 nothing."""
    said = set()
    for rid in (world.mv1, world.mv3):
        cause = _refused2(world, AS_MOVER, Write("delete", world.movement.id, rid))
        assert isinstance(cause, AccessDenied), (rid, cause)
        said.add(str(cause))
    assert len(said) == 1
    assert _get2(world, AS_OWNER, world.movement.id, world.mv1) is not None
    assert _get2(world, AS_OWNER, world.movement.id, world.mv3) is not None


def test_r8_a_child_write_cannot_drive_a_parent_rule_over_a_hidden_key(world) -> None:
    """P1-D. `margin = total - cost` is at most 100, `total` adds the lines, and
    `cost` (50) is hidden from a clerk. A line of a chosen amount would tell the
    clerk the cost by the refusal, so each line write of the clerk gets one text,
    a delete too."""
    said = set()
    for amount in (10, 140, 150, 151, 200):
        cause = _refused2(world, AS_CLERK, Write("create", world.line.id, data={
            "order": {"id": world.o1}, "amount": amount}))
        assert isinstance(cause, AccessDenied), (amount, cause)
        said.add(str(cause))
    assert len(said) == 1
    gone = _refused2(world, AS_CLERK, Write("delete", world.line.id, world.l0))
    assert isinstance(gone, AccessDenied), gone
    total = _get2(world, AS_OWNER, world.order.id, world.o1)
    assert total is not None and total.data["total"] == 10


def test_r8_an_employee_creates_a_record_when_a_guard_reads_a_hidden_key(world) -> None:
    """P2. The machine fills `status` with its initial state. That is no key that
    the employee sent, so the create passes. A move of the state still reads the
    hidden key, so it stays refused."""
    made = _write2(world, AS_E1, Write("create", world.request.id, data={"by": E1}))[0]
    assert made.data["status"] == "draft"
    moved = _refused2(world, AS_E1, Write("update", world.request.id, made.id,
                                          {"status": "approved"}))
    assert isinstance(moved, AccessDenied)
    _write2(world, AS_OWNER, Write("delete", world.request.id, made.id))
