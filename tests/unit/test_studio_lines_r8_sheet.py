"""D17a R8: the sheet sends an update whose parent data holds ONLY the lines key."""
from __future__ import annotations

from tests.unit import test_studio_lines_r8 as base
from tests.unit.test_studio_lines_r8 import (  # noqa: F401
    _counts, _direct, _order_with, _record, _records, _run, app_engine, promoted, world,
)

pytestmark = base.pytestmark


def test_r8_sheet_update_with_only_the_lines_key_saves_and_rolls_up(world) -> None:
    w = world

    async def go():
        made = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 5}}, {"data": {"qty": 7}}])
        first = made["lines"][0]
        res = await _direct(w.a, w.pkg, "change", {"id": made["record"]["id"], "data": {
            "lines": [{"data": {"qty": 3}}, {"id": first, "data": {"qty": 10}}]}})
        return made, res

    made, (status, got) = _run(w, go)
    assert status == 200, got
    rid = made["record"]["id"]
    assert got["record"]["data"] == {"title": "Bike", "total": 20}
    assert sorted(r.data["qty"] for r in _records(w, w.line.id)) == [3, 7, 10]
    assert _record(w, rid).data["total"] == 20


def test_r8_sheet_update_with_only_the_lines_key_refuses_a_broken_rollup(world) -> None:
    w = world

    async def go():
        made = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 5}}])
        before = _counts(w)
        snap = sorted((r.id, r.data["qty"]) for r in _records(w, w.line.id))
        res = await _direct(w.a, w.pkg, "change", {"id": made["record"]["id"], "data": {
            "lines": [{"data": {"qty": 1000.5}}]}})
        return made, before, snap, res

    made, before, snap, (status, got) = _run(w, go)
    assert status == 422 and got["code"] == "rule_refused" and got["rule"] == "total_ok", got
    assert _counts(w) == before
    assert sorted((r.id, r.data["qty"]) for r in _records(w, w.line.id)) == snap
    assert _record(w, made["record"]["id"]).data["total"] == 5


def test_r8_sheet_update_with_an_empty_lines_list_is_a_quiet_no_op(world) -> None:
    w = world

    async def go():
        made = await _order_with(w, w.a, w.pkg, [{"data": {"qty": 5}}])
        return await _direct(w.a, w.pkg, "change", {"id": made["record"]["id"], "data": {"lines": []}})

    status, got = _run(w, go)
    assert status == 200, got
    assert got["record"]["data"] == {"title": "Bike", "total": 5}
