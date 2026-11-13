"""WS-STU E8a: the batch ledger on a real database (R8, task 3095).

Spec: ``project-docs/specs/ai_studio.md`` §14 E8a. Code:
``gateway/studio/batch_store.py`` and migration 431 (``studio_batches`` and
``studio_batch_items``). ``test_studio_batch.py`` holds the hermetic half.

The store runs on a phase-4 promoted scratch database, as the app role with no
BYPASSRLS, so FORCE RLS binds each statement. Two organizations. One chunk is
one ``tenant_session``, as a driver of E8b runs it.

Run (a PRIVATE ladder database, one R8 suite at a time, because the
``promoted`` fixture drops ``<db>_h3rls``)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_batch_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**Acceptance (E8a).**

1. A fault inside chunk 3 at item 120 of 300 leaves 100 records and 200
   pending. A second drive ends with exactly 300 records, one for each key. The
   fault is tried after the write and after the status of the chunk.
2. Two drivers of one batch write no item twice.
3. A range refusal fails ONE item and the store writes the other 49 of the
   chunk. The next call continues past it.
4. An item with lines counts ``1 + n`` toward the 50, and a line keeps its own
   record as its parent.
5. Org B reads and drives no batch of org A.
6. A duplicate key and a delete are refused at open, and by the CHECKs of the
   table.
7. A digest mismatch writes nothing.
8. A busy org is a retry signal: it marks no item.

**The mutations (R7).** Commit before each, and restore with git after. The
result of each was run on 2026-11-13.

* E1. ``batch_store.run_chunk``: ``await db.commit()`` right after
  ``apply_writes`` (the status in a second transaction).
  ``test_r8_a_fault_in_chunk_3_leaves_100_records_and_a_second_drive_ends_at_300``
  goes red for the case ``after the status``.
* E2. ``_LOCK_BATCH_SQL``: drop ``FOR UPDATE``.
  ``test_r8_two_drivers_write_no_item_twice`` goes red.
* E3. ``_take``: count 1 for each item.
  ``test_r8_an_item_with_lines_counts_one_plus_n_toward_the_fifty`` goes red.
* E4. ``_shift``: return the writes with no move.
  The same test goes red, because the lines of the second order name the first.
* E5. ``run_chunk``: skip the digest check.
  ``test_r8_a_digest_mismatch_writes_nothing`` goes red.
* E6. ``run_chunk``: mark every item of the chunk ``failed`` on a ``BatchError``.
  ``test_r8_a_range_refusal_fails_one_item_and_the_store_writes_the_other_49`` goes red.
* E7. ``run_chunk``: do not rerun the other items after a refusal (re-raise).
  The same test goes red.
* E8. Drop ``_BOUND_ORG`` from ``_GET_BATCH_SQL``. RLS still hides the row from
  org B, so only the source fence
  ``test_each_select_and_update_names_the_org`` in ``test_studio_batch.py`` goes red.
* E9. ``run_chunk``: treat ``StoreBusy`` as a refusal of the item.
  ``test_r8_a_busy_org_is_a_retry_signal_and_marks_no_item`` goes red.
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
from gateway.studio import batch_store as bs
from gateway.studio import objects_store as store
from gateway.studio.batch_store import BatchItem
from gateway.studio.objects_store import StoreBusy, Write
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
TAG = uuid.uuid4().hex[:8]
ANA = f"ana.{TAG}@stue8a.test"

_LEDGER: list[dict[str, Any]] = [
    {"key": "sku", "type": "text", "hot": True},
    {"key": "qty", "type": "number", "hot": True},
    {"key": "note", "type": "text"},
]
_RANGE: dict[str, Any] = {"name": "qty_ok", "kind": "range", "field": "qty", "min": 0, "max": 100}
_ORDER: list[dict[str, Any]] = [
    {"key": "title", "type": "text"},
    {"key": "lines", "type": "relation", "to": "order_line", "lines": "order"},
]
_LINE: list[dict[str, Any]] = [
    {"key": "order", "type": "relation", "to": "order", "hot": True},
    {"key": "item", "type": "text"},
    {"key": "qty", "type": "number", "hot": True},
]
_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stue8a', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_MANIFEST = json.dumps({"studio": 1, "id": "stue8a", "name": "stue8a", "purpose": "The E8a R8 suite."})


def _dsn(url: Any) -> str:
    return url.render_as_string(hide_password=False)


@pytest.fixture
def world(promoted):  # noqa: F811
    """A package of org A and one of org B. Org A holds a ledger with a range
    rule and an order with its lines. Org B holds a ledger."""
    p = promoted
    run = uuid.uuid4().hex[:6]
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stue8a-{TAG}-{run}-{key}", "oe": ANA, "org": org,
                "manifest": _MANIFEST}).scalar_one())
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=ids["a"], pkg_b=ids["b"])

    async def setup() -> None:
        async with tenant_session(w.a) as db:
            ledger = await store.define_object(db, ANA, package_id=w.pkg_a, name="ledger",
                                               fields=_LEDGER)
            order = await store.define_object(db, ANA, package_id=w.pkg_a, name="order",
                                              fields=_ORDER)
            line = await store.define_object(db, ANA, package_id=w.pkg_a, name="order_line",
                                             fields=_LINE)
            assert ledger and order and line
            ruled = await store.set_constraints(db, ledger.id, [_RANGE], package_id=w.pkg_a)
        assert ruled is not None
        w.ledger, w.order, w.line = ruled, order, line
        async with tenant_session(w.b) as db:
            w.ledger_b = await store.define_object(db, ANA, package_id=w.pkg_b, name="ledger",
                                                   fields=_LEDGER)

    _go(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        pids = list(ids.values())
        c.execute(text("DELETE FROM studio_batches WHERE package_id = ANY(CAST(:i AS uuid[]))"),
                  {"i": pids})
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:i AS uuid[]))"),
                  {"i": pids})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stue8a-{TAG}-{run}-%"})


def _go(w: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            return await fn()
    return asyncio.run(go())


# -- helpers -----------------------------------------------------------------


def _items(obj: Any, n: int, *, qty: Any = 1) -> list[BatchItem]:
    """*n* ledger items. ``qty`` is a number or a function of the index."""
    out = []
    for i in range(n):
        q = qty(i) if callable(qty) else qty
        out.append(BatchItem(f"r{i:04d}", (
            Write("create", obj.id, data={"sku": f"s{i:04d}", "qty": q}),)))
    return out


def _by_key(items: list[BatchItem]) -> dict[str, tuple[Write, ...]]:
    return {i.key: i.writes for i in items}


async def _open(org: str, pkg: str, items: list[BatchItem], **kw: Any) -> bs.Batch:
    async with tenant_session(org) as db:
        return await bs.open_batch(db, ANA, package_id=pkg, action="load", items=items, **kw)


async def _chunk(org: str, bid: str, wbk: dict[str, Any]) -> bs.ChunkResult:
    """ONE chunk, ONE session, ONE transaction."""
    async with tenant_session(org) as db:
        return await bs.run_chunk(db, ANA, bid, wbk)


async def _drive(org: str, bid: str, wbk: dict[str, Any]) -> list[bs.ChunkResult]:
    """Chunk after chunk, one session at a time, until nothing is pending. A
    busy answer is tried again."""
    done: list[bs.ChunkResult] = []
    for _ in range(200):
        try:
            res = await _chunk(org, bid, wbk)
        except StoreBusy:
            await asyncio.sleep(0.05)
            continue
        done.append(res)
        if res.pending == 0:
            return done
    raise AssertionError("the drive did not end")


def _records(w: Any, obj: Any) -> list[SimpleNamespace]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT id::text, data FROM studio_records "
                              "WHERE object_id = CAST(:o AS uuid) ORDER BY created_at, id"),
                         {"o": obj.id}).all()
    return [SimpleNamespace(id=r[0], data=r[1] if isinstance(r[1], dict) else json.loads(r[1]))
            for r in rows]


def _item_rows(w: Any, bid: str) -> list[SimpleNamespace]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(text("SELECT item_key, status, record_id::text, error_code "
                              "FROM studio_batch_items WHERE batch_id = CAST(:b AS uuid) "
                              "ORDER BY position"), {"b": bid}).all()
    return [SimpleNamespace(key=r[0], status=r[1], record_id=r[2], code=r[3]) for r in rows]


def _count(w: Any, sql: str, **params: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


# -- 1. a crash in a chunk ----------------------------------------------------


@pytest.mark.parametrize("fault", ["after the write", "after the status"])
def test_r8_a_fault_in_chunk_3_leaves_100_records_and_a_second_drive_ends_at_300(
        world, monkeypatch, fault: str) -> None:
    """300 items, 50 to a chunk. The fault falls in chunk 3, at item 120. The
    first two chunks stay. The third is rolled back whole, status and all, so
    the second drive writes each of its items once."""
    w = world
    items = _items(w.ledger, 300)
    wbk = _by_key(items)
    hit = {"left": 1}
    real_apply, real_result = store.apply_writes, bs._result

    async def apply_then_fall(db: Any, actor: str, *, package_id: str, writes: Any) -> Any:
        out = await real_apply(db, actor, package_id=package_id, writes=writes)
        if hit["left"] and any(x.data["sku"] == "s0120" for x in writes):
            hit["left"] -= 1
            raise RuntimeError("the process died after the write")
        return out

    async def result_then_fall(db: Any, bid: str, written: dict, failed: dict) -> Any:
        out = await real_result(db, bid, written, failed)
        if hit["left"] and "r0120" in written:
            hit["left"] -= 1
            raise RuntimeError("the process died after the status")
        return out

    async def go() -> tuple[Any, ...]:
        batch = await _open(w.a, w.pkg_a, items)
        if fault == "after the write":
            monkeypatch.setattr(store, "apply_writes", apply_then_fall)
        else:
            monkeypatch.setattr(bs, "_result", result_then_fall)
        with pytest.raises(RuntimeError):
            await _drive(w.a, batch.id, wbk)
        monkeypatch.setattr(store, "apply_writes", real_apply)
        monkeypatch.setattr(bs, "_result", real_result)
        mid = (len(_records(w, w.ledger)), _count(
            w, "SELECT count(*) FROM studio_batch_items WHERE batch_id = CAST(:b AS uuid) "
               "AND status = 'pending'", b=batch.id))
        runs = await _drive(w.a, batch.id, wbk)
        async with tenant_session(w.a) as db:
            after = await bs.get_batch(db, batch.id)
        return batch, mid, runs, after

    batch, mid, runs, after = _go(w, go)
    assert hit["left"] == 0
    assert mid == (100, 200)
    assert len(_records(w, w.ledger)) == 300
    skus = [r.data["sku"] for r in _records(w, w.ledger)]
    assert len(set(skus)) == 300
    rows = _item_rows(w, batch.id)
    assert [r.status for r in rows] == ["done"] * 300
    assert len({r.record_id for r in rows}) == 300
    assert after is not None and (after.status, after.done, after.pending) == ("done", 300, 0)
    assert len(runs) == 4  # chunks 3 to 6


# -- 2. two drivers -----------------------------------------------------------


def test_r8_two_drivers_write_no_item_twice(world) -> None:
    """Two drivers, two sessions each in turn, start at one barrier. The row
    lock makes the second wait and then read what the first left."""
    w = world
    items = _items(w.ledger, 300)
    wbk = _by_key(items)

    async def go() -> tuple[Any, list[bs.ChunkResult], list[bs.ChunkResult]]:
        batch = await _open(w.a, w.pkg_a, items)
        gate = asyncio.Barrier(2)

        async def driver() -> list[bs.ChunkResult]:
            await gate.wait()
            return await _drive(w.a, batch.id, wbk)

        one, two = await asyncio.gather(driver(), driver())
        return batch, one, two

    batch, one, two = _go(w, go)
    keys_one = {k for r in one for k in r.written}
    keys_two = {k for r in two for k in r.written}
    assert not keys_one & keys_two
    assert keys_one | keys_two == {i.key for i in items}
    assert len(_records(w, w.ledger)) == 300
    assert len({r.data["sku"] for r in _records(w, w.ledger)}) == 300
    rows = _item_rows(w, batch.id)
    assert [r.status for r in rows] == ["done"] * 300 and len({r.record_id for r in rows}) == 300


# -- 3. one refusal fails one item ------------------------------------------


def test_r8_a_range_refusal_fails_one_item_and_the_store_writes_the_other_49(world) -> None:
    w = world
    items = _items(w.ledger, 60, qty=lambda i: 500 if i == 17 else 3)
    wbk = _by_key(items)

    async def go() -> tuple[bs.ChunkResult, bs.ChunkResult, Any]:
        batch = await _open(w.a, w.pkg_a, items)
        first = await _chunk(w.a, batch.id, wbk)
        mid = len(_records(w, w.ledger))
        second = await _chunk(w.a, batch.id, wbk)
        async with tenant_session(w.a) as db:
            got = await bs.get_batch(db, batch.id)
        return first, second, (batch, mid, got)

    first, second, (batch, mid, got) = _go(w, go)
    assert first.failed == {"r0017": "rule_refused"}
    assert len(first.written) == 49 and "r0017" not in first.written
    assert (first.pending, first.status) == (10, "open")
    assert mid == 49
    # The next call continues past the failed item.
    assert len(second.written) == 10 and second.failed == {}
    assert (second.pending, second.status) == (0, "failed")
    assert len(_records(w, w.ledger)) == 59
    assert got is not None and (got.done, got.failed, got.pending, got.status) == (59, 1, 0, "failed")
    bad = [r for r in _item_rows(w, batch.id) if r.status == "failed"]
    assert [(r.key, r.code, r.record_id) for r in bad] == [("r0017", "rule_refused", None)]


# -- 4. an item with lines ---------------------------------------------------


def test_r8_an_item_with_lines_counts_one_plus_n_toward_the_fifty(world) -> None:
    """12 orders of 4 lines are 5 writes each. A chunk takes 10 of them (50
    writes), and the 2 that are left run next. Each line keeps its own order."""
    w = world
    items = [BatchItem(f"o{i:02d}", (
        Write("create", w.order.id, data={"title": f"order {i}"}),
        *[Write("create", w.line.id, data={"item": f"o{i}-l{j}", "qty": j}, parent=0)
          for j in range(4)])) for i in range(12)]
    wbk = _by_key(items)

    async def go() -> tuple[bs.ChunkResult, bs.ChunkResult]:
        batch = await _open(w.a, w.pkg_a, items)
        assert [r.write_count for r in await _list(w.a, batch.id)] == [5] * 12
        return await _chunk(w.a, batch.id, wbk), await _chunk(w.a, batch.id, wbk)

    first, second = _go(w, go)
    assert len(first.written) == 10 and first.pending == 2
    assert len(second.written) == 2 and second.pending == 0 and second.status == "done"
    orders = {r.id: r.data["title"] for r in _records(w, w.order)}
    assert len(orders) == 12 and len(_records(w, w.line)) == 48
    # Each order holds its 4 lines, and only its own.
    for line in _records(w, w.line):
        owner = orders[line.data["order"]["id"]]
        assert line.data["item"].startswith("o" + owner.split()[1] + "-")
    for rid in {**first.written, **second.written}.values():
        assert sum(1 for ln in _records(w, w.line) if ln.data["order"]["id"] == rid) == 4


async def _list(org: str, bid: str) -> list[bs.ItemRow]:
    async with tenant_session(org) as db:
        return await bs.list_items(db, bid)


# -- 5. isolation ------------------------------------------------------------


def test_r8_org_b_reads_and_drives_no_batch_of_org_a(world) -> None:
    w = world
    items = _items(w.ledger, 3)
    wbk = _by_key(items)

    async def go() -> dict[str, Any]:
        batch = await _open(w.a, w.pkg_a, items)
        seen: dict[str, Any] = {"bid": batch.id}
        async with tenant_session(w.b) as db:
            seen["get"] = await bs.get_batch(db, batch.id)
            seen["items"] = await bs.list_items(db, batch.id)
        with pytest.raises(bs.BatchNotFound):
            await _chunk(w.b, batch.id, wbk)
        # Org B opens no batch on a package of org A: the key of the package names the org.
        with pytest.raises(DBAPIError):
            await _open(w.b, w.pkg_a, items)
        # Org B holds its own batch with the same item keys.
        own = await _open(w.b, w.pkg_b, _items(w.ledger_b, 3))
        seen["own"] = own.id
        res = await _chunk(w.b, own.id, _by_key(_items(w.ledger_b, 3)))
        seen["res"] = res
        return seen

    seen = _go(w, go)
    assert seen["get"] is None and seen["items"] == []
    assert [r.status for r in _item_rows(w, seen["bid"])] == ["pending"] * 3
    assert len(_records(w, w.ledger)) == 0
    assert len(seen["res"].written) == 3 and len(_records(w, w.ledger_b)) == 3
    assert _count(w, "SELECT count(*) FROM studio_batches WHERE package_id = CAST(:p AS uuid)",
                  p=w.pkg_a) == 1


# -- 6. refusals at open -----------------------------------------------------


def test_r8_a_duplicate_key_and_a_delete_are_refused_at_open_and_write_nothing(world) -> None:
    w = world
    one = _items(w.ledger, 1)[0]
    gone = BatchItem("d", (Write("delete", w.ledger.id, record_id=str(uuid.uuid4())),))

    async def go() -> None:
        for bad in ([one, one], [one, gone]):
            with pytest.raises(bs.BatchStoreError):
                await _open(w.a, w.pkg_a, bad)
        with pytest.raises(bs.BatchStoreError):
            await _open(w.a, w.pkg_a, [one], run_id=str(uuid.uuid4()))
        with pytest.raises(bs.DigestMismatch):
            await _open(w.a, w.pkg_a, [one], expect_digest="0" * 64)

    _go(w, go)
    assert _count(w, "SELECT count(*) FROM studio_batches WHERE package_id = CAST(:p AS uuid)",
                  p=w.pkg_a) == 0


def test_r8_the_table_refuses_a_delete_op_a_double_key_and_a_second_batch_for_a_node(world) -> None:
    """The CHECKs and keys hold when a caller skips the store."""
    w = world
    run, node = str(uuid.uuid4()), "node-1"

    async def go() -> list[str]:
        first = await _open(w.a, w.pkg_a, _items(w.ledger, 2), run_id=run, node_id=node)
        out = []
        with pytest.raises(DBAPIError):
            await _open(w.a, w.pkg_a, _items(w.ledger, 2), run_id=run, node_id=node)
        for sql in (
            "INSERT INTO studio_batch_items (batch_id, item_key, position, op, write_count, "
            "item_digest) VALUES (CAST(:b AS uuid), 'x', 9, 'delete', 1, repeat('a', 64))",
            "INSERT INTO studio_batch_items (batch_id, item_key, position, op, write_count, "
            "item_digest) VALUES (CAST(:b AS uuid), 'r0000', 9, 'create', 1, repeat('a', 64))",
        ):
            with pytest.raises(DBAPIError) as bad:
                async with tenant_session(w.a) as db:
                    await db.execute(text(sql), {"b": first.id})
            out.append(str(bad.value))
        return out

    delete, double = _go(w, go)
    assert "studio_batch_items_op" in delete
    assert "studio_batch_items_pkey" in double


# -- 7. a digest mismatch ----------------------------------------------------


def test_r8_a_digest_mismatch_writes_nothing(world) -> None:
    w = world
    items = _items(w.ledger, 5)
    changed = _by_key(items)
    changed["r0003"] = (Write("create", w.ledger.id, data={"sku": "s0003", "qty": 99}),)
    missing = {k: v for k, v in _by_key(items).items() if k != "r0004"}

    async def go() -> str:
        batch = await _open(w.a, w.pkg_a, items)
        for wrong in (changed, missing):
            with pytest.raises(bs.DigestMismatch):
                await _chunk(w.a, batch.id, wrong)
        return batch.id

    bid = _go(w, go)
    # Items 0 to 2 are right and come first, and still nothing was written.
    assert len(_records(w, w.ledger)) == 0
    assert [r.status for r in _item_rows(w, bid)] == ["pending"] * 5

    async def right() -> bs.ChunkResult:
        return await _chunk(w.a, bid, _by_key(items))

    assert len(_go(w, right).written) == 5 and len(_records(w, w.ledger)) == 5


# -- 8. a busy org -----------------------------------------------------------


def test_r8_a_busy_org_is_a_retry_signal_and_marks_no_item(world) -> None:
    w = world
    items = _items(w.ledger, 4)
    wbk = _by_key(items)

    async def go() -> str:
        batch = await _open(w.a, w.pkg_a, items)
        async with tenant_session(w.a) as holder:
            await store.lock_org(holder)
            with pytest.raises(StoreBusy):
                await _chunk(w.a, batch.id, wbk)
        return batch.id

    bid = _go(w, go)
    assert [(r.status, r.code) for r in _item_rows(w, bid)] == [("pending", None)] * 4
    assert len(_records(w, w.ledger)) == 0

    async def again() -> bs.ChunkResult:
        return await _chunk(w.a, bid, wbk)

    assert len(_go(w, again).written) == 4


# -- the CI guard ------------------------------------------------------------


def test_this_suite_is_named_in_both_ci_skip_guards() -> None:
    for name in ("pr-check.yml", "deploy.yml"):
        flow = (_ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        block = re.search(r"--must-run \\\n((?:[ \t]+tests/unit/\S+(?: \\)?\n)+)", flow)
        assert block, f"{name}: the --must-run argument list is missing"
        assert re.search(r"^[ \t]+tests/unit/test_studio_batch_r8\.py(?: \\)?$",
                         block.group(1), re.M), name
