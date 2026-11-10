"""R8 — recover a dropped activity, against a real Postgres (WS-53 CRM-Q23b,
``crm_platform.md`` §18.10).

The activity passes run before the module pass. So a Zoho activity whose
parent is a generic module record that the run has not read yet finds no row,
and CRM-Q23a drops it. CRM-Q23b keeps such an activity in the cursor state of
its entity, reads it again by id once its parent is in, and queues a full read
for an overflow and, one time, for the records that sit behind the cursor.

What this proves, one group for each "Done when" item of CRM-Q23b, with the
non-privileged role of the phase-4 rehearsal:

1. the final-page state, the mid-read state and an unavailable module keep the
   list ``{id: [module, parent_ext_id, first_seen]}``;
2. nothing to recover costs no provider call; an entry older than 7 days
   leaves, and a re-add keeps its first ``first_seen``; the step skips a held
   key and stops at the reserve; 100 ids go in each call, and a short answer
   loses nothing;
3. an overflow queues one full read, a second overflow inside 7 days queues
   nothing, and an overflow waits for the first import of the module;
4. a new module record and a new note in one run link in that run;
5. the one-time full read of the activities, and a hold on ``activities``
   that keeps its entry until an admin releases it.

``tests/unit/test_crm_activity_recover.py`` holds the rules that need no
database. The fake Zoho is the one of the CRM-Q6a suite. Its read by ids has
the shape that a probe on a live tenant measured on 2026-10-16.
"""

# ruff: noqa: F811 - the fixtures come in by import and are used as arguments
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

from sqlalchemy import text

from tests.unit.test_crm_module_records_r8 import (  # noqa: F401
    _age_module_reads,
    _connect_all,
    _grant_all_scopes,
    _records,
    _run,
    _seed,
    mz,
)
from tests.unit.test_crm_sync_engine_r8 import (  # noqa: F401
    _connect,
    _q,
    _the_loop_is_not_stopping,
    ns,
    stamp,
)
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

# ⚠️ Every test here needs the real database. Without this gate the suite
# ERRORS where no ladder URL is set (the deploy run), and the deploy stops.
pytestmark = _DB_GATE

BACKFILL = "zoho:activity_backfill"
ACTIVITIES = {"note", "call", "meeting"}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _note(z: Any, ident: str, minute: int, parent: str, module: str = "Install_Base") -> None:
    z.add("Notes", ident, minute, Note_Title=f"{z.tag}-{ident}", Note_Content="Body",
          Parent_Id={"id": parent, "name": "x"}, **{"$se_module": module})


def _edit(z: Any, module: str, ident: str, minute: int) -> None:
    for row in z.data[module]:
        if row["id"] == ident:
            row["Modified_Time"] = stamp(minute)


def _state(p: Any, org: str, module: str = "zoho:note") -> dict[str, Any] | None:
    rows = _q(p, "SELECT cursor_state FROM crm_sync_cursors WHERE organization_id = "
                 "CAST(:o AS uuid) AND module = :m", o=org, m=module)
    return rows[0][0] if rows else None


def _kept(p: Any, org: str, module: str = "zoho:note") -> dict[str, list[str]]:
    return (_state(p, org, module) or {}).get("recover") or {}


def _rereads(p: Any, org: str) -> set[str]:
    return set((_state(p, org, "zoho:field_rules") or {}).get("reread") or ())


def _linked(p: Any, org: str) -> dict[str, str | None]:
    """The org's mirrored activities: ``ext_id`` -> ``module_record_id``."""
    rows = _q(p, "SELECT ext_id, module_record_id FROM crm_activities "
                 "WHERE organization_id = CAST(:o AS uuid)", o=org)
    return {r[0]: (str(r[1]) if r[1] is not None else None) for r in rows}


def _set_first_seen(p: Any, org: str, ext_id: str, days_ago: int) -> None:
    when = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "CAST(:path AS text[]), to_jsonb(CAST(:t AS text))) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:note'"),
            {"path": ["recover", ext_id, "2"], "t": when, "o": org})


def _age_overflow(p: Any, org: str, entity: str, days: int) -> None:
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "CAST(:path AS text[]), to_jsonb(CAST(:t AS text))) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = :m"),
            {"path": ["overflow_at", entity], "t": when, "o": org, "m": BACKFILL})


def _hold(p: Any, org: str, key: str, *, held: bool = True) -> None:
    entry = {key: {"held": held, "by": "admin", "count": None,
                   "at": datetime.now(UTC).isoformat()}}
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_settings SET sync_holds = sync_holds || CAST(:e AS jsonb) "
            "WHERE organization_id = CAST(:o AS uuid)"),
            {"e": json.dumps(entry), "o": org})


def _notes_since(z: Any, start: int) -> list[str | None]:
    """The ``If-Modified-Since`` of each changed-list read of Notes after
    ``start`` requests. ``None`` is a full read."""
    return [r.headers.get("If-Modified-Since") for r in z.requests[start:]
            if r.url.path == "/crm/v2/Notes" and "ids" not in r.url.params]


# ── 1. The list rides in the cursor state ────────────────────────────────────


async def test_the_final_page_keeps_a_drop_and_nothing_to_recover_costs_no_call(mz) -> None:
    p, a, _b = mz
    _seed(a)  # an Account a1 and the Install_Base record i1
    _note(a, "n1", 30, "i9")  # i9 is in no Zoho module
    _note(a, "n2", 31, "i9")
    a.add("Notes", "n3", 32, Note_Title="Linked", Parent_Id={"id": "a1", "name": "x"},
          **{"$se_module": "Accounts"})
    await _connect_all(p, p.org_a, a)
    started = datetime.now(UTC)
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)

    state = _state(p, p.org_a)
    assert state is not None and state["in_progress"] is False
    assert set(state["recover"]) == {"n1", "n2"}, "the stored n3 is never kept"
    module, parent, seen = state["recover"]["n1"]
    assert (module, parent) == ("Install_Base", "i9")
    assert started - timedelta(minutes=1) <= datetime.fromisoformat(seen) <= datetime.now(UTC)
    assert state["recover_overflow"] is False
    for other in ("zoho:call", "zoho:meeting", "zoho:task"):
        assert (_state(p, p.org_a, other) or {}).get("recover") == {}, other
    assert "recover" not in (_state(p, p.org_a, "zoho:company") or {}), "activities only"
    assert a.ids_reads == [], "no parent is in, so no call"

    second = await _run(p.org_a)
    assert second.status == "ok"
    assert a.ids_reads == [], "0 recoverable entries make 0 provider calls"
    assert _kept(p, p.org_a) == state["recover"], "the list and its first_seen stay"


async def test_the_mid_read_state_keeps_a_drop(mz) -> None:
    """The engine suite pages 2 records at a time. The second page of Notes
    fails, so the run stops after the first page committed."""
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note(a, f"n{i}", 30 + i, "i9")
    a.fail_after["Notes"] = 1
    await _connect_all(p, p.org_a, a)
    result = await _run(p.org_a)
    assert result.status == "failed"
    state = _state(p, p.org_a)
    assert state is not None and state["in_progress"] is True
    assert set(state["recover"]) == {"n0", "n1"}, "the page and its list move together"
    assert state["recover"]["n0"][:2] == ["Install_Base", "i9"]


async def test_an_unavailable_module_keeps_the_list(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i9")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    kept = _kept(p, p.org_a)
    assert set(kept) == {"n1"}

    a.faults["Notes"] = (400, {"code": "INVALID_MODULE", "status": "error"})
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    state = _state(p, p.org_a)
    assert state is not None and state["unavailable"] is True
    assert state["recover"] == kept, "_KEPT_WHEN_UNAVAILABLE keeps it"


# ── 2. The recovery step ─────────────────────────────────────────────────────


async def test_an_old_entry_leaves_and_a_re_add_keeps_its_first_seen(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i9")
    _note(a, "n2", 31, "i9")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    first = _kept(p, p.org_a)["n2"][2]

    _set_first_seen(p, p.org_a, "n1", days_ago=8)
    _edit(a, "Notes", "n2", 400)  # the pass reads n2 again and drops it again
    assert (await _run(p.org_a)).status == "ok"
    assert _kept(p, p.org_a) == {"n2": ["Install_Base", "i9", first]}

    # An entry that a re-add keeps alive still leaves at 7 days.
    _set_first_seen(p, p.org_a, "n2", days_ago=8)
    _edit(a, "Notes", "n2", 401)
    assert (await _run(p.org_a)).status == "ok"
    assert _kept(p, p.org_a) == {}
    assert a.ids_reads == []


async def test_a_held_key_keeps_its_list_until_release(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert set(_kept(p, p.org_a)) == {"n1"}

    _hold(p, p.org_a, "activities")
    a.add("Install_Base", "i2", 70, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)
    held = await _run(p.org_a)
    assert held.status == "ok" and held.counts["activities"]["held"] == 1
    assert ("Install_Base", "i2") in _records(p, p.org_a), "the parent is in now"
    assert a.ids_reads == [], "a held key is not read"
    assert "n1" not in _linked(p, p.org_a)
    assert set(_kept(p, p.org_a)) == {"n1"}

    _hold(p, p.org_a, "activities", held=False)
    released = await _run(p.org_a)
    assert released.status == "ok"
    record = _records(p, p.org_a)["Install_Base", "i2"][10]
    assert _linked(p, p.org_a)["n1"] == str(record)
    assert a.ids_reads == [("Notes", ["n1"])]
    assert _kept(p, p.org_a) == {}, "a linked id leaves the list"


async def test_the_step_stops_at_the_reserve(mz, monkeypatch) -> None:
    from gateway.crm_sync import engine

    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    a.add("Install_Base", "i2", 70, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)

    real_step, real_low = engine._recover_activities, engine._budget_low

    async def at_the_reserve(ctx: Any) -> None:
        # Only the recovery step meets a low budget. The module pass reads.
        monkeypatch.setattr(engine, "_budget_low", lambda _ctx, reserve: True)
        try:
            await real_step(ctx)
        finally:
            monkeypatch.setattr(engine, "_budget_low", real_low)

    monkeypatch.setattr(engine, "_recover_activities", at_the_reserve)
    paused = await _run(p.org_a)
    assert (paused.status, paused.error) == ("ok", None)
    assert ("Install_Base", "i2") in _records(p, p.org_a)
    assert a.ids_reads == [] and "n1" not in _linked(p, p.org_a)
    assert set(_kept(p, p.org_a)) == {"n1"}, "the next run goes on"

    monkeypatch.setattr(engine, "_recover_activities", real_step)
    assert (await _run(p.org_a)).status == "ok"
    assert _linked(p, p.org_a)["n1"] is not None
    assert _kept(p, p.org_a) == {}


async def test_100_ids_go_in_each_call_and_a_short_answer_loses_nothing(
    mz, monkeypatch,
) -> None:
    from gateway.crm_sources.zoho import client

    monkeypatch.setattr(client, "PER_PAGE", 200)
    p, a, _b = mz
    _seed(a)
    for i in range(101):
        _note(a, f"n{i:03d}", 30 + i, "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert len(_kept(p, p.org_a)) == 101

    a.add("Install_Base", "i2", 300, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)
    a.ids_missing = {"n005"}  # gone in Zoho: both reads leave it out
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert [len(ids) for _module, ids in a.ids_reads] == [100, 2]
    assert a.ids_reads[0][1] == [f"n{i:03d}" for i in range(100)]
    assert a.ids_reads[1][1] == ["n005", "n100"], "a missing id is asked once more"
    linked = _linked(p, p.org_a)
    assert len([k for k, v in linked.items() if v]) == 100
    assert "n005" not in linked
    assert _kept(p, p.org_a) == {}, "a linked id and a gone id both leave"


# ── 3. An overflow ───────────────────────────────────────────────────────────


async def test_an_overflow_queues_one_full_read_at_most_one_time_in_7_days(
    mz, monkeypatch,
) -> None:
    from gateway.crm_sync import recover

    monkeypatch.setattr(recover, "CAP", 2)
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note(a, f"n{i}", 30 + i, "i9")
    await _connect_all(p, p.org_a, a)
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)
    state = _state(p, p.org_a)
    assert state is not None and len(state["recover"]) == 2, "the cap holds"
    assert state["recover_overflow"] is False, "queued, so the mark is cleared"
    assert "note" in _rereads(p, p.org_a)
    queued = (_state(p, p.org_a, BACKFILL) or {})["overflow_at"]["note"]

    start = len(a.requests)
    second = await _run(p.org_a)
    assert second.status == "ok"
    assert None in _notes_since(a, start), "the queued full read ran"
    assert "note" not in _rereads(p, p.org_a), "a second overflow inside 7 days queues nothing"
    assert (_state(p, p.org_a) or {})["recover_overflow"] is True
    assert (_state(p, p.org_a, BACKFILL) or {})["overflow_at"]["note"] == queued

    _age_overflow(p, p.org_a, "note", days=8)
    assert (await _run(p.org_a)).status == "ok"
    assert "note" in _rereads(p, p.org_a), "after 7 days it queues again"


async def test_an_overflow_waits_for_the_first_import_of_its_module(mz, monkeypatch) -> None:
    from gateway.crm_sync import recover

    monkeypatch.setattr(recover, "CAP", 2)
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note(a, f"n{i}", 30 + i, "i9")
    await _connect_all(p, p.org_a, a)
    _hold(p, p.org_a, "module:Install_Base")
    first = await _run(p.org_a)
    assert (first.status, first.error) == ("ok", None)
    assert (_state(p, p.org_a) or {})["recover_overflow"] is True
    assert "note" not in _rereads(p, p.org_a), "Install_Base has had no first import"

    _hold(p, p.org_a, "module:Install_Base", held=False)
    assert (await _run(p.org_a)).status == "ok"
    assert ("Install_Base", "i1") in _records(p, p.org_a)
    assert "note" in _rereads(p, p.org_a)
    assert (_state(p, p.org_a) or {})["recover_overflow"] is False


# ── 4. A new record and a new note in one run ────────────────────────────────


async def test_a_new_module_record_and_a_new_note_link_in_one_run(mz) -> None:
    p, a, _b = mz
    _seed(a)
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    a.add("Install_Base", "i5", 70, Name="A-New", Account={"id": "a1", "name": "x"})
    _note(a, "n5", 71, "i5")
    _age_module_reads(p, p.org_a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    # The pass dropped n5, and the recovery read took that drop back.
    assert result.counts.get("activity_drops") == {}, "the run lost nothing"
    record = _records(p, p.org_a)["Install_Base", "i5"][10]
    assert _linked(p, p.org_a)["n5"] == str(record), "the recovery step linked it"
    assert a.ids_reads == [("Notes", ["n5"])]
    assert _kept(p, p.org_a) == {}


# ── 5. The one full read of the activities ───────────────────────────────────


async def test_the_backfill_queues_one_full_read_one_time(mz) -> None:
    """A note read before its module was mirrored sits behind the cursor. Only
    the one full read of CRM-Q23b brings it back."""
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i1")
    await _connect(p, p.org_a, a)  # no module scope: the profile mirrors none
    assert (await _run(p.org_a)).status == "ok"
    assert _state(p, p.org_a, BACKFILL) is None, "no module is mirrored"
    assert "n1" not in _linked(p, p.org_a) and _kept(p, p.org_a) == {}

    _grant_all_scopes(p, p.org_a)
    start = len(a.requests)
    second = await _run(p.org_a)
    assert (second.status, second.error) == ("ok", None)
    marker = _state(p, p.org_a, BACKFILL)
    assert marker is not None and marker["queued_at"]
    assert _notes_since(a, start) == [None], "one full read of Notes"
    record = _records(p, p.org_a)["Install_Base", "i1"][10]
    assert _linked(p, p.org_a)["n1"] == str(record)
    assert not (_rereads(p, p.org_a) & (ACTIVITIES | {"task"})), "each read is done"

    start = len(a.requests)
    assert (await _run(p.org_a)).status == "ok"
    assert _state(p, p.org_a, BACKFILL) == marker, "a second run queues nothing"
    assert None not in _notes_since(a, start)


async def test_a_hold_on_activities_keeps_the_backfill_entry_until_release(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i1")
    await _connect(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"

    _hold(p, p.org_a, "activities")
    _grant_all_scopes(p, p.org_a)
    assert (await _run(p.org_a)).status == "ok"
    marker = _state(p, p.org_a, BACKFILL)
    assert marker is not None
    assert _rereads(p, p.org_a) >= ACTIVITIES, "held: the entries wait"
    assert "task" not in _rereads(p, p.org_a), "tasks are not held, so they read"
    assert "n1" not in _linked(p, p.org_a)

    assert (await _run(p.org_a)).status == "ok"
    assert _rereads(p, p.org_a) >= ACTIVITIES, "still held, still waiting"
    assert _state(p, p.org_a, BACKFILL) == marker, "and queued one time only"

    _hold(p, p.org_a, "activities", held=False)
    start = len(a.requests)
    assert (await _run(p.org_a)).status == "ok"
    assert None in _notes_since(a, start)
    assert not (_rereads(p, p.org_a) & ACTIVITIES)
    record = _records(p, p.org_a)["Install_Base", "i1"][10]
    assert _linked(p, p.org_a)["n1"] == str(record)


# ── Fix round 1 (review of 2026-10-16) ───────────────────────────────────────


async def _kept_on_i2(p: Any, a: Any, *ids: str) -> None:
    """Run 1 keeps each note on i2, which is in no Zoho module yet. Then i2
    arrives, and the module reads are aged so the next run reads it."""
    _seed(a)
    for i, ident in enumerate(ids):
        _note(a, ident, 30 + i, "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert set(_kept(p, p.org_a)) == set(ids)
    a.add("Install_Base", "i2", 300, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)


def _age_pause(p: Any, org: str, hours: int) -> None:
    when = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    with p.admin_engine.begin() as c:
        c.execute(text(
            "UPDATE crm_sync_cursors SET cursor_state = jsonb_set(cursor_state, "
            "'{recover_paused_at}', to_jsonb(CAST(:t AS text))) "
            "WHERE organization_id = CAST(:o AS uuid) AND module = 'zoho:note'"),
            {"t": when, "o": org})


async def test_a_hidden_module_never_queues_an_overflow_read(mz, monkeypatch) -> None:
    """Review P2: ``_mark_unavailable`` stamps ``initial_done_at``, so a module
    that the account refuses looked imported, and an overflow queued a full
    read of the notes every 7 days for ever."""
    from gateway.crm_sync import recover

    monkeypatch.setattr(recover, "CAP", 2)
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note(a, f"n{i}", 30 + i, "i9")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert "note" in _rereads(p, p.org_a), "the first overflow queues, as before"

    _age_overflow(p, p.org_a, "note", days=8)
    a.faults["Install_Base"] = (400, {"code": "INVALID_MODULE", "status": "error"})
    _age_module_reads(p, p.org_a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert (_state(p, p.org_a, "zoho:module:Install_Base") or {})["unavailable"] is True
    assert (_state(p, p.org_a) or {})["recover_overflow"] is True, "the read overflowed"
    assert "note" not in _rereads(p, p.org_a), "a refused module queues no full read"


async def test_a_refused_read_by_ids_pauses_the_entity_for_24_hours(mz) -> None:
    p, a, _b = mz
    await _kept_on_i2(p, a, "n1")
    a.ids_fault = (400, {"code": "INVALID_DATA", "status": "error"})
    refused = await _run(p.org_a)
    assert (refused.status, refused.error) == ("ok", None)
    assert len(a.ids_reads) == 1
    assert (_state(p, p.org_a) or {})["recover_paused_at"], "the pause is stored"
    assert set(_kept(p, p.org_a)) == {"n1"}

    assert (await _run(p.org_a)).status == "ok"
    assert len(a.ids_reads) == 1, "a paused entity pays for no call"

    a.ids_fault = None
    _age_pause(p, p.org_a, hours=25)
    assert (await _run(p.org_a)).status == "ok"
    assert len(a.ids_reads) == 2
    assert _linked(p, p.org_a)["n1"] is not None
    assert (_state(p, p.org_a) or {})["recover_paused_at"] is None


async def test_a_plain_401_on_a_read_by_ids_fails_the_run(mz) -> None:
    p, a, _b = mz
    await _kept_on_i2(p, a, "n1")
    a.ids_fault = (401, {"code": "INVALID_TOKEN", "status": "error"})
    result = await _run(p.org_a)
    assert result.status == "failed", "never ok: the token went stale mid-run"
    assert result.error == "source_error"


async def test_a_kept_id_that_never_writes_leaves_at_the_retry_limit(mz, monkeypatch) -> None:
    from gateway.crm_sync import engine

    monkeypatch.setattr(engine, "RETRY_LIMIT", 2)
    p, a, _b = mz
    await _kept_on_i2(p, a, "n1", "n2")
    real = engine._write_record

    async def n1_fails(ctx, db, entity, record, caches):
        if record.ext_id == "n1":
            raise RuntimeError("boom")
        return await real(ctx, db, entity, record, caches)

    monkeypatch.setattr(engine, "_write_record", n1_fails)
    assert (await _run(p.org_a)).status == "ok"
    assert set(_kept(p, p.org_a)) == {"n1"}, "n2 linked, n1 stays"
    assert (_state(p, p.org_a) or {})["recover_tries"] == {"n1": 1}
    assert (await _run(p.org_a)).status == "ok"
    assert _kept(p, p.org_a) == {}, "the second failed try takes n1 off the list"
    assert [ids for _m, ids in a.ids_reads] == [["n1", "n2"], ["n1"]]
    assert (await _run(p.org_a)).status == "ok"
    assert len(a.ids_reads) == 2, "and it is never read again"


async def test_an_id_that_a_read_cannot_send_leaves_without_a_call(mz) -> None:
    p, a, _b = mz
    _seed(a)
    _note(a, "n1", 30, "i2")
    _note(a, "bad-1", 31, "i2")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert set(_kept(p, p.org_a)) == {"n1"}, "the step drops bad-1 at once"
    assert a.ids_reads == []
    a.add("Install_Base", "i2", 300, Name="A-Late", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    assert a.ids_reads == [("Notes", ["n1"])], "the bad id holds back no call"
    assert _linked(p, p.org_a)["n1"] is not None
    assert _kept(p, p.org_a) == {}


# ── Fix round 2 (review of fix round 1, P1) ──────────────────────────────────


@pytest.mark.parametrize("why", ["hidden", "held"])
async def test_an_expired_list_never_lets_an_overflow_read_through(
    mz, monkeypatch, why: str,
) -> None:
    """The reviewer's case. The kept list expires in the same run that the 7
    days of the last overflow read lapse. The list names no module then, so
    only the modules of the overflowed drops can hold the guard."""
    from gateway.crm_sync import recover

    monkeypatch.setattr(recover, "CAP", 2)
    p, a, _b = mz
    _seed(a)
    for i in range(3):
        _note(a, f"n{i}", 30 + i, "i9")
    await _connect_all(p, p.org_a, a)
    assert (await _run(p.org_a)).status == "ok"
    assert "note" in _rereads(p, p.org_a), "the first overflow queues, as before"

    _age_overflow(p, p.org_a, "note", days=8)
    for ext_id in _kept(p, p.org_a):
        _set_first_seen(p, p.org_a, ext_id, days_ago=8)  # the re-add keeps it
    if why == "hidden":
        a.faults["Install_Base"] = (400, {"code": "INVALID_MODULE", "status": "error"})
        _age_module_reads(p, p.org_a)
    else:
        _hold(p, p.org_a, "module:Install_Base")  # it imported in run 1
    result = await _run(p.org_a)
    assert (result.status, result.error) == ("ok", None)
    state = _state(p, p.org_a) or {}
    assert state["recover"] == {}, "every entry expired in this run"
    assert state["recover_overflow"] is True
    assert state["recover_overflow_modules"] == ["Install_Base"]
    assert "note" not in _rereads(p, p.org_a), "no full read every 7 days for ever"

    # The module comes back with the parent. The set still names it, so the
    # guard opens now: it queues the read and clears the mark and the set.
    if why == "hidden":
        del a.faults["Install_Base"]
    else:
        _hold(p, p.org_a, "module:Install_Base", held=False)
    a.add("Install_Base", "i9", 400, Name="A-Back", Account={"id": "a1", "name": "x"})
    _age_module_reads(p, p.org_a)
    back = await _run(p.org_a)
    assert (back.status, back.error) == ("ok", None)
    assert ("Install_Base", "i9") in _records(p, p.org_a)
    state = _state(p, p.org_a) or {}
    assert "note" in _rereads(p, p.org_a), "the guard opens for a module that is back"
    assert state["recover_overflow"] is False
    assert state["recover_overflow_modules"] == []

    assert (await _run(p.org_a)).status == "ok"
    record = _records(p, p.org_a)["Install_Base", "i9"][10]
    linked = _linked(p, p.org_a)
    assert {linked[f"n{i}"] for i in range(3)} == {str(record)}, "the full read links them"
    assert "note" not in _rereads(p, p.org_a)
