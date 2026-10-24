"""H-390: background jobs cannot take the whole database pool.

On 2026-10-22 the gateway logged ``QueuePool limit of size 7 overflow 2`` and a
member read answered 503, because sync, rule, classify and embed jobs share the
one pool. The pooler in front has no spare slot, so the jobs are bounded.

R7 - the mutation that turns each rule red:

1. Saturating the background side leaves a connection for a member read.
   Mutation: drop ``_background_slot`` from ``tenant_session`` ->
   ``test_a_saturated_background_side_leaves_a_connection_for_a_member``.
2. A job that opens a nested session does not deadlock on its own kind.
   Mutation: drop the ``_BACKGROUND_HELD`` check ->
   ``test_a_nested_background_session_does_not_wait_for_a_second_slot``.
3. The email sync loop and the post-sync jobs mark themselves background.
   Mutation: drop either mark -> ``test_the_email_job_seams_mark_background``.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from acb_common import db as dbmod
from acb_common.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]


class _Pool:
    """A pool of ``size`` connections. A session holds one from begin to close."""

    def __init__(self, size: int) -> None:
        self.sem = asyncio.Semaphore(size)
        self.in_use = 0
        self.peak = 0

    def factory(self):
        pool = self

        class Session:
            async def begin(self) -> None:
                await pool.sem.acquire()
                pool.in_use += 1
                pool.peak = max(pool.peak, pool.in_use)

            async def execute(self, *_a, **_k) -> None:
                return None

            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

            async def close(self) -> None:
                pool.in_use -= 1
                pool.sem.release()

        return lambda: Session()


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch) -> _Pool:
    settings = get_settings()
    total = settings.db_pool_size + settings.db_max_overflow  # 9 in production
    p = _Pool(total)
    monkeypatch.setattr(dbmod, "get_session_factory", p.factory)
    dbmod._GATES.clear()
    return p


async def _background_job(hold: asyncio.Event) -> None:
    token = dbmod.mark_background()
    try:
        async with dbmod.tenant_session("org-1"):
            await hold.wait()
    finally:
        dbmod.release_background(token)


async def test_a_saturated_background_side_leaves_a_connection_for_a_member(
    pool: _Pool,
) -> None:
    hold = asyncio.Event()
    jobs = [asyncio.create_task(_background_job(hold)) for _ in range(30)]
    await asyncio.sleep(0.05)
    assert pool.in_use == dbmod.background_slots()
    assert pool.in_use < pool.sem._value + pool.in_use  # the pool is not full

    async def member_read() -> str:
        async with dbmod.tenant_session("org-1"):
            return "ok"

    assert await asyncio.wait_for(member_read(), timeout=1) == "ok"
    hold.set()
    await asyncio.gather(*jobs)
    assert pool.peak <= dbmod.background_slots() + 1
    assert pool.in_use == 0


async def test_a_nested_background_session_does_not_wait_for_a_second_slot(
    pool: _Pool,
) -> None:
    slots = dbmod.background_slots()
    started = 0

    async def job() -> None:
        nonlocal started
        token = dbmod.mark_background()
        try:
            async with dbmod.tenant_session("org-1"):
                started += 1
                await asyncio.sleep(0.02)
                async with dbmod.tenant_session("org-1"):  # a second session
                    return None
        finally:
            dbmod.release_background(token)

    await asyncio.wait_for(
        asyncio.gather(*(job() for _ in range(slots * 3))), timeout=3)
    assert started == slots * 3


async def test_a_member_task_is_never_held_back(pool: _Pool) -> None:
    async def read() -> None:
        async with dbmod.tenant_session("org-1"):
            await asyncio.sleep(0.02)

    await asyncio.wait_for(asyncio.gather(*(read() for _ in range(9))), timeout=1)
    assert pool.peak == 9


def test_the_background_cap_leaves_room_for_members() -> None:
    settings = get_settings()
    assert dbmod.background_slots() <= settings.db_pool_size + settings.db_max_overflow - 1
    assert dbmod.background_slots() >= 1


def test_the_email_job_seams_mark_background() -> None:
    hooks = (ROOT / "apps/services/gateway/gateway/routes/email/scheduler_hooks.py"
             ).read_text(encoding="utf-8")
    sched = (ROOT / "apps/services/email_ingestion/email_ingestion/scheduler.py"
             ).read_text(encoding="utf-8")
    assert "mark_background()" in hooks and "release_background(bg)" in hooks
    assert "mark_background() if (from_loop or background)" in sched


async def test_a_child_task_of_a_job_takes_its_own_slot(pool: _Pool) -> None:
    """A child task copies the context, but it is not the slot holder."""
    hold = asyncio.Event()
    slots = dbmod.background_slots()

    async def child() -> None:
        async with dbmod.tenant_session("org-1"):
            await hold.wait()

    async def parent() -> None:
        token = dbmod.mark_background()
        try:
            await asyncio.gather(*(child() for _ in range(1)))
        finally:
            dbmod.release_background(token)

    async def parent_with_children() -> None:
        token = dbmod.mark_background()
        try:
            kids = [asyncio.create_task(child()) for _ in range(slots * 4)]
            await asyncio.gather(*kids)
        finally:
            dbmod.release_background(token)

    run = asyncio.create_task(parent_with_children())
    await asyncio.sleep(0.05)
    assert pool.in_use == slots
    hold.set()
    await run


async def test_children_of_slot_holders_never_hang(pool: _Pool) -> None:
    """Every slot is held by a parent that waits on its children's sessions.
    The children share their parent's slot, so nothing waits on the gate and
    the jobs finish. Mutation: make a child take its own slot → the parents
    wait on children that wait on the gate → timeout → red."""
    slots = dbmod.background_slots()

    async def child() -> None:
        async with dbmod.tenant_session("org-1"):
            await asyncio.sleep(0)

    async def parent() -> None:
        token = dbmod.mark_background()
        try:
            async with dbmod.tenant_session("org-1"):
                await asyncio.gather(*(asyncio.create_task(child()) for _ in range(3)))
        finally:
            dbmod.release_background(token)

    await asyncio.wait_for(asyncio.gather(*(parent() for _ in range(slots))), timeout=5)


async def test_the_slot_is_released_when_the_context_is_foreign(pool: _Pool) -> None:
    """A finalize in another context makes the reset raise. The gate still frees."""
    import contextvars

    gate_before = None
    token = dbmod.mark_background()
    try:
        cm = dbmod.tenant_session("org-1")
        await cm.__aenter__()
        gate_before = dbmod._background_gate()._value
        ctx = contextvars.copy_context()
        # Close in a different task, so a different Context.
        await asyncio.create_task(cm.__aexit__(None, None, None), context=ctx)
    except Exception:
        pass
    finally:
        dbmod.release_background(token)
    assert gate_before == dbmod.background_slots() - 1
    assert dbmod._background_gate()._value == dbmod.background_slots()


def test_webhook_and_rerun_and_subscription_are_background() -> None:
    sched = (ROOT / "apps/services/email_ingestion/email_ingestion/scheduler.py"
             ).read_text(encoding="utf-8")
    sync = (ROOT / "apps/services/gateway/gateway/routes/email/transport/sync.py"
            ).read_text(encoding="utf-8")
    hooks = (ROOT / "apps/services/gateway/gateway/routes/email/scheduler_hooks.py"
             ).read_text(encoding="utf-8")
    assert 'if_busy="skip", background=True' in sync
    assert "mark_background() if (from_loop or background)" in sched
    assert "bg2 = mark_background()" in sched
    assert "as_mailbox_owner(_ensure_subscription)" in hooks
