"""The Projects operations tasks, PO-1 to PO-11 (WS-46 P3 and later slices).

PO-8 is from P6, PO-9 from P7, PO-10 from P13 and PO-11 from H-273.

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.2. The spec gives
each prompt in a short form. The full prompt below keeps its meaning and names
what the short form leaves to context: PO-6 and PO-7 name the task by its
number, as a member in the app does.

Each task is one session on projects-assistant, as the member
:data:`MEMBER`. ``cards`` is how the member answers each card, in order: the
card responder of :mod:`evals.projects_ops.run` approves or declines. A card
past the end of the list is declined, so an extra card never writes.

PO-3 needs P8 (subprojects in a plan) and P9 (context from email). Until both
ship, it is ``xfail`` with the slice ids, and the runner reports it as such
and does not run it (§11.2).
"""
from __future__ import annotations

from dataclasses import dataclass

from evals.projects_ops.dataset import MEMBER as _MEMBER

#: The acting member of every task.
MEMBER = _MEMBER.email

APPROVE, DECLINE = "APPROVE", "REJECT"


@dataclass(frozen=True)
class TaskSpec:
    id: str
    title: str
    short: str
    prompt: str
    #: The member's answer to each card, in order.
    cards: tuple[str, ...] = ()
    #: Why the task cannot pass yet, with the slice ids. The runner skips it.
    xfail: str | None = None
    #: WS-46 P13: the row ids the member unticks on a card with rows. The
    #: member approves every other row the card ticked.
    untick: tuple[str, ...] = ()


TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        "PO-1", "A weekly task on a named day",
        "Make a recurring weekly task: send the timesheet, every Friday",
        "Make a recurring weekly task in the Launch project: send the timesheet, every Friday.",
        cards=(APPROVE,),
    ),
    TaskSpec(
        "PO-2", "A weekly task with no day",
        "Add a weekly task to review the backlog",
        "Add a weekly task to review the backlog, in the Launch project.",
        cards=(APPROVE,),
    ),
    TaskSpec(
        "PO-3", "A subproject from an email",
        "Create a subproject under Launch with three tasks from Priya's email",
        "Create a subproject under Launch with three tasks from Priya's email about the "
        "launch kits.",
        cards=(APPROVE,),
        xfail="needs P8 (subprojects in a plan) and P9 (context from email)",
    ),
    TaskSpec(
        "PO-4", "A bulk move with approval",
        "Move all overdue tasks to next week, with approval",
        "Move all overdue tasks to next week (Monday), with my approval.",
        cards=(APPROVE,),
    ),
    TaskSpec(
        "PO-5", "A bulk move, declined",
        "PO-4, with the card declined",
        "Move all overdue tasks to next week (Monday), with my approval.",
        cards=(DECLINE,),
    ),
    TaskSpec(
        "PO-6", "A lane that does not exist",
        "Set the status of #12 to Shipped",
        "Set the status of task #12 to Shipped.",
        cards=(APPROVE,),
    ),
    TaskSpec(
        "PO-7", "An invented argument",
        "Make it repeat with a rrule",
        "Make task #7 repeat every Monday. Use the rrule FREQ=WEEKLY;BYDAY=MO.",
        cards=(APPROVE,),
    ),
    # WS-46 P6: the type, the start date and a custom field, in one call.
    TaskSpec(
        "PO-8", "A task with its type, its start and a field",
        "Add a bug to Launch that starts next Monday, for the customer Acme",
        "Add a bug to the Launch project: fix the badge scanner. The work starts next "
        "Monday, and the customer is Acme.",
        cards=(APPROVE,),
    ),
    # WS-46 P7: a saved view with its filters and its grouping, in one call.
    TaskSpec(
        "PO-9", "A saved view with its filters",
        "Save a board of the overdue work in Launch, grouped by owner",
        "Save a board view in the Launch project called Overdue by owner. It shows the "
        "overdue tasks, grouped by assignee.",
        cards=(APPROVE,),
    ),
    # WS-46 P13: several new tasks in a project that exists, as one batch.
    TaskSpec(
        "PO-10", "Three new tasks in one call",
        "Add these 3 tasks to project X",
        "Add these 3 tasks to the Launch project: book the caterer for Priya Menon, print "
        "the badges, and test the projector.",
        cards=(APPROVE,),
    ),
    # H-273: several new tags in a project that exists, as one batch.
    TaskSpec(
        "PO-11", "Three new tags in one call",
        "Register these 3 tags",
        "Register these 3 tags in the Launch project: vendor, print and av.",
        cards=(APPROVE,),
    ),
)

TASK_IDS: tuple[str, ...] = tuple(t.id for t in TASKS)


def by_id(task_id: str) -> TaskSpec:
    return next(t for t in TASKS if t.id == task_id)


def select(spec: str) -> list[TaskSpec]:
    """``all``, or a comma list of ids such as ``PO-1,PO-4``."""
    spec = (spec or "all").strip()
    if spec.lower() == "all":
        return list(TASKS)
    chosen = [p.strip() for p in spec.split(",") if p.strip()]
    unknown = [p for p in chosen if p not in TASK_IDS]
    if unknown:
        raise ValueError(f"unknown task {unknown[0]!r}. The tasks are {TASK_IDS}")
    return [by_id(t) for t in dict.fromkeys(chosen)]
