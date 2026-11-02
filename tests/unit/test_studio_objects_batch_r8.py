"""WS-STU C1b — multi-record writes and constraints on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §3.1 and the §14 C1b row. Code:
``gateway/studio/objects_store.apply_writes`` and ``set_constraints``, and
``acb_common/studio_rules.py``. ``test_studio_rules.py`` holds the hermetic
half. Migration: the file that adds ``studio_objects.constraints`` (327 at
build).

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two writers are two real
connections. Two organizations.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_objects_batch_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip
is not a pass.

**Acceptance (C1b).**

a. A stock move of 7 from a stock of 10 writes exactly 2 rows, in either order.
b. A second write that fails with a database error leaves 0 rows of the batch.
   An earlier row of the caller stays, and the session stays usable.
c. Stock 10, move -11: refused, with the rule name in the message, 0 rows
   written. Move -10 passes.
d. Two connections, stock 10, each takes 7, 10 rounds: exactly 1 success, 1
   refusal and a final sum of 3. The same for ``unique``: exactly 1 row.
e. Two writers with their records in opposite order, 20 rounds: no 40P01.
f. An update that moves a row to another group checks both groups. A delete
   of a positive row that breaks the balance is refused.
g. 50 writes pass, and 51 run no statement. A 17th rule is refused.
h. Org B reads no rule and no sum of org A, also with the same field keys.
   A package reads no rule and no sum of another package.
i. On 50,000 ledger rows the plan of a sum shows no Seq Scan and a bounded
   read. A timeout in a check refuses the batch and leaves the session usable.
j. A waiter for the org lock stops at 1 s with ``store_busy``. The session stays
   usable, and ``lock_timeout`` comes back. On the real pool, one holder and a
   full pool of waiters of org A leave a read of org B a connection. A deadlock
   at the lock gives ``store_busy`` too (fix rounds 1 and 2).
k. On a floor of 0 or less, a write that does not lower a group reads no sum.
   So ``-3`` on a broken key is refused and ``+3`` passes, also on a group of
   200,000 rows. A floor above 0 holds from the first row (fix rounds 1 and 2).
l. The last day of the calendar is a key (fix round 1).
m. A key with no rows passes at any ``min``. A key with rows and no amount
   sums to 0, and the floor judges it (fix rounds 1 and 2).
n. A stored rule that the checks cannot read refuses each write (fix round 1).

**The mutations (R7).** Commit before each, and restore with git after.

* B1. ``objects_store._run_steps``: drop ``await _lock_org(db)``.
  ``test_r8_two_writers_at_once_get_one_stock_move`` and
  ``test_r8_two_writers_at_once_get_one_unique_row`` go red.
* B2a. ``objects_store._run_steps``: run the checks before the write loop.
  ``test_r8_a_move_past_the_stock_is_refused_with_the_rule_name`` and
  ``test_the_writes_run_before_the_checks`` go red.
* B2b. The same function: run the checks after each write.
  ``test_r8_a_move_of_one_record_is_judged_on_the_net_of_the_batch`` goes red.
* B3. ``studio_rules.touched_keys``: read ``(after,)`` for each kind.
  ``test_r8_an_update_that_moves_a_row_checks_both_groups`` and
  ``test_r8_a_delete_that_breaks_the_balance_is_refused`` go red.
* B4. ``objects_store._apply_batch``: remove ``async with db.begin_nested()``.
  ``test_r8_a_failed_second_write_leaves_nothing_and_the_session_usable`` and
  ``test_store_never_commits_or_rolls_back`` go red.
* B5. ``studio_rules.check_group_sum``: ``<`` becomes ``<=``.
  ``test_r8_a_move_past_the_stock_is_refused_with_the_rule_name`` and
  ``test_a_group_sum_at_the_floor_passes`` go red.
* B6. ``objects_store.delete_record``: run ``_DELETE_RECORD_SQL`` with no batch.
  ``test_r8_a_delete_that_breaks_the_balance_is_refused`` and
  ``test_every_record_write_path_runs_the_checks`` go red.
* B7. ``objects_store.create_record``: run ``_CREATE_RECORD_SQL`` with no batch.
  ``test_r8_each_record_function_runs_the_rules`` and
  ``test_every_record_write_path_runs_the_checks`` go red.
* B8. ``studio_rules.MAX_WRITES_PER_BATCH``: 51. ``test_batch_cap_is_50`` and
  ``test_r8_fifty_writes_pass_and_fifty_one_run_no_statement`` go red.
* B9. ``objects_store._lock_records``: drop ``sorted``.
  ``test_r8_the_locks_come_in_one_order`` goes red.
* B9b. ``objects_store._lock_objects``: drop ``sorted``. The same test goes red.
* B10. ``studio_rules.violation``: drop the name from the message.
  ``test_r8_a_move_past_the_stock_is_refused_with_the_rule_name`` goes red.
* B11. ``studio_rules.check_unique_count``: ``count > 1`` becomes ``count > 0``.
  ``test_r8_unique_refuses_a_second_record_and_not_the_record_itself`` and
  ``test_unique_counts_the_record_itself`` go red.
* B12. ``studio_rules._key_of``: drop the test of a blank value.
  ``test_r8_an_empty_value_never_clashes`` and
  ``test_an_empty_value_touches_no_key`` go red.
* B13a to B13e. Drop ``_BOUND_ORG`` from ``_SHARE_OBJECT_SQL``,
  ``_LOCK_PACKAGE_OBJECT_SQL``, ``_SET_RULES_SQL`` and ``_GET_RECORD_SQL``, and
  drop ``s.{_OF_ORG}`` from ``build_group_sum``. RLS hides each one from the
  tests here. ``test_each_statement_of_a_batch_names_the_org_and_the_package``
  and ``test_each_table_of_a_check_names_the_org_and_each_check_the_package``
  go red.
* B14. ``objects_store._apply_batch``: catch the error inside the block, so the
  SAVEPOINT releases. ``test_r8_a_broken_rule_keeps_the_earlier_work_of_the_caller``
  goes red.
* B15a. ``_SHARE_OBJECT_SQL``: drop the package filter.
  ``test_r8_a_batch_of_one_package_writes_no_object_of_another`` goes red.
* B15b. ``_LOCK_PACKAGE_OBJECT_SQL``: drop the package filter.
  ``test_r8_set_constraints_reads_one_package_and_one_org`` goes red.
* B15c, B15d. ``_SET_RULES_SQL`` and ``_LOCK_RECORD_SQL``: drop the package
  filter. An earlier statement holds the package, so only the source fence
  ``test_each_statement_of_a_batch_names_the_org_and_the_package`` goes red.
* B15e. ``objects_store._q_where``: drop ``_IN_PACKAGE``.
  ``test_each_table_of_a_check_names_the_org_and_each_check_the_package`` goes red.
* B16. ``objects_store._check_no_rule_reads``: read no rule.
  ``test_r8_a_field_that_a_rule_reads_stays_hot`` goes red.
* B17. ``objects_store._check_records``: drop ``check_rule_is_hot``.
  ``test_r8_a_rule_on_a_cold_field_refuses_the_write`` goes red.
* B18. ``studio_rules.check_group_sum``: compare with the float.
  ``test_a_float_floor_compares_by_its_digits`` goes red.
* B19. ``objects_store._Timed.execute``: set no timeout.
  ``test_r8_a_timeout_in_a_check_refuses_the_batch_and_keeps_the_session`` goes red.
* B20. ``objects_store.BatchError``: keep the text of a database error.
  ``test_a_database_error_keeps_its_text_out_of_the_batch_error`` goes red.
* B21. The migration: drop the CHECK.
  ``test_r8_an_old_object_reads_no_rule_and_the_column_holds_an_array`` and
  ``test_the_migration_is_expand_only_and_idempotent`` go red.
* B22. ``objects_store._one_write``: raise the ``BatchError`` as it is.
  ``test_r8_each_record_function_runs_the_rules`` goes red.
* B23. ``studio_rules.touched_keys``: read ``(before, after)`` for each kind.
  ``test_r8_old_duplicates_can_be_repaired_one_at_a_time`` and
  ``test_unique_is_touched_by_the_new_key_only`` go red.
* B24. ``objects_store.set_constraints``: lock the object row first.
  ``test_r8_set_constraints_takes_the_org_lock_then_the_object_row`` goes red.
* B25. ``objects_store._check_records``: drop ``check_kind_known``.
  ``test_r8_a_stored_kind_that_this_code_cannot_check_refuses_the_write`` goes red.
* B26. ``objects_store._check_records``: read ``mark.before`` for a range.
  ``test_r8_a_record_that_two_writes_name_keeps_the_last_value`` goes red.

Fix round 1:

* B27. ``objects_store._lock_org``: set no ``lock_timeout``.
  ``test_r8_a_waiter_past_the_bound_gets_store_busy``,
  ``test_r8_each_caller_of_the_org_lock_gets_the_bound`` and
  ``test_r8_a_full_pool_of_waiters_leaves_another_org_a_connection`` go red.
* B28. ``objects_store._lock_org``: do not put the old value back.
  ``test_r8_each_caller_of_the_org_lock_gets_the_bound`` and
  ``test_r8_the_bound_covers_the_wait_only`` go red.
* B29. ``objects_store._lock_org``: remove the SAVEPOINT.
  ``test_r8_each_caller_of_the_org_lock_gets_the_bound`` and
  ``test_store_never_commits_or_rolls_back`` go red.
* B30. ``objects_store._lock_org``: raise the database error.
  ``test_r8_a_waiter_past_the_bound_gets_store_busy`` goes red.
* B31. ``objects_store._check_records``: keep each key of a low floor.
  ``test_r8_a_write_that_does_not_lower_a_group_reads_no_sum``,
  ``test_r8_a_broken_key_takes_a_receipt_and_refuses_a_lower_write`` and
  ``test_r8_a_positive_write_on_200000_rows_reads_no_sum`` go red.
* B32. The same line: ``< 0`` becomes ``<= 0``.
  ``test_r8_a_write_that_does_not_lower_a_group_reads_no_sum`` goes red.
* B33. The same line: drop each key of a group.
  ``test_r8_two_writers_at_once_get_one_stock_move`` goes red.
* B34. ``studio_rules.add_change``: leave out the old value.
  ``test_r8_an_update_that_lowers_a_row_is_checked`` and
  ``test_the_net_change_of_one_record`` go red.
* B35. ``studio_rules._EXACT``: the default 28 digits.
  ``test_the_net_change_of_a_batch_is_exact`` goes red.
* B36. ``studio_rules.check_group_sum``: read None as 0.
  ``test_r8_the_last_row_of_a_group_can_leave_at_any_min`` and
  ``test_a_key_with_no_rows_passes_at_any_floor`` go red.
* B37. ``studio_rules.stored_rules``: skip an item that is no object.
  ``test_r8_a_stored_rule_that_the_checks_cannot_read_refuses_each_write`` and
  ``test_a_malformed_stored_rule_reads_and_fails_closed`` go red.
* B38. ``studio_rules._can_run``: answer True.
  The same two tests go red.
* B39. ``studio_query._day``: refuse the last day again.
  ``test_r8_the_last_day_of_the_calendar_is_a_key`` and
  ``test_the_last_day_of_the_calendar_is_a_key`` go red.

Fix round 2:

* B40. ``studio_rules.floor_holds_at_zero``: answer True for each group.
  ``test_r8_a_floor_above_zero_holds_from_the_first_row`` and
  ``test_only_a_floor_that_holds_at_zero_can_skip_the_read`` go red.
* B41. ``objects_store.build_group_sum``: ``sum(s.v_num)`` alone, so a key with
  rows and no amount reads NULL.
  ``test_r8_a_group_with_rows_and_no_amount_sums_to_zero`` and
  ``test_the_sum_is_null_only_for_a_key_with_no_rows`` go red.
* B42. ``objects_store.build_group_sum``: ``coalesce`` with no ``CASE``, so a key
  with no rows reads 0. ``test_r8_the_last_row_of_a_group_can_leave_at_any_min``
  goes red.
* B43. ``objects_store._BUSY_SQLSTATES``: drop 40P01.
  ``test_r8_a_deadlock_at_the_lock_gives_store_busy`` goes red.
* B44. ``objects_store.ORG_LOCK_WAIT_MS``: 3000.
  ``test_the_wait_for_the_org_lock_is_1_second_and_says_one_sentence`` and
  ``test_r8_a_waiter_past_the_bound_gets_store_busy`` go red.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common import studio_fields as sf
from acb_common import studio_query
from acb_common import studio_rules as sr
from acb_common.db import get_engine, tenant_session
from acb_common.studio_query import QueryError
from acb_common.studio_rules import ConstraintError, ConstraintViolation
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

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = next(
    p for p in (_ROOT / "infra" / "postgres").glob("*.sql")
    if "ADD COLUMN IF NOT EXISTS constraints JSONB" in p.read_text(encoding="utf-8"))

TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stuc1b.test"

_LEDGER: list[dict[str, Any]] = [
    {"key": "sku", "type": "text", "hot": True},
    {"key": "site", "type": "text", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
    {"key": "note", "type": "text"},
]
_STOCK: dict[str, Any] = {
    "name": "stock_not_negative", "kind": "group_sum_min", "field": "qty",
    "group_by": ["sku", "site"], "min": 0}
_ONE_SKU: dict[str, Any] = {"name": "one_sku", "kind": "unique", "fields": ["sku"]}
_POSITIVE: dict[str, Any] = {"name": "qty_ok", "kind": "range", "field": "qty", "min": 0, "max": 100}

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc1b', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc1b", "name": "stuc1b", "purpose": "The C1b R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


def _packages(p: Any, run: str, wanted: tuple[tuple[str, str], ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in wanted:
            out[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc1b-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())
    return out


def _drop_packages(p: Any, run: str, ids: list[str]) -> None:
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": ids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc1b-{TAG}-{run}-%"})


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
    """Run ``fn()`` in a new event loop, on the app role's engine."""
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(world.p.app_url)):
            return await fn()
    return asyncio.run(go())


async def _ledger(org: str, pkg: str, rules: list[dict[str, Any]] | None = None,
                  name: str = "ledger",
                  fields: list[dict[str, Any]] | None = None) -> store.StudioObject:
    """An object with its rules. The default rule is the stock floor."""
    async with tenant_session(org) as db:
        made = await store.define_object(db, ANA, package_id=pkg, name=name,
                                         fields=_LEDGER if fields is None else fields)
        assert made is not None
        done = await store.set_constraints(db, made.id, [_STOCK] if rules is None else rules,
                                           package_id=pkg)
    assert done is not None
    return done


def _move(obj: store.StudioObject, sku: str, site: str, qty: float) -> Write:
    return Write("create", obj.id, data={"sku": sku, "site": site, "qty": qty})


async def _put(org: str, obj: store.StudioObject, *writes: Write) -> list[Any]:
    async with tenant_session(org) as db:
        return await store.apply_writes(db, ANA, package_id=obj.package_id, writes=list(writes))


async def _refused(org: str, obj: store.StudioObject, *writes: Write) -> BatchError:
    """The refusal of *writes*. Fails when the batch passes."""
    with pytest.raises(BatchError) as caught:
        await _put(org, obj, *writes)
    return caught.value


def _rows(world: Any, obj: store.StudioObject) -> int:
    with world.p.admin_engine.connect() as c:
        return int(c.execute(text("SELECT count(*) FROM studio_records "
                                  "WHERE object_id = CAST(:o AS uuid)"), {"o": obj.id}).scalar_one())


def _sum(world: Any, obj: store.StudioObject, sku: str, site: str = "a") -> Any:
    with world.p.admin_engine.connect() as c:
        return c.execute(text(
            "SELECT coalesce(sum((data ->> 'qty')::numeric), 0) FROM studio_records "
            "WHERE object_id = CAST(:o AS uuid) AND data ->> 'sku' = :s AND data ->> 'site' = :t"),
            {"o": obj.id, "s": sku, "t": site}).scalar_one()


# ── The migration and the isolation ─────────────────────────────────────────


def test_the_migration_is_expand_only_and_idempotent() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "lock_timeout" in sql
    assert "JSONB NOT NULL DEFAULT '[]'" in sql
    assert "CHECK (jsonb_typeof(constraints) = 'array')" in sql
    assert "DROP " not in sql and "RENAME" not in sql and "CREATE TABLE" not in sql


def test_this_suite_is_named_in_the_ci_skip_guard() -> None:
    """Its own removal from CI must fail here. The list names the pytest
    ARGUMENT, so a mention in a comment does not count."""
    workflow = (_ROOT / ".github/workflows/pr-check.yml").read_text(encoding="utf-8")
    block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", workflow)
    assert block, "the --must-run argument list is missing"
    assert re.search(r"^[ \t]+tests/unit/test_studio_objects_batch_r8\.py(?: \\)?$",
                     block.group(1), re.M)


def test_isolation_is_read_committed(world) -> None:
    """Each check needs a new snapshot after the org lock. A session under
    REPEATABLE READ keeps its first snapshot, and a check there misses the
    write of the transaction that held the lock."""
    async def go() -> str:
        async with tenant_session(world.a) as db:
            return str((await db.execute(text("SHOW transaction_isolation"))).scalar_one())

    assert _go(world, go) == "read committed"


def test_r8_an_old_object_reads_no_rule_and_the_column_holds_an_array(world) -> None:
    async def go() -> tuple[Any, str]:
        async with tenant_session(world.a) as db:
            made = await store.define_object(db, ANA, package_id=world.pkg_a, name="plain",
                                             fields=_LEDGER)
            assert made is not None
        assert made.constraints == ()
        with pytest.raises(DBAPIError) as bad:
            async with tenant_session(world.a) as db:
                await db.execute(text("UPDATE studio_objects SET constraints = '{}' "
                                      "WHERE id = CAST(:o AS uuid)"), {"o": made.id})
        return made, str(bad.value)

    _, error = _go(world, go)
    assert "studio_objects_constraints_array" in error


# ── a, b, c. The batch and the floor ────────────────────────────────────────


@pytest.mark.parametrize("flip", [False, True], ids=["out first", "in first"])
def test_r8_a_stock_move_writes_two_rows_in_either_order(world, flip: bool) -> None:
    """Stock 10, move 7 from site a to site b. With the rows reversed, the
    first write alone is not the problem: only the net counts."""
    async def go() -> tuple[store.StudioObject, list[Any]]:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "s", "a", 10))
        out, into = _move(obj, "s", "a", -7), _move(obj, "s", "b", 7)
        return obj, await _put(world.a, obj, *((into, out) if flip else (out, into)))

    obj, answers = _go(world, go)
    assert len(answers) == 2 and all(isinstance(r, store.Record) for r in answers)
    assert _rows(world, obj) == 3
    assert _sum(world, obj, "s", "a") == 3 and _sum(world, obj, "s", "b") == 7


def test_r8_a_move_of_one_record_is_judged_on_the_net_of_the_batch(world) -> None:
    """A below-the-floor row and its repair in one batch pass together, in
    both orders. Each row alone would break the floor."""
    async def go() -> store.StudioObject:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "s", "a", -5), _move(obj, "s", "a", 5))
        await _put(world.a, obj, _move(obj, "s", "a", 2), _move(obj, "s", "a", -2))
        return obj

    obj = _go(world, go)
    assert _rows(world, obj) == 4 and _sum(world, obj, "s") == 0


def test_r8_a_failed_second_write_leaves_nothing_and_the_session_usable(world, monkeypatch) -> None:
    """A real database error in the second write: invalid JSON for jsonb. The
    SAVEPOINT rolls back the first write. The row that the caller wrote before
    the batch stays, and the session takes more work."""
    real = store.record_json

    async def go() -> tuple[store.StudioObject, BatchError, Any, Any]:
        obj = await _ledger(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            first = await store.create_record(db, ANA, obj.id, {"sku": "outer", "site": "a", "qty": 1},
                                              package_id=obj.package_id)
            seen: list[Any] = []

            def broken(data: Any) -> str:
                seen.append(data)
                return "{not json" if len(seen) == 2 else real(data)

            monkeypatch.setattr(store, "record_json", broken)
            with pytest.raises(BatchError) as failed:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    _move(obj, "s", "a", 1), _move(obj, "s", "a", 2)])
            monkeypatch.setattr(store, "record_json", real)
            kept = await store.get_record(db, obj.id, first.id, package_id=obj.package_id)
            after = await store.create_record(db, ANA, obj.id, {"sku": "after", "site": "a", "qty": 1},
                                              package_id=obj.package_id)
        return obj, failed.value, kept, after

    obj, failed, kept, after = _go(world, go)
    assert failed.index == 1 and isinstance(failed.cause, DBAPIError)
    assert "INSERT" not in str(failed), "the database text stays in the cause"
    assert kept is not None and after is not None
    assert _rows(world, obj) == 2, "the outer row and the row after, and none of the batch"
    assert _sum(world, obj, "s") == 0


def test_r8_a_move_past_the_stock_is_refused_with_the_rule_name(world) -> None:
    async def go() -> tuple[store.StudioObject, BatchError, int]:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "s", "a", 10))
        refused = await _refused(world.a, obj, _move(obj, "s", "a", -11))
        before = _rows(world, obj)
        await _put(world.a, obj, _move(obj, "s", "a", -10))
        return obj, refused, before

    obj, refused, before = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation)
    assert refused.cause.rule == "stock_not_negative" and refused.cause.object == "ledger"
    assert "stock_not_negative" in str(refused) and "stock_not_negative" in str(refused.cause)
    assert before == 1, "the refused move wrote 0 rows"
    assert _rows(world, obj) == 2 and _sum(world, obj, "s") == 0


def test_r8_a_broken_rule_keeps_the_earlier_work_of_the_caller(world) -> None:
    async def go() -> tuple[store.StudioObject, Any]:
        obj = await _ledger(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            kept = await store.apply_writes(db, ANA, package_id=obj.package_id,
                                            writes=[_move(obj, "s", "a", 5)])
            with pytest.raises(BatchError):
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    _move(obj, "s", "a", 1), _move(obj, "s", "a", -7)])
            again = await store.apply_writes(db, ANA, package_id=obj.package_id,
                                             writes=[_move(obj, "s", "a", -5)])
        return obj, (kept, again)

    obj, _ = _go(world, go)
    assert _rows(world, obj) == 2 and _sum(world, obj, "s") == 0


def test_r8_a_refusal_gives_the_place_of_the_first_write_that_touched_the_key(world) -> None:
    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "good", "a", 1))
        return await _refused(world.a, obj, _move(obj, "good", "a", 1), _move(obj, "bad", "a", -1),
                              _move(obj, "bad", "a", -1))

    assert _go(world, go).index == 1


def test_r8_a_record_that_two_writes_name_keeps_the_last_value(world) -> None:
    """A range reads the last value of a record, and a write after a delete
    finds no record."""
    async def go() -> tuple[Any, BatchError, Any]:
        obj = await _ledger(world.a, world.pkg_a, [_POSITIVE])
        made = (await _put(world.a, obj, _move(obj, "s", "a", 1)))[0]
        two = await _put(world.a, obj,
                         Write("update", obj.id, made.id, {"qty": -5}),
                         Write("update", obj.id, made.id, {"qty": 6, "note": "kept"}))
        gone = await _refused(world.a, obj, Write("delete", obj.id, made.id),
                              Write("update", obj.id, made.id, {"qty": 7}))
        last = await _refused(world.a, obj, Write("update", obj.id, made.id, {"qty": 7}),
                              Write("update", obj.id, made.id, {"qty": -7}))
        await _put(world.a, obj, Write("update", obj.id, made.id, {"qty": -5}),
                   Write("delete", obj.id, made.id))
        return two, gone, last

    two, gone, last = _go(world, go)
    assert two[1].data["qty"] == 6 and two[1].data["note"] == "kept"
    assert gone.index == 1 and isinstance(gone.cause, store.RecordNotFound)
    assert isinstance(last.cause, ConstraintViolation) and last.cause.rule == "qty_ok"


def test_r8_a_write_that_names_nothing_is_refused_with_its_place(world) -> None:
    async def go() -> tuple[BatchError, BatchError, int]:
        obj = await _ledger(world.a, world.pkg_a)
        no_object = await _refused(world.a, obj, _move(obj, "s", "a", 1),
                                   Write("create", str(uuid.uuid4()), data={}))
        no_record = await _refused(world.a, obj, _move(obj, "s", "a", 1),
                                   Write("delete", obj.id, str(uuid.uuid4())))
        return no_object, no_record, _rows(world, obj)

    no_object, no_record, rows = _go(world, go)
    assert no_object.index == 1 and isinstance(no_object.cause, store.RecordNotFound)
    assert no_record.index == 1 and isinstance(no_record.cause, store.RecordNotFound)
    assert rows == 0


def test_r8_each_record_function_runs_the_rules(world) -> None:
    """A batch of one: the cause comes back raw, and the answers stay."""
    async def refusal(call: Any) -> str:
        with pytest.raises(ConstraintViolation) as refused:
            async with tenant_session(world.a) as db:
                await call(db)
        return refused.value.rule

    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [_POSITIVE])
        pid = obj.package_id
        async with tenant_session(world.a) as db:
            rec = await store.create_record(db, ANA, obj.id, {"sku": "s", "site": "a", "qty": 5},
                                            package_id=pid)
        assert rec is not None
        out: dict[str, Any] = {}
        out["create"] = await refusal(lambda db: store.create_record(
            db, ANA, obj.id, {"sku": "s", "site": "a", "qty": 101}, package_id=pid))
        out["update"] = await refusal(lambda db: store.update_record(
            db, ANA, obj.id, rec.id, {"qty": 101}, package_id=pid))
        async with tenant_session(world.a) as db:
            out["no object"] = await store.create_record(
                db, ANA, str(uuid.uuid4()), {}, package_id=pid)
            out["no record"] = await store.update_record(
                db, ANA, obj.id, str(uuid.uuid4()), {"qty": 1}, package_id=pid)
            out["no delete"] = await store.delete_record(
                db, obj.id, str(uuid.uuid4()), package_id=pid)
            out["deleted"] = await store.delete_record(db, obj.id, rec.id, package_id=pid)
        return out

    out = _go(world, go)
    assert out["create"] == "qty_ok" and out["update"] == "qty_ok"
    assert out["no object"] is None and out["no record"] is None
    assert out["no delete"] is False and out["deleted"] is True


# ── d, e. Two writers at once ───────────────────────────────────────────────


async def _two_writers(org: str, first: Any, second: Any, hold: float = 0.12) -> list[Any]:
    """Two real connections. The first writes, then holds its transaction open
    for *hold*. The second starts then. With no lock, the second reads no row
    of the first, and it passes its check."""
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
def test_r8_two_writers_at_once_get_one_stock_move(world, round_no: int) -> None:
    async def go() -> tuple[store.StudioObject, list[Any]]:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "s", "a", 10))

        async def take(db: Any) -> Any:
            return await store.apply_writes(db, ANA, package_id=obj.package_id,
                                            writes=[_move(obj, "s", "a", -7)])

        return obj, await _two_writers(world.a, take, take)

    obj, results = _go(world, go)
    wins = [r for r in results if isinstance(r, list)]
    lost = [r for r in results if isinstance(r, BatchError)]
    assert len(wins) == 1 and len(lost) == 1, results
    assert isinstance(lost[0].cause, ConstraintViolation)
    assert _sum(world, obj, "s") == 3 and _rows(world, obj) == 2


@pytest.mark.parametrize("round_no", range(10))
def test_r8_two_writers_at_once_get_one_unique_row(world, round_no: int) -> None:
    async def go() -> tuple[store.StudioObject, list[Any]]:
        obj = await _ledger(world.a, world.pkg_a, [_ONE_SKU])

        async def make(db: Any) -> Any:
            return await store.apply_writes(db, ANA, package_id=obj.package_id,
                                            writes=[_move(obj, "same", "a", 1)])

        return obj, await _two_writers(world.a, make, make)

    obj, results = _go(world, go)
    assert sum(isinstance(r, list) for r in results) == 1, results
    assert sum(isinstance(r, BatchError) for r in results) == 1, results
    assert _rows(world, obj) == 1


@pytest.mark.parametrize("round_no", range(20))
def test_r8_two_writers_with_the_records_in_opposite_order_never_deadlock(
        world, round_no: int) -> None:
    """Both batches start at once and name two objects in opposite orders.
    The org lock and the sorted row locks leave no 40P01, and each commits."""
    async def go() -> list[Any]:
        x = await _ledger(world.a, world.pkg_a, name="x")
        y = await _ledger(world.a, world.pkg_a, name="y")
        rx = (await _put(world.a, x, _move(x, "s", "a", 5)))[0]
        ry = (await _put(world.a, y, _move(y, "s", "a", 5)))[0]
        ux, uy = Write("update", x.id, rx.id, {"qty": 6}), Write("update", y.id, ry.id, {"qty": 7})

        async def batch(writes: list[Write]) -> Any:
            async with tenant_session(world.a) as db:
                return await store.apply_writes(db, ANA, package_id=x.package_id, writes=writes)

        return list(await asyncio.wait_for(
            asyncio.gather(batch([ux, uy]), batch([uy, ux]), return_exceptions=True), timeout=10))

    results = _go(world, go)
    assert all(isinstance(r, list) for r in results), results


class _Spy:
    """A session that keeps each statement and its binds."""

    def __init__(self, db: Any) -> None:
        self._db = db
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def begin_nested(self) -> Any:
        return self._db.begin_nested()

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.seen.append((str(stmt), dict(params or {})))
        return await self._db.execute(stmt, params)


def test_r8_the_locks_come_in_one_order(world) -> None:
    """The org lock once, the object rows in ascending id, the record rows in
    ascending `(object_id, record_id)`, then the writes in list order."""
    async def go() -> tuple[list[tuple[str, dict[str, Any]]], list[Any]]:
        x = await _ledger(world.a, world.pkg_a, name="x")
        y = await _ledger(world.a, world.pkg_a, name="y")
        low, high = sorted((x, y), key=lambda o: o.id)
        made = await _put(world.a, low, *[_move(low, f"s{n}", "a", 5) for n in range(3)],
                          *[_move(high, f"s{n}", "a", 5) for n in range(3)])
        ids = [r.id for r in made]
        writes = [Write("update", r.object_id, r.id, {"note": "x"}) for r in reversed(made)]
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            await store.apply_writes(spy, ANA, package_id=low.package_id, writes=writes)
        assert len(ids) == 6
        return spy.seen, [(w.object_id, w.record_id) for w in writes]

    seen, wanted = _go(world, go)
    sqls = [s for s, _ in seen]
    assert sqls.count(store._ORG_LOCK_SQL) == 1
    assert (sqls.index(store._LOCK_WAIT_SQL) < sqls.index(store._ORG_LOCK_SQL)
            < sqls.index(store._LOCK_WAIT_BACK_SQL) < sqls.index(store._SHARE_OBJECT_SQL))
    objects = [p["oid"] for s, p in seen if s == store._SHARE_OBJECT_SQL]
    assert objects == sorted(objects) and len(objects) == 2
    locks = [(p["oid"], p["rid"]) for s, p in seen if s == store._LOCK_RECORD_SQL]
    assert locks == sorted(locks) and len(locks) == 6
    updates = [(p["oid"], p["rid"]) for s, p in seen if s == store._UPDATE_RECORD_SQL]
    assert updates == wanted
    assert sqls.index(store._UPDATE_RECORD_SQL) > max(
        i for i, s in enumerate(sqls) if s == store._LOCK_RECORD_SQL)


def test_r8_set_constraints_takes_the_org_lock_then_the_object_row(world) -> None:
    async def go() -> list[str]:
        obj = await _ledger(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            await store.set_constraints(spy, obj.id, [], package_id=obj.package_id)
        return [s for s, _ in spy.seen]

    sqls = _go(world, go)
    assert sqls.index(store._ORG_LOCK_SQL) < sqls.index(store._LOCK_PACKAGE_OBJECT_SQL)
    assert sqls[-1] == store._SET_RULES_SQL


# ── j. The wait for the org lock ────────────────────────────────────────────


@asynccontextmanager
async def _held(org: str, most: float = 6.0) -> AsyncIterator[None]:
    """Hold the org lock of *org* on its own connection. It lets go after
    *most* seconds, so a store with no bound fails a test and hangs nothing."""
    ready, release = asyncio.Event(), asyncio.Event()

    async def holder() -> None:
        async with tenant_session(org) as db:
            await store._lock_org(db)
            ready.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(release.wait(), timeout=most)

    task = asyncio.create_task(holder())
    await ready.wait()
    try:
        yield
    finally:
        release.set()
        await task


async def _lock_timeout(db: Any) -> str:
    return str((await db.execute(text("SELECT current_setting('lock_timeout')"))).scalar_one())


def test_r8_a_waiter_past_the_bound_gets_store_busy(world) -> None:
    """The real 1 s. The wait gives a clean refusal and no database error, the
    session reads on, and the old `lock_timeout` is back."""
    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a)
        out: dict[str, Any] = {"obj": obj}
        async with _held(world.a), tenant_session(world.a) as db:
            await db.execute(text("SELECT set_config('lock_timeout', '42s', true)"))
            began = time.monotonic()
            with pytest.raises(BatchError) as busy:
                await store.apply_writes(db, ANA, package_id=obj.package_id,
                                         writes=[_move(obj, "s", "a", 1)])
            out["waited"] = time.monotonic() - began
            out["busy"] = busy.value
            out["setting"] = await _lock_timeout(db)
            out["read"] = await store.get_object(db, obj.id)
        await _put(world.a, obj, _move(obj, "s", "a", 1))
        return out

    out = _go(world, go)
    cause = out["busy"].cause
    assert isinstance(cause, store.StoreBusy) and cause.code == "store_busy"
    assert str(cause) == "This app is busy. Try again in a moment."
    assert 0.8 <= out["waited"] < 2.5, out["waited"]
    assert out["setting"] == "42s"
    assert out["read"] is not None
    assert _rows(world, out["obj"]) == 1, "the write after the holder left"


@pytest.mark.parametrize("name", [
    "define_object", "add_field", "set_field_hot", "drop_object", "set_constraints",
    "create_record", "update_record", "delete_record",
])
def test_r8_each_caller_of_the_org_lock_gets_the_bound(world, monkeypatch, name: str) -> None:
    """Each caller, with no batch around it for the registry ones. The wait
    fails in its own SAVEPOINT, so the session reads on, and the old
    `lock_timeout` is back. A batch rolls back to the old value on its own, so
    the registry callers are the ones that test the put back."""
    monkeypatch.setattr(store, "ORG_LOCK_WAIT_MS", 150)

    async def go() -> Any:
        obj = await _ledger(world.a, world.pkg_a)
        rec = (await _put(world.a, obj, _move(obj, "s", "a", 1)))[0]
        pid = obj.package_id
        calls = {
            "define_object": lambda db: store.define_object(
                db, ANA, package_id=pid, name="other", fields=_LEDGER),
            "add_field": lambda db: store.add_field(db, obj.id, {"key": "extra", "type": "text"}),
            "set_field_hot": lambda db: store.set_field_hot(db, obj.id, "note", True),
            "drop_object": lambda db: store.drop_object(db, obj.id),
            "set_constraints": lambda db: store.set_constraints(db, obj.id, [], package_id=pid),
            "create_record": lambda db: store.create_record(db, ANA, obj.id, {}, package_id=pid),
            "update_record": lambda db: store.update_record(
                db, ANA, obj.id, rec.id, {"note": "x"}, package_id=pid),
            "delete_record": lambda db: store.delete_record(db, obj.id, rec.id, package_id=pid),
        }
        async with _held(world.a, most=3.0), tenant_session(world.a) as db:
            await db.execute(text("SELECT set_config('lock_timeout', '42s', true)"))
            with pytest.raises(store.StoreBusy):
                await calls[name](db)
            return await _lock_timeout(db), await store.get_object(db, obj.id)

    setting, read = _go(world, go)
    assert setting == "42s"
    assert read is not None


def test_r8_the_bound_covers_the_wait_only(world, monkeypatch) -> None:
    """A later statement may wait past the bound. A raw lock on the record row
    holds the write for longer than the bound, and the write still passes."""
    monkeypatch.setattr(store, "ORG_LOCK_WAIT_MS", 100)

    async def go() -> list[Any]:
        obj = await _ledger(world.a, world.pkg_a, [])
        rec = (await _put(world.a, obj, _move(obj, "s", "a", 1)))[0]
        locked = asyncio.Event()

        async def row_holder() -> None:
            async with tenant_session(world.a) as db:
                await db.execute(text("SELECT 1 FROM studio_records WHERE id = CAST(:r AS uuid) "
                                      "FOR UPDATE"), {"r": rec.id})
                locked.set()
                await asyncio.sleep(0.5)

        async def write() -> list[Any]:
            await locked.wait()
            return await _put(world.a, obj, Write("update", obj.id, rec.id, {"note": "after"}))

        _, done = await asyncio.gather(row_holder(), write())
        return done

    assert _go(world, go)[0].data["note"] == "after"


def test_r8_a_full_pool_of_waiters_leaves_another_org_a_connection(world) -> None:
    """The real pool, full: one holder and waiters on each other connection.
    Each waiter gives its connection back at the bound, so a read of org B gets
    one inside the pool timeout. With no bound the read fails at that timeout.
    This is one pool of writers. §14 C1b gives the numbers for more."""
    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a)
        pool = get_engine().pool
        waiters = pool.size() + pool._max_overflow - 1  # type: ignore[attr-defined]
        wait_limit = float(pool._timeout)  # type: ignore[attr-defined]

        async def waiter() -> Any:
            try:
                return await _put(world.a, obj, _move(obj, "s", "a", 1))
            except BatchError as exc:
                return exc

        async with _held(world.a, most=wait_limit + 2):
            tasks = [asyncio.create_task(waiter()) for _ in range(waiters)]
            await asyncio.sleep(1.0)
            began = time.monotonic()
            async with tenant_session(world.b) as db:
                read = (await db.execute(text("SELECT 1"))).scalar_one()
            waited = time.monotonic() - began
            results = await asyncio.gather(*tasks)
        return {"read": read, "waited": waited, "limit": wait_limit, "results": results}

    out = _go(world, go)
    assert out["read"] == 1 and out["waited"] < out["limit"], out["waited"]
    assert all(isinstance(r, BatchError) and isinstance(r.cause, store.StoreBusy)
               for r in out["results"]), out["results"]


def test_r8_a_deadlock_at_the_lock_gives_store_busy(world, monkeypatch) -> None:
    """A caller that holds `apps` FOR UPDATE, and then waits for the org lock,
    against a writer that holds the org lock and waits for the `apps` row. The
    second side passes its own deadlock check before the first side waits, so
    Postgres picks the wait at the lock. The bound is long, so 40P01 comes and
    55P03 does not. The session reads on, and the other side ends its write."""
    def row_wait_seen() -> bool:
        with world.p.admin_engine.connect() as c:
            return bool(c.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                "AND wait_event_type = 'Lock'")).scalar_one())

    monkeypatch.setattr(store, "ORG_LOCK_WAIT_MS", 8_000)

    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [])
        pid = obj.package_id
        holds_row, out = asyncio.Event(), {}

        async def row_first() -> None:
            async with tenant_session(world.a) as db:
                await db.execute(text("SELECT 1 FROM apps WHERE id = CAST(:p AS uuid) FOR UPDATE"),
                                 {"p": pid})
                await db.execute(text("SELECT set_config('lock_timeout', '42s', true)"))
                holds_row.set()
                while not row_wait_seen():
                    await asyncio.sleep(0.05)
                await asyncio.sleep(1.3)  # past `deadlock_timeout` of the other side
                began = time.monotonic()
                with pytest.raises(store.StoreBusy) as busy:
                    await store.create_record(db, ANA, obj.id, {"sku": "s"}, package_id=pid)
                out["waited"] = time.monotonic() - began
                out["busy"] = busy.value
                out["setting"] = await _lock_timeout(db)
                out["read"] = await store.get_object(db, obj.id)

        async def lock_first() -> None:
            await holds_row.wait()
            async with tenant_session(world.a) as db:
                out["made"] = await store.define_object(db, ANA, package_id=pid, name="second",
                                                        fields=_LEDGER)

        await asyncio.wait_for(asyncio.gather(row_first(), lock_first()), timeout=20)
        return out

    out = _go(world, go)
    assert str(out["busy"]) == "This app is busy. Try again in a moment."
    assert out["waited"] < 4, out["waited"]
    assert out["setting"] == "42s"
    assert out["read"] is not None and out["made"] is not None


# ── f. Update and delete ────────────────────────────────────────────────────


def test_r8_an_update_that_moves_a_row_checks_both_groups(world) -> None:
    """Site a holds +10 and -5, so a sum of 5. A move of the +10 row leaves -5
    in the old group. A move of a -5 row into an empty site breaks the new
    group. Each one alone is refused, and neither row moved."""
    async def go() -> tuple[store.StudioObject, BatchError, BatchError]:
        obj = await _ledger(world.a, world.pkg_a)
        plus, minus = await _put(world.a, obj, _move(obj, "s", "a", 10), _move(obj, "s", "a", -5))
        old_key = await _refused(world.a, obj, Write("update", obj.id, plus.id, {"site": "b"}))
        new_key = await _refused(world.a, obj, Write("update", obj.id, minus.id, {"site": "b"}))
        return obj, old_key, new_key

    obj, old_key, new_key = _go(world, go)
    assert isinstance(old_key.cause, ConstraintViolation)
    assert isinstance(new_key.cause, ConstraintViolation)
    assert _sum(world, obj, "s", "a") == 5 and _sum(world, obj, "s", "b") == 0


def test_r8_an_update_that_empties_a_group_field_checks_the_old_group(world) -> None:
    """A record with no value in a group field joins no group, so it leaves
    the old group."""
    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a, [{
            "name": "floor", "kind": "group_sum_min", "field": "qty", "group_by": ["sku", "site"],
            "min": 0}])
        plus, _ = await _put(world.a, obj, _move(obj, "s", "a", 10), _move(obj, "s", "a", -5))
        return await _refused(world.a, obj, Write("update", obj.id, plus.id, {"site": None}))

    assert isinstance(_go(world, go).cause, ConstraintViolation)


def test_r8_an_update_that_lowers_a_row_is_checked(world) -> None:
    """The net change reads the old value too. An update from 10 to 1 adds a
    positive value, and it lowers the group by 9."""
    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a)
        plus, _ = await _put(world.a, obj, _move(obj, "s", "a", 10), _move(obj, "s", "a", -5))
        return await _refused(world.a, obj, Write("update", obj.id, plus.id, {"qty": 1}))

    assert isinstance(_go(world, go).cause, ConstraintViolation)


def test_r8_a_delete_that_breaks_the_balance_is_refused(world) -> None:
    async def go() -> tuple[store.StudioObject, type, bool]:
        obj = await _ledger(world.a, world.pkg_a)
        plus, minus = await _put(world.a, obj, _move(obj, "s", "a", 10), _move(obj, "s", "a", -7))
        refused: type = object
        try:
            async with tenant_session(world.a) as db:
                await store.delete_record(db, obj.id, plus.id, package_id=obj.package_id)
        except ConstraintViolation as exc:
            refused = type(exc)
        async with tenant_session(world.a) as db:
            ok = await store.delete_record(db, obj.id, minus.id, package_id=obj.package_id)
        return obj, refused, ok

    obj, refused, ok = _go(world, go)
    assert refused is ConstraintViolation and ok is True
    assert _rows(world, obj) == 1 and _sum(world, obj, "s") == 10


def test_r8_a_rule_on_a_number_key_and_a_date_key_groups_right(world) -> None:
    fields = [
        {"key": "bin", "type": "number", "hot": True},
        {"key": "day", "type": "date", "hot": True},
        {"key": "qty", "type": "number", "hot": True},
    ]

    async def go() -> tuple[BatchError, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [
            {"name": "by_bin", "kind": "group_sum_min", "field": "qty", "group_by": ["bin"], "min": 0},
            {"name": "by_day", "kind": "group_sum_min", "field": "qty", "group_by": ["day"], "min": 0},
        ], fields=fields)

        def row(b: float, d: str, q: float) -> Write:
            return Write("create", obj.id, data={"bin": b, "day": d, "qty": q})

        await _put(world.a, obj, row(1.5, "2026-11-01", 5), row(2, "2026-11-02", 5),
                   row(9, "2026-11-03", 100))
        bin_low = await _refused(world.a, obj, row(1.5, "2026-11-03", -6))
        day_low = await _refused(world.a, obj, row(9, "2026-11-02", -6))
        await _put(world.a, obj, row(1.5, "2026-11-01", -5))
        return bin_low, day_low

    bin_low, day_low = _go(world, go)
    assert isinstance(bin_low.cause, ConstraintViolation) and bin_low.cause.rule == "by_bin"
    assert isinstance(day_low.cause, ConstraintViolation) and day_low.cause.rule == "by_day"


# ── k. A write that does not lower a group ──────────────────────────────────

_SUM = "sum(s.v_num)"


def test_r8_a_broken_key_takes_a_receipt_and_refuses_a_lower_write(world) -> None:
    """Old data leaves a key at -5. -3 lowers it, so the store reads the group
    and refuses. +3 cannot lower it, so a repair is possible step by step."""
    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [])
        old = (await _put(world.a, obj, _move(obj, "s", "a", -5)))[0]
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, obj.id, [_STOCK], package_id=obj.package_id)
        lower = await _refused(world.a, obj, _move(obj, "s", "a", -3))
        await _put(world.a, obj, _move(obj, "s", "a", 3))
        await _put(world.a, obj, Write("update", obj.id, old.id, {"note": "an edit"}))
        await _put(world.a, obj, Write("delete", obj.id, old.id))
        return obj, lower

    obj, lower = _go(world, go)
    assert isinstance(lower.cause, ConstraintViolation)
    assert _sum(world, obj, "s") == 3


def test_r8_a_write_that_does_not_lower_a_group_reads_no_sum(world) -> None:
    async def sums(obj: store.StudioObject, *writes: Write) -> int:
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            await store.apply_writes(spy, ANA, package_id=obj.package_id, writes=list(writes))
        return sum(_SUM in s for s, _ in spy.seen)

    async def go() -> dict[str, int]:
        obj = await _ledger(world.a, world.pkg_a)
        plus, minus = await _put(world.a, obj, _move(obj, "s", "a", 10), _move(obj, "s", "a", -1))
        return {
            "receipt": await sums(obj, _move(obj, "s", "a", 1)),
            "no number": await sums(obj, Write("create", obj.id, data={"sku": "s", "site": "a"})),
            "net zero": await sums(obj, _move(obj, "s", "a", -4), _move(obj, "s", "a", 4)),
            "edit": await sums(obj, Write("update", obj.id, plus.id, {"note": "x"})),
            "raise": await sums(obj, Write("update", obj.id, plus.id, {"qty": 11})),
            "delete a debit": await sums(obj, Write("delete", obj.id, minus.id)),
            "lower": await sums(obj, _move(obj, "s", "a", -1)),
            "a move": await sums(obj, _move(obj, "s", "a", -1), _move(obj, "s", "b", 1)),
        }

    out = _go(world, go)
    assert out.pop("lower") == 1 and out.pop("a move") == 1
    assert set(out.values()) == {0}, out


def test_r8_a_floor_above_zero_holds_from_the_first_row(world) -> None:
    """A new group starts at a sum of 0, below this floor. So a rise proves
    nothing, and each write reads its group."""
    rule = {**_STOCK, "min": 1}

    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [rule])
        out: dict[str, Any] = {"obj": obj}
        out["half"] = await _refused(world.a, obj, _move(obj, "half", "a", 0.5))
        out["zero"] = await _refused(world.a, obj, _move(obj, "zero", "a", 0))
        await _put(world.a, obj, _move(obj, "one", "a", 1))
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            await store.apply_writes(spy, ANA, package_id=obj.package_id,
                                     writes=[_move(obj, "one", "a", 1)])
        out["sums"] = sum(_SUM in s for s, _ in spy.seen)
        return out

    out = _go(world, go)
    for name in ("half", "zero"):
        assert isinstance(out[name].cause, ConstraintViolation), name
    assert out["sums"] == 1
    assert _sum(world, out["obj"], "one") == 2 and _rows(world, out["obj"]) == 2


# ── l. The last day of the calendar ─────────────────────────────────────────


def test_r8_the_last_day_of_the_calendar_is_a_key(world) -> None:
    """A legal date in a `unique` field and in a group, and the delete of an
    old record that holds it."""
    last = "9999-12-31"
    fields = [{"key": "day", "type": "date", "hot": True},
              {"key": "qty", "type": "number", "hot": True}]

    async def go() -> BatchError:
        rows = await _ledger(world.a, world.pkg_a, [], name="old", fields=fields)
        old = (await _put(world.a, rows, Write("create", rows.id, data={"day": last, "qty": 2})))[0]
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, rows.id, [{
                "name": "by_day", "kind": "group_sum_min", "field": "qty", "group_by": ["day"],
                "min": 0}], package_id=rows.package_id)
        await _put(world.a, rows, Write("delete", rows.id, old.id))
        one = await _ledger(world.a, world.pkg_a, [
            {"name": "one_day", "kind": "unique", "fields": ["day"]}], name="one", fields=fields)
        await _put(world.a, one, Write("create", one.id, data={"day": last, "qty": 1}))
        return await _refused(world.a, one, Write("create", one.id, data={"day": last, "qty": 1}))

    refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "one_day"


# ── m. A key with no rows ───────────────────────────────────────────────────


def test_r8_the_last_row_of_a_group_can_leave_at_any_min(world) -> None:
    """With `min` 1, a delete and a move of the last row each pass, because a
    group that no longer exists holds nothing to break. A row that stays with
    a sum of 0 is a group, and it is refused."""
    rule = {**_STOCK, "min": 1}

    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [rule])
        gone, moved, kept, _ = await _put(
            world.a, obj, _move(obj, "gone", "a", 5), _move(obj, "moved", "a", 5),
            _move(obj, "kept", "a", 5), _move(obj, "kept", "a", 0))
        await _put(world.a, obj, Write("delete", obj.id, gone.id))
        await _put(world.a, obj, Write("update", obj.id, moved.id, {"sku": "new"}))
        return obj, await _refused(world.a, obj, Write("delete", obj.id, kept.id))

    obj, refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation)
    assert _sum(world, obj, "gone") == 0 and _sum(world, obj, "moved") == 0
    assert _sum(world, obj, "new") == 5 and _sum(world, obj, "kept") == 5


def test_r8_a_group_with_rows_and_no_amount_sums_to_zero(world) -> None:
    """Rows 5 and blank, `min` 1. A delete of the 5 leaves a row, so the group
    still exists and its sum is 0. A delete of both leaves no rows, and passes."""
    rule = {**_STOCK, "min": 1}

    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [rule])
        five = (await _put(world.a, obj, _move(obj, "s", "a", 5)))[0]
        blank = (await _put(world.a, obj, Write("create", obj.id, data={"sku": "s", "site": "a"})))[0]
        refused = await _refused(world.a, obj, Write("delete", obj.id, five.id))
        await _put(world.a, obj, Write("delete", obj.id, five.id),
                   Write("delete", obj.id, blank.id))
        return obj, refused

    obj, refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation)
    assert _rows(world, obj) == 0


# ── The kinds ───────────────────────────────────────────────────────────────


def test_r8_a_range_holds_on_each_write_and_passes_no_value(world) -> None:
    async def go() -> tuple[BatchError, BatchError, Any]:
        obj = await _ledger(world.a, world.pkg_a, [_POSITIVE])
        low = await _refused(world.a, obj, _move(obj, "s", "a", -1))
        made = (await _put(world.a, obj, _move(obj, "s", "a", 0), _move(obj, "s", "a", 100)))[0]
        high = await _refused(world.a, obj, Write("update", obj.id, made.id, {"qty": 100.5}))
        empty = await _put(world.a, obj, Write("create", obj.id, data={"sku": "none"}))
        return low, high, empty

    low, high, empty = _go(world, go)
    assert low.cause.rule == "qty_ok" and high.cause.rule == "qty_ok"
    assert empty[0].data == {"sku": "none"}


def test_r8_unique_refuses_a_second_record_and_not_the_record_itself(world) -> None:
    async def go() -> tuple[BatchError, BatchError, BatchError, list[Any]]:
        obj = await _ledger(world.a, world.pkg_a, [_ONE_SKU])
        one, two = await _put(world.a, obj, _move(obj, "a", "x", 1), _move(obj, "b", "x", 1))
        clash = await _refused(world.a, obj, _move(obj, "a", "y", 2))
        moved = await _refused(world.a, obj, Write("update", obj.id, two.id, {"sku": "a"}))
        in_batch = await _refused(world.a, obj, _move(obj, "c", "x", 1), _move(obj, "c", "y", 1))
        # The record itself never clashes, and a swap in one batch passes.
        same = await _put(world.a, obj, Write("update", obj.id, one.id, {"note": "edited"}),
                          Write("update", obj.id, one.id, {"sku": "z"}),
                          Write("update", obj.id, two.id, {"sku": "a"}),
                          Write("update", obj.id, one.id, {"sku": "b"}))
        return clash, moved, in_batch, same

    clash, moved, in_batch, same = _go(world, go)
    for refused in (clash, moved, in_batch):
        assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "one_sku"
    assert same[-1].data["sku"] == "b"


@pytest.mark.parametrize(("spec", "value", "other"), [
    ({"type": "text"}, "same", "other"),
    ({"type": "email"}, "Ana@Example.test", "ben@example.test"),
    ({"type": "select", "options": ["a", "b"]}, "a", "b"),
    ({"type": "number"}, 1.5, 2),
    ({"type": "number"}, 0, 1),
    ({"type": "date"}, "2026-11-01", "2026-11-02"),
    ({"type": "datetime"}, "2026-11-01T09:30:00+05:30", "2026-11-01T09:30:01+05:30"),
], ids=["text", "email", "select", "number", "zero", "date", "datetime"])
def test_r8_unique_holds_on_each_key_type(world, spec: dict[str, Any], value: Any,
                                          other: Any) -> None:
    fields = [{"key": "k", "hot": True, **spec}]
    rule = {"name": "one_k", "kind": "unique", "fields": ["k"]}

    async def go() -> tuple[store.StudioObject, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [rule], fields=fields)
        await _put(world.a, obj, Write("create", obj.id, data={"k": value}),
                   Write("create", obj.id, data={"k": other}))
        return obj, await _refused(world.a, obj, Write("create", obj.id, data={"k": value}))

    obj, refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "one_k"
    assert _rows(world, obj) == 2


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_r8_an_empty_value_never_clashes(world, empty: Any) -> None:
    async def go() -> store.StudioObject:
        obj = await _ledger(world.a, world.pkg_a, [_ONE_SKU])
        await _put(world.a, obj, Write("create", obj.id, data={"sku": empty, "site": "a"}),
                   Write("create", obj.id, data={"sku": empty, "site": "b"}))
        return obj

    assert _rows(world, _go(world, go)) == 2


def test_r8_a_long_text_compares_all_its_characters(world) -> None:
    """`v_text` holds the first 256 characters. Two texts that differ only
    after them do not clash, and two equal long texts do."""
    head = "k" * sf.HOT_TEXT_CHARS
    one, two = head + "one", head + "two"

    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a, [_ONE_SKU])
        await _put(world.a, obj, _move(obj, one, "a", 1), _move(obj, two, "a", 1))
        return await _refused(world.a, obj, _move(obj, one, "b", 1))

    refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation)
    assert one not in str(refused), "a refusal never echoes a value"


def test_r8_a_group_key_of_a_long_text_compares_all_its_characters(world) -> None:
    head = "k" * sf.HOT_TEXT_CHARS
    rule = {**_STOCK, "group_by": ["sku"]}

    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a, [rule])
        await _put(world.a, obj, _move(obj, head + "one", "a", 100), _move(obj, head + "two", "a", 1))
        return await _refused(world.a, obj, _move(obj, head + "two", "a", -2))

    assert isinstance(_go(world, go).cause, ConstraintViolation)


def test_r8_a_record_with_no_group_value_joins_no_group(world) -> None:
    async def go() -> store.StudioObject:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, Write("create", obj.id, data={"sku": "s", "qty": -5}))
        await _put(world.a, obj, Write("create", obj.id, data={"site": "a", "qty": -5}))
        return obj

    assert _rows(world, _go(world, go)) == 2


# ── The declaration ─────────────────────────────────────────────────────────


def test_r8_a_declaration_does_not_check_old_data(world) -> None:
    """A rule binds each write after it is declared. A broken key stays as it
    is until a write lowers it, and a `unique` clash until a write touches it."""
    async def go() -> tuple[store.StudioObject, BatchError, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [])
        await _put(world.a, obj, _move(obj, "low", "a", -5), _move(obj, "dup", "a", 1),
                   _move(obj, "dup", "b", 1))
        async with tenant_session(world.a) as db:
            done = await store.set_constraints(db, obj.id, [_STOCK], package_id=obj.package_id)
        assert done is not None and [r.name for r in done.constraints] == ["stock_not_negative"]
        await _put(world.a, obj, _move(obj, "other", "a", 1))
        lower = await _refused(world.a, obj, _move(obj, "low", "a", -1))
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, obj.id, [_ONE_SKU], package_id=obj.package_id)
        clash = await _refused(world.a, obj, _move(obj, "dup", "c", 1))
        return obj, lower, clash

    obj, lower, clash = _go(world, go)
    assert isinstance(lower.cause, ConstraintViolation)
    assert isinstance(clash.cause, ConstraintViolation)
    assert _sum(world, obj, "low") == -5


def test_r8_old_duplicates_can_be_repaired_one_at_a_time(world) -> None:
    """A delete and a move away cannot add a clash, so `unique` lets both pass
    while three old records share a value."""
    async def go() -> int:
        obj = await _ledger(world.a, world.pkg_a, [])
        a, b, _ = await _put(world.a, obj, _move(obj, "dup", "a", 1), _move(obj, "dup", "b", 1),
                             _move(obj, "dup", "c", 1))
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, obj.id, [_ONE_SKU], package_id=obj.package_id)
            assert await store.delete_record(db, obj.id, a.id, package_id=obj.package_id) is True
            moved = await store.update_record(db, ANA, obj.id, b.id, {"sku": "new"},
                                              package_id=obj.package_id)
            assert moved is not None
        return _rows(world, obj)

    assert _go(world, go) == 2


def test_r8_set_constraints_replaces_the_list_and_reads_back(world) -> None:
    async def go() -> list[Any]:
        obj = await _ledger(world.a, world.pkg_a, [_STOCK, _POSITIVE])
        assert [r.name for r in obj.constraints] == ["stock_not_negative", "qty_ok"]
        async with tenant_session(world.a) as db:
            read = await store.get_object(db, obj.id)
            by_name = await store.get_object_by_name(db, "ledger", package_id=obj.package_id)
            one = await store.set_constraints(db, obj.id, [_ONE_SKU], package_id=obj.package_id)
            none = await store.set_constraints(db, obj.id, [], package_id=obj.package_id)
        return [read, by_name, one, none, obj]

    read, by_name, one, none, obj = _go(world, go)
    assert read.constraints == obj.constraints == by_name.constraints
    assert [r.name for r in one.constraints] == ["one_sku"]
    assert none.constraints == ()


def test_r8_seventeen_rules_are_refused_and_nothing_changes(world) -> None:
    async def go() -> tuple[str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [])
        many = [{"name": f"r{n}", "kind": "unique", "fields": ["sku"]} for n in range(17)]
        async with tenant_session(world.a) as db:
            with pytest.raises(ConstraintError) as refused:
                await store.set_constraints(db, obj.id, many, package_id=obj.package_id)
            kept = await store.set_constraints(db, obj.id, many[:16], package_id=obj.package_id)
        return refused.value.message, kept

    message, kept = _go(world, go)
    assert "at most 16 rules" in message and len(kept.constraints) == 16


def test_r8_set_constraints_refuses_a_cold_field_and_leaves_it_cold(world) -> None:
    fields = [{"key": "sku", "type": "text"}, {"key": "qty", "type": "number"}]

    async def go() -> tuple[str, str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [], fields=fields)
        async with tenant_session(world.a) as db:
            with pytest.raises(ConstraintError) as unique:
                await store.set_constraints(db, obj.id, [_ONE_SKU], package_id=obj.package_id)
            with pytest.raises(ConstraintError) as gone:
                await store.set_constraints(db, obj.id, [_POSITIVE, {
                    "name": "x", "kind": "range", "field": "gone", "min": 0}],
                    package_id=obj.package_id)
            after = await store.get_object(db, obj.id)
        return unique.value.message, gone.value.message, after

    unique, gone, after = _go(world, go)
    assert "must be hot" in unique and "not a field" in gone
    assert not any(f.hot for f in after.fields) and after.constraints == ()


def test_r8_a_field_that_a_rule_reads_stays_hot(world) -> None:
    """A cold field loses its hot rows. A rule on it would pass each write."""
    async def go() -> tuple[list[bool], bool]:
        obj = await _ledger(world.a, world.pkg_a, [_STOCK, _ONE_SKU])
        out = []
        async with tenant_session(world.a) as db:
            for key in ("sku", "site", "qty"):
                with pytest.raises(store.ObjectStoreError, match="must stay hot"):
                    await store.set_field_hot(db, obj.id, key, False)
                out.append(True)
            await store.set_constraints(db, obj.id, [], package_id=obj.package_id)
            cold = await store.set_field_hot(db, obj.id, "qty", False)
        return out, cold

    out, cold = _go(world, go)
    assert out == [True] * 3 and cold is True


def test_r8_a_rule_on_a_cold_field_refuses_the_write(world) -> None:
    """Raw SQL can turn a field cold under a rule. The check fails closed."""
    async def go() -> BatchError:
        obj = await _ledger(world.a, world.pkg_a)
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_object_fields SET hot = false "
                           "WHERE object_id = CAST(:o AS uuid) AND key = 'qty'"), {"o": obj.id})
        return await _refused(world.a, obj, _move(obj, "s", "a", 1))

    refused = _go(world, go)
    assert isinstance(refused.cause, ConstraintViolation) and "not hot" in str(refused)


@pytest.mark.parametrize("stored", [
    ["junk"],
    [{"name": "floor", "kind": "group_sum_min", "group_by": ["sku", "site"], "min": -5}],
], ids=["junk", "a group with no field"])
def test_r8_a_stored_rule_that_the_checks_cannot_read_refuses_each_write(
        world, stored: list[Any]) -> None:
    """Raw SQL stores what the grammar refuses. A read does not crash, each
    write fails closed, and a new declaration repairs the object."""
    async def go() -> dict[str, Any]:
        obj = await _ledger(world.a, world.pkg_a, [])
        rec = (await _put(world.a, obj, _move(obj, "s", "a", 1)))[0]
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_objects SET constraints = :c WHERE id = CAST(:o AS uuid)"),
                      {"o": obj.id, "c": json.dumps(stored)})
        out: dict[str, Any] = {"obj": obj}
        async with tenant_session(world.a) as db:
            out["read"] = await store.get_object(db, obj.id)
            out["hot"] = await store.set_field_hot(db, obj.id, "qty", False)
            await store.set_field_hot(db, obj.id, "qty", True)
        out["create"] = await _refused(world.a, obj, _move(obj, "s", "a", 1))
        out["delete"] = await _refused(world.a, obj, Write("delete", obj.id, rec.id))
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, obj.id, [], package_id=obj.package_id)
        await _put(world.a, obj, _move(obj, "s", "a", 1))
        return out

    out = _go(world, go)
    assert [r.kind for r in out["read"].constraints] == [sr.MALFORMED] and out["hot"] is True
    for write in ("create", "delete"):
        assert isinstance(out[write].cause, ConstraintViolation), write
    assert _rows(world, out["obj"]) == 2, "the record, and the write after the repair"


def test_r8_a_stored_kind_that_this_code_cannot_check_refuses_the_write(world) -> None:
    async def go() -> tuple[Any, BatchError]:
        obj = await _ledger(world.a, world.pkg_a, [])
        with world.p.admin_engine.begin() as c:
            c.execute(text("UPDATE studio_objects SET constraints = :c WHERE id = CAST(:o AS uuid)"),
                      {"o": obj.id, "c": json.dumps([{"name": "later", "kind": "a_later_kind"}])})
        async with tenant_session(world.a) as db:
            read = await store.get_object(db, obj.id)
        return read, await _refused(world.a, obj, _move(obj, "s", "a", 1))

    read, refused = _go(world, go)
    assert [r.name for r in read.constraints] == ["later"]
    assert isinstance(refused.cause, ConstraintViolation) and refused.cause.rule == "later"


# ── g. The caps ─────────────────────────────────────────────────────────────


def test_r8_fifty_writes_pass_and_fifty_one_run_no_statement(world) -> None:
    async def go() -> tuple[store.StudioObject, int, int]:
        obj = await _ledger(world.a, world.pkg_a)
        writes = [_move(obj, "s", "a", 1) for _ in range(50)]
        answers = await _put(world.a, obj, *writes)
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            with pytest.raises(store.BudgetExceeded):
                await store.apply_writes(spy, ANA, package_id=obj.package_id, writes=[*writes, writes[0]])
        return obj, len(answers), len(spy.seen)

    obj, answered, statements = _go(world, go)
    assert answered == 50 and statements == 0
    assert _rows(world, obj) == 50 and _sum(world, obj, "s") == 50


def test_r8_each_key_is_checked_once(world) -> None:
    """Fifty rows of one key run one sum, under one org lock."""
    async def go() -> list[str]:
        obj = await _ledger(world.a, world.pkg_a)
        await _put(world.a, obj, _move(obj, "s", "a", 100))
        async with tenant_session(world.a) as db:
            spy = _Spy(db)
            await store.apply_writes(spy, ANA, package_id=obj.package_id,
                                     writes=[_move(obj, "s", "a", -1) for _ in range(50)])
        return [s for s, _ in spy.seen]

    sqls = _go(world, go)
    assert sum(_SUM in s for s in sqls) == 1
    assert sqls.count(store._ORG_LOCK_SQL) == 1


# ── h. Tenancy ──────────────────────────────────────────────────────────────


def test_r8_a_rule_and_a_sum_stay_in_their_package_and_org(world) -> None:
    """Four ledgers with the same field keys, rules and values: two objects of
    one package, one of a second package, and one of org B. A sum and a clash
    read one object, so the stock of another lends nothing, and the same sku
    clashes with nothing."""
    async def go() -> dict[str, Any]:
        a = await _ledger(world.a, world.pkg_a, [_STOCK, _ONE_SKU])
        twin = await _ledger(world.a, world.pkg_a, [_STOCK, _ONE_SKU], name="twin")
        a2 = await _ledger(world.a, world.pkg_a2, [_STOCK, _ONE_SKU])
        b = await _ledger(world.b, world.pkg_b, [_STOCK, _ONE_SKU])
        await _put(world.a, a, _move(a, "s", "a", 1))
        await _put(world.a, twin, _move(twin, "s", "a", 100))
        await _put(world.a, a2, _move(a2, "s", "a", 100))
        await _put(world.b, b, _move(b, "s", "a", 100))
        async with tenant_session(world.a) as db:
            await store.set_constraints(db, a.id, [_STOCK], package_id=world.pkg_a)
        low = await _refused(world.a, a, _move(a, "s", "a", -2))
        return {"a": a, "twin": twin, "a2": a2, "b": b, "low": low}

    out = _go(world, go)
    assert isinstance(out["low"].cause, ConstraintViolation)
    for key, stock in (("a", 1), ("twin", 100), ("a2", 100), ("b", 100)):
        assert _sum(world, out[key], "s") == stock, key


def test_r8_a_batch_of_one_package_writes_no_object_of_another(world) -> None:
    async def go() -> tuple[BatchError, BatchError, store.StudioObject, store.StudioObject]:
        a = await _ledger(world.a, world.pkg_a)
        a2 = await _ledger(world.a, world.pkg_a2)
        rec = (await _put(world.a, a2, _move(a2, "s", "a", 1)))[0]
        async with tenant_session(world.a) as db:
            with pytest.raises(BatchError) as other_object:
                await store.apply_writes(db, ANA, package_id=world.pkg_a,
                                         writes=[_move(a2, "s", "a", 1)])
            with pytest.raises(BatchError) as other_record:
                await store.apply_writes(db, ANA, package_id=world.pkg_a, writes=[
                    Write("delete", a2.id, rec.id)])
        return other_object.value, other_record.value, a, a2

    other_object, other_record, _, a2 = _go(world, go)
    assert isinstance(other_object.cause, store.RecordNotFound)
    assert isinstance(other_record.cause, store.RecordNotFound)
    assert _rows(world, a2) == 1


def test_r8_org_b_reads_no_rule_and_writes_nothing_of_org_a(world) -> None:
    async def go() -> dict[str, Any]:
        a = await _ledger(world.a, world.pkg_a)
        await _put(world.a, a, _move(a, "s", "a", 10))
        out: dict[str, Any] = {"a": a}
        async with tenant_session(world.b) as db:
            out["read"] = await store.get_object(db, a.id)
            out["set"] = await store.set_constraints(db, a.id, [], package_id=a.package_id)
            with pytest.raises(BatchError) as write:
                await store.apply_writes(db, ANA, package_id=a.package_id,
                                         writes=[_move(a, "s", "a", 1)])
            sql, params = store.build_group_sum(
                studio_query.parse_query(
                    [sf.parse_field_spec(f) for f in _LEDGER],
                    [{"field": "sku", "op": "eq", "value": "s"}]).conditions,
                "qty", object_id=a.id, package_id=a.package_id)
            out["sum"] = (await db.execute(text(sql), params)).scalar_one()
            out["write"] = write.value
        return out

    out = _go(world, go)
    assert out["read"] is None and out["set"] is None
    assert isinstance(out["write"].cause, store.RecordNotFound)
    assert out["sum"] is None, "the same bind in org B reads no row of org A"
    assert [r.name for r in out["a"].constraints] == ["stock_not_negative"]
    assert _rows(world, out["a"]) == 1


def test_r8_set_constraints_reads_one_package_and_one_org(world) -> None:
    async def go() -> tuple[Any, Any]:
        obj = await _ledger(world.a, world.pkg_a)
        async with tenant_session(world.a) as db:
            wrong = await store.set_constraints(db, obj.id, [], package_id=world.pkg_a2)
            kept = await store.get_object(db, obj.id)
        return wrong, kept

    wrong, kept = _go(world, go)
    assert wrong is None
    assert [r.name for r in kept.constraints] == ["stock_not_negative"]


# ── i. The plans on 50,000 rows ─────────────────────────────────────────────

BIG = 50_000
_BIG_FIELDS: list[dict[str, Any]] = [
    {"key": "sku", "type": "text", "hot": True},
    {"key": "site", "type": "text", "hot": True},
    {"key": "bin", "type": "number", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
]
#: 100 skus with 500 rows each, 5 sites, and 10 bins with 5,000 rows each.
_BIG_RECORDS_SQL = (
    "INSERT INTO studio_records (object_id, id, data, created_by, updated_by) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), "
    "jsonb_build_object('sku', 'sku-' || (g % 100), 'site', 'site-' || (g % 5), "
    "'bin', g % 10, 'qty', 1), :a, :a FROM generate_series(1, :big) g"
)
_BIG_HOT_SQL = (
    "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_text, v_num) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), f.k, "
    "CASE f.k WHEN 'sku' THEN 'sku-' || (g % 100) WHEN 'site' THEN 'site-' || (g % 5) END, "
    "CASE f.k WHEN 'bin' THEN g % 10 WHEN 'qty' THEN 1 END "
    "FROM generate_series(1, :big) g "
    "CROSS JOIN (VALUES ('sku'), ('site'), ('bin'), ('qty')) f(k)"
)
_BY_SKU = {"name": "by_sku", "kind": "group_sum_min", "field": "qty",
           "group_by": ["sku", "site"], "min": 0}
_BY_BIN = {"name": "by_bin", "kind": "group_sum_min", "field": "qty", "group_by": ["bin"], "min": 0}


@pytest.fixture(scope="module")
def big(promoted):  # noqa: F811
    """A ledger of 50,000 rows and a plain object, in a package of org B."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs = _packages(p, run, (("big", p.org_b),))

    async def seed() -> SimpleNamespace:
        obj = await _ledger(p.org_b, pkgs["big"], [_BY_SKU, _BY_BIN], name="big", fields=_BIG_FIELDS)
        plain = await _ledger(p.org_b, pkgs["big"], [], name="plain")
        async with tenant_session(p.org_b) as db:
            await db.execute(text(_BIG_RECORDS_SQL), {"oid": obj.id, "a": ANA, "big": BIG})
            await db.execute(text(_BIG_HOT_SQL), {"oid": obj.id, "big": BIG})
        return SimpleNamespace(obj=obj, plain=plain)

    async def go() -> SimpleNamespace:
        async with tenant_engine_scope(_dsn(p.app_url)):
            return await seed()

    ns = asyncio.run(go())
    with p.admin_engine.begin() as c:
        c.execute(text("ANALYZE studio_objects"))
        c.execute(text("ANALYZE studio_records"))
        c.execute(text("ANALYZE studio_record_hot"))
    ns.p, ns.org = p, p.org_b
    yield ns
    _drop_packages(p, run, list(pkgs.values()))


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    out = [plan]
    for child in plan.get("Plans", []):
        out += _nodes(child)
    return out


def _explain(big: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async def go() -> Any:
        async with tenant_session(big.org) as db:
            return (await db.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) " + sql), params)).scalar_one()

    raw = _go(big, go)
    doc = json.loads(raw) if isinstance(raw, str) else raw
    return _nodes(doc[0]["Plan"])


def _rows_read(nodes: list[dict[str, Any]]) -> int:
    """The rows that the scans of `studio_record_hot` examined. A Bitmap Index
    Scan feeds its heap scan, so only the heap scan counts."""
    total = 0
    for n in nodes:
        hot = (n.get("Relation Name") == "studio_record_hot"
               or str(n.get("Index Name", "")).startswith("studio_record_hot"))
        if hot and n["Node Type"] != "Bitmap Index Scan":
            total += ((int(n["Actual Rows"]) + int(n.get("Rows Removed by Filter", 0))
                       + int(n.get("Rows Removed by Index Recheck", 0))) * int(n["Actual Loops"]))
    return total


def _no_seq_scan(nodes: list[dict[str, Any]]) -> None:
    """The two tables that grow with the records. The planner may scan the
    small `studio_objects` for the package check, as the C2a suite allows."""
    tables = ("studio_records", "studio_record_hot")
    seq = [n["Relation Name"] for n in nodes
           if n["Node Type"] == "Seq Scan" and n.get("Relation Name") in tables]
    assert seq == [], seq


def _conditions(big: Any, rule: dict[str, Any], key: tuple[Any, ...]) -> Any:
    parsed = sr.parse_rule(rule)
    fields = [sf.parse_field_spec(f) for f in _BIG_FIELDS]
    return studio_query.parse_query(fields, sr.key_filter(parsed, key)).conditions


def test_r8_a_sum_reads_the_hot_rows_of_its_group_on_50000_rows(big) -> None:
    """A text key has an index condition, so the sum reads the rows of its
    group: 500 rows of `sku`, and one lookup of `site` and of `qty` for each."""
    sql, params = store.build_group_sum(
        _conditions(big, _BY_SKU, ("sku-7", "site-2")), "qty",
        object_id=big.obj.id, package_id=big.obj.package_id)
    nodes = _explain(big, sql, params)
    _no_seq_scan(nodes)
    assert not any(n.get("Relation Name") == "studio_records" for n in nodes), nodes
    assert _rows_read(nodes) <= 3 * 500 + 10, _rows_read(nodes)


def test_r8_a_sum_on_a_number_key_reads_at_most_the_rows_of_its_field(big) -> None:
    """No `numeric` comparison is LEAKPROOF, so a number key reads the hot
    rows of its field, and then one lookup for each row of the group."""
    sql, params = store.build_group_sum(
        _conditions(big, _BY_BIN, (3,)), "qty",
        object_id=big.obj.id, package_id=big.obj.package_id)
    nodes = _explain(big, sql, params)
    _no_seq_scan(nodes)
    assert not any(n.get("Relation Name") == "studio_records" for n in nodes), nodes
    assert _rows_read(nodes) <= BIG + 5_000 + 10, _rows_read(nodes)


def test_r8_a_unique_check_stops_at_the_second_row_on_50000_rows(big) -> None:
    rule = {"name": "u", "kind": "unique", "fields": ["sku"]}
    sql, params = store.build_count(
        _conditions(big, rule, ("sku-7",)), object_id=big.obj.id,
        package_id=big.obj.package_id, cap=2)
    nodes = _explain(big, sql, params)
    _no_seq_scan(nodes)
    assert _rows_read(nodes) <= 2, _rows_read(nodes)


def test_r8_a_check_on_a_big_ledger_passes_inside_its_deadline(big) -> None:
    """A real batch on the big ledger, rolled back at the end by the error."""
    obj = big.obj

    class Stop(Exception):
        pass

    async def go() -> float:
        started = time.monotonic()
        try:
            async with tenant_session(big.org) as db:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    Write("create", obj.id, data={"sku": "sku-7", "site": "site-2", "bin": 3, "qty": -1})])
                raise Stop
        except Stop:
            pass
        return time.monotonic() - started

    assert _go(big, go) < studio_query.TIMEOUT_MS / 1000


WHALE = 200_000
_WHALE_RECORDS_SQL = (
    "INSERT INTO studio_records (object_id, id, data, created_by, updated_by) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), "
    "jsonb_build_object('sku', 'one', 'qty', 1), :a, :a FROM generate_series(1, :n) g"
)
_WHALE_HOT_SQL = (
    "INSERT INTO studio_record_hot (object_id, record_id, field_key, v_text, v_num) "
    "SELECT CAST(:oid AS uuid), CAST(md5(CAST(:oid AS text) || '-' || g) AS uuid), f.k, "
    "CASE f.k WHEN 'sku' THEN 'one' END, CASE f.k WHEN 'qty' THEN 1 END "
    "FROM generate_series(1, :n) g CROSS JOIN (VALUES ('sku'), ('qty')) f(k)"
)


def test_r8_a_positive_write_on_200000_rows_reads_no_sum(promoted) -> None:  # noqa: F811
    """The cost that the skip removes. A receipt on a group of 200,000 rows
    runs no sum, and a write that lowers the same group runs one. The rows stay
    until the fixture drops the database, because a cascade of them takes
    minutes and proves nothing here."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    pkgs = _packages(p, run, (("whale", p.org_b),))
    rule = {"name": "floor", "kind": "group_sum_min", "field": "qty", "group_by": ["sku"], "min": 0}
    fields = [{"key": "sku", "type": "text", "hot": True},
              {"key": "qty", "type": "number", "hot": True}]

    async def sums(obj: store.StudioObject, qty: int) -> int:
        async with tenant_session(p.org_b) as db:
            spy = _Spy(db)
            await store.apply_writes(spy, ANA, package_id=obj.package_id, writes=[
                Write("create", obj.id, data={"sku": "one", "qty": qty})])
        return sum(_SUM in s for s, _ in spy.seen)

    async def go() -> tuple[int, int]:
        async with tenant_engine_scope(_dsn(p.app_url)):
            obj = await _ledger(p.org_b, pkgs["whale"], [rule], name="whale", fields=fields)
            async with tenant_session(p.org_b) as db:
                await db.execute(text(_WHALE_RECORDS_SQL), {"oid": obj.id, "a": ANA, "n": WHALE})
                await db.execute(text(_WHALE_HOT_SQL), {"oid": obj.id, "n": WHALE})
            with p.admin_engine.begin() as c:
                c.execute(text("ANALYZE studio_records"))
                c.execute(text("ANALYZE studio_record_hot"))
            return await sums(obj, 1), await sums(obj, -1)

    assert asyncio.run(go()) == (0, 1)


def test_r8_a_timeout_in_a_check_refuses_the_batch_and_keeps_the_session(big, monkeypatch) -> None:
    """At 1 ms, a check statement stops. The clock stands still, so the store
    gives the statement its 1 ms and Postgres cancels it. The batch writes
    nothing, the caller's row stays, and the old timeout is back.

    The sum statement waits 50 ms in Postgres first. A real sum of a new group
    can end inside 1 ms on a fast machine, and then the rule refuses the write
    with a ConstraintViolation and no timeout, so the test would depend on the
    speed of the machine."""
    monkeypatch.setattr(studio_query, "TIMEOUT_MS", 1)
    monkeypatch.setattr(store, "_clock", lambda: 0.0)
    real_sum = store.build_group_sum

    def slow_sum(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        sql, params = real_sum(*args, **kwargs)
        return f"SELECT x.* FROM pg_sleep(0.05) CROSS JOIN ({sql}) x", params

    monkeypatch.setattr(store, "build_group_sum", slow_sum)
    obj, plain = big.obj, big.plain

    async def go() -> tuple[BatchError, Any, str]:
        async with tenant_session(big.org) as db:
            await db.execute(text("SELECT set_config('statement_timeout', '42s', true)"))
            kept = await store.create_record(db, ANA, plain.id, {"sku": "kept"},
                                             package_id=plain.package_id)
            with pytest.raises(BatchError) as stopped:
                await store.apply_writes(db, ANA, package_id=obj.package_id, writes=[
                    Write("create", obj.id, data={"sku": "t", "site": "t", "bin": 3, "qty": -1})])
            read = await store.get_record(db, plain.id, kept.id, package_id=plain.package_id)
            timeout = str((await db.execute(
                text("SELECT current_setting('statement_timeout')"))).scalar_one())
            await db.execute(text("DELETE FROM studio_records WHERE id = CAST(:r AS uuid)"),
                             {"r": kept.id})
        return stopped.value, read, timeout

    stopped, read, timeout = _go(big, go)
    assert isinstance(stopped.cause, QueryError) and stopped.cause.code == "query_timeout"
    assert "took too long" in str(stopped) and "filter" not in str(stopped)
    assert read is not None and timeout == "42s"
    assert _rows(big, obj) == BIG
