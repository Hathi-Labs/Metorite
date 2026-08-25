"""Schedule triggers + matured waits — the platform's first real cron loop
(spec F8, D6).

One supervised asyncio loop (the canonical gateway pattern — see
``routes/tasks/scheduler.py``) scans enabled ``schedule`` triggers of
published workflows every ``SCAN_INTERVAL_SECS``, computes due-ness with
APScheduler's ``CronTrigger`` (parser only — no scheduler process), and
CAS-claims ``last_fired_at`` so concurrent gateway workers can never
double-fire one tick. Missed ticks while the gateway was down fire once
(the most recent one), not as a catch-up storm.

The same loop also resumes runs parked at a long ``wait`` node whose deadline
has passed (``scan_due_waits``) — one timekeeper for the app, not two.

Lifecycle: ``start_workflow_scheduler`` / ``stop_workflow_scheduler`` from
the gateway lifespan (main.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from datetime import UTC, datetime
from typing import Any

# H4: the cron loop — `_scan_once` / `scan_due_waits` run in a supervised
# background task with no request and no member session, so there is no bound
# tenant and the ambient `tenant_session()` would raise `TenantUnbound` (and
# inheriting one would be exactly what H4 forbids). Stays on the unbound
# `get_db()` until H4 binds an explicit tenant per workflow row
# (`tenant_session(org_id)`).
from gateway.routes.workflows.core import _get_db, _log, parse_jsonb
from gateway.routes.workflows.service import (
    RunRejected,
    load_version_serialized,
    record_skipped_run,
    resume_run,
    start_run,
)
from sqlalchemy import text

SCAN_INTERVAL_SECS = 30.0

_scheduler_task: asyncio.Task | None = None


def compute_due_fire(
    cron: str,
    last_fired_at: datetime | None,
    now: datetime,
    timezone: str = "UTC",
) -> datetime | None:
    """The most recent cron tick in (last_fired_at, now], or None.

    Anchoring on the PREVIOUS tick before *now* (rather than walking forward
    from ``last_fired_at``) is what collapses downtime into one catch-up fire.

    ``timezone`` is the maker's wall clock, not the server's: "0 9 * * 1-5" in
    ``Asia/Kolkata`` means 9am in Kolkata on both sides of a DST boundary
    anywhere else in the world. APScheduler's CronTrigger owns that arithmetic
    — which is the whole reason it is used as the parser. Instants stay
    UTC-aware end to end; only the *interpretation* of the expression is local.
    """
    from apscheduler.triggers.cron import CronTrigger

    trigger = CronTrigger.from_crontab(cron, timezone=timezone or "UTC")
    # CronTrigger only walks forward; find the last tick <= now by stepping
    # from a probe point one interval-ish behind now.
    probe = last_fired_at
    if probe is None or (now - probe).total_seconds() > 366 * 24 * 3600:
        # Never fired (or absurdly stale): only fire ticks from now onward.
        nxt = trigger.get_next_fire_time(None, now)
        return None if nxt is None or nxt > now else nxt
    # Never fired (or absurdly stale) is handled by the caller, which writes a
    # baseline instead of firing — see _claim_baseline. Reaching here with
    # probe=None would be the bootstrap trap: get_next_fire_time() only ever
    # looks FORWARD, so "the next tick, if it is not in the future" is a
    # condition that is false at every instant except an exact tick boundary,
    # and a schedule that never fires never gets a last_fired_at to fire from.
    last_tick: datetime | None = None
    cursor: datetime | None = probe
    for _ in range(10000):  # hard bound: a minutely cron over a week of downtime
        nxt = trigger.get_next_fire_time(cursor, cursor or now)
        if nxt is None or nxt > now:
            break
        last_tick = nxt
        cursor = nxt
    return last_tick


async def _claim_baseline(db: Any, trigger_id: str, now: datetime) -> None:
    """Arm a schedule that has never fired, without firing it.

    Guarded on ``last_fired_at IS NULL`` so two workers seeing a new trigger
    at the same moment cannot fight over the baseline, and so this can never
    rewind a schedule that has already run.
    """
    await db.execute(
        text(
            """UPDATE workflow_triggers SET last_fired_at = :now
               WHERE id = :id AND last_fired_at IS NULL"""
        ),
        {"id": trigger_id, "now": now},
    )
    await db.commit()
    _log.info("workflows.schedule_armed", trigger_id=trigger_id)


async def _scan_once(now: datetime | None = None) -> int:
    """One scheduler pass. Returns the number of runs fired."""
    now = now or datetime.now(UTC)
    db = await _get_db()
    fired = 0
    try:
        rows = (
            await db.execute(
                text(
                    """SELECT t.id AS trigger_id, t.config, t.last_fired_at,
                      w.id AS workflow_id, w.name, w.latest_version, w.variables
                 FROM workflow_triggers t
                 JOIN workflows w ON w.id = t.workflow_id
                WHERE t.kind = 'schedule' AND t.enabled
                  AND w.status = 'published' AND w.latest_version IS NOT NULL"""
                )
            )
        ).fetchall()
        for row in rows:
            config = parse_jsonb(row.config, {}) or {}
            cron = str(config.get("cron") or "").strip()
            if not cron:
                continue
            if row.last_fired_at is None:
                # First sight of this schedule: arm it, do not fire it. A cron
                # says WHEN, not "how far back to catch up", so a schedule
                # created at 09:01 must not immediately fire this morning's
                # 09:00. Recording the baseline is what lets the next scan
                # compute a real interval — without it the trigger sits in a
                # state that can never produce a tick, and the schedule simply
                # never runs. (Measured: a new daily schedule fired 0 times in
                # a simulated week before this.)
                await _claim_baseline(db, str(row.trigger_id), now)
                continue
            try:
                due = compute_due_fire(
                    cron,
                    row.last_fired_at,
                    now,
                    str(config.get("timezone") or "UTC"),
                )
            except (ValueError, TypeError) as exc:
                _log.warning(
                    "workflows.schedule_bad_cron",
                    trigger_id=str(row.trigger_id),
                    error=str(exc)[:120],
                )
                continue
            if due is None:
                continue
            # CAS claim: only one worker wins this tick.
            claimed = await db.execute(
                text(
                    """UPDATE workflow_triggers SET last_fired_at = :due
                       WHERE id = :id
                         AND (last_fired_at IS NULL OR last_fired_at < :due)"""
                ),
                {"id": str(row.trigger_id), "due": due},
            )
            await db.commit()
            if claimed.rowcount != 1:
                continue
            # From here the tick is CLAIMED and will never be re-offered, so
            # every path out of this block must leave a trace. A schedule that
            # quietly does nothing is the worst failure this app can have: the
            # maker sees a gap in run history and no reason for it.
            serialized = await load_version_serialized(
                db,
                str(row.workflow_id),
                int(row.latest_version),
            )
            if serialized is None:
                await record_skipped_run(
                    workflow_id=str(row.workflow_id),
                    workflow_name=row.name,
                    version=int(row.latest_version),
                    trigger_kind="schedule",
                    trigger_payload={"scheduled_at": due.isoformat()},
                    reason=(
                        f"published version {row.latest_version} is missing — "
                        "republish the workflow"
                    ),
                )
                continue
            try:
                await start_run(
                    workflow_id=str(row.workflow_id),
                    workflow_name=row.name,
                    version=int(row.latest_version),
                    serialized=serialized,
                    trigger_kind="schedule",
                    trigger_payload={"scheduled_at": due.isoformat()},
                    variables=parse_jsonb(row.variables, {}),
                    started_by="scheduler",
                )
                fired += 1
            except RunRejected as exc:
                _log.warning(
                    "workflows.schedule_run_rejected",
                    workflow_id=str(row.workflow_id),
                    error=str(exc),
                )
                await record_skipped_run(
                    workflow_id=str(row.workflow_id),
                    workflow_name=row.name,
                    version=int(row.latest_version),
                    trigger_kind="schedule",
                    trigger_payload={"scheduled_at": due.isoformat()},
                    reason=f"tick skipped — {exc}",
                )
    finally:
        await db.close()
    return fired


async def scan_due_waits(now: float | None = None) -> int:
    """Resume runs parked at a long ``wait`` node whose deadline has passed.

    The durable half of the wait node: a wait longer than
    ``WAIT_INLINE_MAX_SECONDS`` pauses the run with a deadline in the pause
    snapshot rather than sleeping in-process, so it survives a restart. This
    scan (same loop as cron triggers — one timekeeper, not two) picks up every
    matured wait and hands it to the SAME ``resume_run`` the approvals inbox
    uses. The CAS claim on ``status`` means concurrent workers cannot
    double-resume one pause.
    """
    now = now if now is not None else time.time()
    db = await _get_db()
    resumed = 0
    try:
        rows = (
            await db.execute(
                text(
                    """SELECT p.id, p.run_id, p.snapshot
                         FROM workflow_run_pauses p
                         JOIN workflow_runs r ON r.id = p.run_id
                        WHERE p.reason = 'wait' AND p.status = 'pending'
                          AND r.status = 'paused'"""
                )
            )
        ).fetchall()
    finally:
        await db.close()

    for row in rows:
        snapshot = parse_jsonb(row.snapshot, {}) or {}
        try:
            resume_at = float(snapshot.get("resume_at"))
        except (TypeError, ValueError):
            continue
        if resume_at > now:
            continue
        # resume_run flips the pause to 'resolved' under the same read, so a
        # loser of this race simply finds no pending pause and no-ops.
        try:
            result = await resume_run(str(row.run_id), resumed_by="scheduler:wait")
        except Exception as exc:  # one bad run must not stall the rest
            _log.warning(
                "workflows.wait_resume_failed",
                run_id=str(row.run_id),
                error=str(exc)[:160],
            )
            continue
        if result.get("ok"):
            resumed += 1
    return resumed


async def _scheduler_loop() -> None:
    while True:
        try:
            await _scan_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad cycle kill the loop
            _log.warning("workflows.scheduler_cycle_failed", error=str(exc)[:200])
        try:
            await scan_due_waits()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("workflows.wait_scan_failed", error=str(exc)[:200])
        await asyncio.sleep(SCAN_INTERVAL_SECS)


async def start_workflow_scheduler() -> None:
    global _scheduler_task
    # WS-29 launch-defang kill-switch (default ON). The cron scanner is out of
    # launch scope and not yet tenant-bound (H4 slice 6c), so the RLS-cutover
    # runbook sets WORKFLOW_SCHEDULER_ENABLED false to keep it from writing
    # UNBOUND under FORCE ROW LEVEL SECURITY. One flag gates the whole workflow
    # scheduling subsystem (this scanner AND reconcile_orphaned_runs); the gate
    # lives INSIDE the start function, never as an `if` at the gateway call site.
    if os.getenv("WORKFLOW_SCHEDULER_ENABLED", "").strip().lower() in {
        "0", "false", "no", "off",
    }:
        _log.info("workflows.scheduler_disabled")
        return
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.get_running_loop().create_task(
        _scheduler_loop(),
        name="workflow-schedule-scanner",
    )
    _log.info("workflows.scheduler_started")


async def stop_workflow_scheduler() -> None:
    global _scheduler_task
    task, _scheduler_task = _scheduler_task, None
    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


def scheduler_status() -> dict[str, Any]:
    return {
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "scan_interval_secs": SCAN_INTERVAL_SECS,
    }
