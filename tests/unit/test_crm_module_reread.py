"""The weekly full read and the pick-list drift check of a generic module
(WS-53 task 3869, ``crm_platform.md`` §18.7, CRM-Q6a).

Hermetic: no database, no network. A fake Zoho pages the records by
``Modified_Time``, a fake session answers the SQL of a page and of the drift
check, and the fake doors keep the rows in memory. So these tests prove the
rules of the engine. They do not prove the SQL of the drift check on Postgres.

What is proved here:

* A status that changed in Zoho with no later ``Modified_Time`` stays wrong
  after an incremental read. The weekly full read repairs it.
* A module whose last full read is 6 days old reads its changes only. At 7
  days and more it is read in full. With the flag off nothing changes.
* A read in progress goes on as the read that it is. A stopped full read
  goes on from its page, and a stopped read of changes never turns into a
  second, full read.
* A held module is never read in full, also in an "on its own" run.
* The full read obeys the reserve. It stops page by page and goes on in the
  next run.
* A weekly read that meets an error goes on at the page that failed. Three
  failures in a row end it, and the module is not due for 7 days. A page
  that lands begins the count again. A label read that cannot finish leaves
  the list. A weekly read in progress stops when the flag goes off or the
  key is held (review F1 and F5).
* The drift check queues ONE full read of a module whose stored select value
  left the current options. After that read the new label is stored, and the
  module leaves the label list. No drift queues nothing, a drift that a read
  cannot repair queues no second read, and a refused discovery queues
  nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.crm_sources.base import SourceCursor, SourceError, SourcePage, SourceRecord
from gateway.crm_sync import engine
from gateway.crm_sync.runs import CursorRow
from sqlalchemy.exc import OperationalError

#: The real check, kept before a fixture replaces it. ``None`` on the code of
#: before task 3869.
_REAL_CHECK = getattr(engine, "_check_label_drift", None)

ORG = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
T0 = "2026-11-01T10:00:00+00:00"
T1 = "2026-11-02T10:00:00+00:00"
T2 = "2026-11-03T10:00:00+00:00"

INVOICES = "module:Invoices"
QUOTES = "module:Quotes"


def _at(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


# ── The fakes ────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = 1

    def scalar(self) -> Any:
        return self._scalar

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def first(self) -> Any:
        return self._rows[0] if self._rows else None


@dataclass
class World:
    """The fake Zoho, the fake mirror and what the engine did to them."""

    #: API name -> the records that Zoho holds now.
    zoho: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: ``(API name, ext_id)`` -> the stored ``fields`` of the mirror row.
    stored: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    #: cursor module -> the last row that the engine saved.
    saved: dict[str, CursorRow] = field(default_factory=dict)
    #: ``(entity, cursor)`` of each record read.
    reads: list[tuple[str, SourceCursor]] = field(default_factory=list)
    #: The text of each SQL statement.
    sql: list[str] = field(default_factory=list)
    page_size: int = 50
    flag_on: bool = True
    drift_fails: bool = False
    #: An error that is not of SQLAlchemy, raised by the drift statement.
    drift_error: Exception | None = None
    #: ``(API name, page)`` -> how many more reads of that page fail.
    fail: dict[tuple[str, int], int] = field(default_factory=dict)
    #: The ``SourceError.code`` of a failed read (``tie_too_wide`` or none).
    fail_code: str = ""

    def drift_rows(self, params: dict[str, Any]) -> list[Any]:
        """What ``_LABEL_DRIFT_SQL`` answers, done in Python."""
        labels = json.loads(params["labels"])
        found: set[tuple[str, str]] = set()
        for (module, _ext), fields in self.stored.items():
            if module != params["module"]:
                continue
            for key, names in labels.items():
                value = fields.get(key)
                if isinstance(value, str) and value not in ("", "-None-") and value not in names:
                    found.add((key, value))
        rows = sorted(found)[: int(params["cap"])]
        return [SimpleNamespace(key=k, value=v) for k, v in rows]


class _Db:
    def __init__(self, world: World) -> None:
        self.world = world

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.world.sql.append(sql)
        if sql == engine.MODE_SHARE_SQL:
            return _Result(scalar="mirror")
        if sql == getattr(engine, "_LABEL_DRIFT_SQL", None):
            if self.world.drift_fails:
                raise OperationalError(sql, {}, Exception("canceling statement"))
            if self.world.drift_error is not None:
                raise self.world.drift_error
            return _Result(rows=self.world.drift_rows(params or {}))
        return _Result()


class _Zoho:
    """One provider call. It pages the records of a module by
    ``Modified_Time``, the way the keyset read of the client does."""

    def __init__(self, world: World) -> None:
        self.world = world
        self.credits_used = 0

    async def credits_remaining(self) -> int | None:
        return None

    async def list_users(self) -> tuple[SourceRecord, ...]:
        return ()

    async def list_changed(self, entity: str, cursor: SourceCursor) -> SourcePage:
        self.credits_used += 1
        self.world.reads.append((entity, cursor))
        api = entity.split(":", 1)[1]
        if self.world.fail.get((api, cursor.page), 0) > 0:
            self.world.fail[(api, cursor.page)] -= 1
            error = SourceError("HTTP 500")
            error.code = self.world.fail_code
            raise error
        rows = sorted(self.world.zoho[api], key=lambda r: (r["Modified_Time"], r["id"]))
        since = cursor.since
        if since is not None:
            rows = [
                r for r in rows
                if _at(r["Modified_Time"]) > since
                or (_at(r["Modified_Time"]) == since and r["id"] not in cursor.seen_ids)
            ]
        size = self.world.page_size
        start = (cursor.page - 1) * size
        chunk = rows[start:start + size]
        more = start + size < len(rows)
        nxt = SourceCursor(since=since, page=cursor.page + 1, seen_ids=cursor.seen_ids)
        return SourcePage(
            tuple(
                SourceRecord(entity, r["id"], _at(r["Modified_Time"]), dict(r)) for r in chunk
            ),
            nxt if more else None,
        )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    w = World()

    @asynccontextmanager
    async def session(org: str) -> AsyncIterator[_Db]:
        assert org == ORG, "each session is the tenant session of the org"
        yield _Db(w)

    async def save(_ctx: Any, _db: Any, row: CursorRow, _status: str) -> None:
        w.saved[row.module] = row

    async def upsert(
        _db: Any, _src: str, module: str, ext_id: str, values: dict[str, Any],
    ) -> bool:
        new = dict(values["fields"])
        changed = w.stored.get((module, ext_id)) != new
        w.stored[(module, ext_id)] = new
        return changed

    async def credential(_org: str) -> object:
        return object()

    monkeypatch.setattr(engine, "tenant_session", session)
    monkeypatch.setattr(engine, "_save_cursor", save)
    monkeypatch.setattr(engine, "upsert_module_records_from_source", upsert)
    monkeypatch.setattr(engine, "source_factory", lambda _cred, _budget: _Zoho(w))
    monkeypatch.setattr(engine, "_fresh_credential", credential)
    monkeypatch.setattr(engine, "_enabled", lambda _org: True)
    monkeypatch.setattr(engine, "_now", lambda: NOW + timedelta(minutes=5))
    # ``raising=False``: this file must fail on its assertions, not on a
    # missing name, when it runs against the code of before task 3869.
    monkeypatch.setattr(
        engine.flags, "module_reread_allowed", lambda _org: w.flag_on, raising=False,
    )
    return w


def _ctx(
    cursors: dict[str, CursorRow], *, holds: dict[str, Any] | None = None,
    only: frozenset[str] | None = None, budget: int | None = None,
    started: datetime = NOW,
) -> engine._Ctx:
    handle = engine.RunHandle(
        org=ORG, run_id="r", kind="incremental", connection_id="c",
        reconcile=frozenset(), cursors=dict(cursors), counts=engine._blank_counts(),
        estimate=None, holds=holds or {}, only=only,
        scopes=("ZohoCRM.modules.quotes.READ",),
    )
    ctx = engine._Ctx(handle, should_stop=lambda: False, credit_budget=budget)
    ctx.started = started
    ctx.owners = {}
    # ``_field_rules`` loads the label list at the start of a real run.
    rules = handle.cursors.get(engine.FIELD_RULES_ROW)
    ctx.label_rereads = {
        str(e) for e in ((rules.state or {}) if rules else {}).get(engine.LABEL_REREAD_KEY) or ()
    }
    return ctx


def _module_row(
    entity: str, *, full_days: float, checked_hours: float = 2.0,
    pulled: str = T1, watermark: tuple[str, ...] = ("i2",), **state: Any,
) -> CursorRow:
    """The cursor of a generic module that a cursor reads (not by offset)."""
    return CursorRow(
        engine.changed_module(entity), _at(pulled),
        {
            "in_progress": False, "offset_paged": False,
            "watermark_ids": list(watermark), "failed": {}, "retry_from": None,
            "checked_at": (NOW - timedelta(hours=checked_hours)).isoformat(),
            **state,
        },
        NOW - timedelta(days=60), NOW - timedelta(days=full_days),
    )


def _invoices(world: World) -> None:
    """Zoho changed the status of i1 and kept its ``Modified_Time``. The
    mirror still holds the old status."""
    world.zoho["Invoices"] = [
        {"id": "i1", "Status": "Sent", "Modified_Time": T0},
        {"id": "i2", "Status": "Paid", "Modified_Time": T1},
    ]
    world.stored[("Invoices", "i1")] = {"Status": "Draft", "Modified_Time": T0}
    world.stored[("Invoices", "i2")] = {"Status": "Paid", "Modified_Time": T1}


def _status(world: World, module: str, ext_id: str, key: str = "Status") -> Any:
    return world.stored[(module, ext_id)][key]


# ── The rule (pure) ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(("days", "due"), [(6, False), (6.99, False), (7, True), (8, True)])
def test_a_module_is_read_in_full_once_its_last_full_read_is_7_days_old(
    world: World, days: float, due: bool,
) -> None:
    row = _module_row(INVOICES, full_days=days)
    assert engine._module_reread(_ctx({row.module: row}), INVOICES, row) is due


def test_the_rule_is_off_with_the_flag_off(world: World) -> None:
    world.flag_on = False
    row = _module_row(INVOICES, full_days=30)
    assert not engine._module_reread(_ctx({row.module: row}), INVOICES, row)


def test_a_held_key_is_never_due_also_in_an_on_its_own_run(world: World) -> None:
    row = _module_row(INVOICES, full_days=30)
    held = {INVOICES: {"held": True, "by": "auto", "count": 9000, "at": NOW.isoformat()}}
    for only in (None, frozenset({INVOICES})):
        ctx = _ctx({row.module: row}, holds=held, only=only)
        assert not engine._module_reread(ctx, INVOICES, row)


def test_a_read_in_progress_goes_on_as_the_read_that_it_is(world: World) -> None:
    full = _module_row(INVOICES, full_days=30, in_progress=True, reread=True)
    changes = _module_row(INVOICES, full_days=30, in_progress=True)
    assert engine._module_reread(_ctx({full.module: full}), INVOICES, full)
    assert not engine._module_reread(_ctx({changes.module: changes}), INVOICES, changes)


def test_an_offset_paged_module_keeps_its_24_hour_read_with_the_flag_off(
    world: World,
) -> None:
    world.flag_on = False
    row = _module_row(INVOICES, full_days=1.5, offset_paged=True)
    assert engine._module_reread(_ctx({row.module: row}), INVOICES, row)


def test_a_module_on_the_label_list_is_due_at_once(world: World) -> None:
    row = _module_row(QUOTES, full_days=1)
    ctx = _ctx({row.module: row})
    assert not engine._module_reread(ctx, QUOTES, row)
    ctx.label_rereads = {QUOTES}
    assert engine._module_reread(ctx, QUOTES, row)
    world.flag_on = False
    assert not engine._module_reread(ctx, QUOTES, row)


# ── The weekly read (the engine) ─────────────────────────────────────────────


async def test_a_status_change_with_no_new_modified_time_is_repaired_by_the_weekly_read(
    world: World,
) -> None:
    _invoices(world)
    row = _module_row(INVOICES, full_days=8)
    await engine._changed_pass(_ctx({row.module: row}), INVOICES)
    assert world.reads[0][1].since is None, "a full read lists every record"
    assert _status(world, "Invoices", "i1") == "Sent"
    after = world.saved[row.module]
    assert after.last_reconciled_at == NOW + timedelta(minutes=5)
    assert after.last_pulled_at == _at(T1), "the cursor of the changes stays"
    assert after.state is not None and not after.state["in_progress"]


async def test_an_incremental_read_misses_the_change_and_the_flag_off_keeps_it(
    world: World,
) -> None:
    _invoices(world)
    world.flag_on = False
    row = _module_row(INVOICES, full_days=8)
    await engine._changed_pass(_ctx({row.module: row}), INVOICES)
    assert [c.since for _e, c in world.reads] == [_at(T1)]
    assert _status(world, "Invoices", "i1") == "Draft"
    assert world.saved[row.module].last_reconciled_at == row.last_reconciled_at


async def test_a_module_read_in_full_6_days_ago_reads_its_changes_only(world: World) -> None:
    _invoices(world)
    row = _module_row(INVOICES, full_days=6)
    await engine._changed_pass(_ctx({row.module: row}), INVOICES)
    assert [c.since for _e, c in world.reads] == [_at(T1)]
    assert _status(world, "Invoices", "i1") == "Draft"


async def test_a_stopped_full_read_goes_on_from_its_page_and_starts_no_second_read(
    world: World,
) -> None:
    _invoices(world)
    world.page_size = 1
    stopped = _module_row(
        INVOICES, full_days=8, in_progress=True, reread=True, full_read=True,
        since=None, page=2, page_token=None, seen_ids=[], max_modified=T0,
    )
    await engine._changed_pass(_ctx({stopped.module: stopped}), INVOICES)
    assert [(c.since, c.page) for _e, c in world.reads] == [(None, 2)]
    assert world.saved[stopped.module].last_reconciled_at == NOW + timedelta(minutes=5)


async def test_a_stopped_read_of_changes_ends_first_and_the_full_read_comes_next(
    world: World,
) -> None:
    _invoices(world)
    world.zoho["Invoices"].append({"id": "i3", "Status": "Sent", "Modified_Time": T2})
    world.page_size = 1
    stopped = _module_row(
        INVOICES, full_days=8, in_progress=True,
        since=T1, page=1, page_token=None, seen_ids=["i2"], max_modified=T1,
    )
    await engine._changed_pass(_ctx({stopped.module: stopped}), INVOICES)
    assert [(c.since, c.page) for _e, c in world.reads] == [(_at(T1), 1)]
    assert _status(world, "Invoices", "i1") == "Draft", "no full read ran"
    ended = world.saved[stopped.module]
    assert ended.last_reconciled_at == stopped.last_reconciled_at
    # The next run starts the full read, also inside MODULE_READ_EVERY.
    world.reads.clear()
    await engine._changed_pass(_ctx({ended.module: ended}), INVOICES)
    assert world.reads[0][1].since is None
    assert _status(world, "Invoices", "i1") == "Sent"


async def test_a_held_module_is_not_read_in_full_in_an_on_its_own_run(world: World) -> None:
    _invoices(world)
    row = _module_row(INVOICES, full_days=30)
    held = {INVOICES: {"held": True, "by": "auto", "count": 9000, "at": NOW.isoformat()}}
    await engine._changed_pass(
        _ctx({row.module: row}, holds=held, only=frozenset({INVOICES})), INVOICES,
    )
    assert [c.since for _e, c in world.reads] == [_at(T1)]
    assert _status(world, "Invoices", "i1") == "Draft"
    # A normal run reads no page of a held module.
    world.reads.clear()
    await engine._changed_pass(_ctx({row.module: row}, holds=held), INVOICES)
    assert world.reads == []


async def test_the_full_read_stops_at_the_reserve_and_goes_on_in_the_next_run(
    world: World,
) -> None:
    _invoices(world)
    world.zoho["Invoices"].append({"id": "i3", "Status": "Sent", "Modified_Time": T2})
    world.page_size = 1
    row = _module_row(INVOICES, full_days=8)
    await engine._changed_pass(
        _ctx({row.module: row}, budget=engine.MODULES_RESERVE + 2), INVOICES,
    )
    assert len(world.reads) == 2, "two pages, then the reserve"
    paused = world.saved[row.module]
    assert paused.state is not None
    assert paused.state["in_progress"] and paused.state["reread"]
    assert paused.last_reconciled_at == row.last_reconciled_at
    world.reads.clear()
    await engine._changed_pass(_ctx({paused.module: paused}), INVOICES)
    assert [(c.since, c.page) for _e, c in world.reads] == [(None, 3)]
    assert world.saved[row.module].last_reconciled_at == NOW + timedelta(minutes=5)


# ── A weekly read that fails (review F1 and F5) ──────────────────────────────


def _three_invoices(world: World) -> CursorRow:
    """Three records, one on each page, and a module due for its weekly read."""
    _invoices(world)
    world.zoho["Invoices"].append({"id": "i3", "Status": "Sent", "Modified_Time": T2})
    world.page_size = 1
    return _module_row(INVOICES, full_days=8)


async def _run(
    world: World, row: CursorRow, *, hours: float, entity: str = INVOICES,
    **kw: Any,
) -> CursorRow:
    """One run of the changed pass at NOW + *hours*. Returns the saved cursor."""
    cursors = {row.module: row}
    if engine.FIELD_RULES_ROW in world.saved:
        cursors[engine.FIELD_RULES_ROW] = world.saved[engine.FIELD_RULES_ROW]
    ctx = _ctx(cursors, started=NOW + timedelta(hours=hours), **kw)
    await engine._changed_pass(ctx, entity)
    return world.saved.get(row.module, row)


def _pages(world: World) -> list[tuple[datetime | None, int]]:
    return [(c.since, c.page) for _e, c in world.reads]


async def test_an_error_in_a_weekly_read_goes_on_at_its_page_in_the_next_run(
    world: World,
) -> None:
    row = _three_invoices(world)
    world.fail[("Invoices", 2)] = 1
    paused = await _run(world, row, hours=0)
    assert _pages(world) == [(None, 1), (None, 2)]
    state = paused.state or {}
    assert state["in_progress"] and state["reread"] and state["page"] == 2
    assert state[engine.REREAD_FAILURES_KEY] == 1
    assert paused.last_reconciled_at == row.last_reconciled_at
    world.reads.clear()
    done = await _run(world, paused, hours=2)
    assert _pages(world) == [(None, 2), (None, 3)], "it goes on at page 2, not page 1"
    assert done.last_reconciled_at == NOW + timedelta(minutes=5)
    assert engine.REREAD_FAILURES_KEY not in (done.state or {})
    assert _status(world, "Invoices", "i1") == "Sent"


async def test_three_failures_in_a_row_end_the_weekly_read_for_7_days(world: World) -> None:
    row = _three_invoices(world)
    world.fail[("Invoices", 2)] = 99
    for hours in (0, 2, 4):
        row = await _run(world, row, hours=hours)
    assert _pages(world) == [(None, 1), (None, 2), (None, 2), (None, 2)]
    state = row.state or {}
    assert not state.get("in_progress") and not state.get("reread")
    assert row.last_reconciled_at == NOW + timedelta(minutes=5)
    assert row.last_pulled_at == _at(T1), "the cursor of the changes stays"
    # The next run reads the changes, and no full read is due for 7 days.
    world.reads.clear()
    later = _ctx({row.module: row}, started=NOW + timedelta(hours=6))
    assert not engine._module_reread(later, INVOICES, row)
    await engine._changed_pass(later, INVOICES)
    assert _pages(world)[0] == (_at(T1), 1), "the read of changes, from the cursor"
    week = _ctx({row.module: row}, started=NOW + timedelta(days=8))
    assert engine._module_reread(week, INVOICES, row)


async def test_a_page_that_lands_resets_the_count_of_failures(world: World) -> None:
    _three_invoices(world)
    world.fail[("Invoices", 3)] = 1
    paused = _module_row(
        INVOICES, full_days=8, checked_hours=3, in_progress=True, reread=True,
        full_read=True, since=None, page=2, page_token=None, seen_ids=[],
        max_modified=T0, reread_failures=2, unavailable=True, reason="scope",
    )
    after = await _run(world, paused, hours=0)
    assert _pages(world) == [(None, 2), (None, 3)]
    state = after.state or {}
    assert state[engine.REREAD_FAILURES_KEY] == 1, "page 2 landed, so the count began again"
    assert state["in_progress"] and state["reread"] and state["page"] == 3


async def test_three_ties_that_are_too_wide_end_the_weekly_read(world: World) -> None:
    """A tie wider than Zoho pages by offset marks nothing unavailable. Before
    the cap, a weekly read met it on each run for ever."""
    row = _three_invoices(world)
    world.fail[("Invoices", 2)] = 99
    world.fail_code = engine.TIE_TOO_WIDE_CODE
    for hours in (0, 0.5, 1):
        row = await _run(world, row, hours=hours)
    state = row.state or {}
    assert not state.get("in_progress") and not state.get("unavailable")
    assert row.last_reconciled_at == NOW + timedelta(minutes=5)


@pytest.mark.parametrize("gate", ["flag_off", "held"])
async def test_a_weekly_read_in_progress_stops_when_its_gate_closes(
    world: World, gate: str,
) -> None:
    _invoices(world)
    stopped = _module_row(
        INVOICES, full_days=8, in_progress=True, reread=True, full_read=True,
        since=None, page=2, page_token=None, seen_ids=[], max_modified=T0,
    )
    kw: dict[str, Any] = {}
    if gate == "flag_off":
        world.flag_on = False
    else:
        kw = {"holds": {INVOICES: {"held": True, "by": "admin", "count": 9000,
                                   "at": NOW.isoformat()}},
              "only": frozenset({INVOICES})}
    assert not engine._module_reread(_ctx({stopped.module: stopped}, **kw), INVOICES, stopped)
    after = await _run(world, stopped, hours=0, **kw)
    assert _pages(world) == [(_at(T1), 1)], "the read of changes, from the cursor"
    assert not (after.state or {}).get("reread")
    assert after.last_reconciled_at == stopped.last_reconciled_at
    assert _status(world, "Invoices", "i1") == "Draft"


async def test_an_offset_paged_read_in_progress_goes_on_with_the_flag_off(
    world: World,
) -> None:
    """Review F5: the stopped read of an offset-paged module keeps its rule."""
    world.flag_on = False
    row = _module_row(INVOICES, full_days=8, in_progress=True, reread=True, offset_paged=True)
    assert engine._module_reread(_ctx({row.module: row}), INVOICES, row)


async def test_a_label_read_that_cannot_finish_leaves_the_list(world: World) -> None:
    row = _quotes(world)[engine.changed_module(QUOTES)]
    world.saved[engine.FIELD_RULES_ROW] = CursorRow(
        engine.FIELD_RULES_ROW, None,
        {"applied": [], "reread": [], engine.LABEL_REREAD_KEY: [QUOTES]}, NOW, None,
    )
    world.fail[("Quotes", 1)] = 99
    for hours in (0, 2, 4):
        row = await _run(world, row, hours=hours, entity=QUOTES)
    assert len(world.reads) == 3
    assert _label_list(world) == []
    later = _ctx(
        {row.module: row, engine.FIELD_RULES_ROW: world.saved[engine.FIELD_RULES_ROW]},
        started=NOW + timedelta(hours=6),
    )
    assert not engine._module_reread(later, QUOTES, row), "not due each hour for ever"


# ── The drift check ──────────────────────────────────────────────────────────

#: Zoho renamed the value "Draft" to "Pending". ``actual_value`` keeps the
#: first name, and a v2 record carries the label.
STAGE = {
    "api_name": "Stage", "field_label": "Stage", "data_type": "picklist",
    "pick_list_values": [
        {"display_value": "Pending", "actual_value": "Draft"},
        {"display_value": "Won", "actual_value": "Won"},
    ],
}
NAME = {"api_name": "Subject", "field_label": "Subject", "data_type": "text"}


def _quotes(world: World, zoho_stage: str = "Pending") -> dict[str, CursorRow]:
    world.zoho["Quotes"] = [{"id": "q1", "Stage": zoho_stage, "Modified_Time": T0}]
    world.stored[("Quotes", "q1")] = {"Stage": "Draft", "Modified_Time": T0}
    # Read in full a day ago, and its changes 10 minutes ago.
    row = _module_row(
        QUOTES, full_days=1, checked_hours=1 / 6, pulled=T0, watermark=("q1",),
    )
    return {row.module: row}


def _label_list(world: World) -> list[str]:
    row = world.saved.get(engine.FIELD_RULES_ROW)
    return list((row.state or {}).get(engine.LABEL_REREAD_KEY) or []) if row else []


async def test_a_renamed_value_queues_one_full_read_and_the_read_stores_the_new_label(
    world: World,
) -> None:
    ctx = _ctx(_quotes(world))
    await engine._check_label_drift(ctx, {"Quotes": [STAGE, NAME]})
    assert _label_list(world) == [QUOTES]
    assert ctx.label_rereads == {QUOTES}
    await engine._changed_pass(ctx, QUOTES)
    assert world.reads[0][1].since is None
    assert _status(world, "Quotes", "q1", "Stage") == "Pending"
    assert _label_list(world) == [], "the end of the read takes it off the list"
    assert QUOTES not in ctx.label_rereads
    # The next check finds no drift. It drops the marks and queues nothing.
    await engine._check_label_drift(ctx, {"Quotes": [STAGE, NAME]})
    state = world.saved[engine.FIELD_RULES_ROW].state or {}
    assert engine.LABEL_DRIFT_KEY not in state
    assert _label_list(world) == []


async def test_without_the_check_the_old_label_stays(world: World) -> None:
    """The case of task 3729: the module read its changes 10 minutes ago, and
    no change list ever names the record."""
    ctx = _ctx(_quotes(world))
    await engine._changed_pass(ctx, QUOTES)
    assert world.reads == []
    assert _status(world, "Quotes", "q1", "Stage") == "Draft"


async def test_no_drift_queues_no_read_and_writes_nothing(world: World) -> None:
    cursors = _quotes(world)
    world.stored[("Quotes", "q1")]["Stage"] = "Won"
    ctx = _ctx(cursors)
    await engine._check_label_drift(ctx, {"Quotes": [STAGE, NAME]})
    assert engine.FIELD_RULES_ROW not in world.saved
    assert ctx.label_rereads == set()
    await engine._changed_pass(ctx, QUOTES)
    assert world.reads == []


async def test_a_drift_that_a_full_read_cannot_repair_queues_no_second_read(
    world: World,
) -> None:
    # Zoho still sends the old label, so the read cannot repair it.
    ctx = _ctx(_quotes(world, zoho_stage="Draft"))
    await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    await engine._changed_pass(ctx, QUOTES)
    assert _label_list(world) == []
    marks = (world.saved[engine.FIELD_RULES_ROW].state or {})[engine.LABEL_DRIFT_KEY]
    assert list(marks) == [QUOTES]
    assert "Draft" not in json.dumps(marks), "a mark holds no Zoho text"
    # The next discovery sees the same value: no second read.
    after = dict(ctx.cursors)
    after[engine.changed_module(QUOTES)] = world.saved[engine.changed_module(QUOTES)]
    ctx2 = _ctx(after)
    await engine._check_label_drift(ctx2, {"Quotes": [STAGE]})
    assert _label_list(world) == []
    # A new value that the options lose queues one read again.
    world.stored[("Quotes", "q2")] = {"Stage": "Lost", "Modified_Time": T0}
    await engine._check_label_drift(ctx2, {"Quotes": [STAGE]})
    assert _label_list(world) == [QUOTES]


async def test_the_check_skips_a_held_a_stopped_and_a_new_module(world: World) -> None:
    cursors = _quotes(world)
    module = engine.changed_module(QUOTES)
    base = cursors[module]
    held = {QUOTES: {"held": True, "by": "admin", "count": 9000, "at": NOW.isoformat()}}
    stopped = CursorRow(module, base.last_pulled_at, {**(base.state or {}),
                        "in_progress": True}, base.initial_done_at, base.last_reconciled_at)
    new = CursorRow(module, None, {"in_progress": True}, None, None)
    for ctx in (_ctx(cursors, holds=held), _ctx({module: stopped}), _ctx({module: new})):
        await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    assert not any("crm_module_records r" in sql for sql in world.sql)
    assert engine.FIELD_RULES_ROW not in world.saved


async def test_the_check_is_off_with_the_flag_off(world: World) -> None:
    world.flag_on = False
    ctx = _ctx(_quotes(world))
    await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    assert world.sql == []
    assert world.saved == {}


async def test_a_failed_check_queues_nothing_and_does_not_fail_the_run(world: World) -> None:
    world.drift_fails = True
    ctx = _ctx(_quotes(world))
    await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    assert engine.FIELD_RULES_ROW not in world.saved


async def test_the_drift_check_survives_an_error_that_is_not_of_sqlalchemy(
    world: World,
) -> None:
    """Review F7: the profile is stored before the check, so no error of the
    check may end the run, also one of the session itself."""
    world.drift_error = RuntimeError("the session could not start")
    ctx = _ctx(_quotes(world))
    await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    assert engine.FIELD_RULES_ROW not in world.saved


async def test_the_check_keeps_the_label_list_of_the_core_entities(world: World) -> None:
    """The early discovery of CRM-Q10 runs before ``_field_rules`` loads the
    list. The check reads the stored list, so it never drops ``deal``."""
    cursors = _quotes(world)
    cursors[engine.FIELD_RULES_ROW] = CursorRow(
        engine.FIELD_RULES_ROW, None,
        {"applied": [], "reread": ["lead"], engine.LABEL_REREAD_KEY: ["deal"]},
        NOW, None,
    )
    ctx = _ctx(cursors)
    ctx.label_rereads = set()  # the early discovery: _field_rules has not run
    await engine._check_label_drift(ctx, {"Quotes": [STAGE]})
    state = world.saved[engine.FIELD_RULES_ROW].state or {}
    assert state[engine.LABEL_REREAD_KEY] == ["deal", QUOTES]
    assert state["reread"] == ["lead"]


def test_each_write_of_the_rules_cursor_keeps_the_drift_marks() -> None:
    """A rule undo rewrites the state of the rules row. The marks stay."""
    old = {"applied": [], "reread": [], engine.LABEL_DRIFT_KEY: {QUOTES: ["ab"]}}
    row = CursorRow(engine.FIELD_RULES_ROW, None, old, None, None)
    ctx = SimpleNamespace(cursors={engine.FIELD_RULES_ROW: row})
    assert engine._rules_row(ctx, [], set()).state == {  # type: ignore[arg-type]
        "applied": [], "reread": [], engine.LABEL_DRIFT_KEY: {QUOTES: ["ab"]}}


def test_the_drift_sql_binds_the_org_and_is_capped() -> None:
    """R7 fence of the bounds. RLS binds the tenant too. The SQL itself runs
    on no real database in this file (R8 is not proved here)."""
    sql = engine._LABEL_DRIFT_SQL
    assert "r.organization_id = CAST(:org AS uuid)" in sql
    assert "r.module = :module" in sql and "r.ext_source = :src" in sql
    assert sql.rstrip().endswith("ORDER BY 1, 2 LIMIT :cap")
    assert "SELECT DISTINCT" in sql
    assert engine.LABEL_DRIFT_CAP == 50


# ── Discovery calls the check, and a refusal queues nothing ──────────────────


QUOTES_MODULE = {
    "api_name": "Quotes", "module_name": "Quotes", "plural_label": "Quotes",
    "generated_type": "default", "api_supported": True, "visible": True,
}


@pytest.fixture
def discovery(world: World, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``_discover`` with its database steps replaced. ``seen`` holds the
    field rows that discovery passed to the drift check."""
    seen: dict[str, Any] = {"calls": []}
    script: dict[str, Any] = {"discover": None, "fields": [STAGE]}

    class _DiscoveryZoho(_Zoho):
        async def discover(self) -> dict[str, Any]:
            self.credits_used += 1
            if isinstance(script["discover"], Exception):
                raise script["discover"]
            return {"org": None, "modules": [QUOTES_MODULE], "layouts": {},
                    "fields": {}, "pipelines": [], "failed": []}

        async def list_field_defs(self, _entity: str) -> list[dict[str, Any]]:
            self.credits_used += 1
            if isinstance(script["fields"], Exception):
                raise script["fields"]
            return list(script["fields"])

    async def nothing(*_a: Any, **_k: Any) -> Any:
        return None

    async def no_stale(*_a: Any, **_k: Any) -> set[str]:
        return set()

    async def check(_ctx: Any, module_fields: dict[str, Any]) -> None:
        seen["calls"].append(module_fields)

    monkeypatch.setattr(engine, "source_factory", lambda _c, _b: _DiscoveryZoho(world))
    monkeypatch.setattr(engine.profiles, "store", nothing)
    monkeypatch.setattr(engine.profiles, "latest", nothing)
    monkeypatch.setattr(engine.field_rules, "mark_stale", no_stale)
    monkeypatch.setattr(engine.store, "merge_provider_meta", nothing)
    monkeypatch.setattr(engine, "_mirror_pipelines", nothing)
    monkeypatch.setattr(engine, "_write_module_field_defs", nothing)
    monkeypatch.setattr(engine, "_check_label_drift", check, raising=False)
    return {"seen": seen, "script": script}


async def test_discovery_passes_the_fields_that_it_read_now(
    discovery: dict[str, Any],
) -> None:
    await engine._discover(_ctx({}, budget=500))
    assert discovery["seen"]["calls"] == [{"Quotes": [STAGE]}]


async def test_a_module_whose_fields_were_refused_is_not_checked(
    discovery: dict[str, Any],
) -> None:
    discovery["script"]["fields"] = SourceError("refused")
    await engine._discover(_ctx({}, budget=500))
    assert discovery["seen"]["calls"] == [{}]


async def test_a_refused_discovery_calls_no_check(
    world: World, discovery: dict[str, Any],
) -> None:
    discovery["script"]["discover"] = SourceError("refused")
    await engine._discover(_ctx({}, budget=500))
    assert discovery["seen"]["calls"] == []
    assert world.saved == {}


async def test_discovery_runs_the_real_check_and_queues_the_read(
    world: World, discovery: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through ``_discover``: the stored value "Draft" left the
    options that discovery read now, so the module joins the label list."""
    monkeypatch.setattr(engine, "_check_label_drift", _REAL_CHECK, raising=False)
    await engine._discover(_ctx(_quotes(world), budget=500))
    assert _label_list(world) == [QUOTES]


async def test_a_refused_discovery_reads_no_value_and_queues_nothing(
    world: World, discovery: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real check, a drifted row and a done module: the refusal still
    sends no drift statement and writes no rules row."""
    monkeypatch.setattr(engine, "_check_label_drift", _REAL_CHECK, raising=False)
    discovery["script"]["discover"] = SourceError("refused")
    await engine._discover(_ctx(_quotes(world), budget=500))
    assert not any("crm_module_records r" in sql for sql in world.sql)
    assert engine.FIELD_RULES_ROW not in world.saved
