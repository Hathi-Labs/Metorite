"""The Mem0 purge of one mailbox (WS-17 EM-T8f-1, MB-17, and EM-G11f2).

Spec: ``project-docs/specs/email_app_master_plan.md`` §11.7.6, EM-T8f-1 item 2,
and §12.3.13.9, EM-G11f2 items 1 and 2.

Three writers put the drafting memory of a mailbox into Mem0 under the key
``email_memory_scope(owner, account_id)``, which is ``<owner>#acct:<id>``:

* the writing style (``automation/assistant.py``),
* the learned reply preferences (``automation/drafting.py``, the learning job),
* the precedent of each draft, "I replied: ..." (``automation/drafting.py``,
  ``_orchestrate_draft``). It runs in a background task, and the follow-up
  job and the rule runner reach it too.

Each writer takes its key from :func:`writable_memory_scope`, so no writer
puts a memory under a key that no purge finds (EM-G11f2 item 2).

The rules of this module:

1. ``transport/accounts.py::delete_account`` writes one
   ``email_mailbox_purges`` row (migration 264) INSIDE the transaction of its
   ``DELETE`` (:data:`RECORD_PURGE_SQL`). So the commit that deletes the
   mailbox also records the purge that it owes. Then, after the commit, it
   calls ``schedule_mailbox_memory_purge``. A 404, a 409 or a failed
   ``DELETE`` raises before both, so it records and starts no purge.
2. The purge refuses a key with no mailbox in it. ``email_memory_scope`` gives
   the BARE member email when the account id is empty, and the bare key holds
   every personal memory of the member. A purge of it would delete them all.
   An account id that is not a UUID is refused too.
3. The key holds the account id in its canonical form, ``str(UUID(id))``.
   ``core.email_memory_scope`` builds that form for each writer and for this
   purge (review round 2). This module also checks that the id IS a UUID.
4. The purge makes TWO passes, ``SECOND_PASS_DELAY_S`` apart. No lock holds the
   third writer, and stopping the sync does not stop a background add that is
   still running. Such an add can land after the first pass, and the second
   pass deletes it.
5. The route's task holds a strong reference in ``_PURGES`` until it ends. The
   route answers 204 and does not wait for Mem0. When both passes work, the
   task marks the row ``done``. Otherwise the row stays ``open``.
6. **The sweeper loop** (EM-G11f2, fix round 1). ``main.py`` starts
   :func:`start_purge_loop` in the lifespan and stops it with
   :func:`stop_purge_loop`. Each ``TICK_SECONDS`` it lists the organizations
   (one read of the RLS-EXEMPT ``organization`` table) and runs
   :func:`sweep_mailbox_purges`. The sweep CLAIMS ONE ROW AT A TIME, just
   before its pass, so the lease of each row covers one pass. The
   organizations take turns, one row each, so no backlog starves another. That pass has
   the bound ``SWEEP_PASS_BOUND_S``, well inside ``SWEEP_LEASE_S``. A tick
   also stops at ``SWEEP_BUDGET_S``. A row is ``done`` only after its second
   good pass. A failed pass sets ``next_try_at`` with a back-off, and the row
   stays. A new row waits ``TASK_LEASE_S`` first, so the sweeper does not race
   the route's own task.
7. **Mem0 off.** :func:`mem0_on` is false when ``MEM0_ENABLED`` is off, or
   when the client cannot start. Then the task runs no pass, and a tick ends
   before any database read. The rows stay ``open``. A row closes only after a
   real purge. Mem0 keeps its memories in the ``mem0_memories`` table of the
   same Postgres, and that table outlives the flag. So a row that closed while
   the flag was off would claim a purge that never ran. The probe builds the
   client off the event loop, and a failed build waits ``MEM0_RETRY_S``.
8. A failed pass logs ``email.disconnect.memory_purge_failed`` with the pass
   and the error class only. No log line holds memory text or an address.

Fences: ``tests/unit/test_email_disconnect_memory_purge.py`` and
``tests/unit/test_email_mailbox_purge_durable.py`` (R8).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

from acb_common import get_settings
from gateway.db import get_db
from gateway.routes.email.core import _log, _tenant_session, email_memory_scope
from sqlalchemy import text

#: The part of a Mem0 key that names one mailbox. ``email_memory_scope``
#: writes it, and a key without it is the bare member scope.
ACCOUNT_SCOPE_MARK = "#acct:"

#: The time between the two passes of a purge. A background add of the drafter
#: finishes well inside it (one extraction call on the fast tier).
SECOND_PASS_DELAY_S = 120.0

#: A new purge row waits this long before the sweeper may take it. The route's
#: own task needs about ``SECOND_PASS_DELAY_S`` for its two passes.
TASK_LEASE_S = 600.0

#: The sweeper holds a row it took for this long. One pass, bounded by
#: ``SWEEP_PASS_BOUND_S``, runs inside it. A process that stops in the middle
#: of a pass frees the row when the lease ends.
SWEEP_LEASE_S = 900.0

#: The back-off after a failed pass: ``RETRY_BASE_S`` doubled for each earlier
#: try, and never more than ``RETRY_CAP_S``. The row is never given up.
RETRY_BASE_S = 300.0
RETRY_CAP_S = 6 * 3600.0

#: The rows that one tick takes from one organization.
SWEEP_BATCH = 20

#: The bound of one pass in the sweeper.
SWEEP_PASS_BOUND_S = 120.0

#: One tick takes no new row after this time.
SWEEP_BUDGET_S = 600.0

#: The time between two ticks of the loop.
TICK_SECONDS = 900.0

#: A shutdown waits this long for a tick in progress, then cancels it.
STOP_GRACE_SECONDS = 10.0

#: After a failed build of the Mem0 client, the probe waits this long.
MEM0_RETRY_S = 900.0

#: The wait between the passes. Tests replace it, so a test does not wait two
#: minutes.
_sleep = asyncio.sleep

#: The purges that are still running. The set holds a strong reference to each
#: task until it ends, so the loop does not collect a purge part way.
_PURGES: set[asyncio.Task[int | None]] = set()

#: The loop task, its stop signal, and the time of the last failed build.
_loop_task: asyncio.Task[None] | None = None
_stopping: asyncio.Event | None = None
_mem0_failed_at: float | None = None
_build_lock: asyncio.Lock | None = None
_build_lock_loop: asyncio.AbstractEventLoop | None = None

#: Rule 1. The row of the purge, in the transaction of the ``DELETE``. The
#: organization is the column default, the tenant that the session binds.
RECORD_PURGE_SQL = """INSERT INTO email_mailbox_purges
                        (owner_email, account_id, next_try_at)
                      VALUES (:owner, CAST(:aid AS uuid),
                              now() + make_interval(secs => :lease))
                      ON CONFLICT (organization_id, account_id) DO NOTHING"""

#: Rule 5. The route's task closes the row after two good passes.
_TASK_DONE_SQL = """UPDATE email_mailbox_purges
                       SET state = 'done', passes = 2, last_error = NULL,
                           done_at = now(), updated_at = now()
                     WHERE account_id = CAST(:aid AS uuid) AND state = 'open'"""

#: Rule 6. Take ONE due row of one organization, and hold it for
#: ``SWEEP_LEASE_S``. ``SKIP LOCKED`` lets two gateways sweep at once.
_CLAIM_SQL = """UPDATE email_mailbox_purges p
                   SET tries = p.tries + 1, updated_at = now(),
                       next_try_at = now() + make_interval(secs => :lease)
                 WHERE p.id IN (
                       SELECT id FROM email_mailbox_purges
                        WHERE state = 'open' AND next_try_at <= now()
                          AND organization_id = CAST(:org AS uuid)
                        ORDER BY next_try_at
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED)
             RETURNING p.id, p.owner_email, p.account_id, p.passes, p.tries"""

#: A good pass. The second one closes the row.
_PASS_DONE_SQL = """UPDATE email_mailbox_purges
                       SET passes = CAST(:passes AS smallint), last_error = NULL,
                           updated_at = now(),
                           state = CASE WHEN CAST(:passes AS smallint) >= 2
                                        THEN 'done' ELSE 'open' END,
                           done_at = CASE WHEN CAST(:passes AS smallint) >= 2
                                          THEN now() END,
                           next_try_at = now() + make_interval(secs => :delay)
                     WHERE id = CAST(:id AS uuid) AND state = 'open'"""

#: A failed pass. The row stays open, and waits for the back-off.
_PASS_FAILED_SQL = """UPDATE email_mailbox_purges
                         SET last_error = :error, updated_at = now(),
                             next_try_at = now() + make_interval(secs => :delay)
                       WHERE id = CAST(:id AS uuid) AND state = 'open'"""

#: A key that no purge can delete. It closes as ``refused``, never ``done``.
_REFUSED_SQL = """UPDATE email_mailbox_purges
                     SET state = 'refused', last_error = 'scope_refused',
                         updated_at = now()
                   WHERE id = CAST(:id AS uuid) AND state = 'open'"""


class ScopeRefused(ValueError):
    """The key names no mailbox. A purge of it could delete every personal
    memory of the member."""


def mailbox_memory_scope(owner_email: str, account_id: Any) -> str:
    """The Mem0 key of one mailbox, or ``ScopeRefused``.

    The key comes from ``email_memory_scope``, the one function that the
    writers and the reader use, with the account id in canonical UUID form.
    """
    try:
        canonical = str(UUID(str(account_id).strip()))
    except (TypeError, ValueError, AttributeError):
        raise ScopeRefused("a mailbox purge needs a mailbox id") from None
    scope = email_memory_scope(owner_email, canonical)
    member, mark, account = scope.partition(ACCOUNT_SCOPE_MARK)
    if not mark or not account.strip() or not member:
        raise ScopeRefused("a mailbox purge needs an account id and an owner")
    return scope


def writable_memory_scope(owner: str | None, account_id: Any, writer: str) -> str | None:
    """The Mem0 key for a WRITE of one mailbox, or ``None`` (EM-G11f2 item 2).

    Each email writer of Mem0 takes its key from here. It is the key that
    :func:`mailbox_memory_scope` builds, so a disconnect deletes it. With no
    owner or no mailbox id the write is REFUSED, not moved to another key. It
    logs ``email.memory_write_refused`` with the writer name and no text.
    """
    try:
        return mailbox_memory_scope(owner or "", account_id)
    except ScopeRefused:
        _log.info("email.memory_write_refused", writer=writer,
                  reason="no_mailbox_scope")
        return None


def _mem0_build_lock() -> asyncio.Lock:
    """The module lock around the Mem0 build, one for each event loop.

    An ``asyncio.Lock`` binds to the loop that first waits on it, so a new
    loop (a test run, a restart of the lifespan) gets a new lock.
    """
    global _build_lock, _build_lock_loop
    loop = asyncio.get_running_loop()
    if _build_lock is None or _build_lock_loop is not loop:
        _build_lock, _build_lock_loop = asyncio.Lock(), loop
    return _build_lock


def _mem0_flag() -> bool:
    """The ``MEM0_ENABLED`` setting, the one reader in this module."""
    return bool(getattr(get_settings(), "mem0_enabled", False))


async def mem0_on() -> bool:
    """True when the Mem0 client is ready (rule 7).

    A client that is built answers at once. With ``MEM0_ENABLED`` off this
    reads one setting. Otherwise it builds the client in a thread, so the
    event loop does not wait for ``Memory.from_config``. A failed build is
    kept for ``MEM0_RETRY_S``, so a tick does not build it again each time.
    """
    global _mem0_failed_at
    try:
        from acb_memory.mem0_client import get_memory_client

        client = get_memory_client()
        if getattr(client, "_client", None) is not None:
            return True
        if not _mem0_flag():
            return False
        # One build at a time (fix round 2). Two callers that both saw no
        # client would each run Memory.from_config and leak a pool. The second
        # one waits here, and then sees the client or the failure of the first.
        async with _mem0_build_lock():
            if getattr(client, "_client", None) is not None:
                return True
            if (_mem0_failed_at is not None
                    and time.monotonic() - _mem0_failed_at < MEM0_RETRY_S):
                return False
            built = await asyncio.to_thread(client._get_client)
    except Exception as exc:
        _log.warning("email.purge.mem0_probe_failed", error=type(exc).__name__)
        built = None
    if built is None:
        _mem0_failed_at = time.monotonic()
        return False
    _mem0_failed_at = None
    return True


async def _one_pass(scope: str, account_id: str, phase: str) -> int | None:
    """One ``delete_scope`` call. The count, or ``None`` after a logged error."""
    try:
        # Imported at the call, as the writers do, so the route never depends
        # on acb_memory at import time.
        from acb_memory.mem0_client import get_memory_client

        return await get_memory_client().delete_scope(scope)
    except Exception as exc:
        # Logged, and the 204 stands. The class only: a Mem0 or database
        # error text can quote a row.
        _log.warning("email.disconnect.memory_purge_failed",
                     account_id=account_id, phase=phase,
                     error=type(exc).__name__)
        return None


async def _close_after_task(open_session: Callable[..., Any], account_id: Any,
                            shown: str) -> None:
    """Mark the row ``done`` after the two good passes of the route's task.

    A failure leaves the row open, and the sweeper purges it again later. A
    second purge of an empty scope deletes nothing, so that is safe.
    """
    try:
        async with open_session() as db:
            await db.execute(text(_TASK_DONE_SQL),
                             {"aid": str(UUID(str(account_id).strip()))})
    except Exception as exc:
        _log.warning("email.disconnect.purge_row_failed", account_id=shown,
                     error=type(exc).__name__)


async def purge_mailbox_memory(
    owner_email: str, account_id: Any,
    *, open_session: Callable[..., Any] | None = None,
) -> int | None:
    """Delete each Mem0 memory of one mailbox, in two passes. The answer is
    the sum of the two counts, or ``None`` when the purge was refused, Mem0 is
    off, or a pass failed.

    With ``open_session``, two good passes mark the ``email_mailbox_purges``
    row ``done`` in a block of that session (rule 5).

    It never raises, because it runs in a task that nothing awaits. A failed
    first pass does not stop the second one.
    """
    shown = str(account_id)[:64]
    try:
        scope = mailbox_memory_scope(owner_email, account_id)
    except ScopeRefused:
        _log.warning("email.disconnect.memory_purge_refused", account_id=shown)
        return None
    if not await mem0_on():
        # Rule 7: the row stays open until Mem0 runs again.
        _log.info("email.disconnect.memory_purge_deferred", account_id=shown,
                  reason="mem0_off")
        return None
    first = await _one_pass(scope, shown, "first")
    await _sleep(SECOND_PASS_DELAY_S)
    second = await _one_pass(scope, shown, "second")
    if first is None or second is None:
        return None
    _log.info("email.disconnect.memory_purged", account_id=shown,
              first=first, second=second)
    if open_session is not None:
        await _close_after_task(open_session, account_id, shown)
    return first + second


def schedule_mailbox_memory_purge(
    owner_email: str, account_id: Any,
    *, open_session: Callable[..., Any] | None = None,
) -> asyncio.Task[int | None]:
    """Start the purge of one mailbox in a task, and keep a reference to it.

    The caller must call this only after the ``DELETE`` of the mailbox row
    commits (rule 1). ``open_session`` is the session seam of the caller, and
    the task closes the row with it.
    """
    task = asyncio.create_task(
        purge_mailbox_memory(owner_email, account_id, open_session=open_session))
    _PURGES.add(task)
    task.add_done_callback(_PURGES.discard)
    return task


# ── the sweeper (EM-G11f2, rule 6) ──────────────────────────────────────────


def retry_delay_s(tries: int) -> float:
    """The back-off after a failed pass of a row that has ``tries`` tries."""
    step = max(int(tries) - 1, 0)
    return min(RETRY_CAP_S, RETRY_BASE_S * 2 ** min(step, 16))


async def _bounded_pass(scope: str, shown: str, phase: str) -> int | None:
    """One pass under ``SWEEP_PASS_BOUND_S``. ``None`` after a logged error."""
    try:
        return await asyncio.wait_for(_one_pass(scope, shown, phase),
                                      SWEEP_PASS_BOUND_S)
    except TimeoutError:
        _log.warning("email.disconnect.memory_purge_failed", account_id=shown,
                     phase=phase, error="TimeoutError")
        return None


async def _sweep_row(org: str, row: Any, open_session: Callable[..., Any]) -> bool:
    """One pass for one claimed row, with no session open across Mem0. Then
    one block records the outcome. True when the row closed ``done``.

    ⚠️ The row closes only AFTER its second good pass. A failed pass keeps it
    open with a back-off.
    """
    shown = str(row.account_id)
    try:
        scope = mailbox_memory_scope(row.owner_email, row.account_id)
    except ScopeRefused:
        _log.warning("email.purge_sweep.refused", account_id=shown)
        async with open_session(org) as db:
            await db.execute(text(_REFUSED_SQL), {"id": str(row.id)})
        return False
    phase = "first" if int(row.passes) == 0 else "second"
    count = await _bounded_pass(scope, shown, phase)
    passes = int(row.passes) + 1
    async with open_session(org) as db:
        if count is None:
            await db.execute(text(_PASS_FAILED_SQL), {
                "id": str(row.id), "error": "pass_failed",
                "delay": retry_delay_s(int(row.tries))})
            return False
        await db.execute(text(_PASS_DONE_SQL), {
            "id": str(row.id), "passes": passes, "delay": SECOND_PASS_DELAY_S})
    _log.info("email.purge_sweep.pass", account_id=shown, phase=phase,
              count=count, done=passes >= 2)
    return passes >= 2


async def _claim_one(org: str, open_session: Callable[..., Any]) -> Any | None:
    """Claim ONE due row of one organization, or ``None``."""
    async with open_session(org) as db:
        return (await db.execute(text(_CLAIM_SQL), {
            "org": org, "lease": SWEEP_LEASE_S})).fetchone()


async def _turn(org: str, open_session: Callable[..., Any]) -> tuple[bool, int]:
    """One turn of one organization: claim ONE due row and pass it. Answers
    whether the organization had a row, and 1 when that row closed ``done``.
    A failed claim counts as no row, so that organization leaves the tick."""
    try:
        row = await _claim_one(org, open_session)
    except Exception as exc:
        _log.warning("email.purge_sweep.org_failed", org=org, error=type(exc).__name__)
        return False, 0
    if row is None:
        return False, 0
    try:
        return True, int(await _sweep_row(org, row, open_session))
    except Exception as exc:
        # The lease frees the row, and a later tick takes it again.
        _log.warning("email.purge_sweep.row_failed",
                     account_id=str(row.account_id), error=type(exc).__name__)
        return True, 0


async def sweep_mailbox_purges(
    org_ids: Iterable[str],
    *, open_session: Callable[..., Any] = _tenant_session,
    budget_s: float = SWEEP_BUDGET_S,
) -> int:
    """Run one pass for each due purge row of each organization. Answers the
    count of rows that closed ``done``.

    The organizations take TURNS: each turn claims one row of one
    organization, so a large backlog in one organization never starves the
    next one (fix round 2). An organization leaves the tick when it has no
    due row or after ``SWEEP_BATCH`` rows. The tick stops at the budget.

    With Mem0 off it returns 0 before any database read (rule 7). Each
    organization runs in its own ``tenant_session(org)`` blocks, and a failure
    in one organization does not stop the others. It never raises.
    """
    if not await mem0_on():
        return 0
    deadline = time.monotonic() + budget_s
    taken = {str(org): 0 for org in org_ids}
    done = 0
    while taken:
        for org in list(taken):
            if time.monotonic() >= deadline:
                _log.info("email.purge_sweep.budget_spent")
                return done
            had_row, closed = await _turn(org, open_session)
            done += closed
            taken[org] += 1
            if not had_row or taken[org] >= SWEEP_BATCH:
                del taken[org]
    return done


# ── the loop (rule 6) ───────────────────────────────────────────────────────


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The tenant-discovery read of the loop. It answers "which tenants exist",
    so it cannot run bound. Each read after it binds one organization.
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


async def run_tick() -> int:
    """One tick: with Mem0 on, list the organizations and sweep them."""
    if not await mem0_on():
        return 0
    return await sweep_mailbox_purges(await _organization_ids())


async def _loop(stopping: asyncio.Event) -> None:
    while not stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("email.purge_loop.tick_failed", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=TICK_SECONDS)


async def start_purge_loop() -> bool:
    """Start the sweeper loop from the gateway lifespan. Idempotent.

    The loop has no flag. With Mem0 off each tick ends before any database
    read, so the loop costs one setting read each ``TICK_SECONDS``.
    """
    global _loop_task, _stopping
    if _loop_task is not None and not _loop_task.done():
        return True
    _stopping = asyncio.Event()
    _loop_task = asyncio.get_running_loop().create_task(
        _loop(_stopping), name="email-purge-loop")
    _log.info("email.purge_loop.started", tick_secs=TICK_SECONDS)
    return True


async def stop_purge_loop() -> None:
    """Stop the loop and wait for a tick in progress. Safe when it never started."""
    global _loop_task
    if _stopping is not None:
        _stopping.set()
    task, _loop_task = _loop_task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("email.purge_loop.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still
