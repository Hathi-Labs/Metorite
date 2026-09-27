"""What the writer will create, decided before it touches the database.

Spec: ``project_import.md`` §5.3 (where the rows land), §6.3 (statuses),
§6.6 (dates), §6.7 (comments and checklists), §6.8 (attachments) · D80 · I-3.

Pure. The writer (``routes/projects/import_writer.py``) asks this module what
to create and in what order, and only then writes. Each decision here has a
unit test that needs no database; the writer's SQL has the live test.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

from gateway.routes.projects.importer.bundle import Comment, ImportBundle, Task
from gateway.routes.projects.importer.plan import CLOSED, Category, Target

NodeKind = Literal["space", "folder", "project"]

#: Stage order inside one status set (§6.3), and the seed palette of
#: ``tree._SEED_STATUSES`` for the same stages.
STAGE_ORDER: dict[str, int] = {"backlog": 0, "todo": 1, "in_progress": 2, "done": 3, "cancelled": 4}
STAGE_COLOR: dict[str, str] = {
    "backlog": "gray",
    "todo": "gray",
    "in_progress": "blue",
    "done": "green",
    "cancelled": "gray",
}
#: The label a source tool gets in the text the writer adds.
SOURCE_LABEL: dict[str, str] = {"clickup": "ClickUp"}


@dataclass(frozen=True)
class NodeSpec:
    """One ``pm_projects`` row to create, or the existing space to write into."""

    ref: str
    kind: NodeKind
    name: str
    parent_ref: str | None = None
    #: Set only for the existing target space: nothing is created for it.
    existing_id: str | None = None


def build_nodes(bundle: ImportBundle, target: Target) -> tuple[list[NodeSpec], dict[str, str]]:
    """The node tree, parents first, and ``source container ref → node ref``
    for every project a task can land in.

    **New space** (the default): each source Space becomes a space, a Folder a
    folder, a List a project. The tree grammar fits exactly (§5.3). The
    admin's name replaces the Space's name only when the file holds one Space.

    **Existing space**: the grammar allows ONE folder between a space and a
    project. So a source Space and its Folder flatten into one folder named
    "Space / Folder", and a List with no Folder lands in a folder named after
    its Space. Nothing is created for the target itself.
    """
    by_ref = {c.ref: c for c in bundle.containers}
    spaces = [c for c in bundle.containers if c.kind == "space"]
    nodes: list[NodeSpec] = []
    home: dict[str, str] = {}

    if target.kind == "existing":
        root = NodeSpec(ref="target", kind="space", name="", existing_id=target.project_id)
        nodes.append(root)
        folders: dict[str, NodeSpec] = {}
        for project in (c for c in bundle.containers if c.kind == "project"):
            parent = by_ref.get(project.parent_ref or "")
            if parent is not None and parent.kind == "folder":
                space = by_ref.get(parent.parent_ref or "")
                label = f"{space.name} / {parent.name}" if space else parent.name
            else:
                label = parent.name if parent is not None else "Imported"
            folder = folders.get(label)
            if folder is None:
                folder = NodeSpec(
                    ref=f"folder:{label}", kind="folder", name=label[:200], parent_ref=root.ref
                )
                folders[label] = folder
                nodes.append(folder)
            node = NodeSpec(
                ref=project.ref, kind="project", name=project.name, parent_ref=folder.ref
            )
            nodes.append(node)
            home[project.ref] = node.ref
        return nodes, home

    rename = (target.name or "").strip() if len(spaces) == 1 else ""
    for space in spaces:
        nodes.append(NodeSpec(ref=space.ref, kind="space", name=rename or space.name))
    for source_folder in (c for c in bundle.containers if c.kind == "folder"):
        nodes.append(
            NodeSpec(
                ref=source_folder.ref,
                kind="folder",
                name=source_folder.name,
                parent_ref=source_folder.parent_ref,
            )
        )
    for project in (c for c in bundle.containers if c.kind == "project"):
        nodes.append(
            NodeSpec(
                ref=project.ref, kind="project", name=project.name, parent_ref=project.parent_ref
            )
        )
        home[project.ref] = project.ref
    return nodes, home


def project_statuses(
    bundle: ImportBundle, final: dict[str, tuple[str, Category]]
) -> tuple[dict[str, list[tuple[str, Category]]], int]:
    """Each project's status set (§6.3), and how many got a Done added (D79).

    The set is the Metorite names the project's tasks use, one per name
    case-blind, ordered by stage and then by first sight. A set with no Done
    stage gets "Done". A project with no status at all gets "To do" and "Done",
    so it can take a task."""
    used: dict[str, list[tuple[str, Category]]] = {}
    for task in bundle.tasks:
        if task.status_name is None:
            used.setdefault(task.container_ref, [])
            continue
        name, category = final[task.status_name]
        names = used.setdefault(task.container_ref, [])
        if name.lower() not in {n.lower() for n, _ in names}:
            names.append((name, category))
    for project in (c for c in bundle.containers if c.kind == "project"):
        used.setdefault(project.ref, [])

    added = 0
    out: dict[str, list[tuple[str, Category]]] = {}
    for ref, names in used.items():
        ordered = sorted(names, key=lambda nc: STAGE_ORDER[nc[1]])
        if not ordered:
            ordered = [("To do", "todo")]
        if not any(c == "done" for _, c in ordered):
            name = "Done" if "done" not in {n.lower() for n, _ in ordered} else "Done (imported)"
            ordered.append((name, "done"))
            added += 1 if names else 0
        out[ref] = ordered
    return out, added


def order_tasks(bundle: ImportBundle) -> list[Task]:
    """Parents before children, so a child's parent always exists when the
    child is written. Stable inside one depth: the file's own order."""
    by_ref = {t.ref: t for t in bundle.tasks}
    depth: dict[str, int] = {}

    def level(task: Task) -> int:
        chain = []
        node: Task | None = task
        while node is not None and node.ref not in depth:
            chain.append(node)
            node = by_ref.get(node.parent_ref) if node.parent_ref else None
        base = depth[node.ref] if node is not None else -1
        for item in reversed(chain):
            base += 1
            depth[item.ref] = base
        return depth[task.ref]

    return sorted(bundle.tasks, key=level)


def due_instant(task: Task, offset: dt.timedelta | None) -> dt.datetime | None:
    """A due date with no time lands at LOCAL NOON, the same instant the app
    writes (``quickAdd.ts`` ``dueInstantForDay``). Noon keeps the calendar day
    for any viewer within twelve hours of the exporter."""
    if task.due_at is not None:
        return task.due_at
    if task.due_date is None:
        return None
    zone = dt.timezone(offset) if offset is not None else dt.UTC
    return dt.datetime.combine(task.due_date, dt.time(12, 0), tzinfo=zone).astimezone(dt.UTC)


def completed_estimate(
    task: Task,
    comments: list[Comment],
    offset: dt.timedelta | None,
    now: dt.datetime,
) -> dt.datetime:
    """§6.6 — the file holds no completion date, so estimate one: the latest
    date the task carries. A past due date, the last comment, or the creation.
    Never the import time, which would put every closed task into today."""
    candidates = [c.created_at for c in comments if c.created_at is not None]
    due = due_instant(task, offset)
    if due is not None and due <= now:
        candidates.append(due)
    if task.created_at is not None:
        candidates.append(task.created_at)
    return max(candidates) if candidates else now


def description(task: Task, unassigned: list[str], source: str) -> str | None:
    """The task's own text, then what has no field in Metorite (§6.2, §6.7,
    §6.8). A checklist has no tick boxes: the file does not say which items
    are done, and an empty box on a closed task would say something false."""
    label = SOURCE_LABEL.get(source, source)
    parts = [task.description_md.strip()] if task.description_md else []
    for checklist in task.checklists:
        lines = [f"**{checklist.name}**"] + [f"- {item}" for item in checklist.items]
        parts.append("\n".join(lines))
    if task.attachment_names:
        parts.append(f"Attachments in {label}: " + ", ".join(task.attachment_names))
    if unassigned:
        parts.append(f"Assigned in {label} to: " + ", ".join(unassigned))
    return "\n\n".join(parts) or None


def origin(task: Task, source: str, run_id: str, **extra: Any) -> dict[str, Any]:
    """``pm_tasks.origin`` (§6.1). ``kind``, ``source`` and ``external_id``
    are the unique index's key, so they are always set and never NULL."""
    out: dict[str, Any] = {
        "kind": "import",
        "source": source,
        "external_id": task.ref,
        "run_id": run_id,
    }
    if task.custom_id:
        out["custom_id"] = task.custom_id
    if task.url:
        out["url"] = task.url
    if task.time_spent_mins is not None:
        out["time_spent_mins"] = task.time_spent_mins
    out.update({k: v for k, v in extra.items() if v not in (None, [], {})})
    return out


def is_closed(task: Task, final: dict[str, tuple[str, Category]]) -> bool:
    return task.status_name is not None and final[task.status_name][1] in CLOSED
