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
    assert overflow == {"M"}, "the module of the drop that did not fit"
    assert set(out) == {"n1", "n2"}, "a re-add fits, a new id past the cap does not"
    out, overflow = recover.merge(kept, {"n2"}, {"n2": ("M", "p2")}, now=T)
    assert not overflow, "a re-add of a kept id never overflows"


def test_the_real_cap_is_5000() -> None:
    assert recover.CAP == 5_000
    assert timedelta(days=7) == recover.MAX_AGE
    assert timedelta(days=7) == recover.OVERFLOW_EVERY


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
        recover.OVERFLOW_MODULES_KEY: [], recover.TRIES_KEY: {}, recover.PAUSE_KEY: None,
    }
    for entity in ("company", "deal", "module:Install_Base"):
        assert engine._recover_state(ctx, entity) == {}
    kept_keys = {recover.KEY, recover.OVERFLOW_KEY, recover.OVERFLOW_MODULES_KEY,
                 recover.TRIES_KEY, recover.PAUSE_KEY}
    assert kept_keys <= engine._KEPT_WHEN_UNAVAILABLE


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


# ── Fix round 1 (review of 2026-10-16) ───────────────────────────────────────


def test_an_overflow_waits_while_a_module_it_names_is_unavailable() -> None:
    """``_mark_unavailable`` stamps ``initial_done_at``. A module that the
    account refuses must still hold the overflow, or a full read of the notes
    runs every 7 days for ever with no gain."""
    hidden = CursorRow("zoho:module:Install_Base", None,
                       {"unavailable": True, "reason": "hidden"}, NOW - timedelta(days=30), None)
    assert not engine._overflow_ready(_ready_ctx(module=hidden), "note", MIRRORED)


def test_the_recoverable_query_binds_the_org() -> None:
    """R7 fence of the ``organization_id`` predicate. RLS holds the tenant
    too, and this keeps the query safe on a session with RLS off."""
    import inspect

    assert "organization_id = CAST(:org AS uuid)" in engine._RECOVERABLE_SQL
    assert '"org": ctx.org' in inspect.getsource(engine._recover_activities)


def test_tries_and_the_pause_read_back() -> None:
    stored = {recover.TRIES_KEY: {"a": 2, "b": True, "c": 0, "d": "x"}}
    assert recover.tries_of(stored) == {"a": 2}
    assert recover.tries_of(None) == {}
    assert recover.paused(_iso(-timedelta(hours=23)), NOW)
    assert not recover.paused(_iso(-timedelta(hours=24)), NOW)
    assert not recover.paused(None, NOW) and not recover.paused("x", NOW)
    assert timedelta(hours=24) == recover.READ_PAUSE


def test_a_kept_id_leaves_at_the_retry_limit() -> None:
    ctx = _ctx()
    ctx.recover["note"] = {"n1": ["M", "p", T], "n2": ["M", "p", T], "n3": ["M", "p", T]}
    ctx.recover_tries["note"] = {"n1": engine.RETRY_LIMIT - 1}
    engine._spend_tries(ctx, "note", got={"n1", "n2", "gone-already"}, gone={"n3"})
    assert set(ctx.recover["note"]) == {"n2"}, "n1 at the limit, n3 gone in Zoho"
    assert ctx.recover_tries["note"] == {"n2": 1}


def test_a_recovery_read_takes_back_the_drop_of_this_run() -> None:
    ctx = _ctx()
    ctx.counts[engine.ACTIVITY_DROPS_KEY] = {"Install_Base": 2, "(none)": 1}
    ctx.run_drops = {("note", "n1"): "Install_Base", ("note", "n2"): "Install_Base"}
    engine._net_run_drops(ctx, "note", {"n1", "n9"})  # n9 dropped in an earlier run
    assert ctx.counts[engine.ACTIVITY_DROPS_KEY] == {"Install_Base": 1, "(none)": 1}
    engine._net_run_drops(ctx, "note", {"n1", "n2"})  # n1 is taken back once only
    assert ctx.counts[engine.ACTIVITY_DROPS_KEY] == {"(none)": 1}


# ── Fix round 2 (review of fix round 1, P1) ──────────────────────────────────


@pytest.mark.parametrize("why", ["unavailable", "held"])
def test_an_overflow_on_an_expired_list_still_checks_the_overflowed_module(why: str) -> None:
    """The list can expire in the run that OVERFLOW_EVERY lapses. An empty list
    names no module, so the modules of the overflowed drops hold the guard."""
    if why == "unavailable":
        module = CursorRow("zoho:module:Install_Base", None,
                           {"unavailable": True, "reason": "hidden"},
                           NOW - timedelta(days=30), None)
        ctx = _ready_ctx(module=module)
    else:
        ctx = _ready_ctx()  # it imported before its hold
        ctx.held = frozenset({"module:Install_Base"})
    ctx.recover["note"] = {}  # every entry expired in this run
    ctx.recover_overflow_modules["note"] = {"Install_Base"}
    assert not engine._overflow_ready(ctx, "note", MIRRORED)
    ctx.recover_overflow_modules["note"] = set()
    assert engine._overflow_ready(ctx, "note", MIRRORED), "nothing left to check"


def test_the_overflowed_modules_ride_in_the_state() -> None:
    state = {recover.OVERFLOW_MODULES_KEY: ["A", "", 3, "B"]}
    assert recover.overflow_modules_of(state) == {"A", "B"}
    assert recover.overflow_modules_of(None) == set()
    ctx = _ctx({"zoho:note": CursorRow("zoho:note", None, state, None, None)})
    assert engine._recover_state(ctx, "note")[recover.OVERFLOW_MODULES_KEY] == ["A", "B"]
