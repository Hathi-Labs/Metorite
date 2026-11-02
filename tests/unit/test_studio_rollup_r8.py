"""WS-STU STU-C7b: roll-up fields on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` section 3.7 (layer 2) and the section 14
C7b row. Code: the rule kind ``rollup`` in ``acb_common/studio_rules.py`` (the
pure half, in ``test_studio_rules.py``), the hot relation in
``studio_fields.py`` and ``studio_query.py``, the migration that widens the CHECK
``studio_object_fields_hot_type``, and the roll-up section of
``gateway/studio/objects_store.py`` (``_find_rollups``, ``_recompute_parents``,
``_derive_parent`` and ``build_rollup``).

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_rollup_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (C7b).** Sum, count, min and max are exact after each write, with
``where``. A move, a delete and a create each leave both parents exact. Two real
connections that each take 7 from 10 give exactly one success and a sum of 3.
50 child writes to one parent run one aggregate statement. A parent ``range``
that breaks rolls back the child write. The plan of a sum over 50,000 children
shows no Seq Scan. A source in another package is refused.

**The mutations (R7).** Commit before each, and restore with git after.

* M1. ``objects_store._stale_parents``: take only the new parent of a move
  (``rollup_parents`` answers ``[new]``).
  ``test_r8_a_move_leaves_the_old_and_the_new_parent_exact`` goes red.
* M2. ``studio_rules.rollup_parents``: answer ``[]`` for a delete.
  ``test_r8_sum_and_count_follow_a_create_an_update_and_a_delete`` goes red.
* M3. ``objects_store._apply_batch``: run ``_recompute_parents`` after the SAVEPOINT.
  ``test_r8_a_refused_recompute_rolls_back_its_savepoint_and_keeps_the_callers_work`` and
  ``test_r8_a_parent_range_that_breaks_rolls_back_the_child_write`` go red.
* M4. ``objects_store._ROLLUP_OBJECTS_SQL``: drop the package filter.
  ``test_r8_a_write_in_another_package_touches_no_parent_of_this_one`` goes red.
* M5. ``studio_rules.touched_keys``: take ``ROLLUP`` out of the early return.
  ``test_r8_a_rollup_queues_no_key_check`` and ``test_a_rollup_touches_no_key`` go red.
* M6. ``studio_rules.check_rollup_currency``: return at once.
  ``test_r8_a_currency_mismatch_is_refused_and_the_batch_writes_nothing`` goes red.
* M7. ``studio_rules.check_rollups``: skip the key test of the target.
  ``test_r8_a_target_cannot_be_a_key_of_another_rule`` goes red.
* M8. ``objects_store._stale_parents``: give each child write its own entry in
  ``work`` (a key with the mark index). ``test_r8_fifty_children_of_one_parent_run_one_aggregate`` goes red.
* M9. ``objects_store._check_no_rollup_reads``: return at once.
  ``test_r8_a_field_that_a_rollup_reads_cannot_turn_cold`` goes red.
* M10. ``studio_rules.refuse_computed_keys``: ignore the roll-up targets.
  ``test_r8_a_caller_cannot_write_a_rollup_target`` goes red.
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

from acb_common import studio_fields as sf
from acb_common import studio_rules as sr
from acb_common.db import get_engine, tenant_session
from acb_common.studio_rules import (
    ConstraintError,
    ConstraintViolation,
    FormulaError,
    RollupError,
)
from gateway.studio import objects_store as store
from gateway.studio.objects_store import BatchError, BudgetExceeded, Write
from sqlalchemy import event, text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

pytestmark = _DB_GATE

_ROOT = Path(__file__).resolve().parents[2]
TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc7b.test"
BOB = f"bob.{TAG}@stuc7b.test"

_ACCOUNT: list[dict[str, Any]] = [
    {"key": "name", "type": "text"},
    {"key": "balance", "type": "number"},
    {"key": "approved_days", "type": "number"},
    {"key": "entries", "type": "number", "hot": True},
    {"key": "first_day", "type": "date"},
    {"key": "last_day", "type": "date"},
    {"key": "spent", "type": "money"},
    {"key": "top", "type": "money"},
    {"key": "twice", "type": "number"},
]


def _entry_fields(parent: str = "account") -> list[dict[str, Any]]:
    return [
        {"key": "account", "type": "relation", "to": parent, "hot": True},
        {"key": "days", "type": "number", "hot": True},
        {"key": "amount", "type": "money", "hot": True},
        {"key": "kind", "type": "select", "options": ["approved", "pending"], "hot": True},
        {"key": "day", "type": "date", "hot": True},
    ]


def _roll(name: str, target: str, agg: str, field: str | None = None, **more: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"name": name, "kind": "rollup", "target": target, "source": "entry",
                            "via": "account", "agg": agg}
    if field is not None:
        spec["field"] = field
    return {**spec, **more}


_APPROVED_ONLY = [{"field": "kind", "op": "eq", "value": "approved"}]
BAL = _roll("bal", "balance", "sum", "days")
APPROVED = _roll("approved", "approved_days", "sum", "days", where=_APPROVED_ONLY)
COUNT = _roll("how_many", "entries", "count")
FIRST = _roll("first", "first_day", "min", "day")
LAST = _roll("last", "last_day", "max", "day")
SPENT = _roll("spent_in", "spent", "sum", "amount", currency="INR")
TOP = _roll("top_of", "top", "max", "amount", currency="INR")
TWICE = {"name": "twice_of", "kind": "formula", "target": "twice", "rounding": "half_up",
         "expr": {"fn": "mul", "args": [{"ref": "balance"}, {"num": "2"}]}}
BALANCE_OK = {"name": "balance_ok", "kind": "range", "field": "balance", "min": 0}
ALL = [BAL, APPROVED, COUNT, FIRST, LAST, SPENT, TOP]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc7b', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc7b", "name": "stuc7b", "purpose": "The C7b R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _packages(p: Any, run: str, wanted: tuple[tuple[str, str], ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in wanted:
            out[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc7b-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    return out


def _drop_packages(p: Any, run: str, ids: list[str]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc7b-{TAG}-{run}-%"})


@pytest.fixture
def world(promoted):  # noqa: F811
    """Three packages: two of org A and one of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs = _packages(p, run, (("a", p.org_a), ("a2", p.org_a), ("b", p.org_b)))
    yield SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=pkgs["a"], pkg_a2=pkgs["a2"],
                          pkg_b=pkgs["b"])
    _drop_packages(p, run, list(pkgs.values()))


def _go(world: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _objects(org: str, pkg: str, rules: list[dict[str, Any]] | None = None, *,
                   account_fields: list[dict[str, Any]] | None = None
                   ) -> tuple[store.StudioObject, store.StudioObject]:
    """The parent `account` with its roll-ups, and the child `entry`."""
    async with tenant_session(org) as db:
        acc = await store.define_object(
            db, ANA, package_id=pkg, name="account",
            fields=_ACCOUNT if account_fields is None else account_fields)
        ent = await store.define_object(db, ANA, package_id=pkg, name="entry", fields=_entry_fields())
        assert acc is not None and ent is not None
        done = await store.set_constraints(
            db, acc.id, ALL if rules is None else rules, package_id=pkg)
    assert done is not None
    return done, ent


async def _put(org: str, obj: store.StudioObject, *writes: Write, actor: str = ANA) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, actor, package_id=obj.package_id, writes=list(writes))


async def _refused(org: str, obj: store.StudioObject, *writes: Write) -> BatchError:
    with pytest.raises(BatchError) as caught:
        await _put(org, obj, *writes)
    return caught.value


async def _acct(org: str, acc: store.StudioObject, **data: Any) -> store.Record:
    data.setdefault("name", "acme")
    return (await _put(org, acc, Write("create", acc.id, data=data)))[0]


def _entry(ent: store.StudioObject, parent: Any, **data: Any) -> Write:
    ref = parent if isinstance(parent, str) else parent.id
    return Write("create", ent.id, data={"account": {"id": ref}, **data})


async def _give(org: str, ent: store.StudioObject, parent: Any, **data: Any) -> store.Record:
    return (await _put(org, ent, _entry(ent, parent, **data)))[0]


def _data(world: Any, rid: str) -> dict[str, Any]:
    with world.p.admin_engine.connect() as c:
        raw = c.execute(text("SELECT data FROM studio_records WHERE id = CAST(:r AS uuid)"),
                        {"r": rid}).scalar_one()
    return dict(raw if isinstance(raw, dict) else json.loads(raw))


def _row(world: Any, rid: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT updated_by, created_at, updated_at FROM studio_records "
            "WHERE id = CAST(:r AS uuid)"), {"r": rid}).one()


def _rows(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


def _hot(world: Any, rid: str, key: str) -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text("SELECT v_num FROM studio_record_hot WHERE record_id = "
                              "CAST(:r AS uuid) AND field_key = :k"), {"r": rid, "k": key}).scalar()


def _inr(minor: int) -> dict[str, Any]:
    return {"minor": minor, "currency": "INR"}


# -- the migration and the hot relation ------------------------------------------


def test_r8_the_check_names_relation_and_keeps_every_other_rule(world) -> None:
    """The live CHECK takes a hot relation, and still refuses a hot long text and
    a hot field with `many`."""
    async def go() -> bool:
        _acc, ent = await _objects(world.a, world.pkg_a, [])
        async with tenant_session(world.a) as db:
            return next(f for f in await store._fields(db, ent.id) if f.key == "account").hot

    assert _go(world, go) is True
    with world.p.admin_engine.connect() as c:
        defn = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'studio_object_fields_hot_type'")).scalar_one()
    assert "relation" in defn and "email" in defn and "many" in defn
    assert "longText" not in defn


def test_r8_the_migration_is_idempotent_and_a_replay_changes_nothing(world) -> None:
    sql = _latest_hot_type_file().read_text(encoding="utf-8")
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
        defn = c.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'studio_object_fields_hot_type'")).scalar_one()
    assert "relation" in defn


def _latest_hot_type_file() -> Path:
    files = [p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
             if "CONSTRAINT studio_object_fields_hot_type CHECK" in p.read_text(encoding="utf-8")]
    return max(files, key=lambda p: int(p.name.split("_", 1)[0]))


def test_the_migration_is_expand_only_and_names_the_hot_types() -> None:
    sql = _latest_hot_type_file().read_text(encoding="utf-8")
    assert "lock_timeout" in sql
    assert "CREATE TABLE" not in sql and "RENAME" not in sql and "DROP TABLE" not in sql
    assert "DROP COLUMN" not in sql
    check = sql.split("CONSTRAINT studio_object_fields_hot_type CHECK", 1)[1]
    listed = re.findall(r"'(\w+)'", check.split("AND (spec", 1)[0])
    assert listed == list(sf.HOT_TYPES) and "relation" in listed


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """Its own removal from CI must fail here. The list names the pytest
    ARGUMENT, so a mention in a comment does not count."""
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_rollup_r8\.py(?: \\)?$", block.group(1), re.M)


def test_r8_a_hot_relation_filters_by_id_and_takes_no_sort(world) -> None:
    async def go() -> tuple[int, int, str]:
        acc, ent = await _objects(world.a, world.pkg_a, [])
        p1, p2 = await _acct(world.a, acc), await _acct(world.a, acc)
        await _give(world.a, ent, p1, days=1)
        await _give(world.a, ent, p1, days=2)
        await _give(world.a, ent, p2, days=3)
        async with tenant_session(world.a) as db:
            two = await store.query_records(
                db, package_id=ent.package_id, object_id=ent.id,
                filter=[{"field": "account", "op": "eq", "value": p1.id}])
            other = await store.query_records(
                db, package_id=ent.package_id, object_id=ent.id,
                filter=[{"field": "account", "op": "ne", "value": {"id": p1.id}}])
            with pytest.raises(store.QueryError) as refused:
                await store.query_records(db, package_id=ent.package_id, object_id=ent.id,
                                          sort={"field": "account"})
        return two.total or 0, other.total or 0, refused.value.code

    assert _go(world, go) == (2, 1, "query_op_not_allowed")


# -- sum, count, min and max ------------------------------------------------------


def test_r8_a_new_parent_starts_at_zero_and_the_empty_min_and_max_clear(world) -> None:
    async def go() -> dict[str, Any]:
        acc, _ent = await _objects(world.a, world.pkg_a)
        return _data(world, (await _acct(world.a, acc)).id)

    got = _go(world, go)
    assert got == {"name": "acme", "balance": 0, "approved_days": 0, "entries": 0,
                   "spent": _inr(0)}


def test_r8_sum_and_count_follow_a_create_an_update_and_a_delete(world) -> None:
    async def go() -> list[dict[str, Any]]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, APPROVED, COUNT, TWICE])
        p = await _acct(world.a, acc)
        seen = []
        e1 = await _give(world.a, ent, p, days=3, kind="approved")
        e2 = await _give(world.a, ent, p, days=4, kind="pending")
        e3 = await _give(world.a, ent, p, days=5, kind="approved")
        seen.append(_data(world, p.id))
        await _put(world.a, ent, Write("update", ent.id, e2.id, {"days": 10, "kind": "approved"}))
        seen.append(_data(world, p.id))
        await _put(world.a, ent, Write("delete", ent.id, e1.id))
        seen.append(_data(world, p.id))
        await _put(world.a, ent, Write("delete", ent.id, e2.id), Write("delete", ent.id, e3.id))
        seen.append(_data(world, p.id))
        return seen

    created, updated, deleted, empty = _go(world, go)
    assert (created["balance"], created["approved_days"], created["entries"]) == (12, 8, 3)
    assert (updated["balance"], updated["approved_days"], updated["entries"]) == (18, 18, 3)
    assert (deleted["balance"], deleted["approved_days"], deleted["entries"]) == (15, 15, 2)
    assert (empty["balance"], empty["approved_days"], empty["entries"]) == (0, 0, 0)
    # The formula of the parent runs again on each derived write.
    assert [created["twice"], updated["twice"], deleted["twice"], empty["twice"]] == [24, 36, 30, 0]


def test_r8_min_and_max_read_dates_and_money_and_clear_with_no_child(world) -> None:
    async def go() -> list[dict[str, Any]]:
        acc, ent = await _objects(world.a, world.pkg_a, [FIRST, LAST, SPENT, TOP])
        p = await _acct(world.a, acc)
        e1 = await _give(world.a, ent, p, day="2026-03-05", amount=_inr(500))
        e2 = await _give(world.a, ent, p, day="2026-01-10", amount=_inr(900))
        e3 = await _give(world.a, ent, p, day="2026-02-01", amount=_inr(100))
        seen = [_data(world, p.id)]
        await _put(world.a, ent, Write("delete", ent.id, e2.id))
        seen.append(_data(world, p.id))
        await _put(world.a, ent, Write("delete", ent.id, e1.id), Write("delete", ent.id, e3.id))
        seen.append(_data(world, p.id))
        return seen

    full, fewer, none = _go(world, go)
    assert (full["first_day"], full["last_day"]) == ("2026-01-10", "2026-03-05")
    assert (full["spent"], full["top"]) == (_inr(1500), _inr(900))
    assert (fewer["first_day"], fewer["last_day"]) == ("2026-02-01", "2026-03-05")
    assert (fewer["spent"], fewer["top"]) == (_inr(600), _inr(500))
    assert "first_day" not in none and "last_day" not in none and "top" not in none
    assert none["spent"] == _inr(0)


def test_r8_where_keeps_only_the_children_that_meet_it(world) -> None:
    only_big = _roll("big", "approved_days", "sum", "days", where=[
        {"field": "kind", "op": "eq", "value": "approved"},
        {"field": "days", "op": "gte", "value": 5}])

    async def go() -> list[Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [only_big])
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p, days=3, kind="approved")
        e = await _give(world.a, ent, p, days=7, kind="approved")
        await _give(world.a, ent, p, days=9, kind="pending")
        first = _data(world, p.id)["approved_days"]
        await _put(world.a, ent, Write("update", ent.id, e.id, {"kind": "pending"}))
        return [first, _data(world, p.id)["approved_days"]]

    assert _go(world, go) == [7, 0]


def test_r8_a_move_leaves_the_old_and_the_new_parent_exact(world) -> None:
    async def go() -> list[tuple[Any, Any]]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, COUNT])
        p1, p2 = await _acct(world.a, acc), await _acct(world.a, acc)
        e1 = await _give(world.a, ent, p1, days=3)
        await _give(world.a, ent, p1, days=4)
        await _give(world.a, ent, p2, days=10)
        out = []
        await _put(world.a, ent, Write("update", ent.id, e1.id, {"account": {"id": p2.id}}))
        out.append((_data(world, p1.id), _data(world, p2.id)))
        return out

    ((one, two),) = _go(world, go)
    assert (one["balance"], one["entries"]) == (4, 1)
    assert (two["balance"], two["entries"]) == (13, 2)


def test_r8_an_update_that_changes_nothing_a_rule_reads_recomputes_no_parent(world) -> None:
    """The skip rule: the note-like field changes, and the roll-up stays as it is."""
    async def go() -> int:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        e = await _give(world.a, ent, p, days=3, kind="approved")
        async with tenant_session(world.a) as db:
            seen: list[str] = []
            eng = get_engine().sync_engine

            def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
                seen.append(statement)

            event.listen(eng, "before_cursor_execute", _on)
            try:
                await store.apply_writes(db, ANA, package_id=ent.package_id, writes=[
                    Write("update", ent.id, e.id, {"kind": "pending"})])
            finally:
                event.remove(eng, "before_cursor_execute", _on)
        return sum("sum(s.v_num)" in s for s in seen)

    assert _go(world, go) == 0


def test_r8_a_derived_write_rebuilds_the_hot_rows_and_names_the_actor(world) -> None:
    async def go() -> tuple[str, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [COUNT])
        p = await _acct(world.a, acc)
        before = _row(world, p.id)
        await _put(world.a, ent, _entry(ent, p, days=1), actor=BOB)
        await _put(world.a, ent, _entry(ent, p, days=2), actor=BOB)
        async with tenant_session(world.a) as db:
            page = await store.query_records(
                db, package_id=acc.package_id, object_id=acc.id,
                filter=[{"field": "entries", "op": "gte", "value": 2}])
        assert [r.id for r in page.records] == [p.id]
        after = _row(world, p.id)
        assert after[0] == BOB and before[0] == ANA and after[1] == before[1] and after[2] > before[2]
        return p.id, _hot(world, p.id, "entries")

    rid, hot = _go(world, go)
    assert hot == 2 and _data(world, rid)["entries"] == 2


def test_r8_a_delete_with_no_actor_keeps_the_last_actor_of_the_parent(world) -> None:
    async def go() -> tuple[str, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        async with tenant_session(world.a) as db:
            p = await store.create_record(db, BOB, acc.id, {"name": "x"}, package_id=acc.package_id)
            assert p is not None
        e = (await _put(world.a, ent, _entry(ent, p, days=4), actor=BOB))[0]
        async with tenant_session(world.a) as db:
            assert await store.delete_record(db, ent.id, e.id, package_id=ent.package_id)
        return p.id, _row(world, p.id)[0]

    rid, who = _go(world, go)
    assert _data(world, rid)["balance"] == 0 and who == BOB


def test_r8_a_caller_cannot_write_a_rollup_target(world) -> None:
    async def go() -> list[Any]:
        acc, _ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        out = []
        for write in (Write("create", acc.id, data={"name": "y", "balance": 5}),
                      Write("create", acc.id, data={"name": "y", "balance": 0}),
                      Write("update", acc.id, p.id, {"balance": 0}),
                      Write("update", acc.id, p.id, {"balance": None})):
            err = await _refused(world.a, acc, write)
            out.append((type(err.cause), getattr(err.cause, "code", None)))
        return out

    assert set(_go(world, go)) == {(FormulaError, sr.COMPUTED_FIELD)}


# -- one aggregate, the deadline and the caps ---------------------------------------


def test_r8_fifty_children_of_one_parent_run_one_aggregate(world) -> None:
    async def go() -> tuple[int, int]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        seen: list[str] = []
        eng = get_engine().sync_engine

        def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
            seen.append(statement)

        async with tenant_session(world.a) as db:
            event.listen(eng, "before_cursor_execute", _on)
            try:
                await store.apply_writes(db, ANA, package_id=ent.package_id, writes=[
                    _entry(ent, p, days=1) for _ in range(50)])
            finally:
                event.remove(eng, "before_cursor_execute", _on)
        return sum("sum(s.v_num)" in s for s in seen), int(_data(world, p.id)["balance"])

    assert _go(world, go) == (1, 50)


def test_r8_more_parents_than_the_cap_refuse_the_batch_and_write_nothing(world, monkeypatch) -> None:
    monkeypatch.setattr(store, "MAX_ROLLUP_PARENTS", 2)

    async def go() -> tuple[BatchError, int]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        parents = [await _acct(world.a, acc) for _ in range(3)]
        err = await _refused(world.a, ent, *[_entry(ent, p, days=1) for p in parents])
        return err, _rows(world, ent)

    err, rows = _go(world, go)
    assert isinstance(err.cause, BudgetExceeded) and rows == 0


def test_r8_more_aggregates_than_the_cap_refuse_the_batch_and_write_nothing(world) -> None:
    """50 moves touch 100 parents, and two roll-ups on each make 200 aggregates."""
    async def go() -> tuple[BatchError, int, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, APPROVED])
        parents = []
        for _ in range(2):
            parents += await _put(world.a, acc, *[
                Write("create", acc.id, data={"name": "p"}) for _ in range(50)])
        kids = await _put(world.a, ent, *[_entry(ent, parents[i], days=1) for i in range(50)])
        moves = [Write("update", ent.id, kids[i].id, {"account": {"id": parents[50 + i].id}})
                 for i in range(50)]
        err = await _refused(world.a, ent, *moves)
        return err, _rows(world, ent), _data(world, parents[0].id)["balance"]

    err, rows, still = _go(world, go)
    assert isinstance(err.cause, BudgetExceeded) and "aggregates" in str(err.cause)
    assert rows == 50 and still == 1


def test_r8_a_timeout_in_the_recompute_refuses_the_batch(world, monkeypatch) -> None:
    """The clock stands still at the start and the deadline passes at once."""
    async def go() -> tuple[BatchError, int]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        ticks = iter([0.0, 100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0])
        monkeypatch.setattr(store, "_clock", lambda: next(ticks, 900.0))
        err = await _refused(world.a, ent, _entry(ent, p, days=1))
        return err, _rows(world, ent)

    err, rows = _go(world, go)
    assert isinstance(err.cause, store.QueryError) and err.cause.code == "query_timeout"
    assert rows == 0


# -- the parent rules and the SAVEPOINT --------------------------------------------


def test_r8_a_parent_range_that_breaks_rolls_back_the_child_write(world) -> None:
    async def go() -> tuple[BatchError, int, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, BALANCE_OK])
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p, days=10)
        await _give(world.a, ent, p, days=-7)
        err = await _refused(world.a, ent, _entry(ent, p, days=-7))
        return err, _rows(world, ent), _data(world, p.id)["balance"]

    err, rows, balance = _go(world, go)
    assert isinstance(err.cause, ConstraintViolation) and err.cause.rule == "balance_ok"
    assert rows == 2 and balance == 3


def test_r8_a_state_machine_of_the_parent_is_not_moved_by_a_rollup(world) -> None:
    fields = [*_ACCOUNT, {"key": "stage", "type": "select", "options": ["open", "closed"]}]
    machine = {"name": "flow", "kind": "state_machine", "field": "stage", "initial": "open",
               "moves": [{"from": "open", "to": "closed"}]}

    async def go() -> tuple[Any, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, machine], account_fields=fields)
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p, days=2)
        return _data(world, p.id)["stage"], _data(world, p.id)["balance"]

    assert _go(world, go) == ("open", 2)


def test_r8_a_currency_mismatch_is_refused_and_the_batch_writes_nothing(world) -> None:
    async def go() -> tuple[BatchError, int, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [SPENT])
        p = await _acct(world.a, acc)
        err = await _refused(world.a, ent, _entry(ent, p, amount=_inr(100)),
                             _entry(ent, p, amount={"minor": 100, "currency": "USD"}))
        return err, _rows(world, ent), _data(world, p.id)["spent"]

    err, rows, spent = _go(world, go)
    assert isinstance(err.cause, RollupError) and err.cause.code == "currency_mismatch"
    assert err.index == 1 and rows == 0 and spent == _inr(0)
    assert "USD" not in str(err)


def test_r8_a_refused_recompute_rolls_back_its_savepoint_and_keeps_the_callers_work(world) -> None:
    """The recompute runs inside the SAVEPOINT of the batch. A caller that keeps
    its session after the refusal loses the writes of that batch and nothing else."""
    async def go() -> tuple[int, Any, str]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, SPENT])
        p = await _acct(world.a, acc)
        async with tenant_session(world.a) as db:
            await store.apply_writes(db, ANA, package_id=ent.package_id, writes=[
                _entry(ent, p, days=2, amount=_inr(10))])
            with pytest.raises(BatchError) as refused:
                await store.apply_writes(db, ANA, package_id=ent.package_id, writes=[
                    _entry(ent, p, days=5, amount=_inr(10)),
                    _entry(ent, p, days=7, amount={"minor": 1, "currency": "USD"})])
            await store.apply_writes(db, ANA, package_id=ent.package_id, writes=[
                _entry(ent, p, days=1, amount=_inr(10))])
        return _rows(world, ent), _data(world, p.id), refused.value.cause.code

    rows, parent, code = _go(world, go)
    assert code == "currency_mismatch" and rows == 2
    assert parent["balance"] == 3 and parent["spent"] == _inr(20)


def test_r8_a_dangling_parent_is_skipped(world) -> None:
    async def go() -> tuple[int, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        await _put(world.a, ent,
                   _entry(ent, str(uuid.uuid4()), days=5),
                   _entry(ent, "not-a-uuid", days=5),
                   Write("create", ent.id, data={"days": 5}))
        return _rows(world, ent), _data(world, p.id)["balance"]

    assert _go(world, go) == (3, 0)


def test_r8_a_parent_id_in_capitals_still_joins_its_children(world) -> None:
    async def go() -> Any:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p.id.upper(), days=5)
        await _give(world.a, ent, p, days=1)
        return _data(world, p.id)["balance"]

    assert _go(world, go) == 6


def test_r8_a_deleted_parent_leaves_its_children_and_a_write_still_passes(world) -> None:
    async def go() -> int:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p, days=5)
        await _put(world.a, acc, Write("delete", acc.id, p.id))
        await _give(world.a, ent, p, days=2)
        return _rows(world, ent)

    assert _go(world, go) == 2


def test_r8_a_parent_that_the_batch_writes_too_is_locked_once_and_computed_last(world) -> None:
    async def go() -> dict[str, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, TWICE])
        p = await _acct(world.a, acc)
        await _put(world.a, ent, _entry(ent, p, days=4),
                   Write("update", acc.id, p.id, {"name": "renamed"}),
                   _entry(ent, p, days=1))
        return _data(world, p.id)

    got = _go(world, go)
    assert (got["name"], got["balance"], got["twice"]) == ("renamed", 5, 10)


# -- two real connections -----------------------------------------------------------


async def _two_writers(org: str, first: Any, second: Any, hold: float = 0.12) -> list[Any]:
    """Two real connections. The first writes, then holds its transaction open
    for *hold*. The second starts then. With no lock, the second reads the old sum
    and passes its check."""
    wrote = asyncio.Event()

    async def one() -> Any:
        try:
            async with tenant_session(org) as db:
                got = await first(db)
                wrote.set()
                await asyncio.sleep(hold)
            return got
        except Exception as exc:  # the refusal is the result under test
            wrote.set()
            return exc

    async def two() -> Any:
        await wrote.wait()
        try:
            async with tenant_session(org) as db:
                return await second(db)
        except Exception as exc:  # the refusal is the result under test
            return exc

    return list(await asyncio.gather(one(), two()))


@pytest.mark.parametrize("round_no", range(10))
def test_r8_two_writers_that_each_take_7_from_10_get_one_success_and_a_sum_of_3(
        world, round_no: int) -> None:
    async def go() -> tuple[Any, list[Any], store.StudioObject]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL, BALANCE_OK])
        p = await _acct(world.a, acc)
        await _give(world.a, ent, p, days=10)

        async def take(db: Any) -> Any:
            return await store.apply_writes(db, ANA, package_id=ent.package_id,
                                            writes=[_entry(ent, p, days=-7)])

        return p, await _two_writers(world.a, take, take), ent

    p, results, ent = _go(world, go)
    wins = [r for r in results if isinstance(r, list)]
    lost = [r for r in results if isinstance(r, BatchError)]
    assert len(wins) == 1 and len(lost) == 1, results
    assert isinstance(lost[0].cause, ConstraintViolation)
    assert _data(world, p.id)["balance"] == 3 and _rows(world, ent) == 2


@pytest.mark.parametrize("round_no", range(20))
def test_r8_two_batches_that_name_the_objects_in_opposite_order_never_deadlock(
        world, round_no: int) -> None:
    async def go() -> tuple[list[Any], Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        child = Write("create", ent.id, data={"account": {"id": p.id}, "days": 1})
        rename = Write("update", acc.id, p.id, {"name": "n"})

        async def batch(writes: list[Write]) -> Any:
            async with tenant_session(world.a) as db:
                return await store.apply_writes(db, ANA, package_id=acc.package_id, writes=writes)

        results = list(await asyncio.wait_for(asyncio.gather(
            batch([child, rename]), batch([rename, child]), return_exceptions=True), timeout=10))
        return results, _data(world, p.id)["balance"]

    results, balance = _go(world, go)
    assert all(isinstance(r, list) for r in results), results
    assert balance == 2


# -- the plan -------------------------------------------------------------------------

BIG = 50_000
_BIG_RECORDS_SQL = (
    "INSERT INTO studio_records (object_id, id, data, created_by, updated_by) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), "
    "jsonb_build_object('account', jsonb_build_object('id', CAST(:pid AS text)), 'days', 1), "
    ":a, :a FROM generate_series(1, :big) g"
)
_BIG_HOT_SQL = (
    "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_text, v_num) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), f.k, "
    "CASE f.k WHEN 'account' THEN CAST(:pid AS text) END, CASE f.k WHEN 'days' THEN 1 END "
    "FROM generate_series(1, :big) g CROSS JOIN (VALUES ('account'), ('days')) f(k)"
)


@pytest.fixture(scope="module")
def big(promoted):  # noqa: F811
    """One parent with 50,000 children, in a package of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs = _packages(p, run, (("big", p.org_b),))

    async def seed() -> SimpleNamespace:
        acc, ent = await _objects(p.org_b, pkgs["big"], [BAL])
        parent = await _acct(p.org_b, acc)
        async with tenant_session(p.org_b) as db:
            binds = {"oid": ent.id, "pid": parent.id, "a": ANA, "big": BIG}
            await db.execute(text(_BIG_RECORDS_SQL), binds)
            await db.execute(text(_BIG_HOT_SQL), binds)
        return SimpleNamespace(acc=acc, ent=ent, parent=parent)

    async def go() -> SimpleNamespace:
        async with tenant_engine_scope(_dsn(p.app_url)):
            return await seed()

    ns = asyncio.run(go())
    with p.admin_engine.begin() as c:
        for table in ("studio_objects", "studio_records", "studio_record_hot"):
            c.execute(text(f"ANALYZE {table}"))
    ns.p, ns.org = p, p.org_b
    yield ns
    _drop_packages(p, run, list(pkgs.values()))


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


def test_r8_a_sum_over_50000_children_shows_no_seq_scan_and_is_exact(big) -> None:
    fields = [sf.parse_field_spec(f) for f in _entry_fields()]
    rule = sr.parse_rule(BAL)
    sql, params = store.build_rollup(
        rule, fields, object_id=big.ent.id, package_id=big.ent.package_id,
        parent_id=big.parent.id)

    async def go() -> tuple[Any, Any]:
        async with tenant_session(big.org) as db:
            plan = (await db.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params)).scalar_one()
            total = (await db.execute(text(sql), params)).scalar_one()
        return plan, total

    async def run() -> tuple[Any, Any]:
        async with tenant_engine_scope(_dsn(big.p.app_url)):
            return await go()

    raw, total = asyncio.run(run())
    nodes = _nodes((json.loads(raw) if isinstance(raw, str) else raw)[0]["Plan"])
    seq = [n["Relation Name"] for n in nodes if n["Node Type"] == "Seq Scan"
           and n.get("Relation Name") in ("studio_records", "studio_record_hot")]
    assert seq == [], seq
    assert not any(n.get("Relation Name") == "studio_records" for n in nodes), nodes
    assert total == BIG


# -- the package, the org and the declaration ------------------------------------------


def test_r8_a_source_in_another_package_is_refused(world) -> None:
    async def go() -> str:
        async with tenant_session(world.a) as db:
            acc = await store.define_object(db, ANA, package_id=world.pkg_a, name="account",
                                            fields=_ACCOUNT)
            # `entry` exists in the OTHER package only, with the same names.
            await store.define_object(db, ANA, package_id=world.pkg_a2, name="entry",
                                      fields=_entry_fields())
            assert acc is not None
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, acc.id, [BAL], package_id=world.pkg_a)
        return str(refused.value)

    assert "another object of this package" in _go(world, go)


def test_r8_a_write_in_another_package_touches_no_parent_of_this_one(world) -> None:
    async def go() -> tuple[Any, Any, int]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        acc2, ent2 = await _objects(world.a, world.pkg_a2, [])
        p, p2 = await _acct(world.a, acc), await _acct(world.a, acc2)
        # An entry of package 2 names the parent of package 1, and the reverse.
        await _give(world.a, ent2, p, days=9)
        await _give(world.a, ent, p2, days=9)
        await _give(world.a, ent, p, days=1)
        return _data(world, p.id)["balance"], _data(world, p2.id).get("balance"), _rows(world, ent2)

    assert _go(world, go) == (1, None, 1)


def test_r8_org_b_reads_no_sum_of_org_a_and_writes_nothing_of_it(world) -> None:
    async def go() -> tuple[Any, Any, Any]:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        bacc, bent = await _objects(world.b, world.pkg_b, [BAL])
        a, b = await _acct(world.a, acc), await _acct(world.b, bacc)
        await _give(world.a, ent, a, days=7)
        # An entry of org B names the parent of org A. RLS hides it: it is dangling.
        await _give(world.b, bent, a.id, days=100)
        await _give(world.b, bent, b, days=3)
        return _data(world, a.id)["balance"], _data(world, b.id)["balance"], _rows(world, ent)

    assert _go(world, go) == (7, 3, 1)


@pytest.mark.parametrize("key", ["account", "days", "kind"])
def test_r8_a_field_that_a_rollup_reads_cannot_turn_cold(world, key: str) -> None:
    async def go() -> tuple[str, bool]:
        acc, ent = await _objects(world.a, world.pkg_a, [APPROVED])
        async with tenant_session(world.a) as db:
            with pytest.raises(store.ObjectStoreError) as refused:
                await store.set_field_hot(db, ent.id, key, False)
        # A field no rule reads turns cold, and so does this one once the rule is gone.
        async with tenant_session(world.a) as db:
            assert await store.set_field_hot(db, ent.id, "amount", False)
            await store.set_constraints(db, acc.id, [], package_id=acc.package_id)
            ok = await store.set_field_hot(db, ent.id, key, False)
        return str(refused.value), ok

    message, ok = _go(world, go)
    assert "must stay hot" in message and "`approved`" in message and ok is True


def test_r8_a_rollup_queues_no_key_check(world) -> None:
    """A write of a parent runs no `unique` or `group_sum_min` statement for a roll-up."""
    async def go() -> int:
        acc, _ent = await _objects(world.a, world.pkg_a, [BAL])
        seen: list[str] = []
        eng = get_engine().sync_engine

        def _on(conn: Any, cursor: Any, statement: str, *a: Any) -> None:
            seen.append(statement)

        async with tenant_session(world.a) as db:
            event.listen(eng, "before_cursor_execute", _on)
            try:
                await store.apply_writes(db, ANA, package_id=acc.package_id, writes=[
                    Write("create", acc.id, data={"name": "x"})])
            finally:
                event.remove(eng, "before_cursor_execute", _on)
        return sum("count(*)" in s or "sum(" in s for s in seen)

    assert _go(world, go) == 0


def test_r8_a_target_cannot_be_a_key_of_another_rule(world) -> None:
    keyed = [
        [BAL, {"name": "u", "kind": "unique", "fields": ["balance"]}],
        [BAL, {"name": "g", "kind": "group_sum_min", "field": "twice", "group_by": ["balance"],
               "min": 0}],
        [BAL, {"name": "g2", "kind": "group_sum_min", "field": "balance", "group_by": ["name"],
               "min": 0}],
        [BAL, {"name": "m", "kind": "state_machine", "field": "balance", "initial": "a",
               "moves": [{"from": "a", "to": "b"}]}],
    ]

    async def go() -> list[str]:
        acc, _ent = await _objects(world.a, world.pkg_a, [])
        out = []
        for rules in keyed:
            async with tenant_session(world.a) as db:
                with pytest.raises(ConstraintError) as refused:
                    await store.set_constraints(db, acc.id, rules, package_id=acc.package_id)
            out.append(str(refused.value))
        return out

    for message in _go(world, go):
        assert "is a key of rule" in message and "fills no key" in message


def test_r8_a_rollup_of_a_rollup_is_refused_in_both_orders(world) -> None:
    """`region` sums `account.balance`, and `account.balance` is a roll-up."""
    region = [{"key": "name", "type": "text"}, {"key": "total", "type": "number"}]
    account = [{"key": "name", "type": "text"}, {"key": "balance", "type": "number", "hot": True},
               {"key": "region", "type": "relation", "to": "region", "hot": True}]
    over = {"name": "over", "kind": "rollup", "target": "total", "source": "account",
            "via": "region", "agg": "sum", "field": "balance"}
    below = {"name": "bal", "kind": "rollup", "target": "balance", "source": "entry",
             "via": "account", "agg": "sum", "field": "days"}

    async def go() -> list[str]:
        out = []
        for order in ("region first", "account first"):
            pkg = world.pkg_a if order == "region first" else world.pkg_a2
            async with tenant_session(world.a) as db:
                reg = await store.define_object(db, ANA, package_id=pkg, name="region", fields=region)
                acc = await store.define_object(db, ANA, package_id=pkg, name="account", fields=account)
                await store.define_object(db, ANA, package_id=pkg, name="entry",
                                          fields=_entry_fields())
                assert reg is not None and acc is not None
                first, second = ((reg, over), (acc, below)) if order == "region first" \
                    else ((acc, below), (reg, over))
                await store.set_constraints(db, first[0].id, [first[1]], package_id=pkg)
                with pytest.raises(ConstraintError) as refused:
                    await store.set_constraints(db, second[0].id, [second[1]], package_id=pkg)
            out.append(str(refused.value))
        return out

    assert all("roll-up" in m and "cannot" in m for m in _go(world, go))


def test_r8_two_hops_and_a_many_relation_and_a_wrong_target_are_refused(world) -> None:
    line = [{"key": "entry", "type": "relation", "to": "entry", "hot": True},
            {"key": "days", "type": "number", "hot": True}]
    many = [{"key": "account", "type": "relation", "to": "account", "many": True},
            {"key": "days", "type": "number", "hot": True}]

    async def go() -> list[str]:
        acc, _ent = await _objects(world.a, world.pkg_a, [])
        async with tenant_session(world.a) as db:
            await store.define_object(db, ANA, package_id=world.pkg_a, name="line", fields=line)
            await store.define_object(db, ANA, package_id=world.pkg_a, name="tag", fields=many)
        out = []
        for rule in (
            {**BAL, "name": "hop", "source": "line", "via": "entry"},
            {**BAL, "name": "many", "source": "tag"},
            {**BAL, "name": "dotted", "source": "crm.entry"},
            {**BAL, "name": "self", "source": "account"},
            {**BAL, "name": "badagg", "agg": "avg"},
            {**COUNT, "name": "cf", "field": "days"},
            {**BAL, "name": "nofield", "field": None},
            {**BAL, "name": "text", "target": "name"},
            {**SPENT, "name": "nocur", "currency": None},
            {**BAL, "name": "cold", "field": "name"},
        ):
            spec = {k: v for k, v in rule.items() if v is not None}
            with pytest.raises((ConstraintError,)) as refused:
                async with tenant_session(world.a) as db:
                    await store.set_constraints(db, acc.id, [spec], package_id=world.pkg_a)
            out.append(str(refused.value))
        return out

    assert len(_go(world, go)) == 10


def test_r8_a_declaration_on_an_object_with_records_is_refused(world) -> None:
    async def go() -> tuple[str, str]:
        acc, _ent = await _objects(world.a, world.pkg_a, [])
        p = await _acct(world.a, acc)
        async with tenant_session(world.a) as db:
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, acc.id, [BAL], package_id=acc.package_id)
        return str(refused.value), p.id

    message, rid = _go(world, go)
    assert "declare before the first record" in message and "`bal`" in message
    assert "balance" not in _data(world, rid)


def test_r8_an_unchanged_rollup_passes_on_an_object_with_records_and_a_change_does_not(world) -> None:
    async def go() -> tuple[str, bool]:
        acc, _ent = await _objects(world.a, world.pkg_a, [BAL])
        await _acct(world.a, acc)
        async with tenant_session(world.a) as db:
            renamed = await store.set_constraints(
                db, acc.id, [{**BAL, "name": "bal2", "message": "m"}], package_id=acc.package_id)
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(
                    db, acc.id, [{**BAL, "agg": "max"}], package_id=acc.package_id)
        return str(refused.value), renamed is not None

    message, ok = _go(world, go)
    assert ok and "declare before the first record" in message


# -- fail closed ------------------------------------------------------------------------


def test_r8_a_stored_rollup_that_no_longer_fits_refuses_the_write(world) -> None:
    async def go() -> BatchError:
        acc, ent = await _objects(world.a, world.pkg_a, [BAL])
        p = await _acct(world.a, acc)
        with world.p.admin_engine.begin() as c:  # raw SQL, as a tamperer would
            c.execute(text("DELETE FROM studio_record_hot WHERE object_id = CAST(:o AS uuid) "
                           "AND field_key = 'days'"), {"o": ent.id})
            c.execute(text("UPDATE studio_object_fields SET hot = false "
                           "WHERE object_id = CAST(:o AS uuid) AND key = 'days'"), {"o": ent.id})
        return await _refused(world.a, ent, _entry(ent, p, days=1))

    err = _go(world, go)
    assert isinstance(err.cause, RollupError) and err.cause.code == "declaration"
    assert "hot" in str(err.cause)


def test_r8_a_stored_rollup_that_the_store_cannot_read_refuses_each_write_of_the_package(world) -> None:
    async def go() -> BatchError:
        acc, ent = await _objects(world.a, world.pkg_a, [])
        p = await _acct(world.a, acc)
        forged = json.dumps([{"name": "x", "kind": "rollup", "target": "balance"}])
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_objects SET constraints = CAST(:r AS jsonb) "
                           "WHERE id = CAST(:o AS uuid)"), {"r": forged, "o": acc.id})
        return await _refused(world.a, ent, _entry(ent, p, days=1))

    err = _go(world, go)
    assert isinstance(err.cause, RollupError) and "cannot read" in str(err.cause)


def test_r8_a_formula_of_the_source_that_reads_a_rollup_target_is_a_rollup_of_a_rollup(world) -> None:
    """Closed at declaration, through a formula, in the direction of the source."""
    region = [{"key": "name", "type": "text"}, {"key": "total", "type": "number"}]
    account = [{"key": "name", "type": "text"}, {"key": "balance", "type": "number"},
               {"key": "tax", "type": "number", "hot": True},
               {"key": "region", "type": "relation", "to": "region", "hot": True}]
    tax = {"name": "tax_of", "kind": "formula", "target": "tax", "rounding": "half_up",
           "expr": {"fn": "mul", "args": [{"ref": "balance"}, {"num": "2"}]}}
    over = {"name": "over", "kind": "rollup", "target": "total", "source": "account",
            "via": "region", "agg": "sum", "field": "tax"}

    async def go() -> str:
        async with tenant_session(world.a) as db:
            reg = await store.define_object(db, ANA, package_id=world.pkg_a, name="region", fields=region)
            acc = await store.define_object(db, ANA, package_id=world.pkg_a, name="account", fields=account)
            await store.define_object(db, ANA, package_id=world.pkg_a, name="entry",
                                      fields=_entry_fields())
            assert reg is not None and acc is not None
            await store.set_constraints(db, reg.id, [over], package_id=world.pkg_a)
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, acc.id, [BAL, tax], package_id=world.pkg_a)
        return str(refused.value)

    assert "roll-up" in _go(world, go)
