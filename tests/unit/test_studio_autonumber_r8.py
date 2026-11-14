"""WS-STU C15a — the autonumber field of a Studio object, on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14 C15a. Code:
``gateway/studio/objects_store.py`` (``_NEXT_NUMBER_SQL``, ``_fill_autonumbers``,
``_batch_day``), ``acb_common/studio_fields.py`` and the migration that adds
``studio_object_counters`` (444 at build). The hermetic twins are
``test_studio_fields.py``, ``test_studio_field_cases_parity.py`` and
``test_package_field_values.py``.

The store runs on a phase-4 promoted scratch database, as the non-privileged
app role, so FORCE RLS binds. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_autonumber_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**The rules.** The store gives each new record the next number in ONE atomic
statement (INSERT ... ON CONFLICT DO UPDATE ... RETURNING), inside the SAVEPOINT
of the batch and under the org lock. A refused or rolled back write rolls the
counter back, so no gap comes from a refusal. A deleted record leaves a hole,
and no number is used twice. An update never renumbers.

**The mutations (R7).** Each ran on 2026-11-14 against the committed code, and
each named test went red (the others of the file stayed green unless named).

* M1. ``_NEXT_NUMBER_SQL`` becomes a SELECT of ``max(last_value)`` then an
  UPSERT that writes ``:read + 1``. ``test_r8_two_writers_one_object_get_distinct_numbers``
  stays green, because the org lock serializes the writers. The fence is the
  SQL text: ``test_the_next_number_is_one_atomic_statement`` of the hermetic
  file ``test_studio_autonumber.py`` goes red.
* M2. ``_batch_day`` reads SQL ``now()`` for the period.
  ``test_r8_the_day_resets_in_the_zone_of_the_org`` goes red.
* M3. ``_refuse_autonumber_keys`` is not called on an update.
  ``test_r8_an_update_that_names_the_key_is_refused`` goes red.
* M4. ``_fill_autonumbers`` runs on a second SAVEPOINT that releases at once
  (the allocation leaves the SAVEPOINT of the batch).
  ``test_r8_a_refused_batch_rolls_the_counter_back`` goes red.
* M5. The migration 444: drop the FORCE line. The source test
  ``test_the_migration_forces_rls_and_keys_by_the_pair`` (hermetic file) goes red. The
  promoted scratch database applies ``generated/04_policies.sql``, which forces
  RLS again, so ``test_r8_the_counter_table_has_enable_and_force_rls`` stays green.
  That is the D13b limit, and the source test of the hermetic file
  ``test_studio_autonumber.py`` is the fence of the migration file.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from acb_common.studio_access import Viewer
from acb_common.studio_fields import FieldError
from gateway.studio import objects_store as store
from gateway.studio.objects_store import BatchError, Write
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc15.test"
_VIEWER = Viewer(ANA)

_INVOICE: list[dict[str, Any]] = [
    {"key": "no", "type": "autonumber", "prefix": "FV-", "pad": 4},
    {"key": "title", "type": "text"},
]
_DAILY: list[dict[str, Any]] = [
    {"key": "no", "type": "autonumber", "prefix": "D-", "pad": 3, "reset": "day"},
    {"key": "title", "type": "text"},
]
_ORDER: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "lines", "type": "relation", "to": "order_line", "lines": "order"},
]
_LINE: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "no", "type": "autonumber", "prefix": "L-", "pad": 4},
]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc15', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc15", "name": "stuc15", "purpose": "The C15a R8 suite."})


# ── The database ───────────────────────────────────────────────────────────

@pytest.fixture
def world(promoted):  # noqa: F811
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            pkgs[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc15-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
        for org in (p.org_a, p.org_b):
            c.execute(text("DELETE FROM org_settings WHERE key = 'work_schedule' "
                           "AND organization_id = CAST(:o AS uuid)"), {"o": org})
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=pkgs["a"], pkg_b=pkgs["b"])
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE organization_id IN "
                       "(CAST(:a AS uuid), CAST(:b AS uuid))"), {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM org_settings WHERE key = 'work_schedule' "
                       "AND organization_id IN (CAST(:a AS uuid), CAST(:b AS uuid))"),
                  {"a": p.org_a, "b": p.org_b})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc15-{TAG}-{run}-%"})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _define(org: str, pkg: str, name: str, fields: list[dict[str, Any]]) -> store.StudioObject:
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name, fields=fields)
    assert made is not None
    return made


async def _create(org: str, obj: store.StudioObject, title: str = "x") -> dict[str, Any]:
    async with tenant_session(org) as db:
        rec = await store.create_record(
            db, ANA, obj.id, {"title": title}, package_id=obj.package_id, viewer=_VIEWER)
    assert rec is not None
    return rec.data


async def _batch(org: str, obj: store.StudioObject, writes: list[Write]) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(
            db, ANA, package_id=obj.package_id, writes=writes, viewer=_VIEWER)


def _admin_rows(world: Any, sql: str, **params: Any) -> list[Any]:
    with world.p.admin_engine.connect() as c:
        return [tuple(r) for r in c.execute(text(sql), params).all()]


_COUNTERS = ("SELECT field_key, period, last_value FROM studio_object_counters "
             "WHERE object_id = CAST(:o AS uuid) ORDER BY period, field_key")


def _clock(monkeypatch: Any, *moments: datetime) -> list[int]:
    """Make the store clock answer *moments* in turn, the last one for ever."""
    calls: list[int] = []

    def now() -> datetime:
        calls.append(1)
        return moments[min(len(calls), len(moments)) - 1]

    monkeypatch.setattr(store, "_wall_now", now)
    return calls


def _utc(day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=UTC)


def _zone(world: Any, org: str, zone: str) -> None:
    with world.p.admin_engine.begin() as c:
        c.execute(text("INSERT INTO org_settings (organization_id, key, value) "
                       "VALUES (CAST(:o AS uuid), 'work_schedule', CAST(:v AS jsonb))"),
                  {"o": org, "v": json.dumps({"default_timezone": zone})})


# ── The allocation ─────────────────────────────────────────────────────────


def test_r8_the_counter_table_has_enable_and_force_rls(world) -> None:
    rows = _admin_rows(
        world, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relname = 'studio_object_counters'")
    assert rows == [(True, True)]


def test_r8_a_new_record_gets_the_next_number(world) -> None:
    async def go() -> list[Any]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        return [(await _create(world.a, obj, f"t{i}")).get("no") for i in range(3)]

    assert _go(world, go) == ["FV-0001", "FV-0002", "FV-0003"]


def test_r8_two_writers_one_object_get_distinct_numbers(world) -> None:
    async def writer(obj: store.StudioObject) -> list[str]:
        out: list[str] = []
        for i in range(10):
            out.append((await _create(world.a, obj, f"w{i}"))["no"])
        return out

    async def go() -> list[list[str]]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        return list(await asyncio.gather(writer(obj), writer(obj)))

    first, second = _go(world, go)
    every = first + second
    assert len(every) == 20 and len(set(every)) == 20
    assert sorted(every) == [f"FV-{n:04d}" for n in range(1, 21)]
    # Each writer saw its own numbers rise.
    assert first == sorted(first) and second == sorted(second)


def test_r8_the_statement_alone_is_atomic_without_the_org_lock(world) -> None:
    """The org lock already serializes each write. This test takes it away. Session
    one takes a number and holds its transaction open. Session two takes a number
    at the same time, so it must wait for session one and then count on from it.
    A statement that reads the counter and then writes it would give both
    sessions the same number. The first round starts with no counter row, and
    the next two start with one."""
    async def take(oid: str, db: Any) -> int:
        return int((await db.execute(text(store._NEXT_NUMBER_SQL), {
            "oid": oid, "key": "no", "period": ""})).scalar_one())

    async def second(oid: str) -> int:
        async with tenant_session(world.a) as db:
            return await take(oid, db)

    async def go() -> list[tuple[int, int]]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        pairs: list[tuple[int, int]] = []
        for _ in range(3):
            async with tenant_session(world.a) as one:
                first = await take(obj.id, one)
                task = asyncio.create_task(second(obj.id))
                await asyncio.sleep(0.5)  # session two is now at the row of session one
            pairs.append((first, await task))
        return pairs

    assert _go(world, go) == [(1, 2), (3, 4), (5, 6)]


def test_r8_two_orgs_count_apart(world) -> None:
    async def go() -> tuple[list[str], list[str]]:
        a = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        b = await _define(world.b, world.pkg_b, "invoice", _INVOICE)
        got_a = [(await _create(world.a, a))["no"] for _ in range(3)]
        got_b = [(await _create(world.b, b))["no"] for _ in range(2)]
        return got_a, got_b

    assert _go(world, go) == (["FV-0001", "FV-0002", "FV-0003"], ["FV-0001", "FV-0002"])


def test_r8_org_b_reads_no_counter_of_org_a(world) -> None:
    async def go() -> tuple[int, int, str, str]:
        a = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        b = await _define(world.b, world.pkg_b, "invoice", _INVOICE)
        await _create(world.a, a)
        await _create(world.b, b)
        async with tenant_session(world.b) as db:
            seen_b = (await db.execute(text("SELECT count(*) FROM studio_object_counters"))).scalar_one()
            of_a = (await db.execute(text(
                "SELECT count(*) FROM studio_object_counters WHERE object_id = CAST(:o AS uuid)"),
                {"o": a.id})).scalar_one()
        # Org B cannot make a counter row on the object of org A: the pair key refuses it.
        crossing = ""
        try:
            async with tenant_session(world.b) as db:
                await db.execute(text(
                    "INSERT INTO studio_object_counters (object_id, field_key, last_value) "
                    "VALUES (CAST(:o AS uuid), 'no', 7)"), {"o": a.id})
        except DBAPIError as exc:
            crossing = str(exc.orig)
        # Org A cannot write a row that names org B: the WITH CHECK of the policy refuses it.
        naming = ""
        try:
            async with tenant_session(world.a) as db:
                await db.execute(text(
                    "INSERT INTO studio_object_counters "
                    "(organization_id, object_id, field_key, last_value) "
                    "VALUES (CAST(:org AS uuid), CAST(:o AS uuid), 'no', 7)"),
                    {"org": world.b, "o": b.id})
        except DBAPIError as exc:
            naming = str(exc.orig)
        return int(seen_b), int(of_a), crossing, naming

    seen_b, of_a, crossing, naming = _go(world, go)
    assert (seen_b, of_a) == (1, 0)
    assert "foreign key" in crossing.lower() or "row-level security" in crossing.lower()
    assert "row-level security" in naming.lower()


def test_r8_a_refused_batch_rolls_the_counter_back(world) -> None:
    """The caller catches the refusal and goes on in the SAME transaction, as a
    door does. Only the SAVEPOINT of the batch can give the number back."""
    async def go() -> tuple[Any, str, list[Any]]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        async with tenant_session(world.a) as db:
            with pytest.raises(BatchError) as exc:
                await store.apply_writes(db, ANA, package_id=obj.package_id, viewer=_VIEWER, writes=[
                    Write("create", obj.id, data={"title": "ok"}),
                    Write("create", obj.id, data={"title": 5})])  # a bad value
            made = await store.apply_writes(db, ANA, package_id=obj.package_id, viewer=_VIEWER,
                                            writes=[Write("create", obj.id, data={"title": "t"})])
        return exc.value.index, made[0].data["no"], _admin_rows(world, _COUNTERS, o=obj.id)

    index, number, counters = _go(world, go)
    assert index == 1
    assert number == "FV-0001"  # the refused batch took 1 and gave it back
    assert counters == [("no", "", 1)]


def test_r8_an_outer_rollback_leaves_no_gap(world) -> None:
    async def go() -> tuple[str, list[Any]]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        with pytest.raises(RuntimeError):
            async with tenant_session(world.a) as db:
                done = await store.apply_writes(
                    db, ANA, package_id=obj.package_id,
                    writes=[Write("create", obj.id, data={"title": "t"})], viewer=_VIEWER)
                assert done[0].data["no"] == "FV-0001"
                raise RuntimeError("the caller fails after the batch")
        return (await _create(world.a, obj))["no"], _admin_rows(world, _COUNTERS, o=obj.id)

    next_no, counters = _go(world, go)
    assert next_no == "FV-0001"
    assert counters == [("no", "", 1)]


def test_r8_a_delete_frees_no_number(world) -> None:
    async def go() -> list[str]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        made: list[store.Record] = []
        for _ in range(3):
            async with tenant_session(world.a) as db:
                made.append(await store.create_record(
                    db, ANA, obj.id, {"title": "t"}, package_id=obj.package_id, viewer=_VIEWER))
        await _batch(world.a, obj, [Write("delete", obj.id, record_id=made[2].id)])
        await _batch(world.a, obj, [Write("delete", obj.id, record_id=made[0].id)])
        return [(await _create(world.a, obj))["no"] for _ in range(2)]

    assert _go(world, go) == ["FV-0004", "FV-0005"]


def test_r8_an_update_never_renumbers_and_one_that_names_the_key_is_refused(world) -> None:
    async def go() -> tuple[str, str, Any]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        first = (await _batch(world.a, obj, [Write("create", obj.id, data={"title": "a"})]))[0]
        moved = (await _batch(world.a, obj, [
            Write("update", obj.id, record_id=first.id, data={"title": "b"})]))[0]
        with pytest.raises(BatchError) as exc:
            await _batch(world.a, obj, [
                Write("update", obj.id, record_id=first.id, data={"no": "FV-9999"})])
        return first.data["no"], moved.data["no"], exc.value.cause

    before, after, cause = _go(world, go)
    assert before == after == "FV-0001"
    assert isinstance(cause, FieldError) and cause.field == "no"
    assert "`no`" in cause.message and "FV-9999" not in cause.message


def test_r8_an_update_that_names_the_key_is_refused(world) -> None:
    """M3. The same key with the same value is refused too: a caller names no autonumber."""
    async def go() -> list[Any]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        rec = (await _batch(world.a, obj, [Write("create", obj.id, data={"title": "a"})]))[0]
        causes = []
        for body in ({"no": rec.data["no"]}, {"no": None}, {"no": "ZZ-1", "title": "c"}):
            with pytest.raises(BatchError) as exc:
                await _batch(world.a, obj, [Write("update", obj.id, record_id=rec.id, data=body)])
            causes.append(exc.value.cause)
        return causes

    causes = _go(world, go)
    assert all(isinstance(c, FieldError) and c.field == "no" for c in causes)


def test_r8_a_create_that_names_the_key_is_refused(world) -> None:
    async def go() -> tuple[Any, list[Any]]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        with pytest.raises(BatchError) as exc:
            await _batch(world.a, obj, [Write("create", obj.id, data={"title": "a", "no": "FV-0777"})])
        return exc.value.cause, _admin_rows(world, _COUNTERS, o=obj.id)

    cause, counters = _go(world, go)
    assert isinstance(cause, FieldError) and cause.field == "no"
    assert counters == []  # the refusal ran before the allocation, and the SAVEPOINT holds none


def test_r8_a_batch_of_thirty_creates_numbers_them_in_order(world, monkeypatch) -> None:
    calls = _clock(monkeypatch, _utc(14, 6, 0))

    async def go() -> list[str]:
        obj = await _define(world.a, world.pkg_a, "daily", _DAILY)
        done = await _batch(world.a, obj, [
            Write("create", obj.id, data={"title": f"t{i}"}) for i in range(30)])
        return [r.data["no"] for r in done]

    assert _go(world, go) == [f"D-{n:03d}" for n in range(1, 31)]
    assert len(calls) == 1  # the day is read once for each batch


def test_r8_the_day_resets_in_the_zone_of_the_org(world, monkeypatch) -> None:
    _zone(world, world.a, "Asia/Kolkata")
    # 23:59 IST on the 14th is 18:29 UTC. 00:01 IST on the 15th is 18:31 UTC.
    # Org B has no zone, so its day is the UTC day: both moments are the 14th.
    _clock(monkeypatch, _utc(14, 18, 29), _utc(14, 18, 29), _utc(14, 18, 31), _utc(14, 18, 31),
           _utc(14, 18, 29), _utc(14, 18, 29), _utc(14, 18, 31))

    async def go() -> tuple[list[str], list[str]]:
        a = await _define(world.a, world.pkg_a, "daily", _DAILY)
        b = await _define(world.b, world.pkg_b, "daily", _DAILY)
        got_a = [(await _create(world.a, a))["no"] for _ in range(4)]
        got_b = [(await _create(world.b, b))["no"] for _ in range(3)]
        return got_a, got_b

    got_a, got_b = _go(world, go)
    assert got_a == ["D-001", "D-002", "D-001", "D-002"]
    assert got_b == ["D-001", "D-002", "D-003"]
    rows = _admin_rows(
        world, "SELECT period, last_value FROM studio_object_counters "
        "WHERE organization_id = CAST(:o AS uuid) ORDER BY period", o=world.a)
    assert rows == [("2026-11-14", 2), ("2026-11-15", 2)]
    assert _admin_rows(
        world, "SELECT period, last_value FROM studio_object_counters "
        "WHERE organization_id = CAST(:o AS uuid)", o=world.b) == [("2026-11-14", 3)]


def test_r8_two_writers_at_midnight_never_collide(world, monkeypatch) -> None:
    """The writer that takes the org lock second reads the NEW day. Each batch
    reads the clock after the lock, so the two periods differ, and no number
    repeats within a period."""
    _zone(world, world.a, "Asia/Kolkata")
    _clock(monkeypatch, _utc(14, 18, 29, 30), _utc(14, 18, 30, 30))

    async def go() -> list[str]:
        obj = await _define(world.a, world.pkg_a, "daily", _DAILY)
        got = await asyncio.gather(_create(world.a, obj, "x"), _create(world.a, obj, "y"))
        return [r["no"] for r in got]

    got = _go(world, go)
    assert got == ["D-001", "D-001"]  # one number 1 on each day: two periods, no repeat in one
    assert len(_admin_rows(world, "SELECT 1 FROM studio_object_counters")) == 2
    assert {r[0] for r in _admin_rows(world, "SELECT period FROM studio_object_counters")} == {
        "2026-11-14", "2026-11-15"}


def test_r8_the_lines_of_a_save_each_take_a_number(world) -> None:
    async def go() -> list[str]:
        line = await _define(world.a, world.pkg_a, "order_line", _LINE)
        order = await _define(world.a, world.pkg_a, "order", _ORDER)
        done = await _batch(world.a, order, [
            Write("create", order.id, data={"title": "o"}),
            Write("create", line.id, data={}, parent=0),
            Write("create", line.id, data={}, parent=0),
            Write("create", line.id, data={}, parent=0)])
        return [r.data["no"] for r in done[1:]]

    assert _go(world, go) == ["L-0001", "L-0002", "L-0003"]


def test_r8_a_dropped_and_added_field_continues_the_count(world) -> None:
    async def go() -> tuple[str, str]:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        await _create(world.a, obj)
        await _create(world.a, obj)
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM studio_object_fields WHERE object_id = CAST(:o AS uuid) "
                           "AND key = 'no'"), {"o": obj.id})
        async with tenant_session(world.a) as db:
            await store.add_field(db, obj.id, {"key": "no", "type": "autonumber", "prefix": "FV-"})
        return (await _create(world.a, obj))["no"], str(
            _admin_rows(world, _COUNTERS, o=obj.id))

    got, counters = _go(world, go)
    assert got == "FV-0003"
    assert "('no', '', 3)" in counters


def test_r8_a_deleted_object_takes_its_counters(world) -> None:
    async def go() -> str:
        obj = await _define(world.a, world.pkg_a, "invoice", _INVOICE)
        await _create(world.a, obj)
        assert _admin_rows(world, _COUNTERS, o=obj.id) == [("no", "", 1)]
        with world.p.admin_engine.begin() as c:
            c.execute(text("DELETE FROM studio_objects WHERE id = CAST(:o AS uuid)"), {"o": obj.id})
        return obj.id

    oid = _go(world, go)
    assert _admin_rows(world, _COUNTERS, o=oid) == []


def test_r8_a_changed_prefix_binds_new_numbers_and_a_changed_reset_is_refused(world) -> None:
    manifest_fields = [dict(f) for f in _INVOICE]

    async def go() -> tuple[list[str], Any, Any]:
        obj = await _define(world.a, world.pkg_a, "invoice", manifest_fields)
        await _create(world.a, obj)
        async with tenant_session(world.a) as db:
            res = await store.sync_objects(db, ANA, package_id=world.pkg_a, objects=[
                {"name": "invoice", "fields": [
                    {"key": "no", "type": "autonumber", "prefix": "INV/", "pad": 6},
                    {"key": "title", "type": "text"}]}])
        second = await _create(world.a, obj)
        with pytest.raises(store.ObjectsOutOfSync) as exc:
            async with tenant_session(world.a) as db:
                await store.sync_objects(db, ANA, package_id=world.pkg_a, objects=[
                    {"name": "invoice", "fields": [
                        {"key": "no", "type": "autonumber", "prefix": "INV/", "pad": 6,
                         "reset": "day"},
                        {"key": "title", "type": "text"}]}])
        return [second["no"]], res, exc.value.problems

    nos, res, problems = _go(world, go)
    assert nos == ["INV/000002"]
    assert res.fields_respecced == (("invoice", "no"),)
    assert [(p.kind, p.field) for p in problems] == [("field_changed", "no")]
    assert "reset" in problems[0].message
