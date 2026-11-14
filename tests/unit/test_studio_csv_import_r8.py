"""WS-STU C11: the CSV import on a real database (R8, task 1419).

Code: ``gateway/studio/csv_import.py`` (the planner) over
``gateway/studio/batch_store.py``. ``test_studio_csv_import.py`` holds the
hermetic half. The route (``routes/studio/csv_import.py``) adds a session, a
switch and the owner check, and it calls exactly the calls that these tests
make: plan, ``open_batch`` with the digest, then ``run_chunk`` one session at a time.

Two organizations, the app role with no BYPASSRLS (FORCE RLS binds each
statement). Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:5434/<private db>
    uv run pytest tests/unit/test_studio_csv_import_r8.py -v -rs

Without the database every test SKIPS, and the run reads green. A skip is not a pass.

**Acceptance (C11, CSV half).**

1. 120 valid rows land as 120 records in chunks of 50, one for each row, with the
   values the planner showed in the preview.
2. A row that breaks a row rule (a range) fails ONE item. The other rows land.
3. The digest of two plans of one CSV is equal, and ``open_batch`` takes it. A
   digest of another CSV is refused, and nothing is opened.
4. Org B reads and drives no batch of org A, and a CSV into org B's object
   never writes a record of org A.
5. Each valid row is a record of the object of the package, and no other
   object gains a record.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from acb_common.studio_access import Viewer
from gateway.studio import batch_store as bs
from gateway.studio import csv_import as planner
from gateway.studio import objects_store as store
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
ANA = f"ana.{TAG}@stuc11.test"

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
    "format, manifest) VALUES (:s, 'stuc11', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc11", "name": "stuc11", "purpose": "The C11 R8 suite."}
)


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
            ids[key] = str(
                c.execute(
                    text(_PACKAGE_INSERT),
                    {
                        "s": f"stuc11-{TAG}-{run}-{key}",
                        "oe": ANA,
                        "org": org,
                        "manifest": _MANIFEST,
                    },
                ).scalar_one()
            )
    w = SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg_a=ids["a"], pkg_b=ids["b"])

    async def setup() -> None:
        async with tenant_session(w.a) as db:
            ledger = await store.define_object(
                db, ANA, package_id=w.pkg_a, name="ledger", fields=_LEDGER
            )
            order = await store.define_object(
                db, ANA, package_id=w.pkg_a, name="order", fields=_ORDER
            )
            line = await store.define_object(
                db, ANA, package_id=w.pkg_a, name="order_line", fields=_LINE
            )
            assert ledger and order and line
            ruled = await store.set_constraints(db, ledger.id, [_RANGE], package_id=w.pkg_a)
        assert ruled is not None
        w.ledger, w.order, w.line = ruled, order, line
        async with tenant_session(w.b) as db:
            w.ledger_b = await store.define_object(
                db, ANA, package_id=w.pkg_b, name="ledger", fields=_LEDGER
            )

    _go(w, setup)
    yield w
    with p.admin_engine.begin() as c:
        pids = list(ids.values())
        c.execute(
            text("DELETE FROM studio_batches WHERE package_id = ANY(CAST(:i AS uuid[]))"),
            {"i": pids},
        )
        c.execute(
            text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:i AS uuid[]))"),
            {"i": pids},
        )
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc11-{TAG}-{run}-%"})


def _go(w: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(_dsn(w.p.app_url)):
            return await fn()

    return asyncio.run(go())


# -- helpers -----------------------------------------------------------------

_CSV_HEAD = "SKU,Qty,Note\n"


def _csv(n: int, *, bad: dict[int, int] | None = None) -> str:
    bad = bad or {}
    return _CSV_HEAD + "".join(f"s{i:04d},{bad.get(i, 1)},n{i}\n" for i in range(n))


def _plan(obj: Any, text_: str) -> planner.Plan:
    table = planner.parse_csv(text_)
    return planner.plan_import(
        table, obj.fields, {"SKU": "sku", "Qty": "qty", "Note": "note"}, object_id=obj.id
    )


async def _open(org: str, pkg: str, plan: planner.Plan, **kw: Any) -> bs.Batch:
    async with tenant_session(org) as db:
        return await bs.open_batch(
            db, ANA, package_id=pkg, action=planner.ACTION, items=plan.items, **kw
        )


async def _drive(org: str, bid: str, plan: planner.Plan) -> list[bs.ChunkResult]:
    """Chunk after chunk, one session each, as the route runs them."""
    wbk = plan.writes_by_key()
    out: list[bs.ChunkResult] = []
    for _ in range(100):
        async with tenant_session(org) as db:
            res = await bs.run_chunk(db, ANA, bid, wbk, viewer=Viewer(ANA))
        out.append(res)
        if res.pending == 0:
            return out
    raise AssertionError("the drive did not end")


def _records(w: Any, obj: Any) -> list[dict[str, Any]]:
    with w.p.admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT data FROM studio_records "
                "WHERE object_id = CAST(:o AS uuid) ORDER BY created_at, id"
            ),
            {"o": obj.id},
        ).all()
    return [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]


def _total(w: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM studio_records r JOIN studio_objects o ON o.id = r.object_id "
                    "WHERE o.package_id = ANY(CAST(:p AS uuid[]))"
                ),
                {"p": [w.pkg_a, w.pkg_b]},
            ).scalar_one()
        )


# -- 1. rows land ---------------------------------------------------------------


def test_r8_120_rows_land_as_120_records_in_chunks_of_50(world) -> None:
    w = world
    plan = _plan(w.ledger, _csv(120))
    assert plan.valid_rows == 120 and plan.needs_approval

    async def go() -> Any:
        batch = await _open(w.a, w.pkg_a, plan, expect_digest=plan.digest(w.pkg_a))
        return await _drive(w.a, batch.id, plan)

    runs = _go(w, go)
    assert [len(r.written) for r in runs] == [50, 50, 20]
    by_sku = {r["sku"]: r for r in _records(w, w.ledger)}
    assert len(by_sku) == 120 and set(by_sku) == {f"s{i:04d}" for i in range(120)}
    assert by_sku["s0007"] == {"sku": "s0007", "qty": 1, "note": "n7"}
    assert plan.preview[0] == by_sku["s0000"]
    assert len(_records(w, w.order)) == 0 and len(_records(w, w.line)) == 0


# -- 2. a row rule fails one item ---------------------------------------------------


def test_r8_a_row_rule_fails_one_row_and_the_others_land(world) -> None:
    w = world
    plan = _plan(w.ledger, _csv(60, bad={17: 500}))  # the range rule is 0 to 100
    assert plan.valid_rows == 60

    async def go() -> list[bs.ChunkResult]:
        batch = await _open(w.a, w.pkg_a, plan)
        return await _drive(w.a, batch.id, plan)

    runs = _go(w, go)
    failed = {k: v for r in runs for k, v in r.failed.items()}
    assert list(failed) == ["row-18"]
    assert len(_records(w, w.ledger)) == 59


# -- 3. the digest binds the list ------------------------------------------------------


def test_r8_the_digest_is_stable_and_a_different_csv_is_refused(world) -> None:
    w = world
    one, again, other = (
        _plan(w.ledger, _csv(10)),
        _plan(w.ledger, _csv(10)),
        _plan(w.ledger, _csv(11)),
    )
    assert one.digest(w.pkg_a) == again.digest(w.pkg_a) != other.digest(w.pkg_a)

    async def go() -> None:
        with pytest.raises(bs.DigestMismatch):
            await _open(w.a, w.pkg_a, other, expect_digest=one.digest(w.pkg_a))
        batch = await _open(w.a, w.pkg_a, one, expect_digest=again.digest(w.pkg_a))
        assert batch.total == 10

    _go(w, go)
    assert _count_batches(w) == 1


def _count_batches(w: Any) -> int:
    with w.p.admin_engine.connect() as c:
        return int(
            c.execute(
                text(
                    "SELECT count(*) FROM studio_batches WHERE package_id = ANY(CAST(:p AS uuid[]))"
                ),
                {"p": [w.pkg_a, w.pkg_b]},
            ).scalar_one()
        )


# -- 4. two orgs -----------------------------------------------------------------------


def test_r8_org_b_drives_no_batch_of_org_a_and_writes_no_record_of_a(world) -> None:
    w = world
    plan_a = _plan(w.ledger, _csv(5))
    plan_b = _plan(w.ledger_b, _csv(3))

    async def go() -> None:
        batch = await _open(w.a, w.pkg_a, plan_a)
        async with tenant_session(w.b) as db:
            assert await bs.get_batch(db, batch.id) is None
        with pytest.raises(bs.BatchNotFound):
            await _drive(w.b, batch.id, plan_a)
        # A plan of org A's object, driven in org B's session, writes nothing in B.
        batch_b = await _open(w.b, w.pkg_b, plan_b)
        await _drive(w.b, batch_b.id, plan_b)

    _go(w, go)
    assert len(_records(w, w.ledger)) == 0  # the batch of A never ran
    assert len(_records(w, w.ledger_b)) == 3
    assert _total(w) == 3


# -- 5. a second send is found, and org B does not see it -----------------------------


def test_r8_find_recent_batch_finds_the_same_list_in_the_same_org_only(world) -> None:
    w = world
    plan = _plan(w.ledger, _csv(4))

    async def go() -> tuple[Any, ...]:
        before = None
        async with tenant_session(w.a) as db:
            before = await bs.find_recent_batch(
                db, package_id=w.pkg_a, action=planner.ACTION, digest=plan.digest(w.pkg_a)
            )
        batch = await _open(w.a, w.pkg_a, plan)
        async with tenant_session(w.a) as db:
            same = await bs.find_recent_batch(
                db, package_id=w.pkg_a, action=planner.ACTION, digest=plan.digest(w.pkg_a)
            )
            other = await bs.find_recent_batch(
                db, package_id=w.pkg_a, action=planner.ACTION, digest="0" * 64
            )
        async with tenant_session(w.b) as db:
            from_b = await bs.find_recent_batch(
                db, package_id=w.pkg_a, action=planner.ACTION, digest=plan.digest(w.pkg_a)
            )
        return before, batch.id, same, other, from_b

    before, bid, same, other, from_b = _go(w, go)
    assert before is None and same == bid and other is None and from_b is None
