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
            # The ref is built from the names in the FILE, and the shown name
            # from the admin's renames (I-8). A re-run with another rename
            # then reuses the same folder instead of making a second tree.
            if parent is not None and parent.kind == "folder":
                space = by_ref.get(parent.parent_ref or "")
                key = f"{_file_name(space)} / {_file_name(parent)}" if space else _file_name(parent)
                label = f"{space.name} / {parent.name}" if space else parent.name
            else:
                key = _file_name(parent) if parent is not None else "Imported"
                label = parent.name if parent is not None else "Imported"
            folder = folders.get(key)
            if folder is None:
                folder = NodeSpec(
                    ref=f"folder:{key}", kind="folder", name=label[:200], parent_ref=root.ref
                )
                folders[key] = folder
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


def _file_name(container: Any) -> str:
    """A container's name in the file, before any rename of the admin's."""
    return container.source_name or container.name


def project_statuses(
    bundle: ImportBundle, final: dict[str, tuple[str, Category]]
) -> tuple[dict[str, list[tuple[str, Category]]], set[str]]:
    """Each project's status set (§6.3), and the projects that got a Done
    added (D79). A set, not a count: the writer reports only the projects it
    creates, because a reused one already holds its Done.

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

    added: set[str] = set()
    out: dict[str, list[tuple[str, Category]]] = {}
    for ref, names in used.items():
        ordered = sorted(names, key=lambda nc: STAGE_ORDER[nc[1]])
        if not ordered:
            ordered = [("To do", "todo")]
        if not any(c == "done" for _, c in ordered):
            name = "Done" if "done" not in {n.lower() for n, _ in ordered} else "Done (imported)"
            ordered.append((name, "done"))
            added.add(ref)
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
    Never the import time, which would put every closed task into today.
    The one exception is a task that carries no date at all, which a ClickUp
    export never holds: it takes ``now``, and ``origin.completed_at_estimated``
    still marks it."""
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
    if task.extra_columns:
        # I-8: the unknown columns the admin chose to keep. `plan.choose` has
        # already dropped the ones left out, so every entry here is wanted.
        lines = [f"**More from {label}**"] + [
            f"- {col}: {value}" for col, value in task.extra_columns.items()
        ]
        parts.append("\n".join(lines))
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


# ── update mode (I-3b): a new export of the same workspace ──────────────────
#
# Owner decision, 2026-09-28 (§11 Q-5): a re-import UPDATES the tasks it wrote.
# The rule is three-way, per field. The import stores what it last wrote in
# `origin.import_values`. A field changed in the source updates in Metorite
# only while Metorite still holds what the import last wrote. A member's edit
# is never overwritten: the field is kept and counted as a conflict.

#: The fields an update may change. The parent and the project are structure,
#: and an update never moves a task.
UPDATABLE = (
    "title",
    "description",
    "status_id",
    "due_at",
    "start_date",
    "importance",
    "estimate_mins",
    "tags",
    "assignees",
)


def snapshot_value(value: Any) -> Any:
    """One field in the form the snapshot stores and compares: JSON-safe and
    order-free. An instant is compared in UTC to the second."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=dt.UTC)
        return moment.astimezone(dt.UTC).replace(microsecond=0).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, list | tuple | set):
        return sorted({str(v).strip().lower() for v in value if str(v).strip()})
    if isinstance(value, str):
        return value.strip()
    return value


def snapshot(values: dict[str, Any]) -> dict[str, Any]:
    return {field: snapshot_value(values.get(field)) for field in UPDATABLE}


def source_values(task: Task, source: str, offset: dt.timedelta | None) -> dict[str, Any]:
    """What the SOURCE says, before any mapping: the fields of :data:`UPDATABLE`
    keyed the same way, with ClickUp's own values. The status is its NAME, the
    assignees are the source's person refs, and the description has no
    "Assigned in ClickUp to" footer, because that footer depends on the
    mapping and not on the source."""
    return {
        "title": task.title[:500],
        "description": description(task, [], source),
        "status_id": task.status_name,
        "due_at": due_instant(task, offset),
        "start_date": task.start_date,
        "importance": task.importance,
        "estimate_mins": task.estimate_mins,
        "tags": list(task.tags),
        "assignees": list(task.assignee_refs),
    }


@dataclass(frozen=True)
class Merge:
    """What one existing task takes from a new export."""

    #: field → the new value to write (the raw, un-snapshotted value).
    changes: dict[str, Any]
    #: fields a member edited in Metorite while the source changed them too.
    conflicts: tuple[str, ...]
    #: what to store as `origin.import_values`: what Metorite holds FROM the
    #: import after this run.
    new_snapshot: dict[str, Any]
    #: what to store as `origin.import_source`: the source's own values.
    new_source: dict[str, Any]


def merge_fields(
    current: dict[str, Any],
    last_written: dict[str, Any] | None,
    incoming: dict[str, Any],
    *,
    last_source: dict[str, Any] | None = None,
    incoming_source: dict[str, Any] | None = None,
    frozen: tuple[str, ...] = (),
) -> Merge:
    """The three-way rule, field by field.

    Two questions, answered on two different snapshots:

    1. **Did the SOURCE change the field?** Compared on the source's own
       values (``last_source`` against ``incoming_source``). A change of the
       admin's MAPPING is not a change of the source, so a different person
       or status mapping on a later run moves nothing by itself.
    2. **Did a MEMBER change it?** Compared on what Metorite holds
       (``current`` against ``last_written``, what the import last wrote).

    * The source did not change it → nothing to do.
    * The source changed it, and no member did → take the source's value.
    * Both changed it → keep the member's value, and count a conflict.

    A task with no source snapshot falls back to comparing mapped values.
    With no snapshot at all, every difference is a conflict: nothing proves
    Metorite was not edited. ``frozen`` fields never change, for example the
    status and assignees of a task a member moved to another project."""
    now_cur = snapshot(current)
    now_new = snapshot(incoming)
    src_new = snapshot(incoming_source) if incoming_source is not None else None
    changes: dict[str, Any] = {}
    conflicts: list[str] = []
    kept: dict[str, Any] = {}
    for field in UPDATABLE:
        cur, new = now_cur[field], now_new[field]
        wrote = (
            last_written.get(field) if last_written is not None and field in last_written else None
        )
        known = last_written is not None and field in last_written
        if field in frozen:
            kept[field] = wrote if known else cur
            continue
        source_unchanged = (
            src_new is not None
            and last_source is not None
            and field in last_source
            and src_new[field] == last_source[field]
        )
        if source_unchanged:
            if known and cur == wrote and new != wrote and _fills_a_gap(field, wrote, new):
                # The source is the same, but the mapping now resolves what it
                # could not before: a person added to People after the first
                # import. No member touched the field, so the import fills
                # what it left empty. It never takes anybody off (4c.3).
                changes[field] = incoming.get(field)
                continue
            kept[field] = wrote if known else cur
            continue
        if cur == new:
            continue
        if known and wrote == new:
            # No source snapshot, but the source still says what the import
            # last wrote: the difference is a member's edit. Keep it quietly.
            kept[field] = wrote
            continue
        if known and wrote == cur:
            changes[field] = incoming.get(field)
        else:
            conflicts.append(field)
            kept[field] = wrote if known else cur
    new_snapshot = {field: kept.get(field, now_new[field]) for field in UPDATABLE}
    new_source = dict(src_new) if src_new is not None else dict(last_source or {})
    for field in frozen:
        # A frozen field did not take the source's value. Keep the LAST source
        # value, so the change still counts once the task is unfrozen.
        if last_source is not None and field in last_source:
            new_source[field] = last_source[field]
    return Merge(
        changes=changes,
        conflicts=tuple(conflicts),
        new_snapshot=new_snapshot,
        new_source=new_source,
    )


def _fills_a_gap(field: str, wrote: Any, new: Any) -> bool:
    """May a mapping change alone rewrite this field, when no member edited it?

    * **assignees** — only by ADDING people. The first run left a task
      unassigned because its ClickUp person had no member. Once that person
      is in People, the next run of the same export assigns the task. A
      mapping that would take somebody OFF moves nothing, as before.
    * **description** — its "Assigned in ClickUp to" line lists the people
      with no member, so it changes with the same fill. A member's own edit
      to the description is never overwritten: the caller checks that the
      field still holds what the import last wrote.

    Every other field keeps the rule that a mapping change moves nothing."""
    if field == "assignees":
        return set(wrote or []) <= set(new or [])
    return field == "description"


def comment_key(comment: Comment) -> str:
    """A stable key for one source comment, so a re-import adds a comment once."""
    import hashlib

    stamp = comment.created_at.isoformat() if comment.created_at else ""
    raw = f"{comment.author_ref or ''}|{stamp}|{comment.body_md.strip()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
