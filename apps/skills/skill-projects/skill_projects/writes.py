"""Class B tools — writes the app can undo. One card each, and a batch is one card.

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.2 and §5.

**The shape is the CRM agent's** (``apps/agents/agent-crm/agents.py``,
WS-26d-write), copied rather than reinvented:

1. **Reads may precede the card. Writes may not.** A tool reads the row it is
   about to change so the card can NAME it — "update task 8f3c…" is a
   signature bought under a misdescription. Every call before the card is a
   ``GET``, and ``test_projects_agent_writes.py`` asserts that per tool.
2. **The card is** ``acb_skills.ask_tools.request_confirmation``, imported
   inside the tool the way the CRM and email agents do, so a test can stub
   it. It fails CLOSED: a run with nobody to answer writes nothing. No tool
   here passes ``non_interactive_default="approve"``, and a test asserts the
   absence in the source.
3. **Names where a person speaks names.** A status is resolved against the
   project's own vocabulary, every case-insensitive match, never the first.
   Two matches is a question for the member. A person may be given by name
   and is resolved through the picker the same way.
4. **The card shows the payload that goes on the wire**, budgeted under the
   4000-character clip with the fixed line first (the CRM's ``_fields_block``
   lesson).

What is deliberately NOT here: hard delete (D-PM-35), the class C acts
(archive, merge, bulk, move a project — S3), and the vocabulary writes
(statuses, types, fields, tags — S2b). ``manifest.py`` is the record.
"""

from __future__ import annotations

from typing import Any

from skill_projects.client import (
    GatewayRefusal,
    data,
    delete,
    get,
    patch,
    post,
    put,
    uuid_of,
)
from skill_projects.reads import _task_line

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


#: ``request_confirmation`` clips ``context`` at 4000 characters. The clip is
#: ours and visible, never the card silently losing whatever came last.
CARD_CONTEXT_LIMIT = 4000
_TRUNCATED = "… [truncated on this card; the full text is what gets written]"
#: The fixed first line of every card body. The member signs as themselves.
CARD_NOTE = "This change is recorded as yours, made through the Projects assistant."

#: How many rows one batch card may list before it refuses. A card nobody
#: reads is not consent.
MAX_BATCH = 50

LINK_TYPES = ("blocks", "relates_to", "duplicates")
IMPORTANCE = (0, 1, 2, 3, 4)


# ── The card ─────────────────────────────────────────────────────────────────


def _fields_block(payload: dict[str, Any], *, before: dict[str, Any] | None = None) -> str:
    """The payload as the card body, rendered from what goes on the wire.

    ``before`` adds the current value beside each changed field, so a member
    approving an update sees the change and not only the result.
    """
    lines: list[str] = []
    for key, value in payload.items():
        if before is not None and key in before and before[key] != value:
            lines.append(f"{key}: {data(before[key])} → {data(value)}")
        else:
            lines.append(f"{key}: {data(value) if isinstance(value, str) else value}")
    body = "\n".join(lines)
    budget = CARD_CONTEXT_LIMIT - len(CARD_NOTE) - 1
    if len(body) <= budget:
        return f"{CARD_NOTE}\n{body}"
    keep = max(0, budget - len(_TRUNCATED) - 1)
    return f"{CARD_NOTE}\n{body[:keep]}\n{_TRUNCATED}"


async def _confirm(title: str, detail: str, context: str) -> bool:
    """One door for every card. Imported inside so a test can stub the gate."""
    from acb_skills.ask_tools import request_confirmation

    return await request_confirmation(title=title, detail=detail, context=context)


CANCELLED = "Cancelled — nothing was changed."


# ── Naming the row, and resolving names ──────────────────────────────────────


async def _task(task_id: str) -> dict[str, Any]:
    tid = uuid_of(task_id, "task_id")
    return await get(f"/projects/tasks/{tid}")


def _ref(task: dict[str, Any]) -> str:
    """``#7 «Fix the extruder»`` — how a card names a task."""
    number = task.get("task_number")
    return f"#{number} {data(task.get('title'))}" if number is not None else data(task.get("title"))


async def _statuses_of(project_id: str) -> list[dict[str, Any]]:
    pid = uuid_of(project_id, "project_id")
    return ((await get(f"/projects/nodes/{pid}/statuses")) or {}).get("rows") or []


def _matches_by_name(rows: list[dict[str, Any]], wanted: str) -> list[dict[str, Any]]:
    """EVERY case-insensitive match, never the first (WS-26d-write decision 3)."""
    target = str(wanted or "").strip().lower()
    if not target:
        return []
    return [r for r in rows if str(r.get("name") or "").strip().lower() == target]


def _names(rows: list[dict[str, Any]]) -> str:
    found = [str(r.get("name")).strip() for r in rows if str(r.get("name") or "").strip()]
    return ", ".join(found) if found else "(none are configured)"


async def _resolve_status(project_id: str, status: str) -> dict[str, Any]:
    """The one status row a spoken name means, or a refusal that lists them."""
    rows = await _statuses_of(project_id)
    found = _matches_by_name(rows, status)
    if len(found) == 1:
        return found[0]
    if not found:
        raise GatewayRefusal(
            f"No status is called {data(status)} in that project. The statuses are: {_names(rows)}."
        )
    quoted = ", ".join(f"'{r.get('name')}'" for r in found)
    raise GatewayRefusal(
        f"{data(status)} matches more than one status ({quoted}). "
        "Ask the member which one they mean."
    )


async def _resolve_assignee(value: str) -> str:
    """An email or ``agent:<name>`` passes through. A person's name resolves
    through the picker, and only when exactly one person matches."""
    raw = str(value or "").strip()
    if not raw:
        raise GatewayRefusal("An assignee needs a value.")
    if "@" in raw or raw.lower().startswith("agent:"):
        return raw.lower()
    payload = await get("/projects/assignees", {"q": raw})
    people = (payload or {}).get("people") or []
    agents = (payload or {}).get("agents") or []
    exact = [p for p in people + agents if str(p.get("name") or "").strip().lower() == raw.lower()]
    found = exact or (people + agents)
    if len(found) == 1:
        return str(found[0].get("assignee")).lower()
    if not found:
        raise GatewayRefusal(f"Nobody matches {data(raw)}. Use people_for to find them.")
    names = ", ".join(f"{data(p.get('name'))} ({p.get('assignee')})" for p in found[:8])
    raise GatewayRefusal(f"{data(raw)} matches more than one person: {names}. Ask which.")


def _split(csv: str) -> list[str]:
    return [part.strip() for part in str(csv or "").split(",") if part.strip()]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


# ── Tasks ────────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    status: str = "",
    assignees: str = "",
    due: str = "",
    importance: int = 0,
    estimate_mins: int = 0,
    tags: str = "",
    parent_task_id: str = "",
) -> str:
    """Create one task in a project. project_id is a `full_id` from
    projects_tree (a project or subproject, never a folder). status is by
    NAME from that project's vocabulary, or omitted for the default.
    assignees is comma-separated emails, agent:<name>, or people's names.
    due is YYYY-MM-DD. importance is 0 to 4. tags is comma-separated.
    parent_task_id makes it a subtask. The member approves a card first;
    nothing is created if they decline. Archive is the undo."""
    pid = uuid_of(project_id, "project_id")
    name = str(title or "").strip()
    if not name:
        return "A task needs a title."
    payload: dict[str, Any] = {"project_id": pid, "title": name}
    if description.strip():
        payload["description"] = description.strip()
    if status.strip():
        row = await _resolve_status(pid, status)
        payload["status_id"] = str(row.get("id"))
        status_label = str(row.get("name"))
    else:
        status_label = "the default"
    if due.strip():
        payload["due_at"] = due.strip()
    imp = _int_or_none(importance)
    if imp is not None:
        if imp not in IMPORTANCE:
            return "importance is 0 to 4."
        payload["importance"] = imp
    est = _int_or_none(estimate_mins)
    if est is not None:
        payload["estimate_mins"] = est
    if tags.strip():
        payload["tags"] = _split(tags)
    if parent_task_id.strip():
        parent = await _task(parent_task_id)
        payload["parent_task_id"] = str(parent.get("id"))
    who = [await _resolve_assignee(a) for a in _split(assignees)]

    card = dict(payload)
    card["status"] = status_label
    if who:
        card["assignees"] = ", ".join(who)
    if not await _confirm(
        title="Create this task?",
        detail=f"{data(name)} · status {status_label}" + (f" · {', '.join(who)}" if who else ""),
        context=_fields_block(card),
    ):
        return CANCELLED
    task = await post("/projects/tasks", payload)
    tid = str(task.get("id"))
    if who:
        await put(f"/projects/tasks/{tid}/assignees", {"assignees": who})
        task["assignees"] = who
    return "\n".join(["Created:", *_task_line(task, status_label)])


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_task(
    task_id: str,
    title: str = "",
    description: str = "",
    status: str = "",
    due: str = "",
    start: str = "",
    importance: int = 0,
    estimate_mins: int = 0,
    tags: str = "",
    clear: str = "",
) -> str:
    """Change a task's fields. Only the arguments you pass change. status is
    by NAME from the task's project. due and start are YYYY-MM-DD. tags
    REPLACES the tag list. clear is a comma-separated list of fields to
    empty: due, start, description, estimate. The card shows each change as
    before → after. The timeline records every field change, and the app
    can revert one."""
    task = await _task(task_id)
    tid = str(task.get("id"))
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if title.strip():
        payload["title"] = title.strip()
        before["title"] = task.get("title")
    if description.strip():
        payload["description"] = description.strip()
        before["description"] = (task.get("description") or "")[:200]
    status_label = ""
    if status.strip():
        row = await _resolve_status(str(task.get("project_id")), status)
        payload["status_id"] = str(row.get("id"))
        status_label = str(row.get("name"))
    if due.strip():
        payload["due_at"] = due.strip()
        before["due_at"] = (task.get("due_at") or "")[:10]
    if start.strip():
        payload["start_date"] = start.strip()
        before["start_date"] = (task.get("start_date") or "")[:10]
    imp = _int_or_none(importance)
    if imp is not None:
        if imp not in IMPORTANCE:
            return "importance is 0 to 4."
        payload["importance"] = imp
        before["importance"] = task.get("importance")
    est = _int_or_none(estimate_mins)
    if est is not None:
        payload["estimate_mins"] = est
        before["estimate_mins"] = task.get("estimate_mins")
    if tags.strip():
        payload["tags"] = _split(tags)
        before["tags"] = task.get("tags")
    for field in _split(clear):
        key = {"due": "due_at", "start": "start_date", "estimate": "estimate_mins"}.get(
            field.lower(), field.lower()
        )
        if key not in ("due_at", "start_date", "description", "estimate_mins"):
            return f"clear takes due, start, description or estimate, not {data(field)}."
        payload[key] = None
        before[key] = task.get(key)
    if not payload:
        return "Nothing to change. Pass at least one field."

    card = dict(payload)
    if status_label:
        card["status"] = status_label
        card.pop("status_id", None)
    if not await _confirm(
        title="Update this task?",
        detail=_ref(task) + (f" · status → {status_label}" if status_label else ""),
        context=_fields_block(card, before=before),
    ):
        return CANCELLED
    updated = await patch(f"/projects/tasks/{tid}", payload)
    updated["assignees"] = task.get("assignees") or []
    return "\n".join(["Updated:", *_task_line(updated, status_label)])


@_annotate(read_only=False, destructive=False, idempotent=True)
async def assign(task_id: str, assignees: str) -> str:
    """Set who holds a task. assignees is comma-separated: emails,
    agent:<name>, or people's names (resolved through the picker, one match
    each). This REPLACES the set; pass every holder you want to keep. An
    empty string unassigns. The card shows the old set and the new one.
    Assigning agent:<name> starts an agent run on the task (§6.4)."""
    task = await _task(task_id)
    tid = str(task.get("id"))
    who = [await _resolve_assignee(a) for a in _split(assignees)]
    current = [str(a).lower() for a in task.get("assignees") or []]
    if sorted(who) == sorted(current):
        return f"{_ref(task)} already has exactly those assignees."
    agents = [a for a in who if a.startswith("agent:") and a not in current]
    context = _fields_block(
        {"assignees": ", ".join(who) or "(nobody)"},
        before={"assignees": ", ".join(current) or "(nobody)"},
    )
    if agents:
        context += f"\nAssigning {', '.join(agents)} starts an agent run on this task."
    if not await _confirm(
        title="Change who holds this task?",
        detail=f"{_ref(task)} → {', '.join(who) or 'nobody'}",
        context=context,
    ):
        return CANCELLED
    await put(f"/projects/tasks/{tid}/assignees", {"assignees": who})
    task["assignees"] = who
    return "\n".join(["Assigned:", *_task_line(task)])


@_annotate(read_only=False, destructive=False, idempotent=False)
async def comment(task_id: str, body: str, reply_to: str = "") -> str:
    """Add a comment to a task's timeline, in the member's own words.
    reply_to is the id of the comment it answers (one level deep). The card
    shows the exact text that will be posted. The author can delete it
    later from the timeline."""
    text_body = str(body or "").strip()
    if not text_body:
        return "A comment needs a body."
    task = await _task(task_id)
    tid = str(task.get("id"))
    payload: dict[str, Any] = {"body": text_body}
    if reply_to.strip():
        payload["parent_id"] = uuid_of(reply_to, "reply_to")
    if not await _confirm(
        title="Post this comment?",
        detail=f"on {_ref(task)}",
        context=_fields_block(payload),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{tid}/comments", payload)
    return f"Commented on {_ref(task)} (comment id {row.get('id')}).\n  full_id: {tid}"


@_annotate(read_only=False, destructive=False, idempotent=False)
async def add_subtasks(task_id: str, titles: str) -> str:
    """Break a task into steps. titles is one subtask per line, or
    comma-separated. ONE card lists every subtask; the member approves the
    batch once. Each subtask lands in the parent's project with the default
    status. Archive undoes any of them."""
    parent = await _task(task_id)
    pid = str(parent.get("project_id"))
    tid = str(parent.get("id"))
    raw = str(titles or "")
    parts = [p.strip() for p in (raw.split("\n") if "\n" in raw else raw.split(",")) if p.strip()]
    if not parts:
        return "Give at least one subtask title."
    if len(parts) > MAX_BATCH:
        return f"That is {len(parts)} subtasks. The limit for one card is {MAX_BATCH}."
    if not await _confirm(
        title=f"Add {len(parts)} subtask{'s' if len(parts) != 1 else ''}?",
        detail=f"under {_ref(parent)}",
        context=_fields_block({f"{i + 1}": t for i, t in enumerate(parts)}),
    ):
        return CANCELLED
    out = [f"Added under {_ref(parent)}:"]
    for title in parts:
        row = await post(
            "/projects/tasks", {"project_id": pid, "title": title, "parent_task_id": tid}
        )
        out.extend(_task_line(row))
    return "\n".join(out)


@_annotate(read_only=False, destructive=False, idempotent=False)
async def link_tasks(task_id: str, other_task_id: str, link_type: str = "relates_to") -> str:
    """Link two tasks. link_type is blocks (task_id blocks other_task_id),
    relates_to, or duplicates. The card names both tasks. unlink_tasks
    removes the link by its id."""
    kind = str(link_type or "relates_to").strip().lower()
    if kind not in LINK_TYPES:
        return f"link_type is one of {', '.join(LINK_TYPES)}."
    task = await _task(task_id)
    other = await _task(other_task_id)
    if task.get("id") == other.get("id"):
        return "A task cannot be linked to itself."
    if not await _confirm(
        title="Link these tasks?",
        detail=f"{_ref(task)} {kind.replace('_', ' ')} {_ref(other)}",
        context=_fields_block({"link_type": kind, "from": _ref(task), "to": _ref(other)}),
    ):
        return CANCELLED
    row = await post(
        f"/projects/tasks/{task['id']}/links",
        {"target_task_id": str(other.get("id")), "link_type": kind},
    )
    return (
        f"Linked: {_ref(task)} {kind.replace('_', ' ')} {_ref(other)} (link id {row.get('id')})."
        f"\n  full_id: {task.get('id')}"
    )


@_annotate(read_only=False, destructive=False, idempotent=True)
async def unlink_tasks(task_id: str, link_id: str) -> str:
    """Remove a link from a task. link_id comes from task_detail's Links
    list. The card names the task and the link."""
    task = await _task(task_id)
    lid = uuid_of(link_id, "link_id")
    relations = await get(f"/projects/tasks/{task['id']}/relations")
    links = (relations or {}).get("links") or []
    link = next((row for row in links if str(row.get("id")) == lid), None)
    if link is None:
        return f"{_ref(task)} has no link with id {lid}. task_detail lists its links."
    other = link.get("other") or link
    label = (
        f"{link.get('direction', '')} {link.get('link_type', 'link')} {data(other.get('title'))}"
    )
    if not await _confirm(
        title="Remove this link?",
        detail=f"{_ref(task)}: {label}",
        context=_fields_block({"link_id": lid, "link": label}),
    ):
        return CANCELLED
    await delete(f"/projects/tasks/{task['id']}/links/{lid}")
    return f"Removed the link: {label}.\n  full_id: {task.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False)
async def move_task(
    task_ids: str, destination_project_id: str = "", parent_task_id: str = ""
) -> str:
    """Move tasks. With destination_project_id, moves the listed tasks (all
    from ONE source project, comma-separated ids) into another project: the
    server previews what carries over and what drops, the card shows that,
    and the move applies only the drops the member saw. With
    parent_task_id alone, re-parents one task under another in the same
    project. Moving back is the undo."""
    ids = [uuid_of(t, "task_id") for t in _split(task_ids)]
    if not ids:
        return "Give at least one task id."
    if destination_project_id.strip():
        dest = uuid_of(destination_project_id, "destination_project_id")
        dest_node = await get(f"/projects/nodes/{dest}")
        # The preview WRITES NOTHING (move.py `preview_move`), and it makes
        # every refusal the apply makes, so the card cannot offer a move the
        # apply then rejects. It is the read that names what this costs.
        plan = await post(
            "/projects/tasks/move/preview", {"task_ids": ids, "destination_project_id": dest}
        )
        drops = sorted(plan.get("drops") or [])
        missing = plan.get("required_missing") or []
        if missing:
            return (
                "The destination requires " + ", ".join(missing) + ", which these tasks do "
                "not carry. Fill them in first, then move."
            )
        card: dict[str, Any] = {
            "tasks": plan.get("task_count", len(ids)),
            "to": data(dest_node.get("name")),
        }
        if plan.get("crosses_status_set"):
            card["statuses"] = "re-pointed to the destination's lanes by category"
        if drops:
            card["drops (values with no field in the destination)"] = ", ".join(drops)
        if not await _confirm(
            title=f"Move {card['tasks']} task{'s' if card['tasks'] != 1 else ''}?",
            detail=f"to {card['to']}" + (f" · drops {', '.join(drops)}" if drops else ""),
            context=_fields_block(card),
        ):
            return CANCELLED
        body: dict[str, Any] = {"task_ids": ids, "destination_project_id": dest}
        if drops:
            # The member saw exactly these drops. The apply refuses with 409
            # if the destination changed and would now drop more (D-PM-29).
            body["accept_drops"] = True
            body["accepted_drops"] = drops
        result = await post("/projects/tasks/move", body)
        moved = result.get("moved") if isinstance(result, dict) else None
        moved = moved if isinstance(moved, int) else len(ids)
        return f"Moved {moved} task{'s' if moved != 1 else ''} to {card['to']}."
    if parent_task_id.strip():
        if len(ids) != 1:
            return "Re-parenting takes exactly one task id."
        task = await _task(ids[0])
        parent = await _task(parent_task_id)
        if not await _confirm(
            title="Make this a subtask?",
            detail=f"{_ref(task)} under {_ref(parent)}",
            context=_fields_block({"task": _ref(task), "parent": _ref(parent)}),
        ):
            return CANCELLED
        row = await post(
            f"/projects/tasks/{task['id']}/move", {"parent_task_id": str(parent.get("id"))}
        )
        return "\n".join([f"Now a subtask of {_ref(parent)}:", *_task_line(row)])
    return "Pass destination_project_id to move between projects, or parent_task_id to re-parent."


@_annotate(read_only=False, destructive=False, idempotent=True)
async def watch(target_id: str, kind: str = "task", stop: bool = False) -> str:
    """Watch a task or a project for the member, so its changes reach their
    notifications. kind is task or project. stop=true unwatches. Watching
    is the member's own subscription and touches nothing shared."""
    which = str(kind or "task").strip().lower()
    if which not in ("task", "project"):
        return "kind is task or project."
    if which == "task":
        row = await _task(target_id)
        label = _ref(row)
        path = f"/projects/tasks/{row['id']}/watch"
    else:
        pid = uuid_of(target_id, "project_id")
        row = await get(f"/projects/nodes/{pid}")
        label = data(row.get("name"))
        path = f"/projects/nodes/{pid}/watch"
    verb = "Stop watching" if stop else "Watch"
    if not await _confirm(
        title=f"{verb} this {which}?", detail=label, context=_fields_block({which: label})
    ):
        return CANCELLED
    if stop:
        await delete(path)
        return f"You no longer watch {label}.\n  full_id: {row.get('id')}"
    await put(path)
    return f"You now watch {label}.\n  full_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=True)
async def complete(task_id: str) -> str:
    """Mark a task done. This moves the task's SHARED status to its
    project's done lane, for everyone. To reopen, set a status by name with
    update_task."""
    task = await _task(task_id)
    if task.get("completed_at"):
        return f"{_ref(task)} is already done."
    if not await _confirm(
        title="Mark this task done?",
        detail=_ref(task),
        context=_fields_block({"task": _ref(task), "status": "the project's done lane"}),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{task['id']}/complete")
    merged = {**task, **(row if isinstance(row, dict) else {})}
    return "\n".join(["Done:", *_task_line(merged, "done")])


@_annotate(read_only=False, destructive=False, idempotent=True)
async def defer(task_id: str, until: str) -> str:
    """Hide a task from the member's own inbox until a date (YYYY-MM-DD).
    Mine only: the team's board does not change."""
    when = str(until or "").strip()
    if len(when) != 10:
        return "until is a date, YYYY-MM-DD."
    task = await _task(task_id)
    if not await _confirm(
        title=f"Defer until {when}?",
        detail=_ref(task),
        context=_fields_block({"task": _ref(task), "until": when, "scope": "your inbox only"}),
    ):
        return CANCELLED
    await post(f"/projects/tasks/{task['id']}/defer", {"until": when})
    return f"Deferred {_ref(task)} until {when} in your inbox.\n  full_id: {task.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=True)
async def unarchive_task(task_id: str) -> str:
    """Bring an archived task back onto its board. This is the undo of
    archiving. list_tasks with include_archived=true finds archived tasks."""
    task = await _task(task_id)
    if not task.get("archived_at"):
        return f"{_ref(task)} is not archived."
    if not await _confirm(
        title="Restore this task?",
        detail=_ref(task),
        context=_fields_block(
            {"task": _ref(task), "archived": (task.get("archived_at") or "")[:10]}
        ),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{task['id']}/unarchive")
    merged = {**task, **(row if isinstance(row, dict) else {}), "archived_at": None}
    return "\n".join(["Restored:", *_task_line(merged)])


# ── Projects ─────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_project(
    name: str,
    parent_project_id: str = "",
    kind: str = "project",
    description: str = "",
    lead: str = "",
) -> str:
    """Create a space (no parent), a folder, a project or a subproject.
    parent_project_id is a `full_id` from projects_tree. kind is project
    or folder. lead is an email or a person's name. The card names the
    parent. Archive is the undo."""
    label = str(name or "").strip()
    if not label:
        return "A project needs a name."
    which = str(kind or "project").strip().lower()
    if which not in ("project", "folder"):
        return "kind is project or folder."
    payload: dict[str, Any] = {"name": label, "kind": which}
    parent_label = "the top level (a new space)"
    if parent_project_id.strip():
        pid = uuid_of(parent_project_id, "parent_project_id")
        parent = await get(f"/projects/nodes/{pid}")
        payload["parent_project_id"] = pid
        parent_label = data(parent.get("name"))
    if description.strip():
        payload["description"] = description.strip()
    if lead.strip():
        payload["lead"] = await _resolve_assignee(lead)
    if not await _confirm(
        title=f"Create this {which}?",
        detail=f"{data(label)} under {parent_label}",
        context=_fields_block({**payload, "under": parent_label}),
    ):
        return CANCELLED
    row = await post("/projects/nodes", payload)
    return (
        f"Created {which} {data(row.get('name'))} under {parent_label}.\n  full_id: {row.get('id')}"
    )


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_project(
    project_id: str, name: str = "", description: str = "", status: str = "", lead: str = ""
) -> str:
    """Rename a node, change its description, its run state (active,
    paused, stopped) or its lead. Only the arguments you pass change. The
    card shows before → after. Re-parenting is a move (S3), and archiving
    is its own act."""
    pid = uuid_of(project_id, "project_id")
    node = await get(f"/projects/nodes/{pid}")
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if name.strip():
        payload["name"] = name.strip()
        before["name"] = node.get("name")
    if description.strip():
        payload["description"] = description.strip()
        before["description"] = (node.get("description") or "")[:200]
    if status.strip():
        state = status.strip().lower()
        if state not in ("active", "paused", "stopped"):
            return "status is active, paused or stopped. Archiving is a separate act."
        payload["status"] = state
        before["status"] = node.get("status")
    if lead.strip():
        payload["lead"] = await _resolve_assignee(lead)
        before["lead"] = node.get("lead")
    if not payload:
        return "Nothing to change. Pass at least one field."
    if not await _confirm(
        title="Update this project?",
        detail=data(node.get("name")),
        context=_fields_block(payload, before=before),
    ):
        return CANCELLED
    row = await patch(f"/projects/nodes/{pid}", payload)
    return f"Updated {data(row.get('name'))}.\n  full_id: {pid}"


# ── Reports ──────────────────────────────────────────────────────────────────

REPORT_SECTIONS = ("finished", "throughput", "load", "stuck")


@_annotate(read_only=False, destructive=False, idempotent=False)
async def report_save(
    name: str, project_id: str = "", sections: str = "", weeks: int = 0, report_id: str = ""
) -> str:
    """Save a report definition, or change one (pass report_id). A report
    stores the question: scope (project_id, or empty for the portfolio),
    sections (comma-separated from finished, throughput, load, stuck) and
    weeks. Render it with report_render. Delivery and schedules stay in the
    Reports app."""
    label = str(name or "").strip()
    asked = [s.strip().lower() for s in _split(sections)]
    bad = [s for s in asked if s not in REPORT_SECTIONS]
    if bad:
        return f"sections are from {', '.join(REPORT_SECTIONS)}, not {', '.join(bad)}."
    config: dict[str, Any] = {}
    if asked:
        config["sections"] = asked
    wk = _int_or_none(weeks)
    if wk is not None:
        config["weeks"] = wk
    payload: dict[str, Any] = {}
    if label:
        payload["name"] = label
    if config:
        payload["config"] = config
    scope = "the portfolio"
    if project_id.strip():
        pid = uuid_of(project_id, "project_id")
        node = await get(f"/projects/nodes/{pid}")
        payload["project_id"] = pid
        scope = data(node.get("name"))
    if report_id.strip():
        rid = uuid_of(report_id, "report_id")
        existing = await get(f"/projects/reports/{rid}")
        if not payload:
            return "Nothing to change."
        if not await _confirm(
            title="Change this report?",
            detail=data(existing.get("name")),
            context=_fields_block({**payload, "scope": scope}),
        ):
            return CANCELLED
        row = await patch(f"/projects/reports/{rid}", payload)
        return f"Updated report {data(row.get('name'))}.\n  full_id: {rid}"
    if not label:
        return "A report needs a name."
    if not await _confirm(
        title="Save this report?",
        detail=f"{data(label)} · {scope}",
        context=_fields_block({**payload, "scope": scope}),
    ):
        return CANCELLED
    row = await post("/projects/reports", payload)
    return f"Saved report {data(row.get('name'))} for {scope}.\n  full_id: {row.get('id')}"


__all__ = [
    "add_subtasks",
    "assign",
    "comment",
    "complete",
    "create_project",
    "create_task",
    "defer",
    "link_tasks",
    "move_task",
    "report_save",
    "unarchive_task",
    "unlink_tasks",
    "update_project",
    "update_task",
    "watch",
]
