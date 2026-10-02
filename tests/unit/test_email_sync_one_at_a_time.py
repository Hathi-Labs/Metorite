"""EM-T4f part 2 — one sync cycle at a time for each mailbox.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4f.

Production, 2026-10-02. The loop and a second first sync of one mailbox
upserted the same ``(account_id, provider_message_id)`` keys. The upsert is
``ON CONFLICT ... DO UPDATE``, so the loop waited on the uncommitted rows of
the other run until the statement timeout of 2 minutes. Nothing made
``_sync_account`` run once at a time for each mailbox.

``_sync_account`` now takes a lock for each mailbox before it runs
``_sync_cycle``. The loop, the webhook, the manual sync and the resync skip
when a sync holds the mailbox, and a skip marks the mailbox for ONE rerun by
the holder. The deep downloads wait, with a bound (fix round 2).

R7 fence named here: ``email-one-sync-per-mailbox``.

**Hermetic.** A fake ``_sync_cycle`` that parks on an event.

* A skip caller skips while a sync runs, and a waiting caller runs after it.
  Two mailboxes run at the same time. An upper-case id is the same mailbox.
* A skip during a held sync causes exactly one shallow rerun after the
  holder ends, with the tenant of the holder. Two skips cause one rerun. A
  waiter, an error or a cancel causes none.
* The lock is released after an exception and after a cancel, and a waiter
  that gives up at the bound leaves no lock behind. The dicts go empty.
* The loop passes ``if_busy="skip"``, a skip adds no backoff, and the loop
  stops when its row is gone. It drops its entry only while the entry is its
  own task.
* The manual sync and the resync answer 409 at once while a sync runs, and
  the resync purges nothing then.
* Structure: only ``_sync_account`` reaches ``_sync_cycle``, the wrapper opens
  no session, each call of ``_sync_account`` in the code has a constant busy
  mode, and no caller holds a block across the call.

**R8.** On the promoted two-org catalog as ``acb_app_h3rls``: sync A parks
inside phase (c), with an uncommitted row in its open transaction. Sync B of
the same mailbox does not fetch until A is done, and a skip call returns busy.
Both then finish, with no duplicate rows. A resync purges the old rows and
clears the cursor inside the cycle, so the provider gets no cursor.

Run (real Postgres)::

    bash scripts/dev_db.sh && eval "$(bash scripts/dev_db.sh --export)"
    uv run pytest tests/unit/test_email_sync_one_at_a_time.py -v -rs
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.db import bind_tenant, clear_tenant, current_tenant, release_tenant
from email_ingestion.providers.base import EmailAddress, EmailMessage, SyncResult
from fastapi import BackgroundTasks, HTTPException
from gateway.routes.email.transport import sync as sync_mod
from sqlalchemy import text

from tests.unit._tenant_ladder import tenant_engine_scope
from tests.unit.test_email_scheduler_tenancy import (
    _assert_non_priv,
    _purge,
    _seed_account,
    _Store,
)

# ``promoted`` and ``app_engine`` are fixtures, used by name, so the import is
# load-bearing even though it reads as unused.
from tests.unit.test_h3_rls_promotion_rehearsal import (  # noqa: F401
    _DB_GATE,
    app_engine,
    promoted,
)

_ROOT = Path(__file__).resolve().parents[2]
_EMAIL = _ROOT / "apps/services/gateway/gateway/routes/email"
_SCHEDULER = _ROOT / "apps/services/email_ingestion/email_ingestion/scheduler.py"


@pytest.fixture(autouse=True)
def _no_lock_left_over():
    """Each test starts and ends with no mailbox lock in the module."""
    assert sched._sync_locks == {} and sched._sync_lock_users == {}
    assert sched._sync_rerun == set()
    yield
    assert sched._sync_locks == {}, "a mailbox lock outlived its users"
    assert sched._sync_lock_users == {}
    assert sched._sync_rerun == set(), "a rerun mark outlived its lock"


class _Parked:
    """A fake ``_sync_cycle``. Each call records its start, its end, its
    arguments and its tenant. A call for a mailbox in ``park`` waits for that
    mailbox's gate."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.calls: list[dict[str, Any]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.started: dict[str, asyncio.Event] = {}
        self.raise_for: set[str] = set()
        self.error_for: set[str] = set()

    def park(self, account_id: str) -> asyncio.Event:
        self.gates[account_id] = asyncio.Event()
        self.started[account_id] = asyncio.Event()
        return self.gates[account_id]

    def starts(self, account_id: str = "acc-1") -> int:
        return self.events.count(("start", account_id))

    async def __call__(self, account_id: str, **kw: Any) -> dict[str, Any]:
        self.events.append(("start", account_id))
        self.calls.append({"account_id": account_id, "tenant": current_tenant(),
                           "busy_during": sched.sync_busy(account_id), **kw})
        if account_id in self.started:
            self.started[account_id].set()
        # Park each mailbox once, so a second call runs straight. The first
        # call takes the gate, also when a cancel ends its wait.
        gate = self.gates.pop(account_id, None)
        try:
            if gate is not None:
                await gate.wait()
            if account_id in self.raise_for:
                raise RuntimeError("the provider failed")
            if account_id in self.error_for:
                return {"error": "Provider authentication failed"}
            return {"synced": 1}
        finally:
            self.events.append(("end", account_id))


@pytest.fixture()
def cycle(monkeypatch) -> _Parked:
    fake = _Parked()
    monkeypatch.setattr(sched, "_sync_cycle", fake)
    return fake


async def _until(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=5)


class _SchedAsyncio:
    """The ``asyncio`` name of the scheduler module, with its own ``sleep``.

    Fix round 2: a test patches the scheduler's reference, never the global
    ``asyncio.sleep`` that every other coroutine of the test also uses."""

    def __init__(self, sleep: Any) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


# ── hermetic: skip, wait, and two mailboxes ─────────────────────────────────


class TestOneSyncForEachMailbox:

    async def test_a_skip_caller_skips_while_a_sync_runs(self, cycle, caplog):
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        assert sched.sync_busy("acc-1")
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            skipped = await sched._sync_account("acc-1", if_busy="skip")
        assert skipped == sched.SYNC_SKIPPED_BUSY
        assert "sync.skipped_busy account_id=acc-1" in caplog.text
        assert cycle.events == [("start", "acc-1")], "the skip ran a cycle"
        gate.set()
        await first
        assert not sched.sync_busy("acc-1")

    async def test_a_waiting_caller_runs_after_the_first_finishes(self, cycle):
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        second = asyncio.create_task(sched._sync_account("acc-1"))
        await asyncio.sleep(0.05)
        assert cycle.events == [("start", "acc-1")], (
            "the waiting caller started a cycle while the first one ran")
        assert sched._sync_lock_users["acc-1"] == 2
        gate.set()
        assert await first == {"synced": 1}
        assert await second == {"synced": 1}
        assert cycle.events == [("start", "acc-1"), ("end", "acc-1"),
                                ("start", "acc-1"), ("end", "acc-1")]

    async def test_a_skip_caller_also_skips_while_a_caller_waits(self, cycle):
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        second = asyncio.create_task(sched._sync_account("acc-1"))
        await asyncio.sleep(0)
        assert await sched._sync_account("acc-1", if_busy="skip") == (
            sched.SYNC_SKIPPED_BUSY)
        gate.set()
        await asyncio.gather(first, second)

    async def test_two_mailboxes_sync_at_the_same_time(self, cycle):
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        other = await asyncio.wait_for(sched._sync_account("acc-2"), timeout=5)
        assert other == {"synced": 1}, "a second mailbox waited for the first"
        assert ("end", "acc-2") in cycle.events
        assert ("end", "acc-1") not in cycle.events
        gate.set()
        await first

    async def test_an_upper_case_id_is_the_same_mailbox(self, cycle):
        """Fix round 2: the lock key is the id in lower case."""
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        assert sched.sync_busy("ACC-1")
        assert await sched._sync_account("ACC-1", if_busy="skip") == (
            sched.SYNC_SKIPPED_BUSY)
        assert list(sched._sync_locks) == ["acc-1"]
        gate.set()
        await first

    def test_an_unknown_busy_mode_is_refused(self):
        with pytest.raises(ValueError, match="if_busy"):
            asyncio.run(sched._sync_account("acc-1", if_busy="queue"))


# ── hermetic: one rerun after a skip ────────────────────────────────────────


class TestASkipCausesOneRerun:

    async def test_a_skip_during_a_held_sync_causes_one_shallow_rerun(self, cycle):
        gate = cycle.park("acc-1")
        token = bind_tenant("org-of-the-holder")
        try:
            first = asyncio.create_task(
                sched._sync_account("acc-1", deep=True))
        finally:
            release_tenant(token)
        await _until(cycle.started["acc-1"])
        await sched._sync_account("acc-1", if_busy="skip")
        assert cycle.starts() == 1
        gate.set()
        result = await first
        assert cycle.starts() == 2, "the skip caused no rerun"
        rerun = cycle.calls[1]
        assert rerun["deep"] is False, "the rerun was not shallow"
        assert rerun["busy_during"], "the rerun ran after the lock was gone"
        assert rerun["tenant"] == "org-of-the-holder", (
            "the rerun lost the tenant binding of the holder")
        assert result == {"synced": 2, "reran": True}

    async def test_two_skips_cause_one_rerun(self, cycle):
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        await sched._sync_account("acc-1", if_busy="skip")
        await sched._sync_account("acc-1", if_busy="skip")
        gate.set()
        await first
        assert cycle.starts() == 2

    async def test_no_skip_causes_no_rerun(self, cycle):
        assert await sched._sync_account("acc-1") == {"synced": 1}
        assert cycle.starts() == 1

    async def test_a_waiter_makes_the_rerun_needless(self, cycle):
        """The cycle of the waiter starts after the skip, so it fetches the
        mail, and it clears the mark when it starts."""
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        await sched._sync_account("acc-1", if_busy="skip")
        second = asyncio.create_task(sched._sync_account("acc-1"))
        await asyncio.sleep(0)
        gate.set()
        await asyncio.gather(first, second)
        assert cycle.starts() == 2, "a waiter and a rerun both ran"

    async def test_a_failed_holder_causes_no_rerun(self, cycle):
        cycle.error_for.add("acc-1")
        gate = cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        await sched._sync_account("acc-1", if_busy="skip")
        gate.set()
        assert "error" in await first
        assert cycle.starts() == 1

    async def test_a_cancelled_holder_causes_no_rerun(self, cycle):
        cycle.park("acc-1")
        first = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        await sched._sync_account("acc-1", if_busy="skip")
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert cycle.starts() == 1


# ── hermetic: the lock is released on every exit ────────────────────────────


class TestTheLockIsReleasedOnEveryExit:

    async def test_after_an_exception(self, cycle):
        cycle.raise_for.add("acc-1")
        with pytest.raises(RuntimeError, match="the provider failed"):
            await sched._sync_account("acc-1")
        assert not sched.sync_busy("acc-1")
        cycle.raise_for.clear()
        assert await sched._sync_account("acc-1") == {"synced": 1}

    async def test_after_a_cancel_of_the_holder(self, cycle):
        cycle.park("acc-1")
        holder = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        waiter = asyncio.create_task(sched._sync_account("acc-1"))
        await asyncio.sleep(0)
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        assert await asyncio.wait_for(waiter, timeout=5) == {"synced": 1}, (
            "the waiter never got the lock that the cancelled holder had")

    async def test_after_a_cancel_of_a_waiter(self, cycle):
        gate = cycle.park("acc-1")
        holder = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        waiter = asyncio.create_task(sched._sync_account("acc-1"))
        await asyncio.sleep(0)
        assert sched._sync_lock_users["acc-1"] == 2
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert sched._sync_lock_users["acc-1"] == 1, "the cancelled waiter stayed"
        gate.set()
        await holder
        assert cycle.starts() == 1

    async def test_a_waiter_gives_up_at_the_bound(self, cycle, monkeypatch, caplog):
        monkeypatch.setattr(sched, "SYNC_LOCK_WAIT_SECS", 0.05)
        gate = cycle.park("acc-1")
        holder = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        with caplog.at_level(logging.WARNING, logger=sched.logger.name):
            gave_up = await sched._sync_account("acc-1")
        assert gave_up == sched.SYNC_SKIPPED_BUSY
        assert "sync.busy_wait_timeout account_id=acc-1" in caplog.text
        gate.set()
        assert await holder == {"synced": 1}

    async def test_the_lock_dicts_do_not_grow(self, cycle):
        for i in range(25):
            await sched._sync_account(f"acc-{i}")
        assert sched._sync_locks == {} and sched._sync_lock_users == {}

    def test_the_bound_fits_a_first_sync(self):
        assert sched.SYNC_LOCK_WAIT_SECS == 600.0


# ── hermetic: the loop ──────────────────────────────────────────────────────


class _StopLoop(Exception):
    """Raised by the fake sleep, to end the forever loop of a test."""


class TestTheLoop:

    async def test_the_loop_skips_when_busy_and_adds_no_backoff(self, monkeypatch):
        seen: dict[str, Any] = {}

        async def _sync(account_id, **kw):
            seen["if_busy"] = kw.get("if_busy")
            return dict(sched.SYNC_SKIPPED_BUSY)

        async def _hook(hook, account_id):
            return None

        async def _interval(account_id, organization_id):
            return 300

        async def _sleep(secs):
            seen["slept"] = secs
            raise _StopLoop

        monkeypatch.setattr(sched, "_sync_account", _sync)
        monkeypatch.setattr(sched, "run_hook", _hook)
        monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
        monkeypatch.setattr(sched, "asyncio", _SchedAsyncio(_sleep))
        with pytest.raises(_StopLoop):
            await sched._account_sync_loop("acc-1", 300, organization_id="org-1")
        assert seen == {"if_busy": "skip", "slept": 300}, (
            "a busy skip must not count as a failure")

    async def test_the_loop_stops_when_its_row_is_gone(self, monkeypatch, caplog):
        async def _no_hook(*_a, **_k):
            raise AssertionError("a loop with no row ran a hook")

        async def _no_sleep(_secs):
            raise AssertionError("a loop with no row slept for its next tick")

        async def _gone(account_id, **_kw):
            return dict(sched.ACCOUNT_GONE)

        monkeypatch.setattr(sched, "_sync_cycle", _gone)
        monkeypatch.setattr(sched, "run_hook", _no_hook)
        monkeypatch.setattr(sched, "asyncio", _SchedAsyncio(_no_sleep))
        task = asyncio.create_task(
            sched._account_sync_loop("acc-1", 300, organization_id="org-1"))
        monkeypatch.setitem(sched._scheduler_tasks, "acc-1", task)
        with caplog.at_level(logging.INFO, logger=sched.logger.name):
            await asyncio.wait_for(task, timeout=5)
        assert task.done() and task.exception() is None
        assert "acc-1" not in sched._scheduler_tasks, "the gone loop kept its entry"
        assert "sync.loop_row_gone account_id=acc-1" in caplog.text

    async def test_a_gone_loop_keeps_the_entry_of_a_newer_loop(self, monkeypatch):
        async def _gone(account_id, **_kw):
            return dict(sched.ACCOUNT_GONE)

        monkeypatch.setattr(sched, "_sync_cycle", _gone)
        newer = asyncio.create_task(asyncio.sleep(3600))
        monkeypatch.setitem(sched._scheduler_tasks, "acc-1", newer)
        try:
            await asyncio.wait_for(
                sched._account_sync_loop("acc-1", 300, organization_id="org-1"),
                timeout=5)
            assert sched._scheduler_tasks.get("acc-1") is newer
        finally:
            newer.cancel()

    async def test_an_error_that_is_not_gone_keeps_the_loop(self, monkeypatch):
        async def _fails(account_id, **_kw):
            return {"error": "Provider authentication failed"}

        async def _hook(hook, account_id):
            return None

        async def _interval(account_id, organization_id):
            return 300

        slept: list[int] = []

        async def _sleep(secs):
            slept.append(secs)
            raise _StopLoop

        monkeypatch.setattr(sched, "_sync_cycle", _fails)
        monkeypatch.setattr(sched, "run_hook", _hook)
        monkeypatch.setattr(sched, "_get_account_sync_interval", _interval)
        monkeypatch.setattr(sched, "asyncio", _SchedAsyncio(_sleep))
        with pytest.raises(_StopLoop):
            await sched._account_sync_loop("acc-1", 300, organization_id="org-1")
        assert slept == [600], "a failed tick must back off and loop on"


# ── hermetic: the manual sync and the resync ────────────────────────────────


class TestTheManualSyncNeverWaits:

    async def test_a_busy_manual_sync_answers_409_at_once(self, cycle):
        """Fix round 2: the proxy gives this POST 30 seconds, so the route
        must not wait. The refusal marks the mailbox for one rerun."""
        gate = cycle.park("acc-1")
        holder = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        with pytest.raises(HTTPException) as err:
            await asyncio.wait_for(
                sync_mod._run_manual_sync("acc-1", BackgroundTasks(), full=False),
                timeout=1)
        assert err.value.status_code == 409
        assert err.value.detail == sync_mod.SYNC_BUSY_DETAIL
        assert sched._sync_lock_users["acc-1"] == 1, "the manual sync queued"
        gate.set()
        await holder
        assert cycle.starts() == 2, "the refusal did not ask for one rerun"

    async def test_a_busy_full_sync_says_to_start_it_again(self, monkeypatch):
        async def _busy(account_id, **_kw):
            return dict(sched.SYNC_SKIPPED_BUSY)

        monkeypatch.setattr(sched, "_sync_account", _busy)
        with pytest.raises(HTTPException) as err:
            await sync_mod._run_manual_sync("acc-1", BackgroundTasks(), full=True)
        assert err.value.status_code == 409
        assert err.value.detail == sync_mod.FULL_SYNC_BUSY_DETAIL

    async def test_a_busy_resync_purges_nothing(self, cycle, monkeypatch):
        """Fix round 2: the purge and the cursor reset run inside the cycle,
        under the lock. While another sync holds the mailbox, the resync
        answers 409 and writes nothing."""
        statements: list[str] = []

        class _Owned:
            def fetchone(self):
                return ("acc-1",)

        class _DB:
            async def execute(self, stmt, params=None):
                statements.append(" ".join(str(stmt).split()))
                return _Owned()

        @asynccontextmanager
        async def _session(*_a):
            yield _DB()

        monkeypatch.setattr(sync_mod, "_tenant_session", _session)
        gate = cycle.park("acc-1")
        holder = asyncio.create_task(sched._sync_account("acc-1"))
        await _until(cycle.started["acc-1"])
        user = type("U", (), {"email": "owner@em-t4f.test"})()
        with pytest.raises(HTTPException) as err:
            # A bound, so a resync that waits fails here and hangs nothing.
            await asyncio.wait_for(sync_mod.resync_account(
                "acc-1", BackgroundTasks(), purge=True, user=user), timeout=1)
        assert err.value.status_code == 409
        assert err.value.detail == sync_mod.FULL_SYNC_BUSY_DETAIL
        assert all(s.startswith("SELECT") for s in statements), (
            f"the busy resync wrote before the lock: {statements}")
        gate.set()
        await holder
        assert not any(c.get("purge") for c in cycle.calls)

    async def test_a_free_resync_passes_the_purge_and_the_reset_into_the_cycle(
        self, cycle, monkeypatch,
    ):
        class _Owned:
            def fetchone(self):
                return ("acc-1",)

        class _DB:
            async def execute(self, stmt, params=None):
                return _Owned()

        @asynccontextmanager
        async def _session(*_a):
            yield _DB()

        monkeypatch.setattr(sync_mod, "_tenant_session", _session)
        user = type("U", (), {"email": "owner@em-t4f.test"})()
        await sync_mod.resync_account("acc-1", BackgroundTasks(), purge=True, user=user)
        assert cycle.calls[0]["purge"] is True
        assert cycle.calls[0]["reset_cursor"] is True
        assert cycle.calls[0]["deep"] is True


# ── hermetic: structure ─────────────────────────────────────────────────────


def _calls(tree: ast.AST, name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            called = fn.id if isinstance(fn, ast.Name) else (
                fn.attr if isinstance(fn, ast.Attribute) else "")
            if called == name:
                out.append(node)
    return out


#: What ``_if_busy`` reports for a mode that is not a constant.
DYNAMIC = "<not a constant>"


def _if_busy(call: ast.Call) -> str | None:
    """The busy mode of one call: the constant, ``DYNAMIC``, or ``None`` when
    the call passes no ``if_busy`` (the default, "wait")."""
    for kw in call.keywords:
        if kw.arg == "if_busy":
            if isinstance(kw.value, ast.Constant):
                return kw.value.value
            return DYNAMIC
        if kw.arg is None:  # **kwargs can carry a mode
            return DYNAMIC
    return None


def test_a_mode_that_is_not_a_constant_is_not_read_as_wait():
    call = ast.parse("_sync_account(a, if_busy=mode)").body[0].value
    assert _if_busy(call) == DYNAMIC
    call = ast.parse("_sync_account(a, **opts)").body[0].value
    assert _if_busy(call) == DYNAMIC
    call = ast.parse("_sync_account(a)").body[0].value
    assert _if_busy(call) is None


def test_only_sync_account_reaches_the_cycle():
    """Every caller gets the lock: only the wrapper and its rerun call
    ``_sync_cycle``, and only the wrapper calls the rerun."""
    tree = ast.parse(_SCHEDULER.read_text(encoding="utf-8"))
    owners = sorted(
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
        and _calls(fn, "_sync_cycle"))
    assert owners == ["_rerun_once", "_sync_account"]
    rerun_callers = [
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
        and _calls(fn, "_rerun_once")]
    assert rerun_callers == ["_sync_account"]
    for path in _code_files():
        source = path.read_text(encoding="utf-8")
        # Another package may have its own ``_sync_cycle`` (the CRM does).
        # A file that names this scheduler and the cycle reaches past the lock.
        if path != _SCHEDULER and "email_ingestion" in source:
            assert "_sync_cycle" not in source, (
                f"{path} calls the cycle past the lock")


def test_the_wrapper_opens_no_session():
    src = inspect.getsource(sched._sync_account)
    for opener in ("tenant_session", "get_db", "get_session_factory"):
        assert opener not in src, f"_sync_account opens a session ({opener})"
    assert src.index("lock.acquire()") < src.index("_sync_cycle(")


def _code_files() -> list[Path]:
    """Every non-test Python file of the services and the packages."""
    out = []
    for top in ("apps", "packages"):
        for path in (_ROOT / top).rglob("*.py"):
            parts = set(path.parts)
            if parts & {"tests", "node_modules", ".venv", "__pycache__"}:
                continue
            if path.name.startswith("test_"):
                continue
            out.append(path)
    return out


#: Each call of ``_sync_account`` in the code, and its busy mode. ``None`` is
#: the default, "wait". A new call fails this test until somebody decides its
#: mode. Fix round 2: every non-test file, one entry per CALL.
CALLERS: list[tuple[str, str, str | None]] = sorted([
    ("apps/services/email_ingestion/email_ingestion/scheduler.py",
     "_account_sync_loop", "skip"),
    ("apps/services/gateway/gateway/routes/email/transport/sync.py",
     "_webhook_sync", "skip"),
    ("apps/services/gateway/gateway/routes/email/transport/sync.py",
     "_run_manual_sync", "skip"),
    ("apps/services/gateway/gateway/routes/email/automation/cleanup.py",
     "_backfill_and_clean_job", None),
    ("apps/services/gateway/gateway/routes/email/automation/runner.py",
     "_download_past_range", None),
])


def _innermost_function(tree: ast.AST) -> dict[int, str]:
    """For each call node id, the name of the nearest enclosing function."""
    owner: dict[int, str] = {}

    def visit(node: ast.AST, name: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AsyncFunctionDef | ast.FunctionDef):
                visit(child, child.name)
            else:
                if isinstance(child, ast.Call):
                    owner[id(child)] = name
                visit(child, name)

    visit(tree, "<module>")
    return owner


def _caller_modes() -> list[tuple[str, str, str | None]]:
    found: list[tuple[str, str, str | None]] = []
    for path in _code_files():
        source = path.read_text(encoding="utf-8")
        if "_sync_account" not in source:
            continue
        tree = ast.parse(source)
        owner = _innermost_function(tree)
        label = path.relative_to(_ROOT).as_posix()
        for call in _calls(tree, "_sync_account"):
            if owner.get(id(call)) == "_sync_account":
                continue
            found.append((label, owner.get(id(call), "<module>"), _if_busy(call)))
    return sorted(found)


def test_each_call_has_a_decided_busy_mode():
    assert _caller_modes() == CALLERS


#: Calls that take the mailbox lock. None of them may run inside a block.
_LOCKING_CALLS = ("_sync_account", "_run_manual_sync")


def _sync_calls_inside_a_block(source: str) -> list[int]:
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens = any(
            isinstance(item.context_expr, ast.Call)
            and getattr(item.context_expr.func, "id",
                        getattr(item.context_expr.func, "attr", "")
                        ).endswith("tenant_session")
            for item in node.items)
        if opens:
            hits += [c.lineno for stmt in node.body
                     for name in _LOCKING_CALLS for c in _calls(stmt, name)]
    return sorted(hits)


def test_no_caller_holds_a_block_while_it_waits():
    for path in _code_files():
        source = path.read_text(encoding="utf-8")
        if not any(name in source for name in _LOCKING_CALLS):
            continue
        assert _sync_calls_inside_a_block(source) == [], (
            f"{path} calls into the mailbox lock inside a tenant block")


def test_the_block_check_can_fail():
    planted = (
        "async def f(a, bg):\n"
        "    async with _tenant_session() as db:\n"
        "        await _sync_account(a)\n"
        "        await _run_manual_sync(a, bg, full=False)\n"
    )
    assert _sync_calls_inside_a_block(planted) == [3, 4]


# ── hermetic: the deep downloads end as an error when they did not fetch ──


class TestADeepDownloadThatDidNotRunEndsAsAnError:
    """Fix round 2 (item 3). A busy result or an ``error`` result ends the
    job as an error with a reason. Before, the job said "done" with 0
    fetched."""

    @pytest.mark.parametrize("result,reason", [
        ({"skipped": "busy", "synced": 0}, "ran for too long"),
        ({"error": "Provider authentication failed"},
         "failed: Provider authentication failed"),
    ], ids=["busy", "error"])
    async def test_process_past_ends_as_an_error(self, monkeypatch, result, reason):
        from gateway.routes.email.automation import runner as runner_mod

        async def _sync(account_id, **_kw):
            return dict(result)

        monkeypatch.setattr(sched, "_sync_account", _sync)
        token = runner_mod._past_job_start(
            "acc-1", "owner@em-t4f.test", 0, False, downloading=True)
        try:
            await runner_mod._process_past_emails_job(
                "acc-1", None, None, 10, False, "owner@em-t4f.test",
                job_token=token)
            job = runner_mod._PAST_JOBS.get("acc-1")
            assert job["status"] == "error"
            assert reason in job["error"]
        finally:
            runner_mod._PAST_JOBS.pop("acc-1", None)

    async def test_process_past_still_applies_after_a_raised_error(self, monkeypatch):
        from gateway.routes.email.automation import runner as runner_mod

        async def _raises(account_id, **_kw):
            raise RuntimeError("the pool is gone")

        monkeypatch.setattr(sched, "_sync_account", _raises)
        assert await runner_mod._download_past_range("acc-1", None) is None

    @pytest.mark.parametrize("result,reason", [
        ({"skipped": "busy", "synced": 0}, "ran for too long"),
        ({"error": "Provider authentication failed"},
         "failed: Provider authentication failed"),
    ], ids=["busy", "error"])
    async def test_the_cleanup_backfill_ends_as_an_error(
        self, monkeypatch, result, reason,
    ):
        from gateway.routes.email.automation import cleanup as cleanup_mod

        class _Count:
            n = 7

            def fetchone(self):
                return self

        class _DB:
            async def execute(self, stmt, params=None):
                if not str(stmt).lstrip().startswith("SELECT COUNT"):
                    raise AssertionError("the job went on after the failure")
                return _Count()

        @asynccontextmanager
        async def _session(*_a):
            yield _DB()

        async def _sync(account_id, **_kw):
            return dict(result)

        monkeypatch.setattr(cleanup_mod, "_tenant_session", _session)
        monkeypatch.setattr(sched, "_sync_account", _sync)
        token = cleanup_mod._SWEEP_JOBS.start(
            "acc-1", owner="owner@em-t4f.test", status="running",
            phase="downloading")
        try:
            await cleanup_mod._backfill_and_clean_job(
                "acc-1", None, "owner@em-t4f.test", token)
            job = cleanup_mod._SWEEP_JOBS.get("acc-1")
            assert job["status"] == "error" and job["phase"] == "error"
            assert reason in job["error"]
        finally:
            cleanup_mod._SWEEP_JOBS.pop("acc-1", None)


# ── R8: two syncs of one mailbox on a real database ─────────────────────────


def _message(i: int) -> EmailMessage:
    return EmailMessage(
        provider_message_id=f"pm-shared-{i}",
        thread_id=f"t-{i}",
        folder="INBOX",
        from_address=EmailAddress(name="S", email="s@sender.test"),
        subject=f"hello {i}",
        body_text="body",
        snippet="body",
        received_at=datetime.now(UTC),
    )


class _SameKeys:
    """A provider that returns the same message keys for each sync."""

    def __init__(self, label: str, fetches: list[str]) -> None:
        self.label = label
        self.fetches = fetches

    async def authenticate(self) -> bool:
        return True

    def credentials_dirty(self) -> bool:
        return False

    def export_credentials(self) -> dict:
        return {"access_token": "at"}

    async def sync_messages(self, **_kw) -> SyncResult:
        self.fetches.append(self.label)
        return SyncResult(messages=[_message(i) for i in range(3)],
                          new_history_id="h-1")

    async def import_batches(self, **_kw):
        """The first import finds no older mail (EM-T6b)."""
        for batch in ():
            yield batch

    async def get_message(self, provider_message_id):
        raise RuntimeError("no body backfill in this test")


@_DB_GATE
class TestTwoSyncsOfOneMailboxDoNotOverlap:

    async def test_the_second_sync_waits_for_the_first_not_for_its_rows(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner=f"b-{uuid.uuid4().hex[:6]}@em-t4f.test")
        from acb_llm import key_store

        fetches: list[str] = []
        built: list[int] = []

        def _build(name, creds):
            built.append(1)
            return _SameKeys(f"sync-{len(built)}", fetches)

        a_parked = asyncio.Event()
        release_a = asyncio.Event()
        real_upsert = sched.upsert_message
        upserts = {"n": 0}

        async def _parking_upsert(db, aid, msg):
            await real_upsert(db, aid, msg)
            upserts["n"] += 1
            if upserts["n"] == 1:
                # Sync A holds an uncommitted row in its open phase (c).
                a_parked.set()
                await release_a.wait()

        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider", _build)
        monkeypatch.setattr(sched, "upsert_message", _parking_upsert)
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        sync_a = sync_b = None
        try:
            async with tenant_engine_scope(app_dsn):
                try:
                    sync_a = asyncio.create_task(sched._sync_account(
                        account_id, organization_id=p.org_b))
                    await asyncio.wait_for(a_parked.wait(), timeout=15)
                    sync_b = asyncio.create_task(sched._sync_account(
                        account_id, organization_id=p.org_b))
                    await asyncio.sleep(0.5)
                    assert fetches == ["sync-1"], (
                        "sync B reached the provider while sync A held "
                        "uncommitted rows of the same keys")
                    assert await sched._sync_account(
                        account_id, organization_id=p.org_b, if_busy="skip",
                    ) == sched.SYNC_SKIPPED_BUSY
                finally:
                    # Release A before the scope closes its engine. Else a
                    # failed assertion leaves A parked in an open transaction,
                    # and the dispose of the engine waits for it.
                    release_a.set()
                    pending = [t for t in (sync_a, sync_b) if t is not None]
                    done = await asyncio.wait_for(
                        asyncio.gather(*pending, return_exceptions=True),
                        timeout=60)
                res_a, res_b = done
            assert res_a == {"synced": 3, "history_id": "h-1"}, res_a
            assert res_b == {"synced": 3, "history_id": "h-1"}, res_b
            assert fetches == ["sync-1", "sync-2"]
            with p.admin_engine.connect() as c:
                rows = c.execute(text(
                    "SELECT count(*) FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).scalar_one()
                logs = c.execute(text(
                    "SELECT status FROM email_sync_log "
                    "WHERE account_id = CAST(:a AS uuid)"),
                    {"a": account_id}).scalars().all()
            assert rows == 3, "the two syncs wrote duplicate rows"
            assert sorted(logs) == ["success", "success"]
        finally:
            release_a.set()
            for task in (sync_a, sync_b):
                if task is not None and not task.done():
                    task.cancel()
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": account_id})
            _purge(p.admin_engine, [account_id])


class _RecordsCursor(_SameKeys):
    """A provider that records the cursor that each fetch gets."""

    def __init__(self, cursors: list[Any]) -> None:
        super().__init__("resync", [])
        self.cursors = cursors

    async def sync_messages(self, **kw) -> SyncResult:
        self.cursors.append(kw.get("history_id"))
        return await super().sync_messages(**kw)


@_DB_GATE
class TestTheResyncPurgesUnderTheLock:

    async def test_the_purge_and_the_reset_run_inside_the_cycle(
        self, promoted, app_engine, monkeypatch,  # noqa: F811
    ):
        """Fix round 2: the resync passes ``purge`` and ``reset_cursor`` into
        the cycle. Phase (a) deletes the old rows and clears the cursor, so
        the provider gets no cursor, under the lock of the mailbox."""
        _assert_non_priv(app_engine)
        p = promoted
        account_id = _seed_account(p.admin_engine, org=p.org_b,
                                   owner=f"b-{uuid.uuid4().hex[:6]}@em-t4f.test")
        with p.admin_engine.begin() as c:
            c.execute(text(
                "UPDATE email_accounts SET last_history_id = 'old-cursor' "
                "WHERE id = CAST(:a AS uuid)"), {"a": account_id})
            c.execute(text(
                "INSERT INTO email_messages (account_id, provider_message_id, "
                "from_address, to_addresses, subject, organization_id) "
                "VALUES (CAST(:a AS uuid), 'pm-stale', "
                "CAST('{\"email\": \"s@sender.test\"}' AS jsonb), "
                "CAST('[]' AS jsonb), 'stale', CAST(:o AS uuid))"),
                {"a": account_id, "o": p.org_b})
        from acb_llm import key_store

        cursors: list[Any] = []
        monkeypatch.setattr(key_store, "get_key_store", lambda: _Store())
        monkeypatch.setattr(sched, "build_provider",
                            lambda name, creds: _RecordsCursor(cursors))
        app_dsn = p.app_url.render_as_string(hide_password=False)
        token = clear_tenant()
        try:
            async with tenant_engine_scope(app_dsn):
                res = await sched._sync_account(
                    account_id, organization_id=p.org_b, deep=True,
                    if_busy="skip", purge=True, reset_cursor=True)
            assert res == {"synced": 3, "history_id": "h-1"}, res
            assert cursors == [None], "the provider got the old cursor"
            with p.admin_engine.connect() as c:
                ids = c.execute(text(
                    "SELECT provider_message_id FROM email_messages "
                    "WHERE account_id = CAST(:a AS uuid) "
                    "ORDER BY provider_message_id"),
                    {"a": account_id}).scalars().all()
                cursor = c.execute(text(
                    "SELECT last_history_id FROM email_accounts "
                    "WHERE id = CAST(:a AS uuid)"), {"a": account_id}).scalar_one()
            assert "pm-stale" not in ids, "the purge left the old rows"
            assert ids == ["pm-shared-0", "pm-shared-1", "pm-shared-2"]
            assert cursor == "h-1"
        finally:
            release_tenant(token)
            with p.admin_engine.begin() as c:
                c.execute(text("DELETE FROM email_messages WHERE account_id = "
                               "CAST(:a AS uuid)"), {"a": account_id})
            _purge(p.admin_engine, [account_id])
