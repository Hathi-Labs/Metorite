"""``ImportBundle`` — the one canonical model every adapter returns.

Spec: ``project_import.md`` §5.2.

Rule: a value the file does not carry is ``None``, never a guess. A field the
source tool never exports is a :class:`Loss` row, so the dry run can say what
the admin will not get. A defect in one file (a duplicate row, a missing
parent) is a :class:`BundleWarning`.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

ContainerKind = Literal["space", "folder", "project"]


class Container(BaseModel):
    """A space, a folder or a project. ``ref`` is the source id, or a name
    path when the file gives the container no id."""

    ref: str
    kind: ContainerKind
    name: str
    parent_ref: str | None = None
    source_id: str | None = None


class Person(BaseModel):
    """Somebody the file names. ``ref`` is the lowercased email when the file
    gives one, else ``name:<lowercased name>``."""

    ref: str
    display_name: str
    email: str | None = None


class StatusSeen(BaseModel):
    """One status name, as one container spells it, with the tasks in it."""

    container_ref: str
    name: str
    task_count: int = 0
    done_hint: bool | None = None


class Checklist(BaseModel):
    name: str
    items: list[str] = Field(default_factory=list)


class Task(BaseModel):
    ref: str
    container_ref: str
    parent_ref: str | None = None
    title: str
    description_md: str | None = None
    status_name: str | None = None
    #: The source tool's own type name, for example ClickUp's ``Task``. §6.5
    #: maps it by name onto ``pm_task_types``.
    task_type: str | None = None
    #: 0-3 on Metorite's own scale (D78), mapped by the adapter from the
    #: source tool's meaning. ``None`` = the file set no priority.
    importance: int | None = None
    assignee_refs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: dt.datetime | None = None
    start_date: dt.date | None = None
    #: A due date with a real time. Exactly one of ``due_at`` and
    #: ``due_date`` is set when the task has a due date.
    due_at: dt.datetime | None = None
    #: A due date with no time (§4.3 item 1).
    due_date: dt.date | None = None
    completed_at: dt.datetime | None = None
    estimate_mins: int | None = None
    time_spent_mins: int | None = None
    custom_values: dict[str, str] = Field(default_factory=dict)
    #: I-8 — the values of the columns this importer does not read, by column
    #: name, non-empty cells only. The admin keeps each column in the task's
    #: description or leaves it out (`plan.choose`). Never a custom field: I-5
    #: owns those.
    extra_columns: dict[str, str] = Field(default_factory=dict)
    checklists: list[Checklist] = Field(default_factory=list)
    attachment_names: list[str] = Field(default_factory=list)
    blocks_refs: list[str] = Field(default_factory=list)
    custom_id: str | None = None
    url: str | None = None


class Comment(BaseModel):
    task_ref: str
    author_ref: str | None = None
    created_at: dt.datetime | None = None
    body_md: str


class Loss(BaseModel):
    """A whole field the source file never carries."""

    what: str
    why: str
    count: int | None = None


class BundleWarning(BaseModel):
    """A defect in this one file, with how many tasks it touches."""

    code: str
    message: str
    count: int
    sample_refs: list[str] = Field(default_factory=list)


class ImportBundle(BaseModel):
    source: str
    #: The exporter's UTC offset, when the file names one.
    utc_offset: dt.timedelta | None = None
    encoding: str = "utf-8"
    rows_read: int = 0
    containers: list[Container] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    statuses: list[StatusSeen] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    comments: list[Comment] = Field(default_factory=list)
    losses: list[Loss] = Field(default_factory=list)
    warnings: list[BundleWarning] = Field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """The counts the dry run shows before any write (§3.1)."""
        kinds = Counter(c.kind for c in self.containers)
        return {
            "source": self.source,
            "rows_read": self.rows_read,
            "tasks": len(self.tasks),
            "subtasks": sum(1 for t in self.tasks if t.parent_ref),
            "spaces": kinds.get("space", 0),
            "folders": kinds.get("folder", 0),
            "projects": kinds.get("project", 0),
            "people": len(self.people),
            "comments": len(self.comments),
            "statuses": len(self.statuses),
            "warnings": {w.code: w.count for w in self.warnings},
            "losses": [loss.what for loss in self.losses],
        }
