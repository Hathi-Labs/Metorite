"""WS-STU C4a-1 — the change history of Studio records on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C4a-1. Code:
``gateway/studio/objects_store._apply_write``, ``_record_change`` and
``purge_record_changes``, and ``packages_store.delete_package``. Migration: the
file that creates ``studio_record_changes`` (431 at build).
``test_studio_record_history.py`` holds the hermetic half.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_record_history_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (C4a-1).**

a. An update writes one row with the diff and the actor. Org B reads no row
   of org A as the app role, and an unbound session reads none.
b. A batch of two writes to one record gives two rows in id order.
c. A refused batch, and a caller that rolls back, leave no row.
d. A delete keeps the old values, and the row stays after the record goes.
e. An update that changes nothing writes no row.
f. ``purge_record_changes`` removes the history of a package in chunks, and a
   drop of the object takes its rows by the cascade.
g. The table has FORCE RLS and the DEFAULT of the bound tenant.

**The mutations (R7).** Commit before each, and restore with git after.

* R1. The migration: drop ``FORCE  ROW LEVEL SECURITY``.
  ``test_r8_the_table_has_force_rls_and_the_tenant_default`` goes red.
* R2. ``objects_store._apply_write``: write the history row after the SAVEPOINT
  of the batch. ``test_r8_a_refused_batch_leaves_no_row`` goes red.
* R3. ``objects_store._PURGE_CHANGES_SQL``: drop the object filter.
  ``test_r8_purge_removes_the_history_of_one_package_only`` goes red.
* R4. ``packages_store.delete_package``: drop the call of
  ``purge_record_changes``. ``test_r8_a_package_delete_purges_the_history_in_each_step``
  goes red, because the history stays after the first step.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from gateway.studio import objects_store as store
from gateway.studio import packages_store
from gateway.studio.objects_store import BatchError, Write
from sqlalchemy import create_engine, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc4a.test"

_FIELDS: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "qty", "type": "number"},
]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc4a', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc4a", "name": "stuc4a", "purpose": "The C4a-1 R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted: Any) -> Iterator[SimpleNamespace]:  # noqa: F811
    """Three packages: two of org A and one of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("a2", p.org_a), ("b", p.org_b)):
            pkgs[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc4a-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=pkgs["a"], pkg_a2=pkgs["a2"],
                          pkg_b=pkgs["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc4a-{TAG}-{run}-%"})


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _object(org: str, pkg: str, name: str = "thing") -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name, fields=_FIELDS)
    assert made is not None
    return made


async def _put(org: str, obj: store.StudioObject, *writes: Write,
               via: tuple[str, str] | None = None) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=obj.package_id,
                                        writes=list(writes), via=via)


def _history(world: Any, org: str, record_id: str) -> list[dict[str, Any]]:
    """The rows of one record, as the app role bound to *org*."""
    eng = create_engine(world.p.app_url, future=True)
    try:
        with eng.begin() as c:
            c.execute(text("SELECT set_config('app.tenant_id', :o, true)"), {"o": org})
            rows = c.execute(text(
                "SELECT id, op, actor, via_kind, via_id, changes FROM studio_record_changes "
                "WHERE record_id = CAST(:r AS uuid) ORDER BY id"), {"r": record_id}).mappings()
            return [dict(r) for r in rows]
    finally:
        eng.dispose()


def _admin_count(world: Any, where: str, params: dict[str, Any]) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text(
            f"SELECT count(*) FROM studio_record_changes WHERE {where}"), params).scalar_one())


def test_r8_an_update_writes_one_row_that_only_its_org_reads(world: Any, app_engine: Any) -> None:  # noqa: F811
    async def run() -> tuple[str, str]:
        obj = await _object(world.a, world.pkg_a)
        [made] = await _put(world.a, obj, Write("create", obj.id, data={"title": "a", "qty": 1}))
        await _put(world.a, obj, Write("update", obj.id, made.id, {"qty": 5}),
                   via=("workflow_run", "run-7"))
        return obj.id, made.id

    _oid, rid = _go(world, run)
    rows = _history(world, world.a, rid)
    assert [r["op"] for r in rows] == ["create", "update"]
    assert rows[0]["changes"] == {"qty": {"old": None, "new": 1},
                                  "title": {"old": None, "new": "a"}}
    assert rows[1]["changes"] == {"qty": {"old": 1, "new": 5}}
    assert rows[1]["actor"] == ANA
    assert (rows[0]["via_kind"], rows[0]["via_id"]) == (None, None)
    assert (rows[1]["via_kind"], rows[1]["via_id"]) == ("workflow_run", "run-7")
    # Org B reads nothing of org A, as the app role under FORCE RLS.
    assert _history(world, world.b, rid) == []
    # An unbound session reads nothing.
    with app_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM studio_record_changes")).scalar_one() == 0
    # The row holds the org of the bound tenant, from the DEFAULT.
    assert _admin_count(world, "record_id = CAST(:r AS uuid) AND organization_id = "
                        "CAST(:o AS uuid)", {"r": rid, "o": world.a}) == 2


def test_r8_two_writes_to_one_record_give_two_rows_in_id_order(world: Any) -> None:
    async def run() -> str:
        obj = await _object(world.a, world.pkg_a)
        [made] = await _put(world.a, obj, Write("create", obj.id, data={"qty": 1}))
        await _put(world.a, obj, Write("update", obj.id, made.id, {"qty": 2}),
                   Write("update", obj.id, made.id, {"qty": 3}))
        return made.id

    rid = _go(world, run)
    rows = _history(world, world.a, rid)
    assert [r["op"] for r in rows] == ["create", "update", "update"]
    assert [r["changes"] for r in rows[1:]] == [
        {"qty": {"old": 1, "new": 2}}, {"qty": {"old": 2, "new": 3}}]
    assert rows[1]["id"] < rows[2]["id"]


def test_r8_a_refused_batch_leaves_no_row(world: Any) -> None:
    async def run() -> str:
        obj = await _object(world.a, world.pkg_a)
        missing = str(uuid.uuid4())
        with pytest.raises(BatchError):
            await _put(world.a, obj, Write("create", obj.id, data={"title": "gone"}),
                       Write("update", obj.id, missing, {"qty": 1}))
        # A caller that rolls back after a good batch leaves no row either.
        with pytest.raises(RuntimeError):
            async with tenant_session(world.a) as db:
                await store.apply_writes(db, ANA, package_id=obj.package_id,
                                         writes=[Write("create", obj.id, data={"title": "x"})])
                raise RuntimeError("roll back")
        return obj.id

    oid = _go(world, run)
    assert _admin_count(world, "object_id = CAST(:o AS uuid)", {"o": oid}) == 0


def test_r8_a_delete_keeps_the_old_values(world: Any) -> None:
    async def run() -> tuple[str, Any]:
        obj = await _object(world.a, world.pkg_a)
        [made] = await _put(world.a, obj, Write("create", obj.id, data={"title": "a", "qty": 4}))
        await _put(world.a, obj, Write("delete", obj.id, made.id))
        async with tenant_session(world.a) as db:
            left = await store.get_record(db, obj.id, made.id, package_id=obj.package_id)
        return made.id, left

    rid, left = _go(world, run)
    assert left is None
    rows = _history(world, world.a, rid)
    assert [r["op"] for r in rows] == ["create", "delete"]
    assert rows[1]["changes"] == {"qty": {"old": 4, "new": None},
                                  "title": {"old": "a", "new": None}}


def test_r8_an_update_that_changes_nothing_writes_no_row(world: Any) -> None:
    async def run() -> str:
        obj = await _object(world.a, world.pkg_a)
        [made] = await _put(world.a, obj, Write("create", obj.id, data={"qty": 1}))
        await _put(world.a, obj, Write("update", obj.id, made.id, {"qty": 1}))
        return made.id

    rid = _go(world, run)
    assert [r["op"] for r in _history(world, world.a, rid)] == ["create"]


def test_r8_purge_removes_the_history_of_one_package_only(world: Any) -> None:
    async def run() -> tuple[str, str, list[int]]:
        obj = await _object(world.a, world.pkg_a)
        other = await _object(world.a, world.pkg_a2)
        await _put(world.a, obj, *[Write("create", obj.id, data={"qty": i}) for i in range(5)])
        await _put(world.a, other, Write("create", other.id, data={"qty": 9}))
        gone: list[int] = []
        while True:
            async with tenant_session(world.a) as db:
                await store.lock_org(db)
                n = await store.purge_record_changes(db, world.pkg_a, limit=2)
            gone.append(n)
            if n < 2:
                break
        return obj.id, other.id, gone

    oid, other_id, gone = _go(world, run)
    assert gone == [2, 2, 1]
    assert _admin_count(world, "object_id = CAST(:o AS uuid)", {"o": oid}) == 0
    assert _admin_count(world, "object_id = CAST(:o AS uuid)", {"o": other_id}) == 1


def test_r8_a_drop_of_the_object_takes_its_history(world: Any) -> None:
    async def run() -> str:
        obj = await _object(world.a, world.pkg_a)
        await _put(world.a, obj, Write("create", obj.id, data={"qty": 1}))
        async with tenant_session(world.a) as db:
            assert await store.drop_object(db, obj.id) is True
        return obj.id

    oid = _go(world, run)
    assert _admin_count(world, "object_id = CAST(:o AS uuid)", {"o": oid}) == 0


def test_r8_the_table_has_force_rls_and_the_tenant_default(world: Any) -> None:
    with world.p.admin_engine.connect() as c:
        row = c.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = to_regclass('studio_record_changes')")).one()
        default = c.execute(text(
            "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d "
            "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
            "WHERE d.adrelid = to_regclass('studio_record_changes') "
            "AND a.attname = 'organization_id'")).scalar_one()
    assert tuple(row) == (True, True)
    assert "current_setting('app.tenant_id'" in default


def test_r8_a_package_delete_purges_the_history_in_each_step(world: Any) -> None:
    """The real ``delete_package``, with a chunk of 2. Each step purges two
    history rows before its records, so the history goes down in each step,
    and not only by the cascade at the end. 4 creates and 1 update give 5 rows."""
    def history() -> int:
        return _admin_count(world, "organization_id = CAST(:o AS uuid) AND object_id IN "
                            "(SELECT id FROM studio_objects WHERE package_id = CAST(:p AS uuid))",
                            {"o": world.a, "p": world.pkg_a})

    async def fill() -> None:
        obj = await _object(world.a, world.pkg_a)
        made = await _put(world.a, obj, *[Write("create", obj.id, data={"qty": i})
                                          for i in range(4)])
        await _put(world.a, obj, Write("update", obj.id, made[0].id, {"qty": 9}))

    _go(world, fill)
    assert history() == 5
    seen: list[tuple[int, bool, bool]] = []

    async def delete() -> None:
        async with tenant_session(world.a) as db:
            preview = await packages_store.delete_preview(db, ANA, world.pkg_a)
        assert preview is not None
        names: tuple[str, ...] | None = None
        removed = 0
        while True:
            async with tenant_session(world.a) as db:
                step = await packages_store.delete_package(
                    db, ANA, world.pkg_a, confirm=preview.confirm if names is None else None,
                    names=names, chunk=2, removed=removed)
            assert step is not None
            seen.append((history(), step.deleted, step.history_left))
            removed += step.removed
            if step.deleted:
                return
            names = step.names

    _go(world, delete)
    # Step 1: 2 history rows and 2 records. Step 2: 2 and 2, so the history
    # is a full chunk. Step 3: the last history row, and the package goes.
    assert seen == [(3, False, True), (1, False, True), (0, True, False)]
    with world.p.admin_engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM apps WHERE id = CAST(:p AS uuid)"),
                         {"p": world.pkg_a}).scalar_one() == 0
