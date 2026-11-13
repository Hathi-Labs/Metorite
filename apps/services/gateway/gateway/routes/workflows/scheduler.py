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
has passed (``scan_due_waits``) — one timekeeper for the app, not two. The org
loop of that scan also starts the runs of a Studio timer that a record passed
(``scan_due_timers``, STU-E9a). It adds no loop.

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
# STU-E9a: the timer scan (`scan_due_timers`) runs in that same org loop, only
# for an org that `STUDIO_ORGS` holds too. It binds no tenant and never calls
# `triggers.dispatch_event`. It calls `start_run` with the org of the workflow
# row. Fence: `tests/unit/test_workflows_start_run_org_fence.py`.
from gateway.routes.workflows import human_step, park, unattended
from gateway.routes.workflows.core import (
    _log,
    _tenant_session,
    parse_jsonb,
    publish_workflow_activity,
)
from gateway.routes.workflows.service import (
    _MEMBER_BLIND_REASON,
    DuplicateDelivery,
    RunRejected,
    _member_blind_write_nodes,
    _organization_ids,
    load_version_serialized,
    record_skipped_run,
    resume_run,
    start_run,
)
from gateway.routes.workflows.triggers import BREACH_EVENT_TYPE, BREACH_OP
from sqlalchemy import text

SCAN_INTERVAL_SECS = 30.0

#: STU-E9a. The timer triggers that one pass reads for one org, and the
#: breaches that it reads for each trigger, oldest due first. Both bound a
#: backlog storm. A breach that a pass does not reach waits for the next one.
TIMER_TRIGGERS_PER_PASS = 50
TIMER_BREACHES_PER_TRIGGER = 20
#: The ``started_by`` of the run of a breach.
TIMER_STARTED_BY = "scheduler:timer"
#: The first trigger id after which a pass reads, for each org. A pass that
#: reads a full page moves it, so the next pass reads the next triggers.
_timer_cursor: dict[str, str] = {}
_NO_TRIGGER = "00000000-0000-0000-0000-000000000000"

#: The enabled timer triggers of the published workflows of one org, by id.
_TIMER_TRIGGERS_SQL = """SELECT t.id AS trigger_id, t.config, t.last_fired_at,
       w.id AS workflow_id, w.name, w.owner_email, w.latest_version, w.variables,
       w.organization_id
  FROM workflow_triggers t
  JOIN workflows w ON w.id = t.workflow_id
 WHERE t.kind = 'event' AND t.enabled
   AND t.config -> 'filter' ->> 'op' = :op
   AND w.status = 'published' AND w.latest_version IS NOT NULL
   AND w.organization_id = CAST(:org AS uuid)
   AND t.id > CAST(:after AS uuid)
 ORDER BY t.id
 LIMIT :n"""

#: The first sight of a timer trigger arms it. The DB clock is the arm time,
#: and a record due at or before it never breaches. The guard on NULL keeps
#: two workers from moving it, and never moves an armed trigger.
_ARM_TIMER_SQL = """UPDATE workflow_triggers SET last_fired_at = now()
 WHERE id = CAST(:id AS uuid) AND last_fired_at IS NULL"""

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


def _timer_of_config(config: dict[str, Any]) -> tuple[str, str, str] | None:
    """``(package_id, object, rule)`` of a timer trigger, or None (STU-E9a).

    The save path checks the shape (``crud._validate_event_filter``). This
    check runs again on each read, so a row that raw SQL wrote fails closed.
    """
    from gateway.studio.preview import RECORD_EVENT_SOURCE

    if str(config.get("source") or "").strip() != RECORD_EVENT_SOURCE:
        return None
    flt = config.get("filter")
    if not isinstance(flt, dict) or flt.get("op") != BREACH_OP:
        return None
    parts = tuple(flt.get(k) for k in ("package_id", "object", "timer"))
    if not all(isinstance(p, str) and p.strip() for p in parts):
        return None
    package_id, object_name, rule = parts
    return str(package_id), str(object_name), str(rule)


def _timer_key_prefix(rule: str) -> str:
    """The start of the delivery key of a breach: ``timer:<rule>:``. The key is
    ``timer:<rule>:<record id>:<due ISO>``, and ``objects_store`` builds the
    same text in SQL to leave out a breach whose run exists."""
    return f"timer:{rule}:"


async def _timer_triggers(org: str) -> list[Any]:
    """One page of the timer triggers of *org*, after the cursor of the org. A
    full page moves the cursor, and a short page starts the next pass at the
    first trigger again. One short session."""
    after = _timer_cursor.get(org, _NO_TRIGGER)
    async with _tenant_session(org) as db:
        rows = (
            await db.execute(
                text(_TIMER_TRIGGERS_SQL),
                {"op": BREACH_OP, "org": org, "after": after, "n": TIMER_TRIGGERS_PER_PASS},
            )
        ).fetchall()
    if len(rows) >= TIMER_TRIGGERS_PER_PASS:
        _timer_cursor[org] = str(rows[-1].trigger_id)
    else:
        _timer_cursor.pop(org, None)
    return list(rows)


async def _timer_breaches(
    org: str, row: Any, timer_ref: tuple[str, str, str], studio_held: dict[str, bool],
) -> tuple[dict[str, Any], Any, list[Any]] | None:
    """The version, the timer and the breaches of one armed trigger, or None.

    ONE ``_tenant_session(org)``, and no second one inside it. The author of
    the workflow must read the package (``studio.access.member_reads_package``,
    the rule of the record trigger). *studio_held* is asked before, outside
    any session. A workflow with a node of ``_member_blind_write_nodes`` does
    not start, as at the event door. Each refusal logs one line that names the
    workflow and holds no record content.
    """
    from gateway.studio import objects_store
    from gateway.studio.access import member_reads_package

    package_id, object_name, rule = timer_ref
    workflow_id = str(row.workflow_id)
    owner = str(row.owner_email or "").strip().lower()
    async with _tenant_session(org) as db:
        if not await member_reads_package(
            db, member=owner, package_id=package_id, organization_id=org,
            studio_held=bool(studio_held.get(owner, False)),
        ):
            _log.warning("workflows.timer_refused_package_access", workflow_id=workflow_id)
            return None
        serialized = await load_version_serialized(db, workflow_id, int(row.latest_version))
        if serialized is None:
            return None
        if _member_blind_write_nodes(serialized):
            _log.warning("workflows.timer_refused_task_node", workflow_id=workflow_id)
            return None
        timer = await objects_store.timer_of(
            db, package_id=package_id, object_name=object_name, rule_name=rule,
        )
        if timer is None:
            _log.warning("workflows.timer_not_found", workflow_id=workflow_id)
            return None
        due = await objects_store.due_timer_records(
            db, timer, armed_at=row.last_fired_at, workflow_id=workflow_id,
            key_prefix=_timer_key_prefix(rule), limit=TIMER_BREACHES_PER_TRIGGER,
        )
    return serialized, timer, due


async def _scan_studio_timers(org: str) -> int:
    """The timer step of the org loop of :func:`scan_due_waits` (STU-E9a).

    Only that loop calls this, after its list check. It runs
    :func:`scan_due_timers` only when ``STUDIO_ORGS`` holds the org too. A
    failure logs one line and never stops the other steps or the other orgs.
    """
    try:
        # A lazy import: the Studio routes import the workflows package.
        from gateway.routes.studio import flags as studio_flags

        if not studio_flags.studio_allowed(org):
            return 0
        return await scan_due_timers(org)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # one org must not hide the others
        _log.warning(
            "workflows.timer_org_failed",
            organization_id=org,
            error=type(exc).__name__,
        )
        return 0


async def _armed_timers(org: str) -> list[tuple[Any, tuple[str, str, str]]]:
    """The armed timer triggers of one page of *org*, each with its
    ``(package_id, object, rule)``. Its first sight ARMS a trigger and starts
    nothing (``_ARM_TIMER_SQL``, in its own short session). A row of another
    org and a trigger that is not a timer are left out."""
    armed: list[tuple[Any, tuple[str, str, str]]] = []
    for row in await _timer_triggers(org):
        if str(row.organization_id).lower() != org.lower():
            continue  # the row names another org: never run it under this one
        timer_ref = _timer_of_config(parse_jsonb(row.config, {}) or {})
        if timer_ref is None:
            continue
        if row.last_fired_at is None:
            async with _tenant_session(org) as db:
                await db.execute(text(_ARM_TIMER_SQL), {"id": str(row.trigger_id)})
            _log.info("workflows.timer_armed", trigger_id=str(row.trigger_id))
            continue
        armed.append((row, timer_ref))
    return armed


async def scan_due_timers(org: str) -> int:
    """Start one run for each record of ONE org that passed a Studio timer
    (STU-E9a). Returns the number of runs started.

    Only the org loop of :func:`scan_due_waits` calls this, through
    :func:`_scan_studio_timers`, after ``unattended.org_allowed(org)`` and
    ``routes/studio/flags.studio_allowed``. So the org comes from the loop's
    own enumeration, never from a request, a payload or the ambient tenant.
    This scan binds no tenant, and it never calls ``triggers.dispatch_event``:
    only a member request binds the tenant of an event (H-328 part (c)). It
    calls ``start_run`` itself, as ``_scan_org`` does.

    * A timer trigger is an ``event`` trigger with source ``studio`` and a
      filter of ``package_id``, ``object``, ``op = breach`` and ``timer``.
    * Its first sight ARMS it: ``last_fired_at`` takes the DB clock. A record
      due at or before the arm time never breaches.
    * A record breaches when its hot due value is after the arm time and at or
      before the DB clock, and its pause field holds no pause value. A record
      that leaves a pause breaches at the first scan after.
    * The delivery key ``timer:<rule>:<record id>:<due ISO>`` holds one run for
      each workflow, breach and due value (migration 420). A second scan gets
      :class:`DuplicateDelivery`, and a new due value starts one new run.
    * A pass reads at most ``TIMER_TRIGGERS_PER_PASS`` triggers and
      ``TIMER_BREACHES_PER_TRIGGER`` breaches of each one, oldest due first.
    * Each step holds one session, and no session is open at ``start_run``.

    The payload holds ids and names, never a field value.
    """
    from gateway.studio.access import members_holding_studio
    from gateway.studio.preview import RECORD_EVENT_SOURCE

    armed = await _armed_timers(org)
    if not armed:
        return 0
    # The access of each DISTINCT author, outside any session: the resolver
    # opens a session of its own (the STU-E3 pool fix).
    studio_held = await members_holding_studio((r.owner_email for r, _ in armed), org)
    started = 0
    for row, timer_ref in armed:
        workflow_id = str(row.workflow_id)
        try:
            found = await _timer_breaches(org, row, timer_ref, studio_held)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one trigger must not hide the others
            _log.warning(
                "workflows.timer_trigger_failed", workflow_id=workflow_id,
                error=type(exc).__name__,
            )
            continue
        if found is None:
            continue
        serialized, timer, due = found
        rule = timer_ref[2]
        for item in due:
            try:
                await start_run(
                    workflow_id=workflow_id,
                    workflow_name=row.name,
                    version=int(row.latest_version),
                    serialized=serialized,
                    trigger_kind="event",
                    trigger_payload={
                        "source": RECORD_EVENT_SOURCE,
                        "event_type": BREACH_EVENT_TYPE,
                        "package_id": timer.package_id,
                        "object": timer.object_name,
                        "record_id": item.record_id,
                        "rule": rule,
                        "due_at": item.due_at,
                    },
                    variables=parse_jsonb(row.variables, {}),
                    started_by=TIMER_STARTED_BY,
                    organization_id=str(row.organization_id),
                    delivery_key=f"{_timer_key_prefix(rule)}{item.record_id}:{item.due_at}",
                )
                started += 1
            except DuplicateDelivery:
                continue  # another scan started this breach first
            except RunRejected as exc:
                # A run cap holds. The breach stays due, and a later pass
                # starts it. The rest of this trigger waits too.
                _log.warning(
                    "workflows.timer_run_rejected", workflow_id=workflow_id,
                    error=str(exc)[:160],
                )
                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one trigger must not hide the others
                _log.warning(
                    "workflows.timer_start_failed", workflow_id=workflow_id,
                    error=type(exc).__name__,
                )
                break
    if started:
        _log.info("workflows.timer_runs_started", organization_id=org, runs=started)
    return started


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

    STU-E9a: the same loop starts the runs of the Studio timers of the org with
    :func:`_scan_studio_timers`, which runs :func:`scan_due_timers` only when
    ``STUDIO_ORGS`` holds the org too. A run that a timer starts is not counted
    in the answer, which counts resumes.
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
            resumed += await scan_done_tasks(org)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            _log.warning(
                "workflows.task_org_failed",
                organization_id=org,
                error=type(exc).__name__,
            )
        await _scan_studio_timers(org)
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
                    pause_id=str(row.id),
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
      It passes ``decided=True``, so the resumed run carries the decision.
      A ``studio_write`` node right after the gate then runs ONE write,
      after it reads the same decision row again (STU-B2c-2d).
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
                decision = await park.read_decision(db, decision_id, run_id, str(row.id))
            outcome = park.outcome_of(decision)
            if outcome == park.WAIT:
                continue
            if outcome == park.RESUME:
                result = await resume_run(
                    run_id,
                    resumed_by=str(decision.answered_by).strip().lower(),
                    organization_id=str(row.organization_id),
                    pause_id=str(row.id),
                    # STU-B2c-2d: the one caller that carries the decision, so
                    # a Studio write node right after this gate may run once.
                    decided=True,
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
                organization_id=org,
            )
    return settled


async def scan_done_tasks(org: str) -> int:
    """Settle the human steps of ONE org from their task rows (STU-E4).

    Only the org loop of :func:`scan_due_waits` calls this, after its list
    check. So the org comes from the loop's own enumeration, never from a
    request, a payload or the ambient tenant. Each step opens its own
    ``_tenant_session(org)``, and the task is read in that session only.

    The task id comes from the stored pause row. ``human_step.read_task``
    reads the task only when its origin names THIS run and THIS node. The
    status stage of that row alone decides:

    * done: ``resume_run`` with ``task_done=True``. Its compare-and-set lets
      exactly one caller start the run, so a second scan resumes nothing.
    * cancelled, or the task is gone: the pause ``rejected`` and the run
      ``failed`` with a plain message, with the same compare-and-set. That
      covers a cancelled lane, an archived or merged task, a deleted task,
      a TRASH of the assignee that stood for 60 seconds, and an assignee who
      is no longer an active member. A done task wins over an archive, and a
      standing trash wins over a done lane.
    * any other stage: the run stays paused.

    Returns the number of settled steps.
    """
    async with _tenant_session(org) as db:
        rows = (await db.execute(text(human_step.PENDING_SQL), {"org": org})).fetchall()
    settled = 0
    for row in rows:
        if str(getattr(row, "organization_id", "")).lower() != org.lower():
            continue  # the row names another org: never settle it under this one
        snapshot = parse_jsonb(row.snapshot, {}) or {}
        task_id = human_step.task_id_of(snapshot)
        if task_id is None:
            continue
        run_id = str(row.run_id)
        try:
            async with _tenant_session(org) as db:
                task = await human_step.read_task(
                    db, task_id, run_id, str(row.node_id),
                    str(snapshot.get("assignee") or ""), org,
                )
            outcome = human_step.task_outcome(task)
            if outcome == human_step.WAIT:
                continue
            if outcome == human_step.RESUME:
                result = await resume_run(
                    run_id,
                    resumed_by="scheduler:task",
                    organization_id=str(row.organization_id),
                    pause_id=str(row.id),
                    task_done=True,
                )
                if result.get("ok"):
                    settled += 1
                continue
            try:
                async with _tenant_session(org) as db:
                    ended = await human_step.end_step(
                        db, pause_id=str(row.id), run_id=run_id, outcome=outcome,
                    )
            except park.ParkLost:
                ended = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one bad run must not stall the rest
            _log.warning(
                "workflows.task_settle_failed",
                run_id=run_id,
                error=type(exc).__name__,
            )
            continue
        if ended:
            settled += 1
            reason = (
                human_step.CANCELLED_ERROR
                if outcome == human_step.CANCELLED
                else human_step.GONE_ERROR
            )
            _log.info("workflows.human_step_ended", run_id=run_id, reason=reason)
            publish_workflow_activity(
                str(row.workflow_id),
                str(row.workflow_name or ""),
                phase="end",
                run_id=run_id,
                status="failed",
                reason=reason,
                organization_id=org,
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
