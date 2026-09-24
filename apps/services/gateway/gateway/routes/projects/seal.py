"""Sealing a departed member's personal tree — D63, H-49.

**The principle, in one line: the company is entitled to the work, not to the
workspace.**

D63 was taken on 2026-08-26, *before* the deactivation flow it governs existed.
That was deliberate. The default somebody picks under time pressure, while
building deactivation, is exactly the wrong way to settle what happens to a
departed colleague's private tasks.

## What a seal is, and what it is not

A seal is a timestamp on ``pm_projects.sealed_at`` (migration 215). It is not a
delete, and nothing here deletes. R6's reasoning applies as it does everywhere
else: you cannot undo a delete, and "we removed a leaver's notes" is not a
sentence anybody wants to say twice.

Sealing the personal ROOT seals the whole subtree, because both visibility
clauses filter ``sealed_at IS NULL`` on their recursive step as well as their
seed. Every Area under the root therefore leaves the closure with it. This
module still stamps every project in the tree rather than the root alone, for
two reasons: an Area can then be unsealed on its own, and a reader querying
``pm_projects`` directly sees the truth without having to walk ancestry.

## ⚠️ The hand-over is not implemented here, because it does not need code

D63's first rule is that tasks in the tree **assigned to somebody else** are
handed over. ``task_visibility_clause`` has two arms — the project-grant
closure, and an ``EXISTS`` over ``pm_task_assignees`` that WS-27j added. The
assignee arm never consults the project.

So sealing removes the grant arm and leaves the assignee arm untouched, and the
colleague who was already working on that task keeps seeing it. The hand-over
is a *consequence* of the seal, not a second operation: no row moves, no
ownership is rewritten, and there is no half-finished state to recover from.

:func:`seal_counts` exists to say this out loud in the dialog, which is D63's
last requirement — the split must be stated in numbers *before* the click.

## What is deliberately left alone

``pm_task_personal`` rows on TEAM tasks. The task belongs to the team and needs
reassigning through the ordinary route. The overlay — disposition, context,
energy, scheduled block — belonged to the departed member and simply stops
being read, because every read of it is keyed on the member's own address.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

#: Every project in one member's personal tree: the root that names them, plus
#: everything beneath it.
#:
#: ⚠️ Seeded on ``lower(personal_owner)``, which is how migration 147's unique
#: index is built (R10 — every read compares folded). Seeding on the raw column
#: would miss ``Priya@…`` for a caller who typed ``priya@…``.
#:
#: The tenant predicate rides on the SEED only. Migration 161's trigger makes a
#: cross-tenant parent impossible, and the recursion walks parent → child
#: inside one tree.
_TREE_SQL = """
WITH RECURSIVE tree AS (
    SELECT p.id
    FROM pm_projects p
    WHERE lower(p.personal_owner) = :who
      AND p.organization_id = CAST(:org AS uuid)
    UNION
    SELECT c.id
    FROM pm_projects c
    JOIN tree t ON c.parent_project_id = t.id
)
SELECT id FROM tree
"""


async def seal_counts(db: Any, email: str, org_id: str) -> dict[str, int]:
    """What sealing this member's tree would do, in numbers.

    D63: *"The deactivation dialog must state the split in numbers before the
    click"* — ``"14 tasks — 3 handed over, 11 sealed, not deleted; later access
    is recorded"``. A policy nobody is told about at the moment it applies is
    one they discover by being surprised.

    ``handed_over`` counts tasks in the tree carrying an assignee who is not the
    owner. Those stay visible to that assignee through the assignee arm, so the
    number is a description of what will remain reachable, not of work this
    function will do.

    ⚠️ Expect ``handed_over`` to be **0** on a healthy tenant, and that is
    correct rather than broken. ``assert_move_keeps_privacy`` and
    ``assert_assignable_here`` both refuse to create that state, so only rows
    predating those two guards can carry it. Measured on production
    2026-09-23: 2 tasks in 1 personal tree, 0 of them cross-assigned.
    """
    params = {"who": email.strip().lower(), "org": org_id}
    row = (await db.execute(
        text(
            f"""
            WITH tree AS ({_TREE_SQL}),
            tasks AS (
                SELECT t.id FROM pm_tasks t
                JOIN tree ON tree.id = t.project_id
            )
            SELECT
                (SELECT count(*) FROM tree)   AS projects,
                (SELECT count(*) FROM tasks)  AS tasks,
                (SELECT count(*) FROM tasks
                  WHERE EXISTS (
                      SELECT 1 FROM pm_task_assignees a
                      WHERE a.task_id = tasks.id
                        AND lower(a.assignee) <> :who
                  ))                          AS handed_over
            """
        ),
        params,
    )).fetchone()

    projects = int(getattr(row, "projects", 0) or 0)
    tasks = int(getattr(row, "tasks", 0) or 0)
    handed = int(getattr(row, "handed_over", 0) or 0)
    return {
        "projects": projects,
        "tasks": tasks,
        "handed_over": handed,
        # Stated rather than left for the caller to subtract, so the dialog and
        # this function cannot disagree about what "sealed" means.
        "sealed": tasks - handed,
    }


async def seal_personal_tree(db: Any, email: str, org_id: str) -> int:
    """Seal one member's personal tree. Returns how many projects it stamped.

    Idempotent: ``sealed_at IS NULL`` in the WHERE means re-running keeps the
    FIRST seal's timestamp. That matters because the timestamp is evidence of
    when the workspace closed, and a retry must not quietly move it.

    Deliberately takes ``org_id`` rather than reading it from the caller. This
    runs inside the deactivation transaction, where the subject is the member
    being deactivated and never the admin performing it.
    """
    result = await db.execute(
        text(
            f"""
            UPDATE pm_projects
               SET sealed_at = now()
             WHERE id IN ({_TREE_SQL})
               AND sealed_at IS NULL
            """
        ),
        {"who": email.strip().lower(), "org": org_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)


async def unseal_personal_tree(db: Any, email: str, org_id: str) -> int:
    """Reopen a sealed tree. Returns how many projects it cleared.

    ⚠️ **This is half of D63's watched door, and only half.** The decision
    requires the open to be *owner-only* and *logged*. This function is the
    mechanism. The route that calls it owes both, and no route calls it yet.

    It exists now because reactivating a member who was deactivated by mistake
    must be possible without a hand-written UPDATE against production, and
    because a seal nobody can lift is the thing that gets resolved by somebody
    sharing a password instead.
    """
    result = await db.execute(
        text(
            f"""
            UPDATE pm_projects
               SET sealed_at = NULL
             WHERE id IN ({_TREE_SQL})
               AND sealed_at IS NOT NULL
            """
        ),
        {"who": email.strip().lower(), "org": org_id},
    )
    return int(getattr(result, "rowcount", 0) or 0)
