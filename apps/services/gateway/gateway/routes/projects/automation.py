"""Projects · the seam automation writes through — WS-27f / `workflows_app.md` §13 U1.

**Transport-free on purpose.** The `/workflows` engine imports
:func:`apply_task_patch` and nothing else from this app; there is no HTTP hop,
no second validation path, and no route here. That is Paca's discipline stated
as code (research §4): an automation must *mutate through the ordinary task
service* so its edit gets identical validation and an identical timeline entry
— an engine that wrote `pm_tasks` directly would produce tasks whose history
does not explain how they got that way.

Three things this owes, and each is load-bearing:

1. **A status move is a TRANSITION**, delegated to
   :func:`core.apply_status_transition`, never written as a column. That helper
   owns `completed_at` and the `status_change` activity; skipping it looks
   right in the UI and silently empties the timeline.
2. **"Already in target state" is a no-op**, not a write (research §9). It is
   what makes a crashed automation walk safe to retry, and it is why the run
   step records `skipped` rather than inventing a field change that did not
   happen.
3. **Status is named, never keyed.** A workflow says `"Done"`, not a UUID.
   Statuses are per-project rows, so a graph pinned to one project's status id
   is a graph that can only ever automate that project — the opposite of what
   an automation is for.

**Who this acts as.** `system:workflow:<workflow_id>`, inside the existing
`email | agent:<name>` actor vocabulary (D-PM-4), and **not** member-scoped:
there is deliberately no visibility check here. A published workflow is an
org-level artifact — publishing requires the `workflows:publish` capability —
so its writes are the platform's, not any one member's. Scoping them to
whoever happened to trigger the run would mean the same automation silently
did different things depending on who tripped it, which is worse than either
consistent answer.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

# WS-27aa / H4: the sweep refuses to run without an explicit tenant, and it
# refuses with the seam's OWN exception rather than a bespoke one — `db.py`
# already defines "no tenant, never defaulted", and a second exception type
# would be a second vocabulary for the same refusal.
from gateway.db import TenantUnbound
from gateway.routes.projects.core import (
    CLOSING_CATEGORIES,
    TRIAGE_CATEGORY,
    apply_status_transition,
    coerce_write_values,
    diff_changes,
    is_runnable,
    record_activity,
    record_field_change,
    require_row,
    update_row,
)
from gateway.routes.projects.core import (
    now as _clock,  # aliased: the sweep's own parameter is called `now`
)
from sqlalchemy import text

#: The fields an automation may patch. A deliberately SHORT list: everything
#: here is a plain column on the task. Structural moves (`project_id`,
#: `parent_task_id`) are excluded because they re-stamp `root_project_id` across
#: a subtree and belong to the move endpoint, and `status_id` is excluded
#: because a status move is a transition — see the module docstring.
PATCHABLE_FIELDS: tuple[str, ...] = (
    "title", "description", "importance", "leveraged", "due_at", "start_date",
    "estimate_mins",
)

#: Mirrors `tasks.py::_TRACKED_TASK_FIELDS` for the subset above — the same
#: fields earn the same `field_change` activity whoever wrote them.
_TRACKED: tuple[str, ...] = PATCHABLE_FIELDS


class TaskPatchError(Exception):
    """The patch cannot be applied; the message is safe to show in a run."""


def workflow_actor(workflow_id: str) -> str:
    """The actor string an automation writes under.

    One vocabulary for every species that can act on a task — a person by
    email, an agent as `agent:<name>`, an automation as
    `system:workflow:<id>`. A fourth shape would fork the field that the
    timeline, the activity spine and every report already read.
    """
    return f"system:workflow:{workflow_id}"


async def resolve_status(db: Any, root_project_id: str, wanted: str) -> Any:
    """Find a status in this task's project by NAME, then by category.

    Name first because that is what a maker types and sees on the board;
    category as the fallback so `"done"` keeps working on a project whose lane
    is called "Shipped". Both are case-insensitive.

    A miss raises with the project's actual lane names in the message. The
    alternative — failing with "status not found" — sends the maker to guess at
    a vocabulary that is one query away.
    """
    rows = (await db.execute(
        text(
            "SELECT * FROM pm_task_statuses WHERE project_id = CAST(:pid AS uuid) "
            "ORDER BY position"
        ),
        {"pid": root_project_id},
    )).fetchall()
    target = (wanted or "").strip().lower()
    if not target:
        raise TaskPatchError("status must name a lane")
    for row in rows:
        if str(row.name).strip().lower() == target:
            return row
    for row in rows:
        if str(row.category).strip().lower() == target:
            return row
    names = ", ".join(str(r.name) for r in rows) or "none"
    raise TaskPatchError(f"no status '{wanted}' in this project (has: {names})")


async def apply_task_patch(
    db: Any,
    task_id: str,
    fields: dict[str, Any],
    *,
    actor: str,
) -> dict[str, Any]:
    """Patch a task the way a human PATCH would, and report what changed.

    ONE multi-field node, not one node per field — Paca merged five
    single-field actions into `update_task` and recorded it as a consolidation
    lesson (research §4). Adopting the end state directly is cheaper than
    rediscovering it.

    Returns ``{task_id, changed: [field…], status: name|None, skipped: bool}``.
    ``skipped`` is true when the task was already in the requested state, which
    the run step records instead of a phantom edit.
    """
    unknown = [
        k for k in fields
        if k != "status" and k not in PATCHABLE_FIELDS
    ]
    if unknown:
        raise TaskPatchError(
            f"cannot set {sorted(unknown)} — one of "
            f"{[*PATCHABLE_FIELDS, 'status']}"
        )

    task = await require_row(db, "pm_tasks", task_id, "Task")
    wanted_status = fields.get("status")
    values = {k: v for k, v in fields.items() if k in PATCHABLE_FIELDS}

    changed: list[str] = []
    after = task
    if values:
        # Compare BEFORE writing so an unchanged field is not a write. Without
        # this, an automation that fires on every task update rewrites the same
        # value forever and fills the timeline with edits nobody made.
        pending = {
            k: v for k, v in values.items()
            if _differs(getattr(task, k, None), v)
        }
        if pending:
            after = await update_row(db, "pm_tasks", task_id, coerce_write_values(pending))
            diffs = diff_changes(task, after, _TRACKED)
            changed = [str(d["field"]) for d in diffs]
            if diffs:
                # The ONE field_change door (WS-27w): an automation's edit gets
                # the same label resolution and description coalescing a human
                # PATCH gets — a workflow that rewrites a description every run
                # must not write a timeline row every run. Flagged automation
                # (WS-27z) so the timeline renders it distinctly.
                await record_field_change(
                    db, created_by=actor, task_id=task_id, changes=diffs,
                    automation=True,
                )

    status_name: str | None = None
    if wanted_status is not None:
        target = await resolve_status(db, str(after.root_project_id), str(wanted_status))
        if str(target.id) != str(after.status_id):
            moved = await apply_status_transition(
                db, after, str(target.id), created_by=actor, automation=True,
            )
            after = moved["row"]
            changed.append("status")
            status_name = str(moved["to"].name)
        else:
            status_name = str(target.name)

    return {
        "task_id": task_id,
        "changed": changed,
        "status": status_name,
        "skipped": not changed,
    }


def _differs(current: Any, wanted: Any) -> bool:
    """Loose inequality for the pre-write comparison.

    Deliberately stringly for non-null values: the DB hands back a `datetime`
    or a `Decimal` where the graph carries the ISO string or int the maker
    typed, and a strict `!=` would call every one of those a change and rewrite
    it on every single run.
    """
    if current is None or wanted is None:
        return current is not wanted
    return str(current) != str(wanted)


# ── The lifecycle sweep (WS-27z) ────────────────────────────────────────────
#
# Policy lives in migration 166's three ROOT-project columns; the schedule
# lives in a published `/workflows` workflow (D6 — never a PM-app cron). This
# function is the whole meeting point: the workflow's sweep node calls it with
# nothing but the engine's identity, and everything it does is read from the
# database at run time.


def _months_before(midnight: datetime, months: int) -> datetime:
    """``midnight`` moved back a whole number of calendar months.

    Calendar months, not ``months * 30`` days, because the column is named in
    months and an owner who says "one month" means "the 9th of last month",
    not "30 days". The day clamps to the target month's length (May 31 minus
    three months is Feb 28/29), which is the same choice every calendar
    arithmetic library makes.
    """
    total = midnight.year * 12 + (midnight.month - 1) - months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(midnight.day, monthrange(year, month)[1])
    return midnight.replace(year=year, month=month, day=day)


def _project_midnight(moment: datetime, tz_name: str) -> datetime:
    """The start of ``moment``'s day in the project's timezone.

    This is what makes "a month untouched" defensible (P-28): the cutoff walks
    back from the project's OWN midnight, so the boundary lands where the
    people gardening the board live rather than at Greenwich. A bad zone name
    (validated at write time, so this is belt-and-braces) falls back to UTC —
    a sweep that skipped the project instead would silently switch the policy
    off.
    """
    try:
        zone = ZoneInfo(str(tz_name or "UTC"))
    except Exception:
        zone = ZoneInfo("UTC")
    return datetime.combine(moment.astimezone(zone).date(), time.min, tzinfo=zone)


def _closing_status(statuses: list[Any]) -> Any | None:
    """The lane auto-close moves stale work to.

    The project's first ``cancelled``-category lane (by position), else its
    first ``done`` one. Cancelled preferred deliberately: a task nobody has
    touched for months was abandoned, not finished, and calling it done would
    inflate every completion report. There is no dedicated "default closing
    status" flag in the schema (``is_default`` marks where NEW tasks land),
    so the category ranking IS the model — stated here, tested, and easy to
    replace with a flag if one ever earns its place.
    """
    for wanted in ("cancelled", "done"):
        for row in statuses:  # already ordered by position
            if str(row.category) == wanted:
                return row
    return None


async def _sweep_candidates(
    db: Any, root_id: str, status_ids: list[str], cutoff: datetime,
) -> list[Any]:
    """Unarchived tasks in these lanes, untouched since before ``cutoff``.

    Membership is a POSITIVE ``ANY`` list on purpose — the caller hands in
    exactly the lanes a pass may touch, so triage exemption and the archive
    guard hold by construction rather than by a NOT somebody could drop.

    🔴 **The chain predicate is the other half of the slice-1 defect.** The
    caller checks the ROOT is runnable and then sweeps by ``root_project_id``,
    i.e. the WHOLE subtree — so a *paused subproject* beneath an *active* root
    had its stale work closed anyway. That is the same disagreement the
    recurrence and dispatch guards had, mirrored: one guard read too high, two
    read too low, and the subtree in between was governed by nobody. Every task
    now earns its own verdict from its own project's chain.
    """
    if not status_ids:
        return []
    return (await db.execute(
        text(
            "SELECT t.* FROM pm_tasks t "
            "WHERE t.root_project_id = CAST(:root AS uuid) "
            "AND t.archived_at IS NULL "
            "AND t.status_id = ANY(CAST(:sids AS uuid[])) "
            "AND t.updated_at < :cutoff "
            # Every ancestor of the task's OWN project must be runnable, not
            # merely the root the sweep is iterating.
            "AND NOT EXISTS ("
            "  WITH RECURSIVE chain AS ("
            "    SELECT id, parent_project_id, status, archived_at"
            "      FROM pm_projects WHERE id = t.project_id"
            "    UNION ALL"
            "    SELECT p.id, p.parent_project_id, p.status, p.archived_at"
            "      FROM pm_projects p JOIN chain c ON p.id = c.parent_project_id"
            "  )"
            "  SELECT 1 FROM chain"
            "  WHERE status <> 'active' OR archived_at IS NOT NULL)"
        ),
        {"root": root_id, "sids": status_ids, "cutoff": cutoff},
    )).fetchall()


async def run_lifecycle_sweep(
    db: Any, *, organization_id: str, actor: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Apply ONE tenant's root-project lifecycle policies once. Idempotent.

    ⚠️ **``organization_id`` is required and is never defaulted** (WS-27aa,
    H4). This function is reached only from a scheduled ``/workflows`` node, so
    it has no request to inherit a tenant from — and H4's rule for that
    category is the whole point: *a job that forgets doesn't leak one row, it
    leaks unbounded*. Before this argument existed the roots query was
    ``SELECT * FROM pm_projects WHERE parent_project_id IS NULL`` with no
    predicate at all, which is one schedule archiving and closing **every
    customer's** work. A blank tenant raises :class:`TenantUnbound` rather than
    sweeping wider; the caller
    (``routes/workflows/service._pm_lifecycle_sweeper``) resolves it from the
    workflow owner's ``app_user`` row — a stored fact, never request input
    (R11) — and binds it on the session it hands in.

    For each ROOT project **in that organization** with a policy enabled (both
    columns NULL — the default — means the project is never touched):

    * **archive** — tasks whose status category is done/cancelled and whose
      ``updated_at`` is older than ``archive_after_months`` calendar months
      before the project's own midnight leave the default surfaces
      (``archived_at`` stamped, a ``system`` activity flagged automation).
      Only closed-category lanes are eligible BY CONSTRUCTION, which is
      WS-27w's manual archive guard restated as a query shape.
    * **close** — open tasks (not closed, not triage — WS-27u's queue is a
      place work WAITS) untouched beyond ``close_after_months`` move to the
      project's closing status through :func:`core.apply_status_transition`,
      so ``completed_at``, the ``status_change`` activity and recurrence all
      behave exactly as if a person had moved the card.

    A second run does nothing: archiving removes a task from the archive
    pass's candidate set (``archived_at IS NULL``) and closing removes it
    from the close pass's (its lane is now closed-category) — and the close
    pass's transition bumps ``updated_at``, so even a task closed into a
    project with a shorter archive window waits its full archive term.

    Writes go through the ordinary service, as ``actor`` (the workflow's
    ``system:workflow:<id>`` identity), so every change is an ordinary
    timeline row wearing the automation flag.
    """
    org = str(organization_id or "").strip()
    if not org:
        raise TenantUnbound(
            "run_lifecycle_sweep needs an explicit organization_id — a "
            "scheduled sweep must never inherit an ambient tenant or default "
            "to every tenant (saas_multitenancy_handover.md H4)"
        )
    moment = now or _clock()
    # The tenant predicate is on the ROOTS query and nowhere else, on purpose:
    # everything below reaches its rows through `project.id`, so one fence at
    # the top of the walk bounds the whole sweep. Under RLS phase 4 the bound
    # session narrows it a second time — belt and braces, in the order H3's
    # cliff needs (the predicate has to be right BEFORE the policy exists,
    # because today there is no policy to catch a missing one).
    roots = (await db.execute(
        text(
            "SELECT * FROM pm_projects "
            "WHERE parent_project_id IS NULL "
            "AND organization_id = CAST(:org AS uuid)"
        ),
        {"org": org},
    )).fetchall()

    swept = archived = closed = 0
    for project in roots:
        # 🔴 WS-27bg. Before this guard the sweep had NO project-status
        # predicate anywhere, which was harmless only for as long as `status`
        # meant nothing. The moment `on_hold` became real it turned into the
        # worst failure in this file: a project paused for a quarter under a
        # three-month close policy has its entire open backlog moved to the
        # cancelled lane, written by `system:workflow:<id>` through
        # `apply_status_transition` — so `completed_at` is stamped, the
        # `status_change` activity is written and recurrence fires, exactly as
        # if a person had done it. Nobody would find that for months.
        #
        # `is_runnable` is the ONE shared predicate (core.py), consulted here,
        # by recurrence and by agent dispatch. Three copies of this rule is the
        # CLAUDE.md §5 defect authored three times inside one ticket.
        if not is_runnable(project):
            continue
        archive_months = getattr(project, "archive_after_months", None)
        close_months = getattr(project, "close_after_months", None)
        if archive_months is None and close_months is None:
            continue
        swept += 1
        midnight = _project_midnight(
            moment, getattr(project, "timezone", "UTC"),
        )
        statuses = (await db.execute(
            text(
                "SELECT * FROM pm_task_statuses "
                "WHERE project_id = CAST(:pid AS uuid) ORDER BY position, name"
            ),
            {"pid": str(project.id)},
        )).fetchall()

        if archive_months is not None:
            closed_ids = [
                str(s.id) for s in statuses
                if str(s.category) in CLOSING_CATEGORIES
            ]
            cutoff = _months_before(midnight, int(archive_months))
            for task in await _sweep_candidates(
                db, str(project.id), closed_ids, cutoff,
            ):
                await update_row(
                    db, "pm_tasks", str(task.id), {"archived_at": moment},
                )
                await record_activity(
                    db, activity_type="system", created_by=actor,
                    task_id=str(task.id),
                    body=(
                        f"Task archived — untouched for "
                        f"{int(archive_months)} month(s) after closing"
                    ),
                    automation=True,
                )
                archived += 1

        if close_months is not None:
            target = _closing_status(list(statuses))
            open_ids = [
                str(s.id) for s in statuses
                if str(s.category) not in CLOSING_CATEGORIES
                and str(s.category) != TRIAGE_CATEGORY
            ]
            if target is not None:
                cutoff = _months_before(midnight, int(close_months))
                for task in await _sweep_candidates(
                    db, str(project.id), open_ids, cutoff,
                ):
                    await apply_status_transition(
                        db, task, str(target.id), created_by=actor,
                        automation=True,
                    )
                    closed += 1

    return {
        "projects": swept,
        "archived": archived,
        "closed": closed,
        "skipped": not (archived or closed),
    }
