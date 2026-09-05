"""Projects · recurring tasks (WS-27o).

Spec: ``project-docs/specs/project_management_app.md`` §11.2 item 7, §11.13.

    GET    /projects/tasks/{task_id}/recurrence
    PUT    /projects/tasks/{task_id}/recurrence     → set or replace the rule
    DELETE /projects/tasks/{task_id}/recurrence     → stop the series

*"Every operations cadence is recurring. Without it those live in someone's head
or in ClickUp."*

**No scheduler, and that is forced rather than chosen.** §5's non-goals: *"A
second automation engine. ADR-028/D6: `/workflows` is the only engine."* A
recurrence worker here would be exactly that. So the successor is created when a
task **closes** — `apply_status_transition` already owns that moment — and the
feature needs no cron, no worker, no new transport. Migration 157 records what
that costs.

**The date arithmetic is pure and lives at the top of this file.** Recurrence is
one of those features that looks trivial and is not: January 31st monthly, a
weekly rule spanning a Sunday, a task closed six weeks late, and a February 29th
yearly rule are each a different way to be quietly wrong for a year.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from typing import Any

from acb_auth import UserContext, get_current_user
from fastapi import Depends, HTTPException
from gateway.routes.projects.core import (
    _tenant_session,
    actor,
    clean_payload,
    insert_row,
    is_runnable_with_ancestors,
    load_default_status,
    load_visible_task,
    next_task_number,
    record_activity,
    resolve_visibility,
    router,
    update_row,
    status_owner_id,
)
from pydantic import BaseModel
from sqlalchemy import text

FREQS: tuple[str, ...] = ("daily", "weekly", "monthly", "yearly")
ANCHORS: tuple[str, ...] = ("due", "completed")
MAX_INTERVAL = 365

#: How many times the catch-up loop may advance before giving up.
#:
#: A rule anchored on `due` advances until it lands in the future, so a daily
#: task last due five years ago is ~1800 steps. The bound exists because the
#: alternative to a cap is an unbounded loop on data somebody can create, and a
#: series that far behind is dead rather than late.
MAX_CATCHUP = 4000

#: Fields carried from a finished task to its successor.
#:
#: NOT here, deliberately: `status_id` (the successor starts in the project's
#: default lane, because "this month's report" has not been started),
#: `completed_at`, `task_number` (allocated fresh), and the timeline — comments
#: and attachments belong to the occurrence they were made on, and copying last
#: month's discussion onto this month's task is how a recurring task becomes
#: unreadable by March.
CARRIED_FIELDS: tuple[str, ...] = (
    "project_id", "root_project_id", "parent_task_id", "type_id", "title",
    "description", "importance", "estimate_mins", "tags", "custom_fields",
    "source",
)


def _clamp_day(year: int, month: int, day: int) -> int:
    """The requested day-of-month, or the last day the month actually has.

    **The January 31st case**, and the reason `day_of_month` stores what
    somebody asked for rather than what February can deliver: clamping at
    computation time keeps "the 31st" meaning the 31st in the months that have
    one. Storing the clamped value instead would silently and permanently
    demote a monthly rule to the 28th after its first February.
    """
    return min(day, calendar.monthrange(year, month)[1])


def _add_months(when: datetime, months: int, day_of_month: int) -> datetime:
    total = (when.year * 12 + (when.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    return when.replace(
        year=year, month=month, day=_clamp_day(year, month, day_of_month),
    )


def _next_weekday(after: datetime, weekdays: list[int], interval: int) -> datetime:
    """The next allowed weekday strictly after ``after``.

    ISO weekdays: Monday is 1. Within the same week the next allowed day is
    simply the next one up; when the week runs out the rule jumps ``interval``
    weeks and takes the first allowed day of that week — which is what makes
    "every other Monday and Thursday" land on both days of the right weeks
    rather than alternating between them.
    """
    allowed = sorted(set(weekdays))
    current = after.isoweekday()
    later = [d for d in allowed if d > current]
    if later:
        return after + timedelta(days=later[0] - current)
    # Move to the first allowed day of the week `interval` weeks on.
    days_to_monday = 7 - current + 1
    week_start = after + timedelta(days=days_to_monday + 7 * (interval - 1))
    return week_start + timedelta(days=allowed[0] - 1)


def _step(rule: dict[str, Any], when: datetime) -> datetime:
    """One advance of the rule from ``when``."""
    freq = str(rule["freq"])
    interval = int(rule.get("interval") or 1)

    if freq == "daily":
        return when + timedelta(days=interval)
    if freq == "weekly":
        return _next_weekday(when, list(rule.get("weekdays") or []), interval)
    if freq == "monthly":
        return _add_months(when, interval, int(rule["day_of_month"]))
    # yearly
    month = int(rule.get("month_of_year") or when.month)
    day = int(rule["day_of_month"])
    year = when.year + interval
    return when.replace(year=year, month=month, day=_clamp_day(year, month, day))


def validate_rule(rule: dict[str, Any]) -> dict[str, Any]:
    """Refuse a rule that cannot produce a date, with a reason.

    The database refuses these too (157's CHECKs). Doing it here as well is not
    redundancy for its own sake: an IntegrityError surfaces as a 500 that says
    nothing about *which* field was missing, and a rule that silently stops a
    series is the failure people notice weeks later.
    """
    freq = str(rule.get("freq") or "")
    if freq not in FREQS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown frequency '{freq}'. One of: {list(FREQS)}.",
        )
    # `or 1` would be wrong here: an explicit `0` is falsy, so it would become
    # "every 1" and pass — a typo that looks exactly like a save, and one the
    # database's own CHECK would have refused as a 500 rather than a 422.
    # Absent means "every 1"; zero means the sender made a mistake.
    raw_interval = rule.get("interval")
    interval = 1 if raw_interval is None else int(raw_interval)
    if not 1 <= interval <= MAX_INTERVAL:
        raise HTTPException(
            status_code=422,
            detail=f"Repeat every 1 to {MAX_INTERVAL}, not {interval}.",
        )
    anchor = str(rule.get("anchor") or "due")
    if anchor not in ANCHORS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown anchor '{anchor}'. One of: {list(ANCHORS)} — "
                   f"'due' keeps the schedule, 'completed' measures from when "
                   f"the last one was actually finished.",
        )
    weekdays = [int(d) for d in (rule.get("weekdays") or [])]
    if any(d < 1 or d > 7 for d in weekdays):
        raise HTTPException(
            status_code=422, detail="Weekdays are 1 (Monday) to 7 (Sunday).",
        )
    if freq == "weekly" and not weekdays:
        raise HTTPException(
            status_code=422,
            detail="A weekly repeat needs at least one weekday, or it has no "
                   "way to choose a day.",
        )
    if freq in ("monthly", "yearly") and not rule.get("day_of_month"):
        raise HTTPException(
            status_code=422,
            detail=f"A {freq} repeat needs a day of the month.",
        )
    return {
        "freq": freq,
        "interval": interval,
        "anchor": anchor,
        "weekdays": sorted(set(weekdays)),
        "day_of_month": rule.get("day_of_month"),
        "month_of_year": rule.get("month_of_year"),
        "until_at": rule.get("until_at"),
        "max_occurrences": rule.get("max_occurrences"),
    }


def series_exhausted(rule: dict[str, Any], candidate: datetime | None) -> bool:
    """Whether the series has ended before this candidate.

    Both limits are honoured and whichever ends it first wins: a rule with
    `max_occurrences: 6` and an `until_at` next year stops at six.
    """
    if candidate is None:
        return True
    cap = rule.get("max_occurrences")
    if cap is not None and int(rule.get("occurrences_made") or 0) >= int(cap):
        return True
    until = rule.get("until_at")
    return bool(until and candidate > _aware(until))


def _aware(value: Any) -> datetime:
    """A stored timestamp as an aware `datetime`. UTC when it says nothing."""
    when = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def next_occurrence(
    rule: dict[str, Any],
    *,
    due_at: Any | None,
    completed_at: Any | None,
    now: datetime,
) -> datetime | None:
    """When the successor is due, or ``None`` if the series has ended.

    **Anchor decides what it is measured from**, and the two answers mean
    different things (migration 157 spells them out): `due` keeps the schedule,
    so finishing late does not drag the series later; `completed` measures the
    interval from when the work was actually done.

    **An anchor of `due` CATCHES UP.** A monthly task closed six weeks late
    would otherwise produce a successor already in the past — visibly overdue
    the moment it appears, which teaches people the date is meaningless. The
    rule advances until it lands in the future, and the occurrences that were
    missed are *skipped rather than backfilled*: nobody wants four copies of a
    stand-up they did not attend.

    An anchor of `completed` never needs catching up — its base is already
    "now-ish" — so it takes exactly one step and stays honest about the
    interval somebody asked for.
    """
    anchor = str(rule.get("anchor") or "due")
    base = (
        _aware(completed_at) if anchor == "completed" and completed_at
        else _aware(due_at) if due_at
        else _aware(completed_at) if completed_at
        else now
    )

    candidate = _step(rule, base)
    if anchor == "due":
        steps = 0
        while candidate <= now and steps < MAX_CATCHUP:
            candidate = _step(rule, candidate)
            steps += 1
        if candidate <= now:
            # Further behind than the cap allows: the series is dead, not late.
            return None

    return None if series_exhausted(rule, candidate) else candidate


# ── The write path ──────────────────────────────────────────────────────────

class RecurrenceIn(BaseModel):
    freq: str | None = None
    interval: int | None = None
    anchor: str | None = None
    weekdays: list[int] | None = None
    day_of_month: int | None = None
    month_of_year: int | None = None
    until_at: str | None = None
    max_occurrences: int | None = None


def rule_of(row: Any) -> dict[str, Any]:
    """A `pm_recurrences` row as the plain dict the pure functions take."""
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "freq": row.freq,
        "interval": row.interval,
        "anchor": row.anchor,
        "weekdays": list(row.weekdays or []),
        "day_of_month": row.day_of_month,
        "month_of_year": row.month_of_year,
        "until_at": row.until_at.isoformat() if row.until_at else None,
        "max_occurrences": row.max_occurrences,
        "occurrences_made": row.occurrences_made,
    }


async def spawn_successor(db: Any, task: Any, *, actor_id: str) -> str | None:
    """Create the next occurrence of a closing task. Returns its id, or None.

    Called from `apply_status_transition` — the one place that knows a task has
    crossed into a closing category — so a task closed from the board, from My
    work, from an automation or from a bulk edit all recur identically. A second
    call site would be a fifth way to finish a task that forgets to.

    **Guarded by `recurrence_spawned_at`, not by inference.** A task can cross
    into `done` more than once (close, reopen to add a note, close again) and
    every crossing reaches this. Without the stamp, one weekly report becomes
    three.
    """
    if getattr(task, "recurrence_id", None) is None:
        return None
    if getattr(task, "recurrence_spawned_at", None) is not None:
        return None

    # WS-27bg. A series does not advance into a project that is not running:
    # spawning a fresh weekly report into a paused, stopped or archived project
    # is inventing work nobody asked for, in a place nobody is looking.
    #
    # ⚠️ Deliberately NOT stamped `recurrence_spawned_at` on this path — unlike
    # the ended-series case below. This is a pause, not an end: the occurrence
    # is skipped while the project sleeps, and closing a task again after the
    # project resumes must still be able to produce a successor. Stamping here
    # would silently kill the series at the moment somebody paused the project.
    # Ancestor-aware, not just this task's own project: a series must not
    # advance inside an active subproject of a PAUSED department. Reading the
    # immediate row alone was the slice-1 defect (see the function's docstring).
    if not await is_runnable_with_ancestors(
        db, getattr(task, "project_id", None)
    ):
        return None

    row = (await db.execute(
        text("SELECT * FROM pm_recurrences WHERE id = CAST(:rid AS uuid)"),
        {"rid": str(task.recurrence_id)},
    )).fetchone()
    if row is None:
        return None

    rule = rule_of(row)
    when = next_occurrence(
        rule,
        due_at=getattr(task, "due_at", None),
        completed_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )
    if when is None:
        # The series has ended. Stamped anyway, so a reopen-and-close does not
        # re-ask a question already answered.
        await update_row(
            db, "pm_tasks", str(task.id),
            {"recurrence_spawned_at": datetime.now(UTC)}, touch=False,
        )
        return None

    values = {
        field: getattr(task, field, None) for field in CARRIED_FIELDS
    }
    values["due_at"] = when
    values["recurrence_id"] = rule["id"]
    values["created_by"] = actor_id
    # `core`'s own helpers, not copies. `next_task_number` allocates in ONE
    # statement so two concurrent creates cannot be handed the same number, and
    # `load_default_status` is the same "which lane does a new task start in"
    # answer `create_task` gives — a second implementation of either would be a
    # second answer.
    values["status_id"] = str((
        await load_default_status(
            db, await status_owner_id(db, str(task.project_id)),
        )
    ).id)
    values["task_number"] = await next_task_number(db, str(task.root_project_id))

    successor = await insert_row(db, "pm_tasks", values)

    # Assignees carry over — a cadence belongs to whoever runs it, and a
    # recurring task that arrives unassigned every time is a recurring task
    # somebody has to re-assign every time.
    await db.execute(
        text(
            "INSERT INTO pm_task_assignees (task_id, assignee, assigned_by) "
            "SELECT CAST(:new AS uuid), assignee, :by FROM pm_task_assignees "
            " WHERE task_id = CAST(:old AS uuid) "
            "ON CONFLICT (task_id, assignee) DO NOTHING"
        ),
        {"new": str(successor.id), "old": str(task.id), "by": actor_id},
    )

    await db.execute(
        text(
            "UPDATE pm_recurrences SET occurrences_made = occurrences_made + 1, "
            "       updated_at = now() WHERE id = CAST(:rid AS uuid)"
        ),
        {"rid": rule["id"]},
    )
    await update_row(
        db, "pm_tasks", str(task.id),
        {"recurrence_spawned_at": datetime.now(UTC)}, touch=False,
    )
    await record_activity(
        db, activity_type="system", created_by=actor_id, task_id=str(task.id),
        body=f"Recurred: next one due {when.date().isoformat()}",
        meta={"recurrence_id": rule["id"], "successor_id": str(successor.id)},
    )
    return str(successor.id)


# ── Routes ──────────────────────────────────────────────────────────────────

@router.get("/tasks/{task_id}/recurrence")
async def get_recurrence(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        if task.recurrence_id is None:
            return {"rule": None}
        row = (await db.execute(
            text("SELECT * FROM pm_recurrences WHERE id = CAST(:rid AS uuid)"),
            {"rid": str(task.recurrence_id)},
        )).fetchone()
        return {"rule": rule_of(row) if row else None}


@router.put("/tasks/{task_id}/recurrence")
async def set_recurrence(
    task_id: str, payload: RecurrenceIn,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Set or replace this task's repeat rule.

    A PUT rather than a POST/PATCH pair: a task has at most one rule, and
    "change the cadence" is the same act as "give it one".
    """
    rule = validate_rule(clean_payload(payload))
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        root = str(task.root_project_id)

        if task.recurrence_id is not None:
            # Edited in place, so the whole series keeps one rule and
            # `occurrences_made` is not reset by a change of cadence — somebody
            # fixing "every 2 weeks" to "every week" has not started over.
            row = await update_row(db, "pm_recurrences", str(task.recurrence_id), rule)
        else:
            row = await insert_row(db, "pm_recurrences", {
                **rule, "project_id": root, "created_by": actor(user),
            })
            await update_row(
                db, "pm_tasks", task_id, {"recurrence_id": str(row.id)},
            )
        return {"rule": rule_of(row)}


@router.delete("/tasks/{task_id}/recurrence")
async def clear_recurrence(
    task_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Stop the series. The task itself, and every occurrence already made, stay.

    Deleting the rule does not delete the tasks it produced: they are real work,
    some of it finished, and a "stop repeating this" button that swept away
    three months of completed reports would be the last time anybody pressed it.
    """
    async with _tenant_session() as db:
        vis = await resolve_visibility(db, user)
        task = await load_visible_task(db, vis, task_id)
        if task.recurrence_id is None:
            # Already in the target state (the house idempotency rule): not an
            # error, and no write.
            return {"cleared": False}
        rule_id = str(task.recurrence_id)
        detached = int((await db.execute(
            text(
                "UPDATE pm_tasks SET recurrence_id = NULL "
                " WHERE recurrence_id = CAST(:rid AS uuid)"
            ),
            {"rid": rule_id},
        )).rowcount or 0)
        await db.execute(
            text("DELETE FROM pm_recurrences WHERE id = CAST(:rid AS uuid)"),
            {"rid": rule_id},
        )
        return {"cleared": True, "cascaded": {"tasks_detached": detached}}


__all__ = [
    "ANCHORS",
    "CARRIED_FIELDS",
    "FREQS",
    "MAX_CATCHUP",
    "MAX_INTERVAL",
    "next_occurrence",
    "rule_of",
    "series_exhausted",
    "spawn_successor",
    "validate_rule",
]
