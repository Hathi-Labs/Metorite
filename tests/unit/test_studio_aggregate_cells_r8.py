"""WS-STU C2d-b: the cells of a matrix on a real database (R8).

Spec: ``project-docs/specs/ai_studio.md`` §14, the C2d-b row. Code:
``objects_store.build_aggregate_cells`` and ``aggregate_records``. The pure half
is ``test_studio_aggregate_cells.py``. The door half (a viewer of an install, and
org B) is the ``c2d-b`` section of ``test_studio_action_door_r8.py``.

Run (a PRIVATE ladder database, one R8 suite at a time)::

    export TENANT_LADDER_DATABASE_URL=postgresql+psycopg://acb:acb@127.0.0.1:<port>/<private db>
    uv run pytest tests/unit/test_studio_aggregate_cells_r8.py -v -rs

Without the database every test here SKIPS, and the run reads green. A skip is
not a pass.

**The world.** A ``deal`` has a ``region`` and a ``stage`` (5 options each), a
``by`` person, ``amount`` and a hidden ``secret``. The ``scout`` role reads the
rows with ``by = $me``, and ``secret`` is hidden from it. A ``wide`` object has a
``kind`` (2 options) and a ``tag`` text. Its 25 rows hold 25 tags, and the
scout owns 3 of them. A ``narrow`` object holds 21 tags, all of the scout.

**The mutations (R7).** Apply each one in ``objects_store.py``, run this file,
and the named test goes red. Each result was run on 2026-11-14.

* H1. ``build_aggregate_cells``: leave out ``params["me"]``. The scout tests go red.
* H2. ``_refuse_hidden_aggregate``: check the first group only.
  ``test_r8_cells_a_hidden_key_is_refused_on_both_axes`` goes red.
* H3. ``build_aggregate_cells``: count the axes over all rows, with no row rule. ``test_r8_cells_the_cap_counts_only_the_rows_the_viewer_reads`` goes red.
* H4. ``_cell_rows``: leave out the cap check. The too_big tests go red.
* H6. `_cell_rows`: drop the per-cell currency check. The race test of a second currency goes red.
* H10. `_cell_rows`: compare a cell currency with strict equality, a None too. `test_r8_cells_a_cell_with_no_amount_is_not_a_race` goes red.
* H7. `_cell_rows`: drop the check that the cell counts equal the total. The retryable-error test goes red.
* H8. `_read_aggregate`: drop `_check_hot_or_small` on the cells. The cold-guard test goes red.
* H9. `build_aggregate_cells`: count the axis with count(DISTINCT x.k). The cap test of a missing value goes red.
* H5. ``_aggregate_result``: sort ``groups`` the other way round.
  ``test_r8_cells_a_5_by_5_matrix_gives_25_cells_with_the_counts_and_the_sums`` goes red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from acb_common.db import tenant_session
from acb_common.studio_access import Viewer
from acb_common.studio_query import MAX_AXIS_VALUES, QueryError
from gateway.studio import objects_store as store
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
OWNER = f"owner.{TAG}@stuc2db.test"
E1, E2 = (f"e{i}.{TAG}@stuc2db.test" for i in (1, 2))
AS_OWNER = Viewer(OWNER)
AS_SCOUT = Viewer(E1, frozenset({"scout"}))
AS_STRANGER = Viewer(f"x.{TAG}@stuc2db.test", frozenset({"intern"}))

REGIONS = ["north", "south", "east", "west", "centre"]
STAGES = ["new", "talk", "offer", "won", "lost"]
DEAL_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "region", "type": "select", "options": REGIONS, "hot": True},
    {"key": "stage", "type": "select", "options": STAGES, "hot": True},
    {"key": "amount", "type": "number", "hot": True},
    {"key": "secret", "type": "number", "hot": True},
]
DEAL_ACCESS: list[dict[str, Any]] = [
    {"role": "scout", "read": {"where": "by = $me"}, "hide": ["secret"]},
]
WIDE_FIELDS: list[dict[str, Any]] = [
    {"key": "by", "type": "person", "hot": True},
    {"key": "kind", "type": "select", "options": ["a", "b"], "hot": True},
    {"key": "tag", "type": "text", "hot": True},
]
WIDE_ACCESS: list[dict[str, Any]] = [{"role": "scout", "read": {"where": "by = $me"}}]
_COUNT_SUM = [{"op": "count"}, {"op": "sum", "field": "amount"}]
_GROUPS = [{"field": "region"}, {"field": "stage"}]

_PACKAGE_INSERT = (
    "INSERT INTO apps (slug, name, owner_email, workspace_path, organization_id, "
    "format, manifest) VALUES (:s, 'stuc2db', :oe, '', CAST(:org AS uuid), 'manifest', "
    "CAST(:manifest AS jsonb)) RETURNING id"
)
_PACKAGE_MANIFEST = json.dumps(
    {"studio": 1, "id": "stuc2db", "name": "stuc2db", "purpose": "The C2d-b R8 suite."})


def _run(p: Any, fn: Any) -> Any:
    async def go() -> Any:
        async with tenant_engine_scope(p.app_url.render_as_string(hide_password=False)):
            return await fn()
    return asyncio.run(go())


@pytest.fixture(scope="module")
def world(promoted):  # noqa: F811
    p = promoted
    ids: dict[str, str] = {}
    with p.admin_engine.begin() as c:
        for key, org in (("a", p.org_a), ("b", p.org_b)):
            ids[key] = str(c.execute(text(_PACKAGE_INSERT), {
                "s": f"stuc2db-{TAG}-{key}", "oe": OWNER, "org": org,
                "manifest": _PACKAGE_MANIFEST}).scalar_one())

    async def build() -> SimpleNamespace:
        pkg = ids["a"]
        async with tenant_session(p.org_a) as db:
            deal = await store.define_object(db, OWNER, package_id=pkg, name="deal",
                                             fields=DEAL_FIELDS)
            wide = await store.define_object(db, OWNER, package_id=pkg, name="wide",
                                             fields=WIDE_FIELDS)
            narrow = await store.define_object(db, OWNER, package_id=pkg, name="narrow",
                                               fields=WIDE_FIELDS)
            assert deal and wide and narrow
            assert await store.set_access(db, deal.id, DEAL_ACCESS, package_id=pkg)
            assert await store.set_access(db, wide.id, WIDE_ACCESS, package_id=pkg)
            assert await store.set_access(db, narrow.id, WIDE_ACCESS, package_id=pkg)
            # 25 deals: one for each pair, amount = 10 * region + stage, by E1 when the
            # region is even and E2 when it is odd. One more of (north, new) for E2.
            rows = [{"by": E1 if r % 2 == 0 else E2, "region": REGIONS[r], "stage": STAGES[s],
                     "amount": 10 * r + s, "secret": 1} for r in range(5) for s in range(5)]
            rows.append({"by": E2, "region": "north", "stage": "new", "amount": 100, "secret": 1})
            await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", deal.id, data=r) for r in rows])
            tags = [{"by": E1 if i < 3 else E2, "kind": "a" if i % 2 else "b", "tag": f"t{i}"}
                    for i in range(25)]
            await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", wide.id, data=r) for r in tags])
            await store.apply_writes(db, OWNER, package_id=pkg, viewer=AS_OWNER, writes=[
                Write("create", narrow.id, data={"by": E1, "kind": "a", "tag": f"n{i}"})
                for i in range(21)])
        return SimpleNamespace(p=p, a=p.org_a, b=p.org_b, pkg=pkg, pkg_b=ids["b"],
                               deal=deal, wide=wide, narrow=narrow)

    w = _run(p, build)
    yield w
    with p.admin_engine.begin() as c:
        c.execute(text("DELETE FROM studio_objects WHERE package_id = ANY(CAST(:ids AS uuid[]))"),
                  {"ids": list(ids.values())})
        c.execute(text("DELETE FROM apps WHERE slug LIKE :t"), {"t": f"stuc2db-{TAG}-%"})


def _agg(w: Any, viewer: Viewer, obj: Any, org: str | None = None, pkg: str | None = None,
         **query: Any) -> store.AggregateResult | None:
    async def go() -> store.AggregateResult | None:
        async with tenant_session(org or w.a) as db:
            return await store.aggregate_records(
                db, package_id=pkg or w.pkg, object_id=obj.id, viewer=viewer, **query)
    return _run(w.p, go)


def _cells(got: store.AggregateResult) -> dict[tuple[Any, Any], tuple[int, tuple[Any, ...]]]:
    return {(c.key, c.key2): (c.count, c.values) for c in got.cells}


def test_r8_cells_a_5_by_5_matrix_gives_25_cells_with_the_counts_and_the_sums(world) -> None:
    got = _agg(world, AS_OWNER, world.deal, group_by=_GROUPS, measures=_COUNT_SUM)
    assert got is not None and len(got.cells) == 25
    assert [(c.key, c.key2) for c in got.cells] == [(r, s) for r in REGIONS for s in STAGES], \
        "the order of the first key and then the second"
    by_pair = _cells(got)
    assert by_pair[("north", "new")] == (2, (2, 100))  # 0 and the extra 100
    assert by_pair[("east", "offer")] == (1, (1, 22))
    assert sum(c.count for c in got.cells) == got.total.count == 26
    assert sum(int(c.values[1]) for c in got.cells) == int(got.total.values[1])
    # `groups` and `total` are the groups of the first key, as for one key.
    one = _agg(world, AS_OWNER, world.deal, group_by={"field": "region"}, measures=_COUNT_SUM)
    assert one is not None and one.cells == ()
    assert [(g.key, g.count) for g in one.groups] == [
        ("north", 6), ("south", 5), ("east", 5), ("west", 5), ("centre", 5)]
    assert got.groups == one.groups and got.total == one.total
    assert (got.other, got.folded) == (None, 0)


def test_r8_cells_a_list_of_one_key_is_a_single_key(world) -> None:
    one = _agg(world, AS_OWNER, world.deal, group_by={"field": "region"}, measures=_COUNT_SUM)
    listed = _agg(world, AS_OWNER, world.deal, group_by=[{"field": "region"}],
                  measures=_COUNT_SUM)
    assert one == listed and listed is not None and listed.cells == ()


def test_r8_cells_a_missing_value_is_one_value_and_comes_last(world) -> None:
    async def go() -> None:
        async with tenant_session(world.a) as db:
            await store.apply_writes(db, OWNER, package_id=world.pkg, viewer=AS_OWNER, writes=[
                Write("create", world.deal.id, data={"by": E2, "region": "north"})])
    _run(world.p, go)
    got = _agg(world, AS_OWNER, world.deal, group_by=_GROUPS,
               filter=[{"field": "region", "op": "eq", "value": "north"}])
    assert got is not None
    assert [(c.key, c.key2) for c in got.cells][-1] == ("north", None)
    assert len(got.cells) == 6


def test_r8_cells_a_row_bound_viewer_gets_only_its_own_rows(world) -> None:
    got = _agg(world, AS_SCOUT, world.deal, group_by=_GROUPS, measures=_COUNT_SUM)
    assert got is not None
    # E1 holds the rows of the even regions: north, east and centre.
    assert {c.key for c in got.cells} == {"north", "east", "centre"}
    assert sum(c.count for c in got.cells) == got.total.count
    assert _cells(got)[("north", "new")] == (1, (1, 0)), "the extra row of E2 stays hidden"
    owner = _agg(world, AS_OWNER, world.deal, group_by=_GROUPS, measures=_COUNT_SUM)
    assert owner is not None and got.total.count < owner.total.count


def test_r8_cells_a_hidden_key_is_refused_on_both_axes(world) -> None:
    for groups in ([{"field": "region"}, {"field": "secret"}],
                   [{"field": "secret"}, {"field": "region"}]):
        with pytest.raises(QueryError) as refused:
            _agg(world, AS_SCOUT, world.deal, group_by=groups)
        assert refused.value.code == "query_op_not_allowed"
        assert refused.value.field == "secret"
    owner = _agg(world, AS_OWNER, world.deal, group_by=[{"field": "region"}, {"field": "secret"}])
    assert owner is not None and {c.key for c in owner.cells} == set(REGIONS)
    assert sum(c.count for c in owner.cells) == owner.total.count


def test_r8_cells_an_axis_past_the_cap_is_too_big_and_nothing_folds(world) -> None:
    assert MAX_AXIS_VALUES == 20
    for viewer in (AS_OWNER, AS_SCOUT):
        with pytest.raises(QueryError) as refused:
            _agg(world, viewer, world.narrow, group_by=[{"field": "kind"}, {"field": "tag"}])
        assert refused.value.code == "query_too_big"
        assert "Filter first" in refused.value.message
    with pytest.raises(QueryError) as swapped:
        _agg(world, AS_OWNER, world.narrow, group_by=[{"field": "tag"}, {"field": "kind"}])
    assert swapped.value.code == "query_too_big", "the first axis has a cap too"
    # With 20 values the read passes. A filter keeps 20 of the 21.
    ok = _agg(world, AS_OWNER, world.narrow, group_by=[{"field": "kind"}, {"field": "tag"}],
              filter=[{"field": "tag", "op": "ne", "value": "n0"}])
    assert ok is not None and len(ok.cells) == 20


def test_r8_cells_the_cap_counts_only_the_rows_the_viewer_reads(world) -> None:
    """The object holds 25 tags. The scout reads 3. The owner is refused."""
    groups = [{"field": "kind"}, {"field": "tag"}]
    with pytest.raises(QueryError) as refused:
        _agg(world, AS_OWNER, world.wide, group_by=groups)
    assert refused.value.code == "query_too_big"
    got = _agg(world, AS_SCOUT, world.wide, group_by=groups)
    assert got is not None and len(got.cells) == 3
    assert sorted(c.key2 for c in got.cells) == ["t0", "t1", "t2"]


def test_r8_cells_a_viewer_with_no_read_gets_no_cells(world) -> None:
    got = _agg(world, AS_STRANGER, world.deal, group_by=_GROUPS, measures=_COUNT_SUM)
    assert got is not None
    assert got.cells == () and got.groups == () and got.total.count == 0


def test_r8_cells_org_b_reads_nothing_of_org_a(world) -> None:
    assert _agg(world, AS_OWNER, world.deal, org=world.b, pkg=world.pkg_b,
                group_by=_GROUPS) is None


# ── The second statement: a money cell, a race and the guard ────────────────

_CASH_FIELDS: list[dict[str, Any]] = [
    {"key": "kind", "type": "select", "options": ["n", "m"], "hot": True},
    {"key": "region", "type": "select", "options": ["a", "b"], "hot": True},
    {"key": "amt", "type": "money", "hot": True},
    {"key": "day", "type": "date", "hot": True},
    {"key": "long", "type": "text", "hot": True},
    {"key": "tag", "type": "text", "hot": True},
    {"key": "note", "type": "text"},
]
_AMT = [{"op": "count"}, {"op": "sum", "field": "amt"}]
_KR = [{"field": "kind"}, {"field": "region"}]


def _money(minor: int, cur: str = "INR") -> dict[str, Any]:
    return {"minor": minor, "currency": cur}


_MADE: list[Any] = []


@pytest.fixture(autouse=True)
def _drop_made(world):
    """The org holds at most 24 hot fields, so each test frees its objects."""
    yield

    async def go() -> None:
        async with tenant_session(world.a) as db:
            for obj in _MADE:
                await store.drop_object(db, obj.id)
    if _MADE:
        _run(world.p, go)
        _MADE.clear()


def _make(w: Any, name: str, rows: list[dict[str, Any]]) -> Any:
    async def go() -> Any:
        async with tenant_session(w.a) as db:
            obj = await store.define_object(db, OWNER, package_id=w.pkg, name=name,
                                            fields=_CASH_FIELDS)
            assert obj
            _MADE.append(obj)
            await store.apply_writes(db, OWNER, package_id=w.pkg, viewer=AS_OWNER, writes=[
                Write("create", obj.id, data=r) for r in rows])
            return obj
    return _run(w.p, go)


def _insert(w: Any, obj: Any, data: dict[str, Any]) -> Any:
    async def go() -> None:
        async with tenant_session(w.a) as db:
            await store.apply_writes(db, OWNER, package_id=w.pkg, viewer=AS_OWNER, writes=[
                Write("create", obj.id, data=data)])
    return go()


def _race(monkeypatch: pytest.MonkeyPatch, w: Any, obj: Any, data: dict[str, Any]) -> list[int]:
    """Commit one record between the statement of the groups and the one of
    the cells, in another transaction, as a write of another session does."""
    hits: list[int] = []
    orig = store._Timed.execute

    async def racing(self: Any, stmt: Any, params: dict[str, Any]) -> Any:
        if "AS d1" in str(stmt) and not hits:
            hits.append(1)
            await _insert(w, obj, data)
        return await orig(self, stmt, params)
    monkeypatch.setattr(store._Timed, "execute", racing)
    return hits


def test_r8_cells_a_money_measure_with_two_keys_gives_the_sums_of_each_cell(world) -> None:
    obj = _make(world, "cash_sums", [
        {"kind": "n", "region": "a", "amt": _money(100)},
        {"kind": "n", "region": "a", "amt": _money(50)},
        {"kind": "n", "region": "b", "amt": _money(7)},
        {"kind": "m", "region": "a", "amt": _money(3)}])
    got = _agg(world, AS_OWNER, obj, group_by=_KR, measures=_AMT)
    assert got is not None and got.currencies == (None, "INR")
    assert _cells(got) == {("n", "a"): (2, (2, 150)), ("n", "b"): (1, (1, 7)),
                           ("m", "a"): (1, (1, 3))}
    assert got.total.values == (4, 160)


def test_r8_cells_a_cell_with_no_amount_is_not_a_race(world) -> None:
    obj = _make(world, "cash_noamt", [{"kind": "n", "region": "a", "amt": _money(100)},
                                      {"kind": "m", "region": "b"}])
    got = _agg(world, AS_OWNER, obj, group_by=_KR, measures=_AMT)
    assert got is not None and got.currencies == (None, "INR")
    assert _cells(got) == {("n", "a"): (1, (1, 100)), ("m", "b"): (1, (1, 0))}


def test_r8_cells_a_second_currency_between_the_statements_is_never_one_cell(
        world, monkeypatch) -> None:
    obj = _make(world, "cash_usd", [{"kind": "n", "region": "a", "amt": _money(100)}])
    hits = _race(monkeypatch, world, obj, {"kind": "n", "region": "a", "amt": _money(9999, "USD")})
    with pytest.raises(QueryError) as refused:
        _agg(world, AS_OWNER, obj, group_by=_KR, measures=_AMT)
    assert hits and refused.value.field == "amt"
    assert refused.value.code == "query_op_not_allowed"


def test_r8_cells_a_record_between_the_statements_is_a_retryable_error(
        world, monkeypatch) -> None:
    obj = _make(world, "cash_same", [{"kind": "n", "region": "a", "amt": _money(100)}])
    hits = _race(monkeypatch, world, obj, {"kind": "n", "region": "b", "amt": _money(5)})
    with pytest.raises(QueryError) as changed:
        _agg(world, AS_OWNER, obj, group_by=_KR, measures=_AMT)
    assert hits and changed.value.code == "query_timeout"
    assert "changed" in changed.value.message


def test_r8_cells_a_month_and_a_long_text_on_the_second_axis(world) -> None:
    long_a, long_b = "x" * 300, "y" * 300
    obj = _make(world, "cash_axes", [
        {"kind": "n", "day": "2026-01-05", "long": long_a},
        {"kind": "n", "day": "2026-01-20", "long": long_a},
        {"kind": "n", "day": "2026-02-03", "long": long_b},
        {"kind": "m", "long": long_b}])
    by_month = _agg(world, AS_OWNER, obj, group_by=[{"field": "kind"},
                                                    {"field": "day", "bucket": "month"}])
    assert by_month is not None
    assert _cells(by_month) == {("n", "2026-01"): (2, (2,)), ("n", "2026-02"): (1, (1,)),
                                ("m", None): (1, (1,))}
    by_text = _agg(world, AS_OWNER, obj, group_by=[{"field": "kind"}, {"field": "long"}])
    assert by_text is not None
    assert _cells(by_text) == {("n", long_a): (2, (2,)), ("n", long_b): (1, (1,)),
                               ("m", long_b): (1, (1,))}


def test_r8_cells_a_missing_value_counts_toward_the_cap_of_an_axis(world) -> None:
    rows = [{"kind": "n", "tag": f"t{i}"} for i in range(20)]
    over = _make(world, "cash_cap21", [*rows, {"kind": "n"}])
    with pytest.raises(QueryError) as big:
        _agg(world, AS_OWNER, over, group_by=[{"field": "kind"}, {"field": "tag"}])
    assert big.value.code == "query_too_big"
    with pytest.raises(QueryError) as swapped:
        _agg(world, AS_OWNER, over, group_by=[{"field": "tag"}, {"field": "kind"}])
    assert swapped.value.code == "query_too_big", "the first axis counts a missing value too"
    ok = _make(world, "cash_cap20", [*rows[:19], {"kind": "n"}])
    got = _agg(world, AS_OWNER, ok, group_by=[{"field": "kind"}, {"field": "tag"}])
    assert got is not None and len(got.cells) == 20
    assert [c.key2 for c in got.cells][-1] is None
    flip = _agg(world, AS_OWNER, ok, group_by=[{"field": "tag"}, {"field": "kind"}])
    assert flip is not None and len(flip.cells) == 20 and flip.cells[-1].key is None


def test_r8_cells_a_cold_guard_that_flips_between_the_statements_is_refused(
        world, monkeypatch) -> None:
    from acb_common import studio_query
    obj = _make(world, "cash_cold", [{"kind": "n", "region": "a", "note": "x"} for _ in range(3)])
    orig = store.build_aggregate_cells
    flips: list[int] = []

    def flipping(*args: Any, **kwargs: Any) -> Any:
        flips.append(1)
        monkeypatch.setattr(studio_query, "SMALL_OBJECT", 1)
        return orig(*args, **kwargs)
    monkeypatch.setattr(store, "build_aggregate_cells", flipping)
    with pytest.raises(QueryError) as refused:
        _agg(world, AS_OWNER, obj, group_by=_KR,
             filter=[{"field": "note", "op": "eq", "value": "x"}])
    assert flips and refused.value.code == "query_not_indexed"
