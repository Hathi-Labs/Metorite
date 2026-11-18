"""TOF-2b — the one write seam keeps its bounds and never fails a tool (P7).

Spec: ``project-docs/specs/tool_failure_log.md`` §6 and §9 P7. Code:
``acb_common.failure_log.record_tool_failure`` and its drain.

What this file proves, with no database and no Redis (the R8 twin is
``tests/unit/test_failure_store_r8.py``):

* No tenant, an org off ``TOOL_FAILURE_LOG_ORGS`` and a bad field each
  record nothing, and nothing raises.
* The record holds strings only, so it keeps no reference to the exception.
* A full queue drops the record and counts it.
* Equal records join into one ``failure_tally`` call with ``p_n``.
* The sample cap stops event rows at 20 for each fingerprint and hour, and
  the count goes on. A Redis fault writes the rows.
* With the store down, a tool answers as before, and the drain logs the class
  only.
* The org of a write is the org that the record bound, never an argument.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import weakref
from typing import Any

import pytest
from acb_common import failure_fingerprint as ff
from acb_common import failure_log as fl
from acb_common.db import bind_tenant, release_tenant

ORG_A = "0e7a4b5c-1d2e-4f30-8a41-5b6c7d8e9f01"
ORG_B = "1f8b5c6d-2e3f-4a41-9b52-6c7d8e9f0a12"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """A fresh queue, the flag on for org A and org B, and a known set."""
    from acb_common import get_settings

    saved = fl.builtin_names()
    fl._reset_for_tests()
    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", f"{ORG_A},{ORG_B}")
    fl.set_builtin_names({"create_task", "crm_get_record"})
    fl.set_release("abc1234")
    yield
    fl._reset_for_tests()
    fl.set_builtin_names(saved)


@contextlib.contextmanager
def tenant(org: str | None):
    token = bind_tenant(org) if org else None
    try:
        yield
    finally:
        release_tenant(token)


def _fail(org: str | None = ORG_A, *, message: str = "Ravi Kumar", **kw: Any) -> None:
    args = {"source": "chat_tool", "error_code": "exception", "tool": "create_task",
            "surface": "chat"}
    args.update(kw)
    with tenant(org):
        try:
            raise KeyError(message)
        except KeyError as exc:
            fl.record_tool_failure(exc, **args)


class FakeSession:
    def __init__(self, log: list[tuple[str, Any]], org: str) -> None:
        self.log, self.org = log, org

    async def execute(self, stmt: Any, params: Any = None) -> None:
        self.log.append((self.org, str(stmt), params))


class FakeStore:
    """Records each statement, by the org of the session that ran it."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.log: list[tuple[str, str, Any]] = []
        self.fail = fail

    @contextlib.asynccontextmanager
    async def open(self, org: str):
        if self.fail is not None:
            raise self.fail
        yield FakeSession(self.log, org)

    def tallies(self) -> list[tuple[str, dict[str, Any]]]:
        return [(org, p) for org, sql, p in self.log if "failure_tally" in sql]

    def events(self) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for org, sql, p in self.log:
            if "INSERT INTO failure_events" in sql:
                out.extend((org, row) for row in (p if isinstance(p, list) else [p]))
        return out


@pytest.fixture
def store(monkeypatch) -> FakeStore:
    fake = FakeStore()
    monkeypatch.setattr(fl, "_open_session", fake.open)

    async def no_cap(org: str, fingerprint: str, n: int) -> int:
        return min(n, fl.SAMPLE_CAP)

    monkeypatch.setattr(fl, "_sample_slots", no_cap)
    return fake


# ── What records nothing ─────────────────────────────────────────────────────


def test_no_tenant_records_nothing() -> None:
    _fail(None)
    assert fl.pending() == 0


def test_an_org_off_the_flag_records_nothing() -> None:
    _fail("2a9c6d7e-3f40-4b52-8c63-7d8e9f0a1b23")
    assert fl.pending() == 0


def test_an_empty_flag_records_nothing(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "")
    _fail()
    assert fl.pending() == 0


def test_a_star_flag_records_every_org(monkeypatch) -> None:
    from acb_common import get_settings

    monkeypatch.setattr(get_settings(), "tool_failure_log_orgs", "*")
    _fail("2a9c6d7e-3f40-4b52-8c63-7d8e9f0a1b23")
    assert fl.pending() == 1


@pytest.mark.parametrize("bad", [
    {"error_code": "Ravi failed"}, {"source": "tenant_text"}, {"error_code": None},
    {"source": 42},
])
def test_a_bad_field_records_nothing_and_never_raises(bad: dict[str, Any]) -> None:
    _fail(**bad)
    assert fl.pending() == 0


def test_a_bad_surface_reads_as_other() -> None:
    _fail(surface="Ravi's phone")
    [rec] = fl._peek()
    assert rec.surface == "other"


def test_the_record_keeps_strings_only() -> None:
    _fail(tool="ravi_custom_tool", member_email="Ravi@Acme.IN", run_id="r1")
    [rec] = fl._peek()
    assert rec.org == ORG_A
    assert rec.tool == "other" and rec.tool_name == "ravi_custom_tool"
    assert rec.member_email == "ravi@acme.in"
    assert rec.release == "abc1234"
    for value in vars(rec).values():
        assert value is None or isinstance(value, (str, int, float))
    assert "Ravi Kumar" not in repr(rec)


def test_the_record_holds_no_reference_to_the_exception() -> None:
    class Probe(KeyError):
        """A subclass takes a weak reference, and a builtin does not."""

    holder: list[weakref.ref[BaseException]] = []
    with tenant(ORG_A):
        try:
            raise Probe("Ravi")
        except Probe as exc:
            holder.append(weakref.ref(exc))
            fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                                   tool="create_task", surface="chat")
    gc.collect()
    assert holder[0]() is None
    assert fl.pending() == 1


def test_a_builtin_name_keeps_no_tool_name() -> None:
    _fail(tool="create_task")
    [rec] = fl._peek()
    assert rec.tool == "create_task" and rec.tool_name is None


def test_a_tenant_name_is_cut_and_made_storable() -> None:
    _fail(tool="x\x00" + "y" * 400, kind="mcp", error_code="mcp_timeout")
    [rec] = fl._peek()
    assert rec.tool == "mcp"
    assert rec.tool_name is not None and len(rec.tool_name) <= 200
    assert "\x00" not in rec.tool_name


def test_a_bad_release_writes_none() -> None:
    fl.set_release("not a sha")
    _fail()
    [rec] = fl._peek()
    assert rec.release is None


def test_record_never_raises_when_the_settings_fail(monkeypatch) -> None:
    def boom() -> None:
        raise RuntimeError("settings down")

    monkeypatch.setattr(fl, "failure_log_on", lambda org: boom())
    _fail()
    assert fl.pending() == 0


# ── The bound of the queue ───────────────────────────────────────────────────


def test_a_full_queue_drops_and_counts(monkeypatch) -> None:
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(fl._log, "warning", lambda event, **kw: logged.append((event, kw)))
    for _ in range(fl.QUEUE_MAX):
        _fail()
    assert fl.pending() == fl.QUEUE_MAX == 500
    _fail()
    _fail()
    assert fl.pending() == fl.QUEUE_MAX
    assert fl.dropped() == 2
    assert any(event == "failure_log.dropped" and kw.get("count") == 1
               for event, kw in logged)


# ── The drain ────────────────────────────────────────────────────────────────


def test_equal_records_join_into_one_tally(store: FakeStore) -> None:
    for i in range(5):
        _fail(message=f"Ravi {i}", run_id=f"run-{i}")
    took = asyncio.run(fl.flush())
    assert took == 5
    [(org, tally)] = store.tallies()
    assert org == ORG_A and tally["n"] == 5
    # The raise sits under tests/, which is no tree of §3.2 rule 4.
    assert tally["fp"] == ff.fingerprint("create_task", "exception", "builtins.KeyError", "-")
    assert tally["release"] == "abc1234"
    events = store.events()
    assert len(events) == 5
    assert sorted(row["run_id"] for _, row in events) == [f"run-{i}" for i in range(5)]
    for _, row in events:
        assert "organization_id" not in row
        assert "Ravi" not in repr(row)
    assert fl.pending() == 0


def test_two_orgs_write_in_two_sessions(store: FakeStore) -> None:
    _fail(ORG_A)
    _fail(ORG_B)
    _fail(ORG_B)
    asyncio.run(fl.flush())
    assert sorted((org, p["n"]) for org, p in store.tallies()) == [(ORG_A, 1), (ORG_B, 2)]


def test_other_fields_split_the_group(store: FakeStore) -> None:
    _fail(surface="chat")
    _fail(surface="whatsapp")
    _fail(error_code="bad_arguments")
    asyncio.run(fl.flush())
    assert len(store.tallies()) == 3


def test_a_count_over_the_tally_limit_splits_into_calls() -> None:
    assert list(fl._chunks(2500)) == [1000, 1000, 500]
    assert list(fl._chunks(1)) == [1]
    assert list(fl._chunks(0)) == []


def test_the_sample_cap_stops_rows_and_keeps_the_count(store: FakeStore, monkeypatch) -> None:
    seen: dict[str, int] = {}

    async def counter(org: str, fingerprint: str, n: int) -> int:
        before = seen.get(fingerprint, 0)
        seen[fingerprint] = before + n
        return max(0, min(n, fl.SAMPLE_CAP - before))

    monkeypatch.setattr(fl, "_sample_slots", counter)
    for _ in range(15):
        _fail()
    asyncio.run(fl.flush())
    for _ in range(15):
        _fail()
    asyncio.run(fl.flush())
    assert len(store.events()) == 20
    assert sum(p["n"] for _, p in store.tallies()) == 30


def test_a_redis_fault_writes_the_rows(store: FakeStore, monkeypatch) -> None:
    async def fault_free(org: str, fingerprint: str, n: int) -> int:
        return await fl._real_sample_slots(org, fingerprint, n)

    class Down:
        async def incr(self, *a: Any, **k: Any) -> int:
            raise ConnectionError("redis down")

        async def expire(self, *a: Any, **k: Any) -> bool:
            raise ConnectionError("redis down")

    monkeypatch.setattr(fl, "_redis", lambda: Down())
    monkeypatch.setattr(fl, "_sample_slots", fault_free)
    for _ in range(3):
        _fail()
    asyncio.run(fl.flush())
    assert len(store.events()) == 3
    assert store.tallies()[0][1]["n"] == 3


def test_the_redis_counter_is_a_tenant_key(monkeypatch) -> None:
    calls: list[tuple[str, Any]] = []

    class Up:
        async def incr(self, k: Any, amount: int = 1) -> int:
            calls.append(("incr", k.value, amount))
            return 25

        async def expire(self, k: Any, seconds: int) -> bool:
            calls.append(("expire", k.value, seconds))
            return True

    monkeypatch.setattr(fl, "_redis", lambda: Up())
    got = asyncio.run(fl._real_sample_slots(ORG_A, "00bc2542a73b3967", 10))
    assert got == 5
    assert calls[0][1].startswith(f"cc:{ORG_A}:{fl.SAMPLE_NAMESPACE}:00bc2542a73b3967:")


def test_the_store_down_never_fails_the_tool(monkeypatch) -> None:
    fake = FakeStore(fail=ConnectionRefusedError("db down at 10.0.0.5 for ravi@acme.in"))
    monkeypatch.setattr(fl, "_open_session", fake.open)

    async def no_cap(org: str, fingerprint: str, n: int) -> int:
        return n

    monkeypatch.setattr(fl, "_sample_slots", no_cap)
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(fl._log, "warning", lambda event, **kw: logged.append((event, kw)))

    def tool() -> str:
        try:
            raise KeyError("Ravi")
        except KeyError as exc:
            fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                                   tool="create_task", surface="chat")
            return "The tool failed (KeyError)."

    with tenant(ORG_A):
        assert tool() == "The tool failed (KeyError)."
    asyncio.run(fl.flush())
    [(_, kw)] = [x for x in logged if x[0] == "failure_log.write_failed"]
    assert kw == {"error": "ConnectionRefusedError"}


def test_a_slow_store_times_out(monkeypatch) -> None:
    @contextlib.asynccontextmanager
    async def slow(org: str):
        await asyncio.sleep(5)
        yield None

    monkeypatch.setattr(fl, "_open_session", slow)
    monkeypatch.setattr(fl, "WRITE_TIMEOUT_S", 0.05)

    async def no_cap(org: str, fingerprint: str, n: int) -> int:
        return n

    monkeypatch.setattr(fl, "_sample_slots", no_cap)
    logged: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(fl._log, "warning", lambda event, **kw: logged.append((event, kw)))
    _fail()
    asyncio.run(asyncio.wait_for(fl.flush(), timeout=2))
    assert ("failure_log.write_failed", {"error": "TimeoutError"}) in logged


def test_the_drain_starts_on_its_own_and_joins_in_its_window(store, monkeypatch) -> None:
    monkeypatch.setattr(fl, "JOIN_WINDOW_S", 0.05)

    async def run() -> None:
        with tenant(ORG_A):
            for _ in range(3):
                try:
                    raise KeyError("x")
                except KeyError as exc:
                    fl.record_tool_failure(exc, source="chat_tool", error_code="exception",
                                           tool="create_task", surface="chat")
        await asyncio.sleep(0.3)
        await fl.stop_drain()

    asyncio.run(run())
    [(_, tally)] = store.tallies()
    assert tally["n"] == 3


def test_the_org_of_the_write_is_the_bound_org(store: FakeStore) -> None:
    _fail(ORG_B, member_email=f"x@{ORG_A}")
    asyncio.run(fl.flush())
    assert {org for org, _ in store.tallies()} == {ORG_B}


# ── Fences added after the review of TOF-2b ─────────────────────────────────


def test_record_tool_failure_takes_no_org_argument() -> None:
    """R5: the org is the bound tenant. A parameter of that kind fails here (the
    mutation: add ``org=`` to the signature)."""
    import inspect

    names = set(inspect.signature(fl.record_tool_failure).parameters)
    assert not {n for n in names if "org" in n or "tenant" in n}, names


def test_the_writer_asks_the_field_check_before_it_queues(monkeypatch) -> None:
    """The mutation: skip ``fields_problem``. A bad record would then fail the
    whole tenant write, and the good rows of that write would roll back."""
    monkeypatch.setattr(ff, "fields_problem", lambda *_a, **_k: "forced refusal")
    _fail()
    assert fl.pending() == 0


def test_a_hung_redis_costs_one_wait_for_the_whole_org(store, monkeypatch) -> None:
    """Review P2: the slot lookups run at the same time. Six groups with a hung
    Redis must still write inside WRITE_TIMEOUT_S."""
    import time

    async def hung(org: str, fingerprint: str, n: int) -> int:
        await asyncio.sleep(0.5)
        return min(n, fl.SAMPLE_CAP)

    monkeypatch.setattr(fl, "_sample_slots", hung)
    for i in range(6):
        _fail(tool="create_task", error_code=("exception", "bad_arguments", "unknown_tool",
                                              "auth_401", "gateway_unreachable",
                                              "outside_unreachable")[i])
    start = time.monotonic()
    asyncio.run(fl.flush())
    assert time.monotonic() - start < fl.WRITE_TIMEOUT_S
    assert len(store.tallies()) >= 1


def test_a_record_from_a_thread_with_no_loop_still_drains(store) -> None:
    """Review P2: a sync tool in an executor has no running loop."""
    import threading
    import time

    t = threading.Thread(target=_fail)
    t.start()
    t.join()
    deadline = time.monotonic() + 5
    while fl.pending() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert fl.pending() == 0
    assert len(store.tallies()) == 1
