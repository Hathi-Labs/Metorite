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

The drafter reads them back under the same key. Before EM-T8f-1, a disconnect
deleted the mailbox row and left those memories in Mem0 (MB-17).

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
6. **The sweeper** (EM-G11f2). :func:`sweep_mailbox_purges` takes the open
   rows that are due and runs ONE pass for each. A row is ``done`` only after
   its second good pass. A failed pass sets ``next_try_at`` with a back-off,
   and the row stays. A new row waits ``TASK_LEASE_S`` first, so the sweeper
   does not race the route's own task. The host is the calendar auto-rollover
   loop (``routes/tasks/calendar.py::_run_rollover_sweep``), an existing loop
   that runs on every box with no flag and already lists each organization.
7. **Mem0 off.** :func:`mem0_on` is false when ``MEM0_ENABLED`` is off, or
   when the client cannot start. Then the task runs no pass, and the sweeper
   returns before any database read. The rows stay ``open``. A row closes only
   after a real purge. Mem0 keeps its memories in the ``mem0_memories`` table
   of the same Postgres, and that table outlives the flag. So a row that
   closed while the flag was off would claim a purge that never ran.
8. A failed pass logs ``email.disconnect.memory_purge_failed`` with the pass
   and the error class only. No log line holds memory text or an address.

Fences: ``tests/unit/test_email_disconnect_memory_purge.py`` and
``tests/unit/test_email_mailbox_purge_durable.py`` (R8).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import Any
from uuid import UUID

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

#: The sweeper holds a row it took for this long. A process that stops in the
#: middle of a pass frees the row when the lease ends.
SWEEP_LEASE_S = 900.0

#: The back-off after a failed pass: ``RETRY_BASE_S`` doubled for each earlier
#: try, and never more than ``RETRY_CAP_S``. The row is never given up.
RETRY_BASE_S = 300.0
RETRY_CAP_S = 6 * 3600.0

#: The rows that one sweep takes from one organization.
SWEEP_BATCH = 20

#: The bound of one pass in the sweeper.
SWEEP_PASS_BOUND_S = 120.0

#: The wait between the passes. Tests replace it, so a test does not wait two
#: minutes.
_sleep = asyncio.sleep

#: The purges that are still running. The set holds a strong reference to each
#: task until it ends, so the loop does not collect a purge part way.
_PURGES: set[asyncio.Task[int | None]] = set()

#: The sweeps that are still running. The host loop starts one at most.
_SWEEPS: set[asyncio.Task[int]] = set()

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

#: Rule 6. Take the due rows of one organization, and hold each one for
#: ``SWEEP_LEASE_S``. ``SKIP LOCKED`` lets two gateways sweep at once.
_CLAIM_SQL = """UPDATE email_mailbox_purges p
                   SET tries = p.tries + 1, updated_at = now(),
                       next_try_at = now() + make_interval(secs => :lease)
                 WHERE p.id IN (
                       SELECT id FROM email_mailbox_purges
                        WHERE state = 'open' AND next_try_at <= now()
                          AND organization_id = CAST(:org AS uuid)
                        ORDER BY next_try_at
                        LIMIT :batch
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
    A writer calls this too, so that it never writes a key that no purge
    finds (EM-G11f2 item 2).
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


def mem0_on() -> bool:
    """True when the Mem0 client is ready (rule 7).

    With ``MEM0_ENABLED`` off this reads one setting and nothing else. The
    client keeps no public probe, so this asks its lazy builder, which returns
    ``None`` when the flag is off or the client cannot start.
    """
    try:
        from acb_memory.mem0_client import get_memory_client

        return get_memory_client()._get_client() is not None
    except Exception as exc:
        _log.warning("email.purge.mem0_probe_failed", error=type(exc).__name__)
        return False


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
    if not mem0_on():
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


async def _sweep_org(org: str, open_session: Callable[..., Any]) -> int:
    """Claim the due rows of one organization, then run one pass for each."""
    async with open_session(org) as db:
        rows = (await db.execute(text(_CLAIM_SQL), {
            "org": org, "lease": SWEEP_LEASE_S, "batch": SWEEP_BATCH,
        })).fetchall()
    done = 0
    for row in rows:
        try:
            done += int(await _sweep_row(org, row, open_session))
        except Exception as exc:
            # The lease frees the row, and a later sweep takes it again.
            _log.warning("email.purge_sweep.row_failed",
                         account_id=str(row.account_id), error=type(exc).__name__)
    return done


async def sweep_mailbox_purges(
    org_ids: Iterable[str],
    *, open_session: Callable[..., Any] = _tenant_session,
) -> int:
    """Run one pass for each due purge row of each organization. Answers the
    count of rows that closed ``done``.

    With Mem0 off it returns 0 before any database read (rule 7). Each
    organization runs in its own ``tenant_session(org)`` blocks, and a failure
    in one organization does not stop the others. It never raises.
    """
    if not mem0_on():
        return 0
    done = 0
    for org in org_ids:
        try:
            done += await _sweep_org(str(org), open_session)
        except Exception as exc:
            _log.warning("email.purge_sweep.org_failed", org=str(org),
                         error=type(exc).__name__)
    return done


def start_purge_sweep(org_ids: Iterable[str]) -> asyncio.Task[int] | None:
    """Start one sweep in a task, for the host loop. ``None`` when Mem0 is off
    or a sweep still runs.

    The host loop does not wait for the sweep, so a slow Mem0 never delays
    the host's own work. The set keeps one sweep at most.
    """
    if not mem0_on():
        return None
    if any(not t.done() for t in _SWEEPS):
        return None
    task = asyncio.create_task(sweep_mailbox_purges(list(org_ids)))
    _SWEEPS.add(task)
    task.add_done_callback(_SWEEPS.discard)
    return task
