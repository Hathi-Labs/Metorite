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

# H4 / H-328 part (b): the cron loop — `_scan_once` / `scan_due_waits` run in a
# supervised background task with no request and no member session, so there
# is no bound tenant to inherit (and inheriting one is what H4 forbids). Each
# scan loops over the orgs of `service._organization_ids()` (the RLS-exempt
# `organization` table, the one unbound read). For each org it checks
# `unattended.org_allowed(org)` FIRST, and skips an org that is not on
# `WORKFLOW_UNATTENDED_ORGS` (default empty, so nothing starts). It then binds
# that one org with `_tenant_session(org)` for each step. A step never holds a
# session across a commit, because a commit drops the bound tenant. The org of
# a run or a workflow comes from the row, never from the ambient context.
from gateway.routes.workflows import park, unattended
from gateway.routes.workflows.core import (
    _log,
    _tenant_session,
    parse_jsonb,
    publish_workflow_activity,
)
from gateway.routes.workflows.service import (
    _MEMBER_BLIND_REASON,
    RunRejected,
    _member_blind_write_nodes,
    _organization_ids,
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
    """One scheduler pass over the LISTED orgs. Returns the number of runs fired.

    H-328 part (b): the loop reads each org of ``_organization_ids()`` and
    skips an org that is not on ``WORKFLOW_UNATTENDED_ORGS``, BEFORE it reads a
    trigger. With the list empty, the pass reads no trigger, claims no tick and
    starts no run. One failing org does not stop the others.
    """
    now = now or datetime.now(UTC)
    fired = 0
    for org in await _organization_ids():
        if not unattended.org_allowed(org):
            continue
        try:
            fired += await _scan_org(org, now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning(
                "workflows.schedule_org_failed",
                organization_id=org,
                error=type(exc).__name__,
            )
    return fired


async def _scan_org(org: str, now: datetime) -> int:
    """The schedule pass of ONE listed org. Each step binds *org* on its own.

    A step that commits ends its transaction, and that drops the bound tenant.
    So the read, the arm, the claim and the version load each open their own
    ``_tenant_session(org)``. The run takes the org of the workflow ROW, and a
    row of another org is skipped.

    A workflow that holds a node of ``service._member_blind_write_nodes`` does
    not start. A schedule has no member to check, so the claimed tick leaves a
    ``cancelled`` row with ``_MEMBER_BLIND_REASON``.
    """
    fired = 0
    async with _tenant_session(org) as db:
        rows = (
            await db.execute(
                text(
                    """SELECT t.id AS trigger_id, t.config, t.last_fired_at,
                      w.id AS workflow_id, w.name, w.latest_version, w.variables,
                      w.organization_id
                 FROM workflow_triggers t
                 JOIN workflows w ON w.id = t.workflow_id
                WHERE t.kind = 'schedule' AND t.enabled
                  AND w.status = 'published' AND w.latest_version IS NOT NULL
                  AND w.organization_id = CAST(:org AS uuid)"""
                ),
                {"org": org},
            )
        ).fetchall()
    for row in rows:
        if str(row.organization_id).lower() != org.lower():
            continue  # the row names another org: never run it under this one
        row_org = str(row.organization_id)
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
            async with _tenant_session(row_org) as db:
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
        async with _tenant_session(row_org) as db:
            claimed = await db.execute(
                text(
                    """UPDATE workflow_triggers SET last_fired_at = :due
                       WHERE id = :id
                         AND (last_fired_at IS NULL OR last_fired_at < :due)"""
                ),
                {"id": str(row.trigger_id), "due": due},
            )
            won = claimed.rowcount == 1
        if not won:
            continue
        # From here the tick is CLAIMED and will never be re-offered, so
        # every path out of this block must leave a trace. A schedule that
        # quietly does nothing is the worst failure this app can have: the
        # maker sees a gap in run history and no reason for it.
        async with _tenant_session(row_org) as db:
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
                organization_id=row_org,
            )
            continue
        if _member_blind_write_nodes(serialized):
            # A task node writes `pm_tasks` with no member check, and a
            # schedule has no member to check. So the scan refuses the
            # workflow, as `run_published_workflow` does (H-328 part (b)).
            await record_skipped_run(
                workflow_id=str(row.workflow_id),
                workflow_name=row.name,
                version=int(row.latest_version),
                trigger_kind="schedule",
                trigger_payload={"scheduled_at": due.isoformat()},
                reason=(
                    "tick skipped — this workflow can change Projects tasks, so "
                    f"a schedule cannot start it yet. {_MEMBER_BLIND_REASON}"
                ),
                organization_id=row_org,
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
                organization_id=row_org,
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
                organization_id=row_org,
            )
    return fired


async def scan_due_waits(now: float | None = None) -> int:
    """Resume runs parked at a long ``wait`` node whose deadline has passed.

    The durable half of the wait node: a wait longer than
    ``WAIT_INLINE_MAX_SECONDS`` pauses the run with a deadline in the pause
    snapshot rather than sleeping in-process, so it survives a restart. This
    scan (same loop as cron triggers — one timekeeper, not two) picks up every
    matured wait and hands it to the SAME ``resume_run`` the approvals inbox
    uses. ``resume_run`` claims the pause with ``AND status = 'pending'`` and
    the run with ``AND status = 'paused'``, each with ``RETURNING``. So
    concurrent workers cannot double-resume one pause. AUTO-7b added that
    guard: this docstring claimed it before the code held it.

    H-328 part (b): the scan loops over the orgs of ``_organization_ids()`` and
    skips an org that is not on ``WORKFLOW_UNATTENDED_ORGS`` BEFORE it reads a
    pause. A resumed run carries the org of its run ROW. With the list empty,
    the scan reads no pause and resumes no run.

    AUTO-7b: the same org loop settles the parked gates of the org with
    :func:`scan_decided_pauses`, after the list check. There is no second loop
    over the orgs and no second scheduler.
    """
    now = now if now is not None else time.time()
    resumed = 0
    for org in await _organization_ids():
        if not unattended.org_allowed(org):
            continue
        try:
            resumed += await scan_decided_pauses(org)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning(
                "workflows.decided_org_failed",
                organization_id=org,
                error=type(exc).__name__,
            )
        try:
            async with _tenant_session(org) as db:
                rows = (
                    await db.execute(
                        text(
                            """SELECT p.id, p.run_id, p.snapshot, r.organization_id
                                 FROM workflow_run_pauses p
                                 JOIN workflow_runs r ON r.id = p.run_id
                                WHERE p.reason = 'wait' AND p.status = 'pending'
                                  AND r.status = 'paused'
                                  AND r.organization_id = CAST(:org AS uuid)"""
                        ),
                        {"org": org},
                    )
                ).fetchall()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning(
                "workflows.wait_org_failed",
                organization_id=org,
                error=type(exc).__name__,
            )
            continue
        for row in rows:
            if str(row.organization_id).lower() != org.lower():
                continue  # the row names another org: never resume it under this one
            snapshot = parse_jsonb(row.snapshot, {}) or {}
            try:
                resume_at = float(snapshot.get("resume_at"))
            except (TypeError, ValueError):
                continue
            if resume_at > now:
                continue
            # resume_run claims the pause with a compare-and-set, so a loser
            # of this race matches no row and starts nothing.
            try:
                result = await resume_run(
                    str(row.run_id),
                    resumed_by="scheduler:wait",
                    organization_id=str(row.organization_id),
                )
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


async def scan_decided_pauses(org: str) -> int:
    """Settle the parked gates of ONE org from their decision rows (AUTO-7b).

    Only the org loop of :func:`scan_due_waits` calls this, after its list
    check. So the org comes from the loop's own enumeration, never from a
    request, a payload or the ambient tenant. Each step opens its own
    ``_tenant_session(org)``.

    The answer comes from the ``pm_decisions`` row only, through
    ``park.outcome_of``. The snapshot, the trigger payload and a node output
    never decide.

    * ``yes`` from the decider: ``resume_run`` as the decider. Its
      compare-and-set lets exactly one caller start the run. The run keeps
      its stored kind, so it stays marked, and a second gate parks again.
    * any other answer: the pause ``rejected`` and the run ``cancelled``,
      with the error ``decision declined``.
    * no answer, and the DB clock passed ``expires_at``: the same end, with
      ``decision expired``. The winner of the pause compare-and-set tells the
      owner once, as a comment on the subject task.

    This scan runs with no ``no_person_scope``. It answers nothing, and it
    writes no column of the answer. Returns the number of settled gates.
    """
    async with _tenant_session(org) as db:
        rows = (await db.execute(text(park.PARKED_SQL), {"org": org})).fetchall()
    settled = 0
    for row in rows:
        if str(getattr(row, "organization_id", "")).lower() != org.lower():
            continue  # the row names another org: never settle it under this one
        snapshot = parse_jsonb(row.snapshot, {}) or {}
        decision_id = park.decision_id_of(snapshot)
        if decision_id is None:
            continue
        run_id = str(row.run_id)
        try:
            async with _tenant_session(org) as db:
                decision = await park.read_decision(db, decision_id, run_id)
            outcome = park.outcome_of(decision)
            if outcome == park.WAIT:
                continue
            if outcome == park.RESUME:
                result = await resume_run(
                    run_id,
                    resumed_by=str(decision.answered_by).strip().lower(),
                    organization_id=str(row.organization_id),
                )
                if result.get("ok"):
                    settled += 1
                continue
            try:
                async with _tenant_session(org) as db:
                    ended = await park.end_parked(
                        db,
                        pause_id=str(row.id),
                        run_id=run_id,
                        outcome=outcome,
                        workflow_id=str(row.workflow_id),
                        subject_task_id=str(decision.subject_id),
                        decision_id=decision_id,
                    )
            except park.ParkLost:
                ended = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one bad run must not stall the rest
            _log.warning(
                "workflows.decided_settle_failed",
                run_id=run_id,
                error=type(exc).__name__,
            )
            continue
        if ended:
            settled += 1
            reason = park.DECLINED if outcome == park.DECLINE else park.EXPIRED
            _log.info("workflows.parked_run_ended", run_id=run_id, reason=reason)
            publish_workflow_activity(
                str(row.workflow_id),
                str(row.workflow_name or ""),
                phase="end",
                run_id=run_id,
                status="cancelled",
                reason=reason,
            )
    return settled


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
    # WS-29 launch-defang kill-switch (default ON). The RLS-cutover runbook
    # sets WORKFLOW_SCHEDULER_ENABLED false to keep the scanner from writing
    # UNBOUND under FORCE ROW LEVEL SECURITY. Since H-328 part (b) the scans
    # also bind each org, and act only for an org on WORKFLOW_UNATTENDED_ORGS
    # (default empty). Both gates stay: this flag stops the loop, and the list
    # decides which orgs it acts for. One flag gates the whole workflow
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
