"""Recover a dropped activity: the rules that need no database (WS-53 CRM-Q23b,
``crm_platform.md`` §18.10).

``tests/unit/test_crm_activity_recover_r8.py`` runs the engine on a real
Postgres. This file holds the list rules of ``crm_sync/recover.py`` and the
engine's choices that read no database: which drop is kept, and when an
overflow may queue a full read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from gateway.crm_sources.zoho import mapping
from gateway.crm_sync import engine, recover
from gateway.crm_sync.runs import CursorRow

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
T = NOW.isoformat()


def _iso(delta: timedelta) -> str:
    return (NOW + delta).isoformat()


# ── The list ─────────────────────────────────────────────────────────────────


def test_a_dropped_id_joins_and_a_stored_id_leaves() -> None:
    kept = {"n1": ["Install_Base", "i1", T], "n2": ["Install_Base", "i2", T]}
    out, overflow = recover.merge(
        kept, landed={"n1", "n2", "n3"}, dropped={"n2": ("Install_Base", "i2"),
                                                  "n3": ("Install_Base", "i3")},
        now=_iso(timedelta(hours=1)),
    )
    assert not overflow
    assert set(out) == {"n2", "n3"}, "n1 landed and linked, so it leaves"
    assert out["n3"] == ["Install_Base", "i3", _iso(timedelta(hours=1))]


def test_a_re_add_keeps_its_first_first_seen() -> None:
    first = _iso(-timedelta(days=6))
    kept = {"n1": ["Install_Base", "i1", first]}
    out, _ = recover.merge(kept, {"n1"}, {"n1": ("Install_Base", "i9")}, now=T)
    assert out["n1"] == ["Install_Base", "i9", first], "the parent moves, first_seen stays"


def test_an_id_that_failed_to_write_stays() -> None:
    kept = {"n1": ["Install_Base", "i1", T]}
    out, _ = recover.merge(kept, landed=set(), dropped={}, now=T)
    assert out == kept


def test_the_cap_keeps_the_old_entries_and_marks_an_overflow(monkeypatch) -> None:
    monkeypatch.setattr(recover, "CAP", 2)
    kept = {"n1": ["M", "p1", T], "n2": ["M", "p2", T]}
    out, overflow = recover.merge(
        kept, {"n2", "n3"}, {"n2": ("M", "p2"), "n3": ("M", "p3")}, now=T,
    )
    assert overflow
    assert set(out) == {"n1", "n2"}, "a re-add fits, a new id past the cap does not"
    out, overflow = recover.merge(kept, {"n2"}, {"n2": ("M", "p2")}, now=T)
    assert not overflow, "a re-add of a kept id never overflows"


def test_the_real_cap_is_5000() -> None:
    assert recover.CAP == 5_000
    assert recover.MAX_AGE == timedelta(days=7)
    assert recover.OVERFLOW_EVERY == timedelta(days=7)


def test_an_entry_older_than_7_days_leaves() -> None:
    kept = {
        "old": ["M", "p1", _iso(-timedelta(days=7, seconds=1))],
        "edge": ["M", "p2", _iso(-timedelta(days=6, hours=23))],
        "bad": ["M", "p3", "not a time"],
    }
    assert set(recover.expire(kept, NOW)) == {"edge"}


def test_a_stored_state_of_a_wrong_shape_is_read_as_empty() -> None:
    state: dict[str, Any] = {recover.KEY: {
        "n1": ["M", "p1", T], "n2": ["M", "p2"], "n3": "x", "n4": ["M", "", T],
    }}
    assert recover.entries_of(state) == {"n1": ["M", "p1", T]}
    assert recover.entries_of(None) == {}
    assert recover.entries_of({recover.KEY: ["n1"]}) == {}


def test_only_a_parent_of_a_mirrored_module_is_asked_for() -> None:
    kept = {"n1": ["A", "p1", T], "n2": ["B", "p2", T], "n3": ["A", "p3", T]}
    assert recover.parents(kept, {"A"}) == {("A", "p1"), ("A", "p3")}
    present = {("A", "p3"), ("A", "p1")}
    kept["n1"][2] = _iso(timedelta(hours=1))
    assert recover.recoverable(kept, present) == ["n3", "n1"], "oldest first"
    assert recover.modules_named(kept) == {"A", "B"}


def test_an_overflow_queues_at_most_one_time_in_7_days() -> None:
    assert recover.overflow_due(None, NOW)
    assert not recover.overflow_due(_iso(-timedelta(days=6)), NOW)
    assert recover.overflow_due(_iso(-timedelta(days=7)), NOW)


# ── The engine's choices ─────────────────────────────────────────────────────


def _ctx(cursors: dict[str, CursorRow] | None = None) -> engine._Ctx:
    handle = engine.RunHandle(
        org="00000000-0000-0000-0000-000000000001", run_id="r", kind="incremental",
        connection_id="c", reconcile=frozenset(), cursors=cursors or {},
        counts=engine._blank_counts(), estimate=None,
    )
    ctx = engine._Ctx(handle, should_stop=lambda: False, credit_budget=None)
    ctx.started = NOW
    return ctx


def _done(module: str) -> CursorRow:
    return CursorRow(module, None, {}, NOW - timedelta(days=30), None)


def _mapped(entity: str, fields: dict[str, Any]) -> mapping.Mapped:
    return mapping.map_record(entity, {"id": "x", **fields}, modified_at=None, owners={})


@pytest.mark.parametrize(("fields", "kept"), [
    ({"Parent_Id": {"id": "i1"}, "$se_module": "Install_Base"}, ("Install_Base", "i1")),
    ({"Parent_Id": {"id": "g1"}, "$se_module": "Ghost"}, None),   # not mirrored
    ({"Parent_Id": {"id": "a1"}}, None),                           # no $se_module
    ({"Parent_Id": {"id": "a1"}, "$se_module": "Accounts"}, None),  # a core parent
])
def test_only_a_drop_on_a_mirrored_module_is_kept(
    monkeypatch, fields: dict[str, Any], kept: tuple[str, str] | None,
) -> None:
    ctx = _ctx()
    monkeypatch.setattr(engine, "_mirrored_modules", lambda _c: frozenset({"Install_Base"}))
    caches = engine._Caches()
    engine._note_dropped(ctx, _mapped("note", fields), "n1", caches)
    assert caches.dropped.get("n1") == kept


def test_a_call_keeps_the_what_id_and_not_the_person(monkeypatch) -> None:
    ctx = _ctx()
    monkeypatch.setattr(engine, "_mirrored_modules", lambda _c: frozenset({"Install_Base"}))
    caches = engine._Caches()
    mapped = _mapped("call", {"Who_Id": {"id": "w1"}, "What_Id": {"id": "i7"},
                              "$se_module": "Install_Base"})
    engine._note_dropped(ctx, mapped, "c1", caches)
    assert caches.dropped == {"c1": ("Install_Base", "i7")}


def test_a_task_with_no_parent_is_never_kept(monkeypatch) -> None:
    """DEWiN's 1,145 tasks name no ``What_Id`` and no ``$se_module``."""
    ctx = _ctx()
    monkeypatch.setattr(engine, "_mirrored_modules", lambda _c: frozenset({"Install_Base"}))
    caches = engine._Caches()
    engine._note_dropped(ctx, _mapped("task", {"Subject": "x"}), "t1", caches)
    assert caches.dropped == {}


def test_the_recover_keys_ride_on_activity_cursors_only() -> None:
    ctx = _ctx({"zoho:note": CursorRow("zoho:note", None, {
        recover.KEY: {"n1": ["M", "p", T]}, recover.OVERFLOW_KEY: True}, None, None)})
    assert engine._recover_state(ctx, "note") == {
        recover.KEY: {"n1": ["M", "p", T]}, recover.OVERFLOW_KEY: True,
    }
    for entity in ("company", "deal", "module:Install_Base"):
        assert engine._recover_state(ctx, entity) == {}
    assert {recover.KEY, recover.OVERFLOW_KEY} <= engine._KEPT_WHEN_UNAVAILABLE


def _ready_ctx(**over: CursorRow | None) -> engine._Ctx:
    cursors: dict[str, CursorRow] = {
        "zoho:note": _done("zoho:note"),
        "zoho:module:Install_Base": _done("zoho:module:Install_Base"),
        engine.ACTIVITY_BACKFILL_ROW: CursorRow(
            engine.ACTIVITY_BACKFILL_ROW, None, {"queued_at": T, "overflow_at": {}},
            NOW, None),
    }
    for key, row in over.items():
        name = {"note": "zoho:note", "module": "zoho:module:Install_Base",
                "marker": engine.ACTIVITY_BACKFILL_ROW}[key]
        if row is None:
            cursors.pop(name)
        else:
            cursors[name] = row
    ctx = _ctx(cursors)
    ctx.recover["note"] = {"n1": ["Install_Base", "i1", T]}
    ctx.recover_overflow["note"] = True
    return ctx


MIRRORED = frozenset({"Install_Base"})


def test_an_overflow_is_ready_when_both_imports_are_done() -> None:
    assert engine._overflow_ready(_ready_ctx(), "note", MIRRORED)


def test_an_overflow_waits_for_the_first_import_of_its_entity() -> None:
    ctx = _ready_ctx(note=CursorRow("zoho:note", None, {"in_progress": True}, None, None))
    assert not engine._overflow_ready(ctx, "note", MIRRORED)


def test_an_overflow_waits_for_each_mirrored_module_that_the_list_names() -> None:
    ctx = _ready_ctx(module=None)
    assert not engine._overflow_ready(ctx, "note", MIRRORED)
    # A module that the profile does not mirror any more never holds it.
    assert engine._overflow_ready(ctx, "note", frozenset())


def test_a_second_overflow_inside_7_days_is_not_ready() -> None:
    recent = CursorRow(engine.ACTIVITY_BACKFILL_ROW, None, {
        "queued_at": T, "overflow_at": {"note": _iso(-timedelta(days=6))}}, NOW, None)
    assert not engine._overflow_ready(_ready_ctx(marker=recent), "note", MIRRORED)
    old = CursorRow(engine.ACTIVITY_BACKFILL_ROW, None, {
        "queued_at": T, "overflow_at": {"note": _iso(-timedelta(days=8))}}, NOW, None)
    assert engine._overflow_ready(_ready_ctx(marker=old), "note", MIRRORED)
    assert not engine._overflow_ready(_ready_ctx(marker=None), "note", MIRRORED)
