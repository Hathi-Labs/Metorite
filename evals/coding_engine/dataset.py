"""The synthetic Projects dataset of the light eval (WS-43v).

Spec: ``project-docs/specs/maf_coding_engine.md`` §16 and the WS-43v slice.

``fixtures/projects_dataset.json`` holds dates relative to the day of the
run. :func:`load` turns them into rows for one day, so "last month" and
"overdue" stay true on any day. Every expected value that a checker needs
comes from these rows (:func:`open_per_assignee`, :func:`median_lead_days`
and the rest), so no expected number is copied into the spec or a test.

Each timestamp is 10:00 UTC. A tool prints a date as ``YYYY-MM-DD``, so a
lead time in whole days is the same from the tool output as from the rows.
"""
from __future__ import annotations

import json
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
DATASET_FILE = FIXTURES / "projects_dataset.json"
UPLOAD_FILE = FIXTURES / "parts_upload.csv"

#: The namespace of every id in the dataset, so an id is the same on each run.
_ID_NS = uuid.UUID("0e43a5e1-7e57-4c0d-9e00-000000000043")

#: The status categories that close a task.
CLOSED = frozenset({"done", "cancelled"})

#: The status name that the app prints for each category.
STATUS_NAMES = {"todo": "To do", "in_progress": "In progress", "done": "Done"}


def stable_id(*parts: str) -> str:
    """A UUID that depends only on *parts*."""
    return str(uuid.uuid5(_ID_NS, "/".join(parts)))


@dataclass(frozen=True)
class Member:
    email: str
    name: str
    hr_read: bool

    @property
    def first_name(self) -> str:
        return self.name.split()[0]

    @property
    def local_part(self) -> str:
        return self.email.split("@", 1)[0]


@dataclass(frozen=True)
class Project:
    id: str
    name: str
    space: str
    space_id: str


@dataclass(frozen=True)
class Task:
    id: str
    number: int
    project: str
    title: str
    status_category: str
    assignees: tuple[str, ...]
    created_at: datetime
    due: date | None
    completed_at: datetime | None
    estimate_mins: int | None
    cycle_hours: float | None

    @property
    def is_open(self) -> bool:
        return self.status_category not in CLOSED

    @property
    def status(self) -> str:
        return STATUS_NAMES.get(self.status_category, self.status_category)

    def lead_days(self) -> float | None:
        """Days from created to done, or ``None`` for an open task."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.created_at).total_seconds() / 86400.0


@dataclass(frozen=True)
class Dataset:
    """The rows of the fixture, for one day."""

    today: date
    organization_id: str
    members: tuple[Member, ...]
    projects: tuple[Project, ...]
    tasks: tuple[Task, ...]
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    def member(self, email: str) -> Member | None:
        key = email.strip().lower()
        return next((m for m in self.members if m.email == key), None)

    def project(self, name: str) -> Project:
        return next(p for p in self.projects if p.name == name)

    def project_by_id(self, project_id: str) -> Project | None:
        return next((p for p in self.projects if p.id == project_id), None)

    def tasks_in(self, project: str) -> list[Task]:
        return [t for t in self.tasks if t.project == project]

    def people_of(self, project: str) -> list[Member]:
        """The members assigned to a task in *project*, in name order."""
        emails = {a for t in self.tasks_in(project) for a in t.assignees}
        return sorted((m for m in self.members if m.email in emails), key=lambda m: m.name)


# ── load ────────────────────────────────────────────────────────────────────


def _at_ten(day: date) -> datetime:
    return datetime.combine(day, time(10, 0), tzinfo=UTC)


def _month_start(today: date, months_ago: int) -> date:
    year, month = today.year, today.month - months_ago
    while month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)


def previous_month(today: date) -> tuple[date, date]:
    """The first and the last day of the calendar month before *today*'s."""
    first_this = today.replace(day=1)
    last_prev = first_this - timedelta(days=1)
    return last_prev.replace(day=1), last_prev


def _relative_day(spec: dict[str, Any] | None, today: date) -> date | None:
    if not spec:
        return None
    if "months_ago" in spec:
        return _month_start(today, int(spec["months_ago"])) + timedelta(days=int(spec["day"]) - 1)
    return today + timedelta(days=int(spec["days"]))


def _task(row: dict[str, Any], today: date) -> Task:
    completed_day = _relative_day(row.get("completed"), today)
    completed_at = _at_ten(completed_day) if completed_day else None
    if completed_at is not None and row.get("lead_days") is not None:
        created_at = completed_at - timedelta(days=int(row["lead_days"]))
    else:
        created_day = _relative_day(row.get("created"), today) or today
        created_at = _at_ten(created_day)
    return Task(
        id=stable_id("task", str(row["number"])),
        number=int(row["number"]),
        project=str(row["project"]),
        title=str(row["title"]),
        status_category=str(row["status"]),
        assignees=tuple(str(a).lower() for a in row.get("assignees") or ()),
        created_at=created_at,
        due=_relative_day(row.get("due"), today),
        completed_at=completed_at,
        estimate_mins=row.get("estimate_mins"),
        cycle_hours=row.get("cycle_hours"),
    )


def load(today: date | None = None, path: Path = DATASET_FILE) -> Dataset:
    """The dataset for *today* (UTC today when ``None``)."""
    day = today or datetime.now(UTC).date()
    raw = json.loads(path.read_text(encoding="utf-8"))
    members = tuple(
        Member(email=m["email"].lower(), name=m["name"], hr_read=bool(m.get("hr_read")))
        for m in raw["members"]
    )
    projects: list[Project] = []
    for space in raw["spaces"]:
        space_id = stable_id("space", space["name"])
        projects.extend(
            Project(id=stable_id("project", name), name=name, space=space["name"], space_id=space_id)
            for name in space["projects"]
        )
    tasks = tuple(_task(row, day) for row in raw["tasks"])
    return Dataset(
        today=day,
        organization_id=str(raw["organization_id"]),
        members=members,
        projects=tuple(projects),
        tasks=tasks,
        raw=raw,
    )


# ── the expected values ─────────────────────────────────────────────────────


def open_per_assignee(ds: Dataset, project: str = "Alpha") -> dict[str, int]:
    """``email → open task count`` in *project*. A task with two people counts twice."""
    counts: dict[str, int] = {}
    for task in ds.tasks_in(project):
        if task.is_open:
            for email in task.assignees:
                counts[email] = counts.get(email, 0) + 1
    return counts


def closed_last_month(ds: Dataset, project: str = "Alpha") -> list[Task]:
    """The tasks of *project* closed in the calendar month before today's."""
    first, last = previous_month(ds.today)
    return [
        t for t in ds.tasks_in(project)
        if t.completed_at is not None and first <= t.completed_at.date() <= last
    ]


def median_lead_days(ds: Dataset, project: str = "Alpha") -> float:
    """The median of the days from created to done, over :func:`closed_last_month`."""
    leads = [t.lead_days() for t in closed_last_month(ds, project)]
    return float(statistics.median([v for v in leads if v is not None]))


def overdue(ds: Dataset, project: str = "Alpha") -> list[Task]:
    """The open tasks of *project* whose due day is before today."""
    return sorted(
        (t for t in ds.tasks_in(project) if t.is_open and t.due is not None and t.due < ds.today),
        key=lambda t: t.number,
    )


def member_markers(ds: Dataset) -> list[str]:
    """Strings that mark member data: every task title, email and full name."""
    marks = [t.title for t in ds.tasks]
    marks += [m.email for m in ds.members]
    marks += [m.name for m in ds.members]
    return marks
