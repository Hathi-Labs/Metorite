"""Class C tools — hard to undo. One act, one card, the impact first.

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.3, §5.2 and §10.2.

Three rules bind every tool here, and ``test_projects_agent_writes.py``
holds each one:

1. **One act, one card.** A tool given a list where it takes one id refuses
   before any card (``_many``). A member who wants five projects archived
   answers five cards. ``bulk_update`` and ``merge_tasks`` take a selection
   because the SELECTION is the act the route performs in one transaction;
   both cap it at ``MAX_BATCH`` and name every task on the card.
2. **No pre-approval.** No argument skips the card, and no tool passes
   ``non_interactive_default``. The card is ``writes._confirm``, the one
   door class B uses, so the same source fence covers both files.
3. **The impact comes first.** The card's first line after ``CARD_NOTE`` is
   ``impact: …`` — the counts the route will act on, read BEFORE the write
   wherever a read exists: the summary and the tree for an archive, the
   task list's total for a status, ``/tags/{id}/impact`` for a tag, the
   preview for a status set. Where no read exists (a type's tasks, a field's
   values, a view's positions) the line names the scope and says the receipt
   carries the count the route reports. A card that says "delete X" with no
   size is a signature bought under a misdescription.

What is deliberately NOT here: hard delete of a project or a task (D-PM-35,
class X until WS-40), a bulk ``delete`` action for the same reason, and a
grant write (spec §12, question 2).
"""

from __future__ import annotations

from typing import Any

from skill_projects import client as _client
from skill_projects.client import (
    data,
    delete,
    get,
    post,
    uuid_of,
)
from skill_projects.reads import _day, _task_line
from skill_projects.writes import (
    CANCELLED,
    CARD_NOTE,
    IMPORTANCE,
    MAX_BATCH,
    _confirm,
    _field_of,
    _fields_block,
    _importance,
    _int_or_none,
    _node,
    _one_named,
    _org_wide,
    _ref,
    _resolve_assignee,
    _resolve_status,
    _split,
    _task,
    _vocab,
)

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


CLOSING = ("done", "cancelled")
ONE_ACT = "takes ONE id. A guarded act is one card per row. Ask again for each one."


def _many(value: str) -> bool:
    """A comma or a newline in a one-row argument is a list. A space is
    not: a status is named, and "In progress" is one status."""
    raw = str(value or "").strip()
    return ("," in raw) or ("\n" in raw)


def _guard_card(impact: str, rest: dict[str, Any]) -> str:
    """The class C card body: the note, then ``impact: …``, then the rest."""
    body = _fields_block({"impact": impact, **rest})
    assert body.startswith(CARD_NOTE)
    return body


def _open_count(summary: dict[str, Any]) -> int:
    """Open tasks from a summary's ``by_category``, or its total when the
    breakdown is absent. Never a sum of a page (§9.12.7)."""
    by_cat = summary.get("by_category")
    if isinstance(by_cat, dict) and by_cat:
        return sum(int(v or 0) for k, v in by_cat.items() if k not in CLOSING)
    return int(summary.get("tasks") or 0)


def _find_node(rows: list[dict[str, Any]], pid: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("id")) == pid:
            return row
        found = _find_node(row.get("children") or [], pid)
        if found is not None:
            return found
    return None


def _descendants(node: dict[str, Any] | None, *, archived: bool | None = None) -> int:
    """How many projects sit under a node in the tree read.

    ``archived=None`` counts every descendant, ``False`` the live ones and
    ``True`` the filed ones.
    """
    if node is None:
        return 0
    total = 0
    for child in node.get("children") or []:
        is_archived = bool(child.get("archived_at"))
        if archived is None or is_archived == archived:
            total += 1
        total += _descendants(child, archived=archived)
    return total


async def _tree_node(pid: str) -> dict[str, Any] | None:
    tree = (await get("/projects/tree")) or {}
    return _find_node(tree.get("rows") or [], pid)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ── Projects ─────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=True)
async def archive_project(project_id: str) -> str:
    """File a project and its whole subtree out of the default surfaces.
    ONE project per call. The card leads with how many projects the archive
    stamps and how many open tasks go out of sight with them. Tasks are not
    touched. unarchive_project restores exactly these rows."""
    if _many(project_id):
        return f"archive_project {ONE_ACT}"
    pid, node = await _node(project_id)
    if node.get("archived_at"):
        return f"{data(node.get('name'))} is already archived."
    summary = (await get(f"/projects/nodes/{pid}/summary")) or {}
    below = _descendants(await _tree_node(pid), archived=False)
    open_tasks = _open_count(summary)
    impact = (
        f"{_plural(1 + below, 'project')} archived · {_plural(open_tasks, 'open task')} "
        "leave the boards, lists and searches with them"
    )
    if not await _confirm(
        title="Archive this project?",
        detail=f"{data(node.get('name'))} · {impact}",
        context=_guard_card(
            impact,
            {
                "project": data(node.get("name")),
                "tasks": "not changed; they follow their project out of view",
                "undo": "unarchive_project restores exactly the rows this archive stamps",
            },
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/archive")) or {}
    return (
        f"Archived {data(node.get('name'))}: {_plural(int(result.get('projects') or 0), 'project')} "
        f"filed, {_plural(int(result.get('open_tasks') or 0), 'open task')} out of view."
        f"\n  project_id: {pid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=True)
async def unarchive_project(project_id: str) -> str:
    """Bring an archived project back, with every row its own archive filed.
    ONE project per call. A project filed by an ANCESTOR's archive is refused
    by the route: restore that ancestor instead. The card leads with how many
    archived projects sit in the subtree."""
    if _many(project_id):
        return f"unarchive_project {ONE_ACT}"
    pid, node = await _node(project_id)
    if not node.get("archived_at"):
        return f"{data(node.get('name'))} is not archived."
    filed = _descendants(await _tree_node(pid), archived=True)
    impact = (
        f"up to {_plural(1 + filed, 'project')} restored — this one and the archived rows "
        "under it that its own archive filed"
    )
    if not await _confirm(
        title="Restore this project?",
        detail=f"{data(node.get('name'))} · archived {_day(node.get('archived_at'))}",
        context=_guard_card(
            impact,
            {
                "project": data(node.get("name")),
                "note": "a subproject archived on its own before this stays archived",
            },
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/unarchive")) or {}
    return (
        f"Restored {data(node.get('name'))}: {_plural(int(result.get('projects') or 0), 'project')} "
        f"back on the surface.\n  project_id: {pid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False)
async def move_project(
    project_id: str, parent_project_id: str = "", to_top_level: bool = False
) -> str:
    """Re-parent a project, folder or subproject under another node, or
    make it a space (to_top_level=true). ONE node per call. Every task under
    it is re-rooted, its types and counter follow the new root, and a task
    whose lane the new set lacks is re-pointed by category. The card leads
    with the subtree size and the task count."""
    if _many(project_id):
        return f"move_project {ONE_ACT}"
    if bool(parent_project_id.strip()) == bool(to_top_level):
        return "Pass parent_project_id, or to_top_level=true to make it a space, not both."
    pid, node = await _node(project_id)
    parent: dict[str, Any] | None = None
    parent_id = ""
    if parent_project_id.strip():
        parent_id, parent = await _node(parent_project_id)
        if parent_id == pid:
            return "A project cannot be moved under itself."
        if str(parent.get("id")) == str(node.get("parent_project_id")):
            return f"{data(node.get('name'))} is already under {data(parent.get('name'))}."
    elif node.get("parent_project_id") is None:
        return f"{data(node.get('name'))} is already a space."
    summary = (await get(f"/projects/nodes/{pid}/summary")) or {}
    below = _descendants(await _tree_node(pid))
    tasks = int(summary.get("tasks") or 0)
    where = "the top level (a space)" if parent is None else data(parent.get("name"))
    impact = (
        f"{_plural(1 + below, 'project')} moved · {_plural(tasks, 'task')} re-rooted under {where}"
    )
    if not await _confirm(
        title="Move this project?",
        detail=f"{data(node.get('name'))} → {where} · {impact}",
        context=_guard_card(
            impact,
            {
                "project": data(node.get("name")),
                "to": where,
                "statuses": "a task whose lane the destination set lacks is re-pointed by category",
                "undo": "move it back; the counter and types follow the new root",
            },
        ),
    ):
        return CANCELLED
    payload: dict[str, Any] = {"parent_project_id": parent_id or None}
    result = (await post(f"/projects/nodes/{pid}/move", payload)) or {}
    remapped = (result.get("statuses_remapped") or {}).get("moved")
    tail = f" {remapped} task(s) changed lane." if remapped else ""
    return f"Moved {data(node.get('name'))} under {where}.{tail}\n  project_id: {pid}"


# ── Tasks ────────────────────────────────────────────────────────────────────


async def _status_name(task: dict[str, Any]) -> str:
    rows = await _vocab(str(task.get("project_id")), "statuses")
    for row in rows:
        if str(row.get("id")) == str(task.get("status_id")):
            return data(row.get("name"))
    return "(unknown)"


@_annotate(read_only=False, destructive=True, idempotent=True)
async def archive_task(task_id: str) -> str:
    """Shelve one task from every board, list, calendar and search. ONE
    task per call, from any status; archiving is a filing decision and
    claims no outcome. Subtasks stay where they are. The card carries the
    title, the lane and how many open subtasks it leaves behind.
    unarchive_task is the undo."""
    if _many(task_id):
        return f"archive_task {ONE_ACT}"
    tid, task = await _task(task_id)
    if task.get("archived_at"):
        return f"{_ref(task)} is already archived."
    relations = (await get(f"/projects/tasks/{tid}/relations")) or {}
    progress = relations.get("progress") or {}
    subtasks = relations.get("subtasks") or []
    open_subs = max(0, len(subtasks) - int(progress.get("done") or 0))
    lane = await _status_name(task)
    impact = (
        f"1 task archived from lane {lane} · {_plural(open_subs, 'open subtask')} left on the board"
    )
    if not await _confirm(
        title="Archive this task?",
        detail=f"{_ref(task)} · {impact}",
        context=_guard_card(impact, {"task": _ref(task), "status": lane, "undo": "unarchive_task"}),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{tid}/archive")
    merged = {**task, **(row if isinstance(row, dict) else {})}
    return "\n".join(["Archived:", *_task_line(merged)])


@_annotate(read_only=False, destructive=True, idempotent=False)
async def merge_tasks(target_task_id: str, source_task_ids: str) -> str:
    """Fold one or more tasks into a survivor, in the SAME project. Their
    comments, attachments, links, subtasks and watchers move to the
    survivor; each source is archived and points at it. The card names the
    survivor and every source. Not reversible: the sources become stubs."""
    if _many(target_task_id):
        return f"merge_tasks {ONE_ACT.replace('ONE id', 'ONE survivor')}"
    ids = [uuid_of(t, "source_task_id") for t in _split(source_task_ids)]
    if not ids:
        return "Name at least one source task."
    if len(ids) > MAX_BATCH:
        return f"That is {len(ids)} sources. The limit for one card is {MAX_BATCH}."
    tid, target = await _task(target_task_id)
    if tid in ids:
        return "A task cannot be merged into itself."
    if target.get("merged_into_task_id"):
        return f"{_ref(target)} has itself been merged away. Merge into the task it went to."
    sources = [(await _task(s))[1] for s in ids]
    wrong = [s for s in sources if str(s.get("project_id")) != str(target.get("project_id"))]
    if wrong:
        return (
            "Merge is same-project only. Move first: "
            + ", ".join(_ref(s) for s in wrong)
            + f" are not in the project of {_ref(target)}."
        )
    impact = f"{_plural(len(sources), 'task')} merged into {_ref(target)} and archived as stubs"
    rest: dict[str, Any] = {"survivor": _ref(target)}
    for i, s in enumerate(sources):
        rest[f"source {i + 1}"] = _ref(s)
    rest["moves"] = "comments, attachments, links, subtasks and watchers go to the survivor"
    if not await _confirm(
        title=f"Merge {_plural(len(sources), 'task')} into {_ref(target)}?",
        detail=impact,
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{tid}/merge", {"sources": ids})
    merged = (row or {}).get("merged") or ids
    out = [f"Merged {len(merged)} into {_ref(target)}:", *_task_line({**target, **(row or {})})]
    return "\n".join(out)


BULK_ACTIONS = ("archive", "unarchive")
_CLEARABLE = {"due": "due_at", "start": "start_date", "estimate": "estimate_mins"}


def _bulk_patch(
    status: str, importance: int, due: str, start: str, estimate_mins: int, clear: str
) -> dict[str, Any] | str:
    """The ``patch`` half of a bulk body, or the refusal."""
    patch: dict[str, Any] = {}
    if status.strip():
        patch["status"] = status.strip()
    imp = _importance(importance)
    if imp is not None:
        if imp not in IMPORTANCE:
            return "importance is 0 to 4."
        patch["importance"] = imp
    if due.strip():
        patch["due_at"] = due.strip()
    if start.strip():
        patch["start_date"] = start.strip()
    est = _int_or_none(estimate_mins)
    if est is not None:
        patch["estimate_mins"] = est
    for field in _split(clear):
        key = {**_CLEARABLE, "priority": "importance"}.get(field.lower(), field.lower())
        if key not in ("due_at", "start_date", "estimate_mins", "importance"):
            return f"clear takes due, start, estimate or importance, not {data(field)}."
        patch[key] = None
    return patch


async def _bulk_body(
    ids: list[str],
    patch: dict[str, Any],
    assignees_add: str,
    assignees_remove: str,
    tags_add: str,
    tags_remove: str,
    action: str,
) -> dict[str, Any] | str:
    """The wire body of ``POST /projects/tasks/bulk``, or the refusal."""
    body: dict[str, Any] = {"task_ids": ids}
    if patch:
        body["patch"] = patch
    for key, raw in (("assignees_add", assignees_add), ("assignees_remove", assignees_remove)):
        people = [await _resolve_assignee(a) for a in _split(raw)]
        if people:
            body[key] = people
    for key, raw in (("tags_add", tags_add), ("tags_remove", tags_remove)):
        values = _split(raw)
        if values:
            body[key] = values
    verb = str(action or "").strip().lower()
    if verb == "delete":
        return "Deleting tasks is not offered here (D-PM-35). Archive is the remove verb."
    if verb and verb not in BULK_ACTIONS:
        return f"action is {' or '.join(BULK_ACTIONS)}."
    if verb and len(body) > 1:
        return f"'{verb}' goes on its own. Send it without a patch, assignees or tags."
    if verb:
        body["action"] = verb
    if len(body) == 1:
        return "Nothing to change. Pass a field, assignees, tags or an action."
    return body


def _bulk_impact(body: dict[str, Any], n: int) -> str:
    verb = body.get("action")
    if verb:
        return f"{_plural(n, 'task')} {verb}d"
    described = {**(body.get("patch") or {})}
    for key in ("assignees_add", "assignees_remove", "tags_add", "tags_remove"):
        if key in body:
            described[key] = ", ".join(body[key])
    return f"{_plural(n, 'task')} changed: " + ", ".join(f"{k} → {v}" for k, v in described.items())


@_annotate(read_only=False, destructive=True, idempotent=False)
async def bulk_update(
    task_ids: str,
    status: str = "",
    importance: int = -1,
    due: str = "",
    start: str = "",
    estimate_mins: int = 0,
    clear: str = "",
    assignees_add: str = "",
    assignees_remove: str = "",
    tags_add: str = "",
    tags_remove: str = "",
    action: str = "",
) -> str:
    """One change across a selection of tasks, in one transaction. task_ids
    is comma-separated, at most 50. status is by NAME and is resolved per
    task's project. importance 0 to 4, due and start YYYY-MM-DD, clear
    empties due, start, estimate or importance. assignees_add/remove and
    tags_add/remove take comma-separated values. action is archive or
    unarchive, on its own with no other change. Deleting is not offered.
    The card names every task and the exact change."""
    ids = [uuid_of(t, "task_id") for t in _split(task_ids)]
    if not ids:
        return "Give at least one task id."
    if len(ids) > MAX_BATCH:
        return f"That is {len(ids)} tasks. The limit for one card is {MAX_BATCH}."
    patch = _bulk_patch(status, importance, due, start, estimate_mins, clear)
    if isinstance(patch, str):
        return patch
    body = await _bulk_body(
        ids, patch, assignees_add, assignees_remove, tags_add, tags_remove, action
    )
    if isinstance(body, str):
        return body
    tasks = [(await _task(t))[1] for t in ids]
    impact = _bulk_impact(body, len(tasks))
    rest: dict[str, Any] = {}
    for i, t in enumerate(tasks):
        rest[f"task {i + 1}"] = _ref(t)
    if not await _confirm(
        title=f"Change {_plural(len(tasks), 'task')} at once?",
        detail=impact,
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    result = (await post("/projects/tasks/bulk", body)) or {}
    out = [f"Applied to {result.get('applied', 0)} of {result.get('requested', len(ids))} tasks."]
    for row in result.get("skipped") or []:
        out.append(f"- skipped {row.get('task_id')}: {data(row.get('reason'))}")
    for row in result.get("failed") or []:
        out.append(f"- failed {row.get('task_id')}: {data(row.get('reason'))}")
    for t in tasks:
        out.extend(_task_line(t))
    return "\n".join(out)


# ── The timeline ─────────────────────────────────────────────────────────────


async def _timeline_row(task_id: str, activity_id: str, kind: str) -> dict[str, Any] | None:
    tid = uuid_of(task_id, "task_id")
    aid = uuid_of(activity_id, "activity_id")
    payload = await get(
        f"/projects/tasks/{tid}/timeline", {"kind": kind, "page": 1, "page_size": 50}
    )
    for row in (payload or {}).get("rows") or []:
        if str(row.get("id")) == aid:
            return row
    return None


@_annotate(read_only=False, destructive=True, idempotent=True)
async def delete_comment(task_id: str, comment_id: str) -> str:
    """Delete a comment the member wrote. The words are cleared and the row
    is hidden; replies keep their place. The author only, checked before the
    card. The card shows the exact text that goes."""
    if _many(comment_id):
        return f"delete_comment {ONE_ACT}"
    tid, task = await _task(task_id)
    row = await _timeline_row(tid, comment_id, "comments")
    if row is None:
        return f"No comment with that id is in the latest 50 comments of {_ref(task)}."
    author = str(row.get("created_by") or "").lower()
    # Through the module, not a bound name: the acting member is a run
    # ContextVar read at call time, and a test patches it on the module.
    if author != _client.current_user_email().lower():
        return f"Only the author can delete a comment. This one is by {data(author)}."
    aid = uuid_of(comment_id, "comment_id")
    impact = "1 comment deleted; its text is cleared for everyone"
    if not await _confirm(
        title="Delete your comment?",
        detail=f"on {_ref(task)}",
        context=_guard_card(impact, {"task": _ref(task), "comment": data(row.get("body"))}),
    ):
        return CANCELLED
    await delete(f"/projects/comments/{aid}")
    return f"Deleted your comment on {_ref(task)}.\n  full_id: {tid}"


def _change_lines(changes: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """``(field, "now → restored")`` per change, labels over ids."""
    out: list[tuple[str, str]] = []
    for c in changes:
        field = str(c.get("field") or "?")
        now = c.get("new_label", c.get("new"))
        back = c.get("old_label", c.get("old"))
        out.append((field, f"{data(now)} → {data(back)}"))
    return out


@_annotate(read_only=False, destructive=True, idempotent=False)
async def revert_activity(task_id: str, activity_id: str) -> str:
    """Undo one field change from a task's timeline: the values it recorded
    as "old" are written back. activity_id comes from task_detail's
    timeline. The revert is itself a change, so the timeline shows it
    happened. The card lists each field as now → restored. A move between
    projects or a status change is not revertible here."""
    if _many(activity_id):
        return f"revert_activity {ONE_ACT}"
    tid, task = await _task(task_id)
    row = await _timeline_row(tid, activity_id, "events")
    if row is None or row.get("type") != "field_change":
        return f"No field change with that id is in the latest 50 events of {_ref(task)}."
    changes = [c for c in ((row.get("meta") or {}).get("changes") or []) if isinstance(c, dict)]
    if not changes:
        return "That change carries nothing to restore."
    lines = _change_lines(changes)
    impact = f"{_plural(len(lines), 'field')} restored on {_ref(task)}"
    aid = uuid_of(activity_id, "activity_id")
    if not await _confirm(
        title="Revert this change?",
        detail=f"{_ref(task)} · " + ", ".join(f"{f}: {v}" for f, v in lines),
        context=_guard_card(impact, {"task": _ref(task), **dict(lines)}),
    ):
        return CANCELLED
    result = (await post(f"/projects/activities/{aid}/revert")) or {}
    reverted = ", ".join(result.get("reverted") or []) or "nothing"
    skipped = result.get("skipped") or []
    tail = f" Not revertible here: {', '.join(skipped)}." if skipped else ""
    return f"Reverted {reverted} on {_ref(task)}.{tail}\n  full_id: {tid}"


# ── Vocabulary deletes ───────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=False)
async def delete_status(project_id: str, status: str, move_to: str = "") -> str:
    """Delete a lane, moving the tasks in it to move_to (a status NAME in
    the same set) first. The card leads with how many tasks move. The last
    lane, and the last lane that closes a task, cannot be deleted; the
    route says so."""
    if _many(status):
        return f"delete_status {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = await _resolve_status(pid, status)
    sid = uuid_of(str(row.get("id")), "status_id")
    owner = (await get(f"/projects/nodes/{pid}/status-set")) or {}
    scope = uuid_of(str(owner.get("owner_id") or pid), "owner_id")
    listing = (
        await get(
            "/projects/tasks",
            {
                "project_id": scope,
                "include_subtree": True,
                "include_archived": True,
                "status_id": sid,
                "page": 1,
                "page_size": 1,
            },
        )
    ) or {}
    in_use = int(listing.get("total") or 0)
    target: dict[str, Any] | None = None
    if move_to.strip():
        target = await _resolve_status(pid, move_to)
        if str(target.get("id")) == sid:
            return "A status cannot hand its tasks to itself."
    elif in_use:
        return (
            f"{data(row.get('name'))} still holds {_plural(in_use, 'task')}. "
            "Pass move_to, the status they go to."
        )
    where = data(target.get("name")) if target else "(none needed; the lane is empty)"
    impact = f"1 lane deleted · {_plural(in_use, 'task')} moved to {where}"
    if not await _confirm(
        title="Delete this status?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))} · {impact}",
        context=_guard_card(
            impact,
            {"status": data(row.get("name")), "category": row.get("category"), "move to": where},
        ),
    ):
        return CANCELLED
    params: dict[str, Any] = {}
    if target is not None:
        params["move_to"] = uuid_of(str(target.get("id")), "move_to")
    result = (await delete(f"/projects/statuses/{sid}", params)) or {}
    moved = result.get("moved", in_use)
    return (
        f"Deleted status {data(row.get('name'))}; {_plural(int(moved or 0), 'task')} moved to {where}."
        f"\n  status_id: {sid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False)
async def set_status_set(project_id: str, mode: str, copy_from: str = "") -> str:
    """Switch where a project's lanes come from. mode=inherit drops its own
    set and uses the parent's; mode=own gives it a set of its own, copied
    from copy_from (a project id) or from the set it uses today. Every task
    is re-pointed by category in one transaction. The card leads with the
    preview's counts: tasks that change lane, that become done, that
    reopen."""
    if _many(project_id):
        return f"set_status_set {ONE_ACT}"
    which = str(mode or "").strip().lower()
    if which not in ("inherit", "own"):
        return "mode is inherit or own."
    pid, node = await _node(project_id)
    current = (await get(f"/projects/nodes/{pid}/status-set")) or {}
    if which == "inherit" and not current.get("can_inherit"):
        return f"{data(node.get('name'))} is a space. It has nothing to inherit from."
    if which == "inherit" and not current.get("owns"):
        return f"{data(node.get('name'))} already inherits its statuses."
    payload: dict[str, Any] = {"mode": which}
    source_name = ""
    if copy_from.strip():
        if which != "own":
            return "copy_from goes with mode=own."
        src_id, src = await _node(copy_from)
        payload["copy_from"] = src_id
        source_name = data(src.get("name"))
    preview = (await post(f"/projects/nodes/{pid}/status-set/preview", payload)) or {}
    moving = int(preview.get("moving") or 0)
    completing = int(preview.get("completing") or 0)
    reopening = int(preview.get("reopening") or 0)
    lanes = ", ".join(data(lane.get("name")) for lane in preview.get("lanes") or [])
    impact = (
        f"{_plural(moving, 'task')} change lane · {completing} become done · {reopening} reopen"
    )
    rest: dict[str, Any] = {"project": data(node.get("name")), "mode": which, "lanes after": lanes}
    if source_name:
        rest["copied from"] = source_name
    if which == "inherit":
        rest["inherits from"] = data(current.get("inherit_from_name"))
    if not await _confirm(
        title="Switch this project's status set?",
        detail=f"{data(node.get('name'))} → {which} · {impact}",
        context=_guard_card(impact, rest),
    ):
        return CANCELLED
    result = (await post(f"/projects/nodes/{pid}/status-set", payload)) or {}
    moved = result.get("moved", moving)
    return (
        f"{data(node.get('name'))} now {'inherits its statuses' if which == 'inherit' else 'owns its statuses'}; "
        f"{_plural(int(moved or 0), 'task')} changed lane.\n  project_id: {pid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False)
async def delete_type(project_id: str, type_name: str) -> str:
    """Delete a task type from the project's root. Tasks that carry it keep
    existing, untyped. The route counts them as it deletes and the receipt
    carries the number. The Epic system type and an org-wide type cannot
    be deleted from here."""
    if _many(type_name):
        return f"delete_type {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _one_named(await _vocab(pid, "types"), type_name, "type")
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    if row.get("is_system"):
        return f"{data(row.get('name'))} is a system type and cannot be deleted."
    kid = uuid_of(str(row.get("id")), "type_id")
    impact = (
        f"1 type deleted · every task in {data(node.get('name'))}'s tree that carries it becomes "
        "untyped (the receipt carries the count)"
    )
    if not await _confirm(
        title="Delete this task type?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))}",
        context=_guard_card(impact, {"type": data(row.get("name"))}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/types/{kid}")) or {}
    return (
        f"Deleted type {data(row.get('name'))}; {_plural(int(result.get('tasks_untyped') or 0), 'task')} "
        f"now untyped.\n  type_id: {kid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False)
async def delete_field(project_id: str, field: str) -> str:
    """Delete a custom field AND every value filed under its key, across
    the project's tree. The route counts the values as it clears them and
    the receipt carries the number. An org-wide field is not deleted from
    here."""
    if _many(field):
        return f"delete_field {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _field_of(await _vocab(pid, "fields"), field)
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    fid = uuid_of(str(row.get("id")), "field_id")
    impact = (
        f"1 field deleted · every value under key {data(row.get('field_key'))} in "
        f"{data(node.get('name'))}'s tree is cleared (the receipt carries the count)"
    )
    if not await _confirm(
        title="Delete this custom field and its values?",
        detail=f"{data(row.get('name'))} (key {data(row.get('field_key'))}) in {data(node.get('name'))}",
        context=_guard_card(
            impact, {"field": data(row.get("name")), "type": row.get("field_type")}
        ),
    ):
        return CANCELLED
    result = (await delete(f"/projects/fields/{fid}")) or {}
    cleared = int(((result.get("cascaded") or {}).get("values_cleared")) or 0)
    return (
        f"Deleted field {data(row.get('name'))}; {_plural(cleared, 'value')} cleared."
        f"\n  field_id: {fid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=False)
async def delete_tag(project_id: str, tag: str) -> str:
    """Delete a tag and strip it from every task in the project's tree.
    The card leads with the impact read: how many tasks, in how many
    trees. An org-wide tag is not deleted from here."""
    if _many(tag):
        return f"delete_tag {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    row = _one_named(await _vocab(pid, "tags"), tag, "tag")
    if _org_wide(row):
        return f"{data(row.get('name'))} is organization-wide. It is not deleted from a project."
    gid = uuid_of(str(row.get("id")), "tag_id")
    hit = (await get(f"/projects/tags/{gid}/impact")) or {}
    tasks = int(hit.get("tasks") or 0)
    impact = f"1 tag deleted · {_plural(tasks, 'task')} untagged"
    if not await _confirm(
        title="Delete this tag?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))} · {impact}",
        context=_guard_card(impact, {"tag": data(row.get("name"))}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/tags/{gid}")) or {}
    stripped = int(((result.get("cascaded") or {}).get("tasks_untagged")) or 0)
    return f"Deleted tag {data(row.get('name'))}; {_plural(stripped, 'task')} untagged.\n  tag_id: {gid}"


@_annotate(read_only=False, destructive=True, idempotent=False)
async def merge_tags(project_id: str, tag: str, into: str) -> str:
    """Fold one tag into another and delete the first. Every task wearing
    the first gets the second once. Same project only, root-local only.
    The card leads with how many tasks are retagged."""
    if _many(tag) or _many(into):
        return f"merge_tags {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    rows = await _vocab(pid, "tags")
    source = _one_named(rows, tag, "tag")
    target = _one_named(rows, into, "tag")
    if source.get("id") == target.get("id"):
        return "A tag cannot be merged into itself."
    if _org_wide(source) or _org_wide(target):
        return "An organization-wide tag is not merged from a project."
    sid = uuid_of(str(source.get("id")), "tag_id")
    tid = uuid_of(str(target.get("id")), "into")
    worn = int(source.get("task_count") or 0)
    impact = f"{_plural(worn, 'task')} retagged {data(source.get('name'))} → {data(target.get('name'))} · 1 tag deleted"
    if not await _confirm(
        title="Merge these tags?",
        detail=f"{data(source.get('name'))} into {data(target.get('name'))} in {data(node.get('name'))}",
        context=_guard_card(
            impact, {"from": data(source.get("name")), "into": data(target.get("name"))}
        ),
    ):
        return CANCELLED
    result = (await post(f"/projects/tags/{sid}/merge", {"into_tag_id": tid})) or {}
    return (
        f"Merged {data(result.get('merged') or source.get('name'))} into "
        f"{data(result.get('into') or target.get('name'))}; "
        f"{_plural(int(result.get('retagged') or 0), 'task')} retagged.\n  tag_id: {tid}"
    )


# ── Views, reports, attachments ──────────────────────────────────────────────


@_annotate(read_only=False, destructive=True, idempotent=False)
async def delete_view(project_id: str, view_name: str) -> str:
    """Delete a saved view, its hand-arranged order and every member's
    arrangement of it. The route counts both as it deletes and the receipt
    carries the numbers."""
    if _many(view_name):
        return f"delete_view {ONE_ACT.replace('id', 'name')}"
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    row = _one_named(rows, view_name, "view")
    vid = uuid_of(str(row.get("id")), "view_id")
    impact = (
        "1 view deleted · its manual order and every member's arrangement of it go with it "
        "(the receipt carries the counts)"
    )
    if not await _confirm(
        title="Delete this view?",
        detail=f"{data(row.get('name'))} ({row.get('view_type')}) in {data(node.get('name'))}",
        context=_guard_card(impact, {"view": data(row.get("name")), "type": row.get("view_type")}),
    ):
        return CANCELLED
    result = (await delete(f"/projects/views/{vid}")) or {}
    cascaded = result.get("cascaded") or {}
    return (
        f"Deleted view {data(row.get('name'))}; {cascaded.get('positions', 0)} positions and "
        f"{cascaded.get('user_states', 0)} member arrangements went with it.\n  view_id: {vid}"
    )


@_annotate(read_only=False, destructive=True, idempotent=True)
async def report_delete(report_id: str) -> str:
    """Delete a saved report definition, with its schedule and recipients.
    The numbers it renders are not stored, so nothing else is lost."""
    if _many(report_id):
        return f"report_delete {ONE_ACT}"
    rid = uuid_of(report_id, "report_id")
    row = (await get(f"/projects/reports/{rid}")) or {}
    scope = "the portfolio" if not row.get("project_id") else f"project {row.get('project_id')}"
    impact = "1 report deleted, with its schedule and recipient list"
    if not await _confirm(
        title="Delete this report?",
        detail=f"{data(row.get('name'))} · {scope}",
        context=_guard_card(impact, {"report": data(row.get("name")), "scope": scope}),
    ):
        return CANCELLED
    await delete(f"/projects/reports/{rid}")
    return f"Deleted report {data(row.get('name'))}.\n  report_id: {rid}"


@_annotate(read_only=False, destructive=True, idempotent=True)
async def delete_attachment(task_id: str, attachment_id: str) -> str:
    """Detach a file from a task. The bytes are kept, because the same
    file may hang off another task. attachment_id comes from task_detail.
    The card names the file."""
    if _many(attachment_id):
        return f"delete_attachment {ONE_ACT}"
    tid, task = await _task(task_id)
    aid = uuid_of(attachment_id, "attachment_id")
    files = ((await get(f"/projects/tasks/{tid}/attachments")) or {}).get("rows") or []
    row = next((f for f in files if str(f.get("id")) == aid), None)
    if row is None:
        return f"{_ref(task)} has no attachment with that id. task_detail lists them."
    impact = f"1 file detached from {_ref(task)}; the bytes are kept"
    if not await _confirm(
        title="Detach this file?",
        detail=f"{data(row.get('name'))} from {_ref(task)}",
        context=_guard_card(impact, {"file": data(row.get("name")), "task": _ref(task)}),
    ):
        return CANCELLED
    await delete(f"/projects/tasks/{tid}/attachments/{aid}")
    return f"Detached {data(row.get('name'))} from {_ref(task)}.\n  full_id: {tid}"


__all__ = [  # noqa: RUF022 — grouped by what they act on
    "archive_project",
    "unarchive_project",
    "move_project",
    "archive_task",
    "merge_tasks",
    "bulk_update",
    "delete_comment",
    "revert_activity",
    "delete_status",
    "set_status_set",
    "delete_type",
    "delete_field",
    "delete_tag",
    "merge_tags",
    "delete_view",
    "report_delete",
    "delete_attachment",
]
