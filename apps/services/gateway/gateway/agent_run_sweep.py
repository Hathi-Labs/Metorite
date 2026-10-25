"""Projects · the sweep of stranded agent runs (WS-55 SW-10a).

Spec: ``project-docs/specs/external_agent_access.md`` §12.6 (Metorite task
#341). Nudge once, then hand it to a person.

A run can lose its agent: the session ends, the machine sleeps, or the agent
stops with no disposition. Every :data:`SWEEP_EVERY_SECS` this loop finds such
runs, org by org, and does two things, never more:

1. **First miss.** It records one fingerprint ``(run_id, reason)`` in
   ``pm_agent_run_strands`` and sends ONE bell notification to the run's
   member through ``notifications.notify``. The run then reads
   ``needs_agent`` ``stranded``, so ``list_agent_runs`` with
   ``needs_agent=true`` returns it to the member's next session.
2. **Second miss.** One lease later, with still no activity on the run, it
   turns the fingerprint into a recovery record (``recovered_at`` and
   ``evidence``), owned by the member, and posts one system line on the run
   and one on the task timeline. SW-11 (Needs attention) reads it.

**Stranded** is ``filters.stranded_clause()``: ``in_progress``, derived state
``stale``, and no live path (the lease ended, or the connection is revoked or
expired). An ``awaiting`` run waits on a person and is never swept.

**What it never does.** It never reassigns the task, never picks another
agent, never moves the task and never ends the run at either miss. The one
run it closes is a run of the old archive shape (the member approved the
archive, and no agent came back), through ``agent_runs.close_archived_runs``.

**A new activity starts over.** Any activity on the run after the first miss
clears its fingerprint (step 2 of a tick), and ``needs_agent`` reads the same
fit at once (``filters.strand_fits_sql``). A later strand starts at the first
miss again.

**One org at a time.** ``_organization_ids`` reads the RLS-exempt
``organization`` table, the one unbound read of this loop (a tenant-discovery
site registered in ``tests/unit/test_db_engine_seam.py``). Each org then runs
in its own ``tenant_session(org)``, and one session never binds a second org.
The model is ``routes/knowledge/proposal_expiry.py``. It is NOT
``workflows/scheduler.py``, which runs on the unbound ``get_db()``.

**Why it lives here and not in ``routes/projects/``.** That package is
H2-converted, and ``test_db_engine_seam.py`` refuses any ``get_db()`` inside
it. An org loop needs the one unbound discovery read, so it lives at the root
of the gateway, beside ``github_reconcile_loop.py`` and ``my_day_loop.py``.

**Two processes, one bell.** The fingerprint is a partial unique index, and
the first miss inserts with ``ON CONFLICT DO NOTHING``: only the process
whose insert returns a row sends the bell. The second miss is an ``UPDATE``
guarded on ``recovered_at IS NULL``. So no Redis guard is needed.

**Writes.** This module is the only writer of ``pm_agent_run_strands``. The
writes to ``pm_agent_runs`` and ``pm_agent_run_activities`` stay in
``agent_runs.py``, which this module calls.

**Dark.** ``agent_run_sweep_enabled`` (default off) gates the START inside
:func:`start_agent_run_sweep`, and each tick reads it and
``agent_runs_enabled`` again. :func:`sweep_enabled` is the one reader of the
flag. Fence: ``tests/unit/test_agent_run_sweep_r8.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from dataclasses import dataclass
from typing import Any

from acb_auth import agent_oauth
from acb_common import get_logger, get_settings
from sqlalchemy import text

from gateway.db import get_db, tenant_session
from gateway.routes.projects import agent_runs
from gateway.routes.projects.core import record_activity
from gateway.routes.projects.filters import (
    STRAND_REASON_SQL,
    agent_run_lease_s,
    agent_runs_enabled,
    stranded_clause,
)
from gateway.routes.projects.notifications import excerpt_of, notify

_log = get_logger("gateway.projects.agent_run_sweep")

#: Seconds between two ticks. The stale band of §12.1 is five minutes, so a
#: tick every five minutes sees each change of state once.
SWEEP_EVERY_SECS = 300.0

#: The most stranded runs (and the most old-shape archive runs) that one org
#: handles in one tick, the oldest first.
MAX_PER_ORG = 200

#: Seconds to wait for a tick in progress when the gateway stops.
STOP_GRACE_SECONDS = 30.0

#: The bell kind of the first miss. ``nudge`` already means "I am telling you
#: so, once, on purpose" (WS-27bk), so no CHECK changes.
NUDGE_KIND = "nudge"

#: The author of the task timeline row of a recovery record.
SWEEP_ACTOR = "system:agent-runs"

_task: asyncio.Task[None] | None = None
_stopping = asyncio.Event()


def sweep_enabled() -> bool:
    """True when ``agent_run_sweep_enabled`` is on. The ONE reader of the
    flag. Only the gateway reads it."""
    return bool(get_settings().agent_run_sweep_enabled)


@dataclass
class TickResult:
    """What one tick did. A test reads it, and the log line prints it."""

    orgs: int = 0
    closed: int = 0
    cleared: int = 0
    nudged: int = 0
    recovered: int = 0
    failed: int = 0


async def _organization_ids() -> list[str]:
    """Every organization id, from the RLS-EXEMPT ``organization`` table.

    The one unbound read of this loop (a tenant-discovery site).
    """
    db = await get_db()
    try:
        rows = (await db.execute(text("SELECT id FROM organization ORDER BY id"))).fetchall()
    finally:
        await db.close()
    return [str(r.id) for r in rows]


# ── The SQL ─────────────────────────────────────────────────────────────────

#: Step 2. Clear each open strand that no longer fits its run: an activity
#: after the first miss (``new_activity``), or a run that left ``in_progress``
#: (``run_moved``). The same fit as ``filters.strand_fits_sql``, negated.
_CLEAR_SQL = (
    "UPDATE pm_agent_run_strands s SET cleared_at = clock_timestamp(),"
    " cleared_reason = CASE WHEN r.status <> 'in_progress' THEN 'run_moved'"
    "                       ELSE 'new_activity' END"
    "  FROM pm_agent_runs r"
    " WHERE r.id = s.run_id AND r.organization_id = s.organization_id"
    "   AND s.cleared_at IS NULL"
    "   AND (r.status <> 'in_progress' OR r.last_activity_at <> s.seen_activity_at)"
    " RETURNING s.id"
)


def _stranded_sql() -> str:
    """Step 3. The stranded runs of the bound org, the oldest first, each
    with its reason and the open strand of that reason, if any. Built when a
    tick runs, because :func:`filters.stranded_clause` reads the lease then.
    """
    return (
        "SELECT r.id, r.task_id, r.member_email, r.client_via, r.connection_id,"
        "       r.status, r.last_activity_at, r.last_agent_activity_at,"
        f"      {STRAND_REASON_SQL} AS reason,"
        "       EXTRACT(EPOCH FROM (clock_timestamp() - r.last_activity_at)) AS idle_s,"
        "       t.task_number, s.id AS strand_id, s.recovered_at,"
        "       (s.nudged_at < now() - make_interval(secs => :window)) AS window_passed"
        "  FROM pm_agent_runs r"
        "  JOIN pm_tasks t ON t.id = r.task_id"
        "  LEFT JOIN pm_agent_run_strands s"
        "    ON s.run_id = r.id AND s.organization_id = r.organization_id"
        f"   AND s.cleared_at IS NULL AND s.reason = {STRAND_REASON_SQL}"
        "   AND s.seen_activity_at = r.last_activity_at"
        f" WHERE {stranded_clause()}"
        " ORDER BY r.last_activity_at, r.id"
        " LIMIT :n"
    )


#: The first miss: the fingerprint. ``seen_activity_at`` is copied from the
#: run in SQL, so the fit test of ``filters.strand_fits_sql`` compares two
#: values that came from one column. ``ON CONFLICT DO NOTHING`` on the
#: partial unique index: a second process finds the row and sends no bell.
_FIRST_MISS_SQL = (
    "INSERT INTO pm_agent_run_strands"
    " (run_id, task_id, member_email, reason, seen_activity_at)"
    " SELECT r.id, r.task_id, r.member_email, :reason, r.last_activity_at"
    "   FROM pm_agent_runs r"
    "  WHERE r.id = CAST(:rid AS uuid) AND r.last_activity_at = :seen"
    "    AND r.status = 'in_progress'"
    " ON CONFLICT (organization_id, run_id, reason) WHERE cleared_at IS NULL"
    " DO NOTHING RETURNING id"
)

#: The second miss: the recovery record, ONCE (the guard on NULL).
_SECOND_MISS_SQL = (
    "UPDATE pm_agent_run_strands SET recovered_at = clock_timestamp(),"
    " evidence = CAST(:evidence AS jsonb)"
    " WHERE id = CAST(:sid AS uuid) AND recovered_at IS NULL AND cleared_at IS NULL"
    " RETURNING id"
)


# ── The texts (server text, so no member input in any of them) ─────────────


def _minutes(idle_s: Any) -> int:
    return max(int(float(idle_s or 0) // 60), 0)


def _nudge_text(reason: str, client: str, minutes: int) -> str:
    """The excerpt of the one bell of a first miss."""
    if reason == "answer_unread":
        return (f"{client} did not read your answer for {minutes} minutes. "
                "Start an agent session to pick it up, or release the run.")
    return (f"{client} sent nothing on this task for {minutes} minutes. "
            "Start an agent session to pick it up, or release the run.")


def _run_line(reason: str, minutes: int, owner: str) -> str:
    """The system line of a recovery record on the run."""
    what = ("No agent read the answer" if reason == "answer_unread"
            else "No agent activity")
    return (f"{what} for {minutes} minutes, after one nudge. "
            f"A recovery record is open for {owner}.")


def _task_line(reason: str, client: str, minutes: int, owner: str) -> str:
    """The ``system`` row of a recovery record on the task timeline."""
    what = ("did not read the answer" if reason == "answer_unread"
            else "sent nothing")
    return (f"The agent run of {client} {what} for {minutes} minutes. "
            f"{owner} got one nudge, and nothing changed. "
            f"A recovery record is open for {owner}.")


def _evidence(row: Any, minutes: int) -> dict[str, Any]:
    """The evidence of a recovery record: the run, the task, the client, the
    last activity, the reason and the quiet minutes."""
    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "run_id": str(row.id),
        "task_id": str(row.task_id),
        "task_number": int(row.task_number) if row.task_number is not None else None,
        "client_via": row.client_via,
        "connection_id": str(row.connection_id) if row.connection_id else None,
        "reason": row.reason,
        "last_activity_at": iso(row.last_activity_at),
        "last_agent_activity_at": iso(row.last_agent_activity_at),
        "idle_minutes": minutes,
    }


# ── One org ─────────────────────────────────────────────────────────────────


async def _first_miss(db: Any, row: Any) -> bool:
    """Record the fingerprint and send ONE bell. False when another process
    won the insert, or the run moved since the read."""
    made = (await db.execute(text(_FIRST_MISS_SQL), {
        "rid": str(row.id), "reason": row.reason, "seen": row.last_activity_at,
    })).fetchone()
    if made is None:
        return False
    client = agent_oauth.client_label(row.client_via)
    # The actor is the client stamp, as EA12-8 does for a question, so
    # `notifiable()` keeps the run's member. `notify` drops a member who
    # can no longer open the task (rule 3), and the fingerprint stays.
    await notify(
        db, recipients=[row.member_email], kind=NUDGE_KIND, task_id=str(row.task_id),
        actor_id=row.client_via,
        excerpt=excerpt_of(_nudge_text(row.reason, client, _minutes(row.idle_s))),
    )
    return True


async def _second_miss(db: Any, row: Any) -> bool:
    """Open the recovery record ONCE, and post its two system lines."""
    minutes = _minutes(row.idle_s)
    made = (await db.execute(text(_SECOND_MISS_SQL), {
        "sid": str(row.strand_id),
        "evidence": json.dumps(_evidence(row, minutes), ensure_ascii=False),
    })).fetchone()
    if made is None:
        return False
    owner = await agent_runs.member_name(db, row.member_email)
    client = agent_oauth.client_label(row.client_via)
    await agent_runs.write_strand_line(db, row, _run_line(row.reason, minutes, owner))
    await record_activity(
        db, activity_type="system", created_by=SWEEP_ACTOR, task_id=str(row.task_id),
        body=_task_line(row.reason, client, minutes, owner),
        meta={"agent_run_id": str(row.id), "agent_run_strand_id": str(row.strand_id),
              "strand_reason": row.reason},
    )
    return True


async def _one_run(db: Any, row: Any, result: TickResult) -> None:
    """The first miss, or the second miss, or nothing. Any other state writes
    nothing: a nudge inside its window, or a recovery record already open."""
    if row.strand_id is None:
        result.nudged += int(await _first_miss(db, row))
    elif row.recovered_at is None and row.window_passed:
        result.recovered += int(await _second_miss(db, row))


async def run_org(org: str, result: TickResult) -> None:
    """One org's share of a tick, in ONE tenant session. Each stranded run
    gets its own savepoint, so one failure does not undo the others."""
    async with tenant_session(org) as db:
        result.closed += await agent_runs.close_archived_runs(db, limit=MAX_PER_ORG)
        result.cleared += len((await db.execute(text(_CLEAR_SQL))).fetchall())
        rows = (await db.execute(text(_stranded_sql()), {
            "n": MAX_PER_ORG, "window": agent_run_lease_s(),
        })).fetchall()
        for row in rows:
            try:
                async with db.begin_nested():
                    await _one_run(db, row, result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one run must not hide the others
                result.failed += 1
                _log.warning("projects.agent_run_sweep.run_failed",
                             error=type(exc).__name__)


async def run_tick() -> TickResult:
    """One pass over every org. With either flag off it reads nothing."""
    result = TickResult()
    if not (sweep_enabled() and agent_runs_enabled()):
        return result
    orgs = await _organization_ids()
    result.orgs = len(orgs)
    for org in orgs:
        if _stopping.is_set():
            break
        try:
            await run_org(org, result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # one org must not hide the others
            result.failed += 1
            _log.warning("projects.agent_run_sweep.org_failed", error=type(exc).__name__)
    if result.closed or result.cleared or result.nudged or result.recovered or result.failed:
        _log.info("projects.agent_run_sweep.tick", orgs=result.orgs, closed=result.closed,
                  cleared=result.cleared, nudged=result.nudged,
                  recovered=result.recovered, failed=result.failed)
    return result


async def _loop() -> None:
    while not _stopping.is_set():
        try:
            await run_tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # never let one bad tick end the loop
            _log.warning("projects.agent_run_sweep.loop_error", error=type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_stopping.wait(), timeout=SWEEP_EVERY_SECS)


async def start_agent_run_sweep() -> bool:
    """Start the sweep when ``agent_run_sweep_enabled`` is on. Idempotent.

    With the flag off it starts no task, logs once, and returns False.
    """
    global _task
    if not sweep_enabled():
        _log.info("projects.agent_run_sweep.disabled")
        return False
    if _task is not None and not _task.done():
        return True
    _stopping.clear()
    _task = asyncio.get_running_loop().create_task(_loop(), name="agent-run-sweep")
    _log.info("projects.agent_run_sweep.started", tick_secs=SWEEP_EVERY_SECS,
              max_per_org=MAX_PER_ORG)
    return True


async def stop_agent_run_sweep() -> None:
    """Stop the sweep and wait for a tick in progress. Safe when it never
    started."""
    global _task
    _stopping.set()
    task, _task = _task, None
    if task is None or task.done():
        return
    _done, pending = await asyncio.wait({task}, timeout=STOP_GRACE_SECONDS)
    for still in pending:
        _log.warning("projects.agent_run_sweep.stop_timed_out")
        still.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await still


def loop_running() -> bool:
    return _task is not None and not _task.done()


__all__ = [
    "MAX_PER_ORG",
    "NUDGE_KIND",
    "SWEEP_ACTOR",
    "SWEEP_EVERY_SECS",
    "TickResult",
    "loop_running",
    "run_org",
    "run_tick",
    "start_agent_run_sweep",
    "stop_agent_run_sweep",
    "sweep_enabled",
]
