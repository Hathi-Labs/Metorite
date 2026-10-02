"""EM-T4f part 2 — one sync cycle at a time for each mailbox.

Spec: ``project-docs/specs/email_app_master_plan.md`` §10.4.6, EM-T4f.

Production, 2026-10-02. The loop and a second first sync of one mailbox
upserted the same ``(account_id, provider_message_id)`` keys. The upsert is
``ON CONFLICT ... DO UPDATE``, so the loop waited on the uncommitted rows of
the other run until the statement timeout of 2 minutes. Nothing made
``_sync_account`` run once at a time for each mailbox.

``_sync_account`` now takes a lock for each mailbox before it runs
``_sync_cycle``. The loop and the webhook skip when a sync holds the mailbox.
The manual sync, the resync and the deep downloads wait, with a bound.

R7 fence named here: ``email-one-sync-per-mailbox``.

**Hermetic.** A fake ``_sync_cycle`` that parks on an event.

* A skip caller skips while a sync runs, and a waiting caller runs after it.
  Two mailboxes run at the same time.
* The lock is released after an exception and after a cancel, and a waiter
  that gives up at the bound leaves no lock behind. The dicts go empty.
* The loop passes ``if_busy="skip"``, a skip adds no backoff, and the loop
  stops when its row is gone. It drops its entry only while the entry is its
  own task.
* The manual sync answers 409 when its wait reaches the bound.
* Structure: only ``_sync_account`` calls ``_sync_cycle``, the wrapper opens
  no session, and no gateway caller holds a block across the call.

**R8.** On the promoted two-org catalog as ``acb_app_h3rls``: sync A parks
inside phase (c), with an uncommitted row in its open transaction. Sync B of
the same mailbox does not fetch until A is done, and a skip call returns busy.
Both then finish, with no duplicate rows.

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("sqlalchemy")

import email_ingestion.scheduler as sched
from acb_common.db import clear_tenant, release_tenant
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
    yield
    assert sched._sync_locks == {}, "a mailbox lock outlived its users"
    assert sched._sync_lock_users == {}


class _Parked:
    """A fake ``_sync_cycle``. Each call records its start and its end, and a
    call for a mailbox in ``park`` waits for that mailbox's gate."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.started: dict[str, asyncio.Event] = {}
        self.raise_for: set[str] = set()

    def park(self, account_id: str) -> asyncio.Event:
        self.gates[account_id] = asyncio.Event()
        self.started[account_id] = asyncio.Event()
        return self.gates[account_id]

    async def __call__(self, account_id: str, **_kw: Any) -> dict[str, Any]:
        self.events.append(("start", account_id))
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
        assert await first == {"synced": 1}
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

    def test_an_unknown_busy_mode_is_refused(self):
        with pytest.raises(ValueError, match="if_busy"):
            asyncio.run(sched._sync_account("acc-1", if_busy="queue"))


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
        assert cycle.events.count(("start", "acc-1")) == 1

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
        monkeypatch.setattr(sched.asyncio, "sleep", _sleep)
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
        monkeypatch.setattr(sched.asyncio, "sleep", _no_sleep)
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
        monkeypatch.setattr(sched.asyncio, "sleep", _sleep)
        with pytest.raises(_StopLoop):
            await sched._account_sync_loop("acc-1", 300, organization_id="org-1")
        assert slept == [600], "a failed tick must back off and loop on"


# ── hermetic: the manual sync ───────────────────────────────────────────────


async def test_a_manual_sync_that_waited_its_bound_answers_409(monkeypatch):
    async def _busy(account_id, **_kw):
        return dict(sched.SYNC_SKIPPED_BUSY)

    monkeypatch.setattr(sched, "_sync_account", _busy)
    with pytest.raises(HTTPException) as err:
        await sync_mod._run_manual_sync("acc-1", BackgroundTasks(), full=False)
    assert err.value.status_code == 409
    assert err.value.detail == sync_mod.SYNC_BUSY_DETAIL


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


def _if_busy(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "if_busy" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    return None


def test_only_sync_account_runs_the_cycle():
    """Every caller gets the lock: nothing calls ``_sync_cycle`` directly."""
    tree = ast.parse(_SCHEDULER.read_text(encoding="utf-8"))
    owners = []
    for fn in ast.walk(tree):
        if (isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef)
                and _calls(fn, "_sync_cycle")):
            owners.append(fn.name)
    assert owners == ["_sync_account"]
    for path in _EMAIL.rglob("*.py"):
        assert "_sync_cycle" not in path.read_text(encoding="utf-8"), (
            f"{path.name} calls the cycle past the lock")


def test_the_wrapper_opens_no_session():
    src = inspect.getsource(sched._sync_account)
    for opener in ("tenant_session", "get_db", "get_session_factory"):
        assert opener not in src, f"_sync_account opens a session ({opener})"
    assert src.index("lock.acquire()") < src.index("_sync_cycle(")


#: Every caller of ``_sync_account`` and its busy mode. A new caller fails
#: this test until somebody decides its mode.
CALLERS: dict[tuple[str, str], str | None] = {
    ("scheduler.py", "_account_sync_loop"): "skip",
    ("transport/sync.py", "_webhook_sync"): "skip",
    ("transport/sync.py", "_run_manual_sync"): None,
    ("automation/cleanup.py", "_backfill_and_clean_job"): None,
    ("automation/runner.py", "_process_past_emails_job"): None,
}


def _caller_modes() -> dict[tuple[str, str], str | None]:
    found: dict[tuple[str, str], str | None] = {}
    files = [(_SCHEDULER, "scheduler.py")] + [
        (p, p.relative_to(_EMAIL).as_posix()) for p in _EMAIL.rglob("*.py")]
    for path, label in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            if fn.name == "_sync_account":
                continue
            for call in _calls(fn, "_sync_account"):
                found[(label, fn.name)] = _if_busy(call)
    return found


def test_each_caller_has_a_decided_busy_mode():
    assert _caller_modes() == CALLERS


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
                     for c in _calls(stmt, "_sync_account")]
    return hits


def test_no_caller_holds_a_block_while_it_waits():
    for path in _EMAIL.rglob("*.py"):
        assert _sync_calls_inside_a_block(path.read_text(encoding="utf-8")) == [], (
            f"{path.name} waits for the mailbox lock inside a tenant block")


def test_the_block_check_can_fail():
    planted = (
        "async def f(a):\n"
        "    async with _tenant_session() as db:\n"
        "        await _sync_account(a)\n"
    )
    assert _sync_calls_inside_a_block(planted) == [3]


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
