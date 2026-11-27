"""The delete walk of a generic module (WS-53 task 3870, ``crm_platform.md``
§18.7, CRM-Q6a).

Hermetic: no database, no network. A fake Zoho pages the records of a module
by ``Modified_Time``, as in ``test_crm_module_reread.py``. A fake session
answers the two reads of ``engine._drop_stale``. The fake doors keep the
mirror rows in memory, each with its ``created_at`` and its seen stamp. So
these tests prove the rules of the engine. ``test_crm_module_delete_walk_r8.py``
proves the SQL on Postgres.

What is proved here, each with a positive control on the same fixture, so a
test that says "nothing happens" fails on the code of before task 3870:

* The first complete weekly read that misses a record keeps it (strike one).
  The second complete read that misses it deletes it. The first walk never
  deletes.
* A record that a read lists again loses its strike.
* A read that stops (the reserve, an error, a tie that is too wide), a read
  that finds a record with no ``Modified_Time``, a read of changes, a first
  import and a weekly read from before task 3870 mark and delete nothing. A
  read that runs over two runs keeps its start and its count.
* The guard of the core walk refuses a mass delete and keeps every row. An
  empty read deletes nothing.
* The flag off and a held key do nothing. Two walks closer than 24 hours are
  one strike. A weekly read that fails three times ends with no delete.
* Each statement names the org. Another module is never touched. No log line
  holds a record id.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from gateway.crm_sources.base import SourceCursor, SourceError, SourcePage, SourceRecord
from gateway.crm_sync import engine
from gateway.crm_sync.runs import CursorRow

ORG = "00000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=90)
#: The start of each weekly read in these tests, 8 days apart.
W1, W2, W3, W4 = (NOW + timedelta(days=8 * n) for n in range(4))
EPOCH = datetime(2026, 10, 1, tzinfo=UTC)

INVOICES = "module:Invoices"
QUOTES = "module:Quotes"

#: The stale predicate of the core walk. The fake session answers the read
#: that holds it, and fails on any other text, so the fake and the SQL agree.
STALE_PREDICATE = "((ext_seen_at IS NULL AND created_at < :prev) OR ext_seen_at < :prev)"

_MIGRATIONS = Path(__file__).resolve().parents[2] / "infra" / "postgres"


def _stamp(minute: int) -> str:
    return (EPOCH + timedelta(minutes=minute)).isoformat()


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
class Row:
    """One mirror row: its ``created_at`` and its ``ext_seen_at``."""

    created: datetime
    seen: datetime | None = None


@dataclass
class World:
    """The fake Zoho, the fake mirror and what the engine did to them."""

    #: API name -> the records that Zoho holds now.
    zoho: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    #: ``(API name, ext_id)`` -> the mirror row.
    rows: dict[tuple[str, str], Row] = field(default_factory=dict)
    #: cursor module -> the last row that the engine saved.
    saved: dict[str, CursorRow] = field(default_factory=dict)
    reads: list[tuple[str, SourceCursor]] = field(default_factory=list)
    #: ``(sql, params)`` of each statement of the fake session.
    sql: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: Each call of the seen door and of the delete door.
    marks: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    page_size: int = 50
    flag_on: bool = True
    #: The start of the run in progress. A new row takes it as ``created_at``.
    now: datetime = NOW
    #: ``(API name, page)`` -> how many more reads of that page fail.
    fail: dict[tuple[str, int], int] = field(default_factory=dict)
    fail_code: str = ""
    #: True: each ``count_records`` call fails. The calls are counted.
    count_fails: bool = False
    count_calls: int = 0

    def stale(self, module: str, prev: datetime) -> list[str]:
        """What the stale read of ``_drop_stale`` answers, done in Python."""
        return sorted(
            ext for (api, ext), row in self.rows.items()
            if api == module and (
                (row.seen is None and row.created < prev)
                or (row.seen is not None and row.seen < prev)
            )
        )


class _Db:
    def __init__(self, world: World) -> None:
        self.world = world

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        p = dict(params or {})
        self.world.sql.append((sql, p))
        if sql == engine.MODE_SHARE_SQL:
            return _Result(scalar="mirror")
        if sql == engine._MODULE_OURS_SQL or sql.startswith(
            "SELECT count(*) AS n FROM crm_module_records",
        ):
            return _Result(scalar=sum(1 for api, _e in self.world.rows if api == p["module"]))
        if sql.startswith("SELECT ext_id FROM crm_module_records"):
            assert STALE_PREDICATE in sql, sql
            rows = self.world.stale(p["module"], p["prev"])
            return _Result(rows=[SimpleNamespace(ext_id=e) for e in rows])
        assert "crm_module_records" not in sql, f"an unknown statement: {sql}"
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

    async def count_records(self, entity: str) -> int | None:
        self.credits_used += 1
        self.world.count_calls += 1
        if self.world.count_fails:
            raise SourceError("HTTP 500")
        return len(self.world.zoho.get(entity.split(":", 1)[1], []))

    async def list_changed(self, entity: str, cursor: SourceCursor) -> SourcePage:
        self.credits_used += 1
        self.world.reads.append((entity, cursor))
        api = entity.split(":", 1)[1]
        if self.world.fail.get((api, cursor.page), 0) > 0:
            self.world.fail[(api, cursor.page)] -= 1
            error = SourceError("HTTP 500")
            error.code = self.world.fail_code
            raise error
        rows = sorted(self.world.zoho.get(api, []),
                      key=lambda r: (r.get("Modified_Time") or "", r["id"]))
        since = cursor.since
        if since is not None:
            rows = [r for r in rows if r.get("Modified_Time") and (
                datetime.fromisoformat(r["Modified_Time"]) > since
                or (datetime.fromisoformat(r["Modified_Time"]) == since
                    and r["id"] not in cursor.seen_ids))]
        size = self.world.page_size
        start = (cursor.page - 1) * size
        chunk = rows[start:start + size]
        more = start + size < len(rows)
        nxt = SourceCursor(since=since, page=cursor.page + 1, seen_ids=cursor.seen_ids)
        return SourcePage(
            tuple(
                SourceRecord(
                    entity, r["id"],
                    datetime.fromisoformat(r["Modified_Time"]) if r.get("Modified_Time") else None,
                    dict(r),
                )
                for r in chunk
            ),
            nxt if more else None,
        )


class _Log:
    """The log of the engine: each line as its text."""

    def __init__(self, world: World) -> None:
        self.world = world

    def _line(self, message: str, *args: Any, **_kw: Any) -> None:
        self.world.logs.append(message % args if args else message)

    info = warning = error = debug = _line


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
        _db: Any, _src: str, module: str, ext_id: str, _values: dict[str, Any],
    ) -> bool:
        if (module, ext_id) in w.rows:
            return False
        w.rows[(module, ext_id)] = Row(created=w.now + timedelta(minutes=1))
        return True

    async def mark(
        _db: Any, src: str, module: str, ext_ids: list[str], seen_at: datetime,
        *, org: str,
    ) -> int:
        w.marks.append({"src": src, "module": module, "ids": list(ext_ids),
                        "seen": seen_at, "org": org})
        moved = 0
        for ext in ext_ids:
            row = w.rows.get((module, ext))
            if row is not None and (row.seen is None or row.seen < seen_at):
                row.seen = seen_at
                moved += 1
        return moved

    async def delete(
        _db: Any, src: str, module: str, ext_ids: list[str], *, org: str | None = None,
    ) -> int:
        w.deletes.append({"src": src, "module": module, "ids": list(ext_ids), "org": org})
        return sum(1 for ext in ext_ids if w.rows.pop((module, ext), None) is not None)

    async def credential(_org: str) -> object:
        return object()

    monkeypatch.setattr(engine, "tenant_session", session)
    monkeypatch.setattr(engine, "_save_cursor", save)
    monkeypatch.setattr(engine, "upsert_module_records_from_source", upsert)
    # ``raising=False``: this file must fail on its assertions, not on a
    # missing name, when it runs against the code of before task 3870.
    monkeypatch.setattr(engine, "mark_module_records_seen_from_source", mark, raising=False)
    monkeypatch.setattr(engine, "delete_module_records_from_source", delete)
    monkeypatch.setattr(engine, "source_factory", lambda _cred, _budget: _Zoho(w))
    monkeypatch.setattr(engine, "_fresh_credential", credential)
    monkeypatch.setattr(engine, "_enabled", lambda _org: True)
    monkeypatch.setattr(engine, "_now", lambda: w.now + timedelta(minutes=5))
    monkeypatch.setattr(engine, "_log", _Log(w))
    monkeypatch.setattr(
        engine.flags, "module_reread_allowed", lambda _org: w.flag_on, raising=False,
    )
    return w


def _ctx(
    cursors: dict[str, CursorRow], *, started: datetime,
    holds: dict[str, Any] | None = None, budget: int | None = None,
    label: bool = False, entity: str = INVOICES, final: bool = False,
) -> engine._Ctx:
    handle = engine.RunHandle(
        org=ORG, run_id="r", kind="incremental", connection_id="c",
        reconcile=frozenset(), cursors=dict(cursors), counts=engine._blank_counts(),
        estimate=None, holds=holds or {}, scopes=("ZohoCRM.modules.invoices.READ",),
        final=final,
    )
    ctx = engine._Ctx(handle, should_stop=lambda: False, credit_budget=budget)
    ctx.started = started
    ctx.owners = {}
    ctx.label_rereads = {entity} if label else set()
    return ctx


def _api(entity: str) -> str:
    return entity.split(":", 1)[1]


def _start(
    world: World, entity: str = INVOICES, *, mirror: tuple[str, ...] = ("i1", "i2", "i3"),
    zoho: tuple[str, ...] = ("i1", "i2"), first_import: bool = False,
) -> None:
    """A module that the mirror read in full 8 days before ``W1``. The mirror
    holds *mirror*, made long ago and never stamped. Zoho holds *zoho*. A
    record of *mirror* that is not in *zoho* was deleted in Zoho, and the
    deleted list never named it."""
    api = _api(entity)
    for ext in mirror:
        world.rows[(api, ext)] = Row(created=LONG_AGO)
    world.zoho[api] = [{"id": ext, "Modified_Time": _stamp(n)} for n, ext in enumerate(zoho)]
    module = engine.changed_module(entity)
    if first_import:
        world.saved[module] = CursorRow(module, None, {"in_progress": False}, None, None)
        return
    world.saved[module] = CursorRow(
        module, EPOCH + timedelta(minutes=len(zoho)),
        {
            "in_progress": False, "offset_paged": False, "watermark_ids": [],
            "failed": {}, "retry_from": None,
            "checked_at": (W1 - timedelta(hours=2)).isoformat(),
        },
        LONG_AGO, W1 - timedelta(days=8),
    )


async def _run(
    world: World, *, at: datetime, entity: str = INVOICES, count: Any = "zoho",
    **kw: Any,
) -> engine._Ctx:
    """One changed pass of *entity* at *at*. The module count of the run is
    the number of records that Zoho holds, unless *count* says another. That
    count answered at *at*, so the walk trusts it."""
    world.now = at
    module = engine.changed_module(entity)
    cursors: dict[str, CursorRow] = {module: world.saved[module]}
    known = len(world.zoho.get(_api(entity), [])) if count == "zoho" else count
    if known is not None:
        cursors[engine.MODULE_COUNTS_ROW] = CursorRow(
            engine.MODULE_COUNTS_ROW, None,
            {"counts": {entity: known}, "at": {entity: at.isoformat()},
             "good_at": {entity: at.isoformat()}}, LONG_AGO, None,
        )
    ctx = _ctx(cursors, started=at, entity=entity, **kw)
    await engine._changed_pass(ctx, entity)
    return ctx


def _state(world: World, entity: str = INVOICES) -> dict[str, Any]:
    return dict(world.saved[engine.changed_module(entity)].state or {})


def _seen_prev(world: World, entity: str = INVOICES) -> Any:
    return _state(world, entity).get("seen_prev")


def _gone(world: World, ext: str, entity: str = INVOICES) -> bool:
    return (_api(entity), ext) not in world.rows


# ── Two strikes ──────────────────────────────────────────────────────────────


async def test_a_first_miss_keeps_the_row_and_a_second_miss_drops_it(world: World) -> None:
    _start(world)
    first = await _run(world, at=W1)
    assert not _gone(world, "i3"), "the first walk never deletes"
    assert world.rows[("Invoices", "i3")].seen is None, "strike one"
    assert world.rows[("Invoices", "i1")].seen == W1
    assert world.deletes == []
    assert _seen_prev(world) == W1.isoformat()
    assert first.counts[INVOICES]["deleted"] == 0

    second = await _run(world, at=W2)
    assert _gone(world, "i3"), "strike two"
    assert world.deletes == [{"src": "zoho", "module": "Invoices", "ids": ["i3"], "org": ORG}]
    assert second.counts[INVOICES]["deleted"] == 1
    assert _seen_prev(world) == W2.isoformat()
    assert not _gone(world, "i1") and not _gone(world, "i2")


async def test_a_record_seen_again_loses_its_strike(world: World) -> None:
    _start(world)
    await _run(world, at=W1)  # i3 missed: strike one
    world.zoho["Invoices"].append({"id": "i3", "Modified_Time": _stamp(9)})
    await _run(world, at=W2)  # listed again
    assert world.rows[("Invoices", "i3")].seen == W2
    world.zoho["Invoices"] = [r for r in world.zoho["Invoices"] if r["id"] != "i3"]
    await _run(world, at=W3)  # missed once more: strike one only
    assert not _gone(world, "i3")
    await _run(world, at=W4)
    assert _gone(world, "i3")


# ── A read that does not end, or is no complete walk ─────────────────────────


@pytest.mark.parametrize("stop", ["reserve", "error", "tie"])
async def test_a_read_that_stops_drops_nothing_and_goes_on_as_the_walk(
    world: World, stop: str,
) -> None:
    _start(world, mirror=("i1", "i2", "i3", "i4"), zoho=("i1", "i2", "i4"))
    await _run(world, at=W1)  # i3: strike one
    world.page_size = 1
    kw: dict[str, Any] = {}
    if stop == "reserve":
        kw["budget"] = engine.MODULES_RESERVE + 1
    else:
        world.fail[("Invoices", 2)] = 1
        world.fail_code = engine.TIE_TOO_WIDE_CODE if stop == "tie" else ""
    await _run(world, at=W2, **kw)
    assert not _gone(world, "i3"), "a read that stopped deleted a row"
    assert world.deletes == []
    paused = _state(world)
    assert paused["seen_prev"] == W1.isoformat(), "the strikes stay as they were"
    assert paused["seen_start"] == W2.isoformat() and paused["seen_count"] == 1
    assert world.rows[("Invoices", "i1")].seen == W2

    # The next run goes on at page 2 as the same walk, and ends it.
    await _run(world, at=W2 + timedelta(hours=2))
    assert _gone(world, "i3")
    assert world.rows[("Invoices", "i1")].seen == W2, "page 1 kept the stamp of its walk"
    assert world.rows[("Invoices", "i4")].seen == W2
    assert _seen_prev(world) == W2.isoformat()


async def test_a_record_with_no_modified_time_stops_the_delete(world: World) -> None:
    """A read by offset can skip a row when Zoho changes under it."""
    _start(world)
    await _run(world, at=W1)
    world.zoho["Invoices"][0].pop("Modified_Time")
    await _run(world, at=W2)
    assert [m["ids"] for m in world.marks][-1] == ["i1", "i2"], "the walk read the module"
    assert not _gone(world, "i3")
    assert world.deletes == []
    assert _seen_prev(world) == W1.isoformat()


async def test_a_read_of_changes_marks_nothing_and_drops_nothing(world: World) -> None:
    _start(world)
    await _run(world, at=W1)
    marks = len(world.marks)
    world.zoho["Invoices"].append({"id": "i5", "Modified_Time": _stamp(50)})
    await _run(world, at=W1 + timedelta(days=1))
    assert world.reads[-1][1].since is not None, "a read of changes"
    assert len(world.marks) == marks
    assert world.rows[("Invoices", "i5")].seen is None
    assert not _gone(world, "i3")
    assert _seen_prev(world) == W1.isoformat(), "a read of changes keeps the strikes"
    # The next weekly read deletes i3, and stamps i5.
    await _run(world, at=W2)
    assert _gone(world, "i3")
    assert world.rows[("Invoices", "i5")].seen == W2


async def test_a_module_in_its_first_import_is_not_walked(world: World) -> None:
    _start(world, first_import=True)
    await _run(world, at=W1)
    assert world.marks == [] and world.deletes == []
    assert not _gone(world, "i3")
    state = _state(world)
    assert "seen_prev" not in state and "seen_start" not in state
    # After the import, two weekly reads delete it.
    await _run(world, at=W2)
    assert not _gone(world, "i3")
    await _run(world, at=W3)
    assert _gone(world, "i3")


async def test_a_weekly_read_from_before_the_walk_is_no_walk(world: World) -> None:
    """It stamped none of its pages, so a row that it read looks missed."""
    _start(world, mirror=("i1", "i2", "i3"), zoho=("i1", "i2"))
    await _run(world, at=W1)
    module = engine.changed_module(INVOICES)
    done = world.saved[module]
    world.saved[module] = CursorRow(
        module, done.last_pulled_at,
        {**(done.state or {}), "in_progress": True, "reread": True, "full_read": True,
         "since": None, "page": 2, "page_token": None, "seen_ids": []},
        done.initial_done_at, W2 - timedelta(days=8),
    )
    world.page_size = 1
    marks = len(world.marks)
    await _run(world, at=W2)
    assert len(world.marks) == marks and world.deletes == []
    assert not _gone(world, "i3")
    assert _seen_prev(world) == W1.isoformat()
    world.page_size = 50
    await _run(world, at=W3)
    assert _gone(world, "i3")


# ── The guards ───────────────────────────────────────────────────────────────


def _hundred(world: World, missing: int) -> None:
    mirror = tuple(f"zid-{n:04d}" for n in range(100))
    _start(world, mirror=mirror, zoho=mirror[missing:])


@pytest.mark.parametrize("missing", [26, 30])
async def test_a_mass_drop_is_refused_and_keeps_every_row(world: World, missing: int) -> None:
    """More than 20 % and more than 25 rows: the guard of the core walk
    (``REFUSE_FRACTION`` of more than ``REFUSE_MIN_ROWS``) refuses it."""
    assert (engine.REFUSE_FRACTION, engine.REFUSE_MIN_ROWS) == (0.2, 10)
    _hundred(world, missing)
    await _run(world, at=W1)
    refused = await _run(world, at=W2)
    assert len([k for k in world.rows if k[0] == "Invoices"]) == 100
    assert world.deletes == []
    assert refused.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert engine.WALK_SHORT_FLAG not in refused.counts[INVOICES]
    assert _seen_prev(world) == W1.isoformat(), "the strikes stay for a person to look"
    line = next(x for x in world.logs if x.startswith("crm.sync.reconcile_refused"))
    assert line == (
        f"crm.sync.reconcile_refused entity={INVOICES} reason=large_delete "
        f"walked={100 - missing} known={100 - missing} local=100 stale={missing}"
    )


async def test_a_small_drop_of_a_large_module_goes(world: World) -> None:
    """The positive control of the guard: 5 of 100 rows go."""
    _hundred(world, 5)
    await _run(world, at=W1)
    done = await _run(world, at=W2)
    assert len([k for k in world.rows if k[0] == "Invoices"]) == 95
    assert done.counts[INVOICES]["deleted"] == 5


async def test_an_empty_read_drops_nothing(world: World) -> None:
    _start(world, mirror=("i1", "i2", "i3", "i4", "i5"), zoho=())
    for at in (W1, W2):
        empty = await _run(world, at=at)
        assert empty.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert len(world.rows) == 5 and world.deletes == []
    assert "seen_prev" not in _state(world), "an empty read is no complete walk"
    # Zoho answers again with four of the five: two walks delete the fifth.
    world.zoho["Invoices"] = [
        {"id": e, "Modified_Time": _stamp(n)} for n, e in enumerate(("i1", "i2", "i3", "i4"))
    ]
    await _run(world, at=W3)
    assert not _gone(world, "i5")
    await _run(world, at=W4)
    assert _gone(world, "i5")


async def test_a_read_shorter_than_the_count_or_with_no_count_drops_nothing(
    world: World,
) -> None:
    _start(world)
    await _run(world, at=W1)
    short = await _run(world, at=W2, count=3)  # Zoho counts 3, the read listed 2
    assert not _gone(world, "i3")
    assert short.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    unknown = await _run(world, at=W3, count=None)
    assert not _gone(world, "i3")
    assert unknown.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert _seen_prev(world) == W1.isoformat()
    await _run(world, at=W4)
    assert _gone(world, "i3")


# ── The count of the walk (task 3870, review round 2) ─────────────────────────


async def _count_run(world: World, *, at: datetime, entity: str = INVOICES, **kw: Any) -> None:
    """One run that reads no page and only asks for the count of *entity*, by
    the real :func:`engine._module_estimate`. The counts row it saves is the
    one that the next walk reads."""
    world.now = at
    cursors = {k: v for k, v in world.saved.items() if k == engine.MODULE_COUNTS_ROW}
    await engine._module_estimate(_ctx(cursors, started=at, entity=entity, **kw), entity)


async def _real_run(
    world: World, *, at: datetime, entity: str = INVOICES, **kw: Any,
) -> engine._Ctx:
    """One run of the module step in its real order: the changed pass, which
    ends the walk, then the count. Each takes the cursors that runs before it
    saved, the counts row too. A run at the reserve reads no count."""
    world.now = at
    ctx = _ctx(dict(world.saved), started=at, entity=entity, **kw)
    await engine._changed_pass(ctx, entity)
    await engine._module_estimate(ctx, entity)
    return ctx


def _refused_for(world: World, reason: str) -> list[str]:
    return [x for x in world.logs
            if x.startswith("crm.sync.reconcile_refused") and f"reason={reason} " in x]


async def test_a_walk_refuses_when_the_last_count_call_failed(world: World) -> None:
    """``_module_estimate`` keeps the old count and moves its stamp when the
    call fails. The walk must not take that old count as fresh."""
    _start(world)
    await _count_run(world, at=W1 - timedelta(hours=1))  # a good count: 2
    await _real_run(world, at=W1)  # i3: strike one
    assert _seen_prev(world) == W1.isoformat()

    world.count_fails = True
    # Each day of the week the count call fails, the last time 1 hour before
    # the walk. So the count of the walk run, after its walk, is not due.
    for at in [*(W1 + timedelta(days=d) for d in range(1, 7)), W2 - timedelta(hours=1)]:
        await _count_run(world, at=at)
    calls = world.count_calls
    counts = world.saved[engine.MODULE_COUNTS_ROW].state or {}
    assert counts["counts"][INVOICES] == 2, "Sync health still shows the old count"

    refused = await _real_run(world, at=W2)
    assert not _gone(world, "i3"), "a walk deleted by a count of a failed call"
    assert world.deletes == []
    assert refused.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert _refused_for(world, "count_unknown")
    assert _seen_prev(world) == W1.isoformat(), "the strikes stay as they were"
    assert world.count_calls == calls, "the walk spent no count call of its own"

    # The positive control: the count answers again, and the next walk deletes.
    world.count_fails = False
    await _count_run(world, at=W3 - timedelta(hours=1))
    await _real_run(world, at=W3)
    assert _gone(world, "i3")


async def test_a_walk_refuses_a_count_older_than_a_day_and_the_margin(world: World) -> None:
    """The budget can stop the count for days, and then its stamp does not
    move. A walk must not take a count of any age."""
    _start(world)
    await _count_run(world, at=W1 - timedelta(hours=1))
    await _real_run(world, at=W1)  # i3: strike one
    # A week with no count: each count run meets the reserve.
    for day in range(1, 8):
        await _count_run(world, at=W1 + timedelta(days=day), budget=engine.MODULES_RESERVE)
    refused = await _real_run(world, at=W2)
    assert not _gone(world, "i3"), "a walk deleted by a count 8 days old"
    assert refused.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert _refused_for(world, "count_unknown")
    assert _seen_prev(world) == W1.isoformat()


#: ``engine.MODULE_COUNT_MARGIN``. A copy, so this file collects and fails
#: on its assertions against the code of before the rule.
COUNT_MARGIN = timedelta(hours=1)


async def _paused_walk(world: World, *, good_before: timedelta) -> None:
    """i3 has strike one. The count answered *good_before* before ``W2``.
    The walk starts at ``W2``, reads one page and pauses at the reserve, so
    that run reads no count."""
    _start(world)
    await _count_run(world, at=W1 - timedelta(hours=1))
    await _real_run(world, at=W1)  # i3: strike one
    await _count_run(world, at=W2 - good_before)
    world.page_size = 1
    await _real_run(world, at=W2, budget=engine.MODULES_RESERVE + 1)
    paused = _state(world)
    assert paused["seen_start"] == W2.isoformat() and paused["seen_count"] == 1
    assert not _gone(world, "i3")


async def test_a_walk_over_25_hours_with_a_count_good_at_its_start_deletes(
    world: World,
) -> None:
    """The run that ends the walk is 30 hours after its start, and no count
    was read during the walk. The core walk counts at its start too."""
    await _paused_walk(world, good_before=timedelta(hours=1))
    done = await _real_run(world, at=W2 + timedelta(hours=30))
    assert _gone(world, "i3")
    assert done.counts[INVOICES]["deleted"] == 1
    assert _seen_prev(world) == W2.isoformat()


async def test_a_walk_whose_count_was_stale_at_its_start_refuses(world: World) -> None:
    await _paused_walk(world, good_before=timedelta(hours=26))
    refused = await _real_run(world, at=W2 + timedelta(hours=2))
    assert not _gone(world, "i3"), "a walk deleted by a count stale at its start"
    assert refused.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert _refused_for(world, "count_unknown")
    assert _seen_prev(world) == W1.isoformat()


async def test_a_count_call_that_fails_during_the_walk_makes_it_refuse(
    world: World,
) -> None:
    """The count was good 1 hour before the start, inside the window. The
    next count call, during the walk, fails. The walk must not keep the
    good stamp of the call before."""
    await _paused_walk(world, good_before=timedelta(hours=1))
    world.count_fails = True
    calls = world.count_calls
    await _count_run(world, at=W2 + timedelta(hours=23))
    assert world.count_calls == calls + 1, "the count call was due, and failed"
    refused = await _real_run(world, at=W2 + timedelta(hours=24))
    assert not _gone(world, "i3"), "a walk trusted a count whose last call failed"
    assert refused.counts[INVOICES][engine.DELETE_KEPT_FLAG] == 1
    assert _refused_for(world, "count_unknown")
    assert _seen_prev(world) == W1.isoformat()


@pytest.mark.parametrize(("age", "kept"), [
    (timedelta(hours=24) + COUNT_MARGIN, False),
    (timedelta(hours=24) + COUNT_MARGIN + timedelta(minutes=1), True),
])
async def test_the_walk_takes_a_count_up_to_a_day_and_the_margin_old(
    world: World, age: timedelta, kept: bool,
) -> None:
    assert timedelta(hours=24) == engine.DISCOVERY_EVERY
    assert getattr(engine, "MODULE_COUNT_MARGIN", None) == COUNT_MARGIN
    _start(world)
    await _run(world, at=W1)  # i3: strike one
    module = engine.changed_module(INVOICES)
    stamp = (W2 - age).isoformat()
    cursors = {
        module: world.saved[module],
        engine.MODULE_COUNTS_ROW: CursorRow(
            engine.MODULE_COUNTS_ROW, None,
            {"counts": {INVOICES: 2}, "at": {INVOICES: stamp},
             "good_at": {INVOICES: stamp}}, LONG_AGO, None,
        ),
    }
    world.now = W2
    await engine._changed_pass(_ctx(cursors, started=W2), INVOICES)
    assert _gone(world, "i3") is not kept


async def test_a_counts_row_from_before_the_good_stamp_fails_closed(world: World) -> None:
    """A row that the code of before this rule saved has no ``good_at``."""
    _start(world)
    await _run(world, at=W1)
    module = engine.changed_module(INVOICES)
    cursors = {
        module: world.saved[module],
        engine.MODULE_COUNTS_ROW: CursorRow(
            engine.MODULE_COUNTS_ROW, None,
            {"counts": {INVOICES: 2}, "at": {INVOICES: W2.isoformat()}}, LONG_AGO, None,
        ),
    }
    world.now = W2
    await engine._changed_pass(_ctx(cursors, started=W2), INVOICES)
    assert not _gone(world, "i3")
    assert _refused_for(world, "count_unknown")


# ── The gates ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("gate", ["flag_off", "held"])
async def test_the_flag_off_and_a_held_key_walk_nothing(world: World, gate: str) -> None:
    _start(world)
    await _run(world, at=W1)
    marks = len(world.marks)
    kw: dict[str, Any] = {}
    if gate == "flag_off":
        world.flag_on = False
    else:
        kw["holds"] = {INVOICES: {"held": True, "by": "admin", "count": 9000,
                                  "at": NOW.isoformat()}}
    await _run(world, at=W2, **kw)
    assert len(world.marks) == marks and world.deletes == []
    assert not _gone(world, "i3")
    assert _seen_prev(world) == W1.isoformat()
    world.flag_on = True
    await _run(world, at=W2 + timedelta(hours=1))
    assert _gone(world, "i3")


async def test_two_walks_closer_than_a_day_are_one_strike(world: World) -> None:
    """A read for the label list can follow a weekly read by minutes."""
    _start(world)
    await _run(world, at=W1)
    await _run(world, at=W1 + timedelta(minutes=20), label=True)
    assert world.reads[-1][1].since is None, "the label read is a full read"
    assert not _gone(world, "i3")
    assert _seen_prev(world) == W1.isoformat()
    await _run(world, at=W2)
    assert _gone(world, "i3")


async def test_a_weekly_read_that_fails_three_times_ends_with_no_delete(
    world: World,
) -> None:
    _start(world, mirror=("i1", "i2", "i3", "i4"), zoho=("i1", "i2", "i4"))
    await _run(world, at=W1)
    world.page_size = 1
    world.fail[("Invoices", 2)] = 99
    for hours in (0, 2, 4):
        await _run(world, at=W2 + timedelta(hours=hours))
    state = _state(world)
    assert not state.get("in_progress") and not state.get("reread")
    assert world.deletes == [] and not _gone(world, "i3")
    assert "seen_prev" not in state, "an ended read starts the strikes again"
    world.fail.clear()
    world.page_size = 50
    await _run(world, at=W3)
    assert not _gone(world, "i3")
    await _run(world, at=W4)
    assert _gone(world, "i3")


async def test_the_final_sync_of_the_switch_deletes_nothing(world: World) -> None:
    """No read of Zoho follows the switch (CRM-Z10b), so a wrong delete there
    could never heal. The final sync stamps, and keeps the strikes."""
    _start(world)
    await _run(world, at=W1)  # strike one
    await _run(world, at=W2, final=True)
    assert world.reads[-1][1].since is None, "the final sync read the module in full"
    assert world.rows[("Invoices", "i1")].seen == W2
    assert not _gone(world, "i3") and world.deletes == []
    assert _seen_prev(world) == W1.isoformat()
    await _run(world, at=W3)
    assert _gone(world, "i3")


async def test_a_module_marked_unavailable_starts_its_strikes_again(world: World) -> None:
    """A read of changes that fails marks the module unavailable. The mark
    drops the walk keys, as for the core walk, so the next walk only strikes."""
    _start(world)
    await _run(world, at=W1)
    world.fail[("Invoices", 1)] = 1
    await _run(world, at=W1 + timedelta(days=1))
    state = _state(world)
    assert state.get("unavailable") and "seen_prev" not in state
    await _run(world, at=W2)
    assert not _gone(world, "i3"), "the first walk after the mark deleted a row"
    await _run(world, at=W3)
    assert _gone(world, "i3")


# ── Tenancy, scope and logs ──────────────────────────────────────────────────


async def test_each_statement_names_the_org_and_the_module(world: World) -> None:
    _start(world)
    _start(world, QUOTES, mirror=("q1", "q9"), zoho=("q1",))
    for at in (W1, W2):
        await _run(world, at=at)
    walk_sql = [(s, p) for s, p in world.sql if "crm_module_records" in s]
    assert walk_sql, "the walk read the mirror"
    for sql, params in walk_sql:
        assert "organization_id = CAST(:org AS uuid)" in sql
        assert "module = :module" in sql and "ext_source = :src" in sql
        assert (params["org"], params["module"], params["src"]) == (ORG, "Invoices", "zoho")
    assert {(m["org"], m["module"]) for m in world.marks} == {(ORG, "Invoices")}
    assert [(d["org"], d["module"]) for d in world.deletes] == [(ORG, "Invoices")]
    # Quotes was not read, and its row that Zoho lacks is untouched.
    assert ("Quotes", "q9") in world.rows
    assert world.rows[("Quotes", "q9")].seen is None


async def test_no_log_line_holds_a_record_id(world: World) -> None:
    _hundred(world, 5)
    await _run(world, at=W1)
    await _run(world, at=W2)
    _hundred(world, 30)
    for at in (W3, W4):
        await _run(world, at=at)
    assert any(x.startswith("crm.sync.module_walk_done") for x in world.logs)
    assert any(x.startswith("crm.sync.reconcile_refused") for x in world.logs)
    assert not any("zid-" in x for x in world.logs), world.logs
    done = [x for x in world.logs if x.startswith("crm.sync.module_walk_done")]
    assert f"crm.sync.module_walk_done entity={INVOICES} walked=95 known=95 dropped=5" in done


# ── The doors and the migration (static) ─────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        self.calls.append((str(statement), dict(params)))
        return _Result()


async def test_the_seen_door_names_the_org_and_writes_only_the_stamp() -> None:
    from gateway.routes.crm import core

    db = _Recorder()
    await core.mark_module_records_seen_from_source(
        db, "zoho", "Invoices", ["b", "a", "a"], W1, org=ORG,
    )
    ((sql, params),) = db.calls
    assert sql == core.MARK_MODULE_SEEN_SQL
    assert sql.startswith("UPDATE crm_module_records SET ext_seen_at = :seen WHERE")
    assert "organization_id = CAST(:org AS uuid)" in sql
    assert "updated_at" not in sql and "fields" not in sql
    assert "(ext_seen_at IS NULL OR ext_seen_at < :seen)" in sql
    assert params == {"seen": W1, "org": ORG, "ext_source": "zoho",
                      "module": "Invoices", "ext_ids": ["a", "b"]}
    with pytest.raises(ValueError):
        await core.mark_module_records_seen_from_source(db, "zoho", "Invoices", ["a"], W1, org="")
    with pytest.raises(ValueError):
        await core.mark_module_records_seen_from_source(
            db, "zoho", "Bad Name;", ["a"], W1, org=ORG)


async def test_the_delete_door_names_the_org_only_for_the_walk() -> None:
    from gateway.routes.crm import core

    db = _Recorder()
    await core.delete_module_records_from_source(db, "zoho", "Invoices", ["a"], org=ORG)
    await core.delete_module_records_from_source(db, "zoho", "Invoices", ["a"])
    (walk_sql, walk_params), (list_sql, list_params) = db.calls
    assert walk_sql.endswith("AND organization_id = CAST(:org AS uuid)")
    assert walk_params["org"] == ORG
    assert "organization_id" not in list_sql and "org" not in list_params, (
        "the deleted list keeps its statement as before"
    )


def _migration() -> Path:
    found = [
        f for f in sorted(_MIGRATIONS.glob("*.sql"))
        if f.name != "schema.generated.sql" and re.search(
            r"ALTER TABLE crm_module_records ADD COLUMN IF NOT EXISTS ext_seen_at",
            f.read_text(encoding="utf-8"),
        )
    ]
    assert len(found) == 1, [f.name for f in found]
    return found[0]


def test_the_migration_adds_one_nullable_column_and_nothing_else() -> None:
    text = _migration().read_text(encoding="utf-8")
    bare = "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())
    assert re.search(r"SET lock_timeout = '\d+s';", bare)
    assert re.search(
        r"ALTER TABLE crm_module_records ADD COLUMN IF NOT EXISTS ext_seen_at "
        r"TIMESTAMPTZ NULL;", bare)
    for word in ("DEFAULT", "NOT NULL", "UPDATE ", "DELETE ", "DROP ", "RENAME",
                 "ROW LEVEL SECURITY", "POLICY", "CREATE TABLE", "INSERT "):
        assert word not in bare.upper(), word
    statements = [s.strip() for s in bare.split(";") if s.strip()]
    assert len(statements) == 3, statements  # the timeout, the column, the comment
