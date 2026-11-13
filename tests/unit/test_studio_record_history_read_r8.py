"""WS-STU C4a-2 — the read of the change history on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C4a-2. Code:
``gateway/studio/objects_store.list_record_changes`` and the door op
``object:<name>.history`` in ``routes/studio/invoke.py``.
``test_studio_record_history_read.py`` holds the hermetic half.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations. A viewer
holds its roles directly, as in ``test_studio_row_rules_r8.py``: C8b gives a
member the roles of the role map of an install.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_record_history_read_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The world.** A ``slip`` names an ``employee`` (a hot person field). ``hr``
reads all. ``employee`` reads the slips where ``employee = $me``, and
``bonus`` is hidden from it. The formula ``total`` reads ``bonus``, so it is
hidden too.

**Acceptance (C4a-2).**

a. The owner reads the history of a record oldest first, paged by id, with
   the actor, the ``via`` and the old and the new value.
b. A viewer whose row rule does not read the record gets None, as a get does.
c. A hidden key leaves ``changes`` for the employee, and the owner and ``hr``
   read it. The store keeps the value.
d. Org B reads nothing of org A.
e. Only the owner reads the history of a deleted record.
f. The door answers the history to the owner, writes one audit row with no
   value, and answers 404 to another member and to org B.

**The mutations (R7).** Commit before each, and restore with git after.

* RR1. ``objects_store._CHANGES_PAGE_SQL``: ``ORDER BY id DESC``.
  ``test_r8_the_owner_reads_the_history_oldest_first_in_pages`` goes red.
* RR2. ``objects_store._read_record``: read with ``_GET_RECORD_SQL``
  always (no row rule). ``test_r8_a_viewer_whose_row_rule_does_not_read_the_record_gets_none``
  goes red.
* RR3. ``objects_store.list_record_changes``: drop ``and not view.owner``.
  ``test_r8_only_the_owner_reads_the_history_of_a_deleted_record`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_auth import UserContext, UserRole, build_access
from acb_common import get_settings
from acb_common.db import bind_tenant, release_tenant, tenant_session
from acb_common.studio_access import Viewer
from fastapi import HTTPException
from gateway.routes.studio import invoke
from gateway.studio import objects_store as store
from gateway.studio import packages_store
from gateway.studio.objects_store import Write
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
OWNER = f"owner.{TAG}@stuc4a2.test"
E1, E2 = (f"e{i}.{TAG}@stuc4a2.test" for i in (1, 2))
HR = f"hr.{TAG}@stuc4a2.test"
BEN = f"ben.{TAG}@stuc4a2.test"

AS_OWNER = Viewer(OWNER)
AS_E1 = Viewer(E1, frozenset({"employee"}))
AS_E2 = Viewer(E2, frozenset({"employee"}))
AS_HR = Viewer(HR, frozenset({"hr"}))

SLIP_FIELDS: list[dict[str, Any]] = [
    {"key": "employee", "type": "person", "hot": True},
    {"key": "amount", "type": "number"},
    {"key": "bonus", "type": "number"},
    {"key": "total", "type": "number"},
    {"key": "note", "type": "text"},
]
SLIP_RULES: list[dict[str, Any]] = [
    {"name": "pay", "kind": "formula", "target": "total", "rounding": "half_up",
     "expr": {"fn": "add", "args": [{"ref": "amount"}, {"ref": "bonus"}]}},
]
SLIP_ACCESS: list[dict[str, Any]] = [
    {"role": "hr", "read": "all", "write": "all"},
    {"role": "employee", "read": {"where": "employee = $me"}, "write": "all",
     "hide": ["bonus"]},
]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc4a2', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc4a2", "name": "stuc4a2", "purpose": "The C4a-2 R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _run(p: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(p.app_url)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    """One package of org A, owned by OWNER, with a slip object and three
    records: ``s1`` of E1 with four changes, ``s2`` of E2, and ``gone`` of E1,
    deleted. One package of org B."""
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc4a2-{TAG}-{key}", "oe": OWNER, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def build() -> SimpleNamespace:
        pkg = ids["a"]
        async with tenant_session(p.org_a) as db:
            slip = await store.define_object(db, OWNER, package_id=pkg, name="slip",
                                             fields=SLIP_FIELDS)
            assert slip is not None
            assert await store.set_constraints(db, slip.id, SLIP_RULES, package_id=pkg)
            assert await store.set_access(db, slip.id, SLIP_ACCESS, package_id=pkg)

            async def put(*writes: Write, via: tuple[str, str] | None = None) -> list[Any]:
                return await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER,
                                                writes=list(writes), via=via)

            s1, s2, gone = await put(
                Write("create", slip.id, data={"employee": E1, "amount": 100, "bonus": 10}),
                Write("create", slip.id, data={"employee": E2, "amount": 200, "bonus": 20}),
                Write("create", slip.id, data={"employee": E1, "amount": 5, "bonus": 1}))
            await put(Write("update", slip.id, s1.id, {"amount": 150}))
            await put(Write("update", slip.id, s1.id, {"bonus": 30}))
            await put(Write("update", slip.id, s1.id, {"note": "paid"}),
                      via=("workflow_run", f"run-{TAG}"))
            await put(Write("delete", slip.id, gone.id))
        return SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg=pkg, pkg_b=ids["b"], slip=slip,
                               s1=s1.id, s2=s2.id, gone=gone.id)

    w = _run(p, build)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc4a2-{TAG}-%"})


def _read(w: Any, viewer: Viewer, rid: str, *, org: str | None = None,
          **page: Any) -> store.ChangePage | None:
    async def go() -> store.ChangePage | None:
        async with tenant_session(org or w.a) as db:
            return await store.list_record_changes(db, w.slip.id, rid, package_id=w.pkg,
                                                   viewer=viewer, **page)
    return _run(w.p, go)


def _keys(page: store.ChangePage) -> list[set[str]]:
    return [set(c.changes) for c in page.changes]


# ── a. The owner, oldest first, in pages ────────────────────────────────────


def test_r8_the_owner_reads_the_history_oldest_first_in_pages(world) -> None:
    first = _read(world, AS_OWNER, world.s1, limit=2)
    assert first is not None and first.next is not None
    assert [c.op for c in first.changes] == ["create", "update"]
    second = _read(world, AS_OWNER, world.s1, limit=2, after=first.next)
    assert second is not None and second.next is None
    assert [c.op for c in second.changes] == ["update", "update"]
    ids = [c.id for c in (*first.changes, *second.changes)]
    assert ids == sorted(ids) and len(set(ids)) == 4
    every = (*first.changes, *second.changes)
    assert every[0].changes["amount"] == {"old": None, "new": 100}
    assert every[1].changes["amount"] == {"old": 100, "new": 150}
    assert every[2].changes["bonus"] == {"old": 10, "new": 30}
    assert {c.actor for c in every} == {OWNER}
    assert (every[3].via_kind, every[3].via_id) == ("workflow_run", f"run-{TAG}")
    assert every[3].changes == {"note": {"old": None, "new": "paid"}}
    # One read with no page names each row too.
    whole = _read(world, AS_OWNER, world.s1)
    assert whole is not None and [c.id for c in whole.changes] == ids


# ── b. The gate of a get ────────────────────────────────────────────────────


def test_r8_a_viewer_whose_row_rule_does_not_read_the_record_gets_none(world) -> None:
    assert _read(world, AS_E2, world.s1) is None
    assert _read(world, AS_E1, world.s2) is None
    stranger = Viewer(f"x.{TAG}@stuc4a2.test", frozenset({"intern"}))
    assert _read(world, stranger, world.s1) is None


# ── c. The hide ─────────────────────────────────────────────────────────────


def test_r8_a_hidden_key_leaves_changes_and_the_owner_and_hr_read_it(world) -> None:
    mine = _read(world, AS_E1, world.s1)
    assert mine is not None
    for change in mine.changes:
        assert "bonus" not in change.changes and "total" not in change.changes
    # The update of only the bonus (and its formula) leaves the page.
    assert [c.op for c in mine.changes] == ["create", "update", "update"]
    assert _keys(mine) == [{"employee", "amount"}, {"amount"}, {"note"}]
    for viewer in (AS_OWNER, AS_HR):
        full = _read(world, viewer, world.s1)
        assert full is not None
        assert {"bonus", "total"} <= set(full.changes[0].changes)
        assert full.changes[2].changes["bonus"] == {"old": 10, "new": 30}
    # The store keeps the hidden value.
    with world.p.admin_engine.connect() as c:
        rows = c.execute(text(
            "SELECT changes FROM studio_record_changes WHERE record_id = CAST(:r AS uuid) "
            "ORDER BY id"), {"r": world.s1}).scalars().all()
    stored = [r if isinstance(r, dict) else json.loads(r) for r in rows]
    assert stored[2]["bonus"] == {"old": 10, "new": 30}


# ── d. The wall of the org ──────────────────────────────────────────────────


def test_r8_org_b_reads_nothing_of_org_a(world) -> None:
    assert _read(world, AS_OWNER, world.s1, org=world.b) is None
    assert _read(world, AS_OWNER, world.gone, org=world.b) is None


# ── e. A deleted record ─────────────────────────────────────────────────────


def test_r8_only_the_owner_reads_the_history_of_a_deleted_record(world) -> None:
    owner = _read(world, AS_OWNER, world.gone)
    assert owner is not None
    assert [c.op for c in owner.changes] == ["create", "delete"]
    assert owner.changes[1].changes["bonus"] == {"old": 1, "new": None}
    # E1 read the record when it was there, and HR reads all. Neither reads it now.
    assert _read(world, AS_E1, world.gone) is None
    assert _read(world, AS_HR, world.gone) is None
    # A record id that never was is None for the owner too.
    assert _read(world, AS_OWNER, str(uuid.uuid4())) is None


# ── f. The door ─────────────────────────────────────────────────────────────

_DOOR_FIELDS: list[dict[str, Any]] = [{"key": "site", "type": "text"}]


def _door_manifest(mid: str) -> dict[str, Any]:
    return {"studio": 1, "id": mid, "name": "Visits", "purpose": "Track install visits.",
            "objects": [{"name": "visit", "fields": _DOOR_FIELDS}],
            "actions": [{"id": "trail", "uses": "object:visit.history"}]}


def _user(org: str, email: str) -> UserContext:
    return UserContext(email=email, role=UserRole.EMPLOYEE,
                       access=build_access(["feature:studio"]), organization_id=org)


class _Request:
    """The parts of a request that the door reads."""

    def __init__(self, payload: Any) -> None:
        self._raw = json.dumps(payload).encode("utf-8")
        self.method, self.headers = "POST", {}
        self.url = SimpleNamespace(path="/studio/apps/x/actions/trail")
        self.query_params: dict[str, str] = {}

    async def stream(self) -> Any:
        yield self._raw


async def _call(org: str, email: str, pid: str, payload: Any) -> tuple[int, Any]:
    token = bind_tenant(org)
    try:
        found = await invoke.invoke_action(pid, "trail", request=_Request(payload),
                                           user=_user(org, email))
        return 200, found
    except HTTPException as exc:
        return exc.status_code, exc.detail
    finally:
        release_tenant(token)


@pytest.fixture
def door(promoted, monkeypatch):  # noqa: F811
    """A package of OWNER in org A with one record and two changes, and a
    package of OWNER in org B with the same manifest."""
    p = promoted
    monkeypatch.setattr(get_settings(), "studio_orgs", "*")
    mid = f"hist-{TAG}-{uuid.uuid4().hex[:6]}"
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b)

    async def setup() -> None:
        for key, org in (("pkg", w.a), ("pkg_b", w.b)):
            async with tenant_session(org) as db:
                made = await packages_store.create_package(db, OWNER, _door_manifest(mid))
            setattr(w, key, made.id)
        async with tenant_session(w.a) as db:
            obj = await store.define_object(db, OWNER, package_id=w.pkg, name="visit",
                                            fields=_DOOR_FIELDS)
            assert obj is not None
            [rec] = await store.apply_writes(db, OWNER, package_id=w.pkg, viewer=AS_OWNER,
                                             writes=[Write("create", obj.id,
                                                           data={"site": "Plant 4"})])
            await store.apply_writes(db, OWNER, package_id=w.pkg, viewer=AS_OWNER,
                                     writes=[Write("update", obj.id, rec.id,
                                                   {"site": "Plant 9"})])
        w.rec = rec.id

    _run(p, setup)
    yield w
    with p.admin_engine.begin() as c:
        ids = [w.pkg, w.pkg_b]
        c.execute(text("DELETE FROM app_audit WHERE app_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE id = ANY(CAST(:ids AS uuid[]))"), {"ids": ids})


def _audits(w: Any, pid: str) -> list[dict[str, Any]]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT detail FROM app_audit WHERE app_id = CAST(:p AS uuid) "
                              "ORDER BY id"), {"p": pid}).scalars().all()
    return [r if isinstance(r, dict) else json.loads(r) for r in rows]


def test_r8_the_door_answers_the_history_and_one_value_free_audit_row(door) -> None:
    status, got = _run(door.p, lambda: _call(door.a, OWNER, door.pkg,
                                             {"id": door.rec, "limit": 1}))
    assert status == 200, got
    assert [h["op"] for h in got["history"]] == ["create"]
    assert got["next"] == got["history"][0]["id"]
    status, more = _run(door.p, lambda: _call(door.a, OWNER, door.pkg,
                                              {"id": door.rec, "after": got["next"]}))
    assert status == 200, more
    assert [h["changes"] for h in more["history"]] == [
        {"site": {"old": "Plant 4", "new": "Plant 9"}}]
    assert more["history"][0]["actor"] == OWNER and more["next"] is None
    rows = _audits(door, door.pkg)
    assert rows == [{"action": "trail", "uses": "object:visit.history", "class": "A",
                     "outcome": "ran"}] * 2
    text_of_rows = json.dumps(rows)
    for word in ("Plant", "site", "changes", door.rec):
        assert word not in text_of_rows, word


def test_r8_another_member_and_org_b_get_404_from_the_door(door) -> None:
    status, _ = _run(door.p, lambda: _call(door.a, BEN, door.pkg, {"id": door.rec}))
    assert status == 404
    # Org B holds a package of the same owner, but no record of org A.
    status, got = _run(door.p, lambda: _call(door.b, OWNER, door.pkg_b, {"id": door.rec}))
    assert status == 404, got
    status, _ = _run(door.p, lambda: _call(door.b, OWNER, door.pkg, {"id": door.rec}))
    assert status == 404
