"""The synthetic Projects dataset of the operations eval (WS-46 P3).

Spec: ``project-docs/specs/projects_agent_parity.md`` §11.

One organization, one space, two projects with their own lanes, and twelve
tasks. Every date is relative to *today*, so a sequence and a checker agree
on any day. No row is real data: the names and the addresses are made up,
and every address ends in ``.example``.

The facts that the tasks lean on:

* **Launch** has the lanes To do, In progress, In review and Done. No lane
  is called Shipped (PO-6).
* Four tasks are overdue: #2, #3 and #5 in Launch, and #8 in Ops. A task
  that is done is never overdue, and a task due today is not overdue
  (PO-4 and PO-5).
* #7 in Ops has no repeat rule yet (PO-7). #12 is in Launch (PO-6).

Today is the UTC date, because the tools take today as the UTC date
(``skill_projects.writes._today`` and ``reads.legend``).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

#: A fixed namespace, so every id is the same on every run.
_NS = uuid.UUID("5d1c0b9e-46a3-4f0e-9b7c-2a1e6f3d8c40")

ORGANIZATION_ID = "7a0e2f4c-5b1d-4c3e-9f21-0d6b8a4e3c10"

#: The days of the week, Monday first, as the tools name them.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

#: Lane categories that close a task.
CLOSED = frozenset({"done", "cancelled"})


def ident(*parts: str) -> str:
    return str(uuid.uuid5(_NS, "/".join(parts)))


@dataclass(frozen=True)
class Member:
    email: str
    name: str


@dataclass(frozen=True)
class Lane:
    id: str
    name: str
    category: str


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    lanes: tuple[Lane, ...]

    def lane(self, name: str) -> Lane:
        return next(lane for lane in self.lanes if lane.name == name)


@dataclass(frozen=True)
class Task:
    number: int
    title: str
    project: str
    lane: str
    due: date | None
    assignees: tuple[str, ...] = ()

    @property
    def id(self) -> str:
        return ident("task", str(self.number))


@dataclass(frozen=True)
class Dataset:
    today: date
    organization_id: str
    space_id: str
    space: str
    members: tuple[Member, ...]
    projects: tuple[Project, ...]
    tasks: tuple[Task, ...]

    def member(self, email: str) -> Member | None:
        wanted = (email or "").strip().lower()
        return next((m for m in self.members if m.email == wanted), None)

    def project(self, name: str) -> Project:
        return next(p for p in self.projects if p.name == name)

    def task(self, number: int) -> Task:
        return next(t for t in self.tasks if t.number == number)

    def category(self, task: Task) -> str:
        return self.project(task.project).lane(task.lane).category

    def is_overdue(self, task: Task) -> bool:
        return (
            self.category(task) not in CLOSED and task.due is not None and task.due < self.today
        )


MEMBER = Member("asha.iyer@eval.example", "Asha Iyer")
#: A second member. PO-3's email comes from her.
PRIYA = Member("priya.menon@eval.example", "Priya Menon")


def _lanes(project: str, *rows: tuple[str, str]) -> tuple[Lane, ...]:
    return tuple(Lane(ident("lane", project, name), name, category) for name, category in rows)


def load(today: date | None = None) -> Dataset:
    """The dataset for *today* (default: the UTC date now)."""
    day = today or datetime.now(UTC).date()

    def due(offset: int | None) -> date | None:
        return None if offset is None else day + timedelta(days=offset)

    launch = Project(ident("project", "Launch"), "Launch", _lanes(
        "Launch", ("To do", "todo"), ("In progress", "in_progress"),
        ("In review", "in_progress"), ("Done", "done"),
    ))
    ops = Project(ident("project", "Ops"), "Ops", _lanes(
        "Ops", ("Backlog", "todo"), ("Doing", "in_progress"), ("Done", "done"),
    ))
    me, priya = MEMBER.email, PRIYA.email
    rows = (
        (1, "Draft the launch plan", "Launch", "Done", -10, (me,)),
        (2, "Book the venue", "Launch", "To do", -3, (priya,)),
        (3, "Write the press note", "Launch", "In progress", -1, (me,)),
        (4, "Order the banners", "Launch", "To do", 5, (priya,)),
        (5, "Send the invites", "Launch", "In review", -7, (me,)),
        (6, "Plan the demo", "Launch", "To do", None, ()),
        (7, "Clean the demo printer", "Ops", "Doing", 2, (me,)),
        (8, "Renew the domain", "Ops", "Backlog", -2, (me,)),
        (9, "Fix the badge printer", "Ops", "Done", -4, (priya,)),
        (10, "Check the stock", "Ops", "Backlog", 0, ()),
        (11, "Pack the kits", "Launch", "To do", 1, (priya,)),
        (12, "Ship the samples", "Launch", "In progress", 3, (me,)),
    )
    tasks = tuple(Task(n, title, p, lane, due(off), who) for n, title, p, lane, off, who in rows)
    return Dataset(
        today=day, organization_id=ORGANIZATION_ID, space_id=ident("space", "Product"),
        space="Product", members=(MEMBER, PRIYA), projects=(launch, ops), tasks=tasks,
    )


# ── the expected values, from the dataset alone ─────────────────────────────


def overdue(ds: Dataset) -> list[Task]:
    """Every overdue task the member can see, by number."""
    return sorted((t for t in ds.tasks if ds.is_overdue(t)), key=lambda t: t.number)


def next_monday(today: date) -> date:
    """The Monday of next week. On a Monday it is seven days ahead."""
    return today + timedelta(days=7 - today.weekday())


def weekday_name(day: date) -> str:
    return WEEKDAYS[day.isoweekday() - 1]
