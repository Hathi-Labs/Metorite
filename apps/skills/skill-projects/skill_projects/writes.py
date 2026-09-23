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

What is deliberately NOT here: hard delete (D-PM-35) and the class C acts
(archive, merge, bulk, move a project, every delete — S3). ``manifest.py``
is the record.

S2b (2026-09-23) added the rest of class B: the vocabulary writes (a status,
a type, a field, a tag — create and update), editing the member's own
comment, the repeat rule, the private capture and the member's own overlay.
A vocabulary row is named by the member; the tool resolves it against the
project's own list the way a status is, every match and never the first.
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
from skill_projects.reads import _rule_text, _task_line

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

    def shown(value: Any) -> Any:
        # A value that carries a fence is usually ours (`data()`, `_ref()`),
        # and fencing it again would strip the inner pair of `#7 «title»`.
        # A raw payload string may carry one too, so the newline collapse
        # runs regardless: a card is read line by line, and a value that
        # spans lines can forge one (S2b review).
        if isinstance(value, str):
            return " ".join(value.split()) if "«" in value else data(value)
        return value

    lines: list[str] = []
    for key, value in payload.items():
        if before is not None and key in before and before[key] != value:
            lines.append(f"{key}: {shown(before[key])} → {shown(value)}")
        else:
            lines.append(f"{key}: {shown(value)}")
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


async def _task(task_id: str) -> tuple[str, dict[str, Any]]:
    """``(canonical id, row)``. The id is the one every path below uses, so a
    server-supplied ``row["id"]`` never reaches a URL: the writes fence
    accepts a path segment bound from ``uuid_of``, ``_task`` or ``_node``."""
    tid = uuid_of(task_id, "task_id")
    return tid, await get(f"/projects/tasks/{tid}")


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
    """The rows' names, each fenced. A refusal prints these, and the receipt
    card reads a refusal line by line: an unfenced name with a newline and a
    ``status_id:`` in it would paint the refusal green (S2b review)."""
    found = [data(r.get("name")) for r in rows if str(r.get("name") or "").strip()]
    return ", ".join(found) if found else "(none are configured)"


def _one_named(
    rows: list[dict[str, Any]], wanted: str, what: str, plural: str = ""
) -> dict[str, Any]:
    """The one row a spoken name means, or a refusal that lists them all.

    Every case-insensitive match, never the first. Two matches is a question
    for the member, not a coin toss (WS-26d-write decision 3).
    """
    found = _matches_by_name(rows, wanted)
    if len(found) == 1:
        return found[0]
    many = plural or f"{what}s"
    if not found:
        raise GatewayRefusal(
            f"No {what} is called {data(wanted)} in that project. The {many} are: {_names(rows)}."
        )
    quoted = ", ".join(data(r.get("name")) for r in found)
    raise GatewayRefusal(
        f"{data(wanted)} matches more than one {what} ({quoted}). "
        "Ask the member which one they mean."
    )


async def _resolve_status(project_id: str, status: str) -> dict[str, Any]:
    """The one status row a spoken name means, or a refusal that lists them."""
    return _one_named(await _statuses_of(project_id), status, "status", "statuses")


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
    # The picker matches name, email AND title by substring, so "ops" can
    # return one person whose title is "Ops Lead". Only an exact name match
    # resolves on its own. Anything looser is a list for the member to pick
    # from, never a silent pick.
    exact = [p for p in people + agents if str(p.get("name") or "").strip().lower() == raw.lower()]
    if len(exact) == 1:
        return str(exact[0].get("assignee")).lower()
    found = exact or (people + agents)
    if not found:
        raise GatewayRefusal(f"Nobody matches {data(raw)}. Use people_for to find them.")
    names = ", ".join(f"{data(p.get('name'))} ({data(p.get('assignee'))})" for p in found[:8])
    if len(exact) > 1:
        raise GatewayRefusal(f"{data(raw)} matches more than one person: {names}. Ask which.")
    raise GatewayRefusal(
        f"No person is called exactly {data(raw)}. Close matches: {names}. "
        "Pass the address, or ask which one."
    )


async def _unknown_addresses(who: list[str]) -> list[str]:
    """The addresses among ``who`` that the people directory does not know.

    An assignee is a bare string the route accepts whatever it is (D-PM-4),
    so an address passes through ``_resolve_assignee`` unchecked. The card
    names the ones the picker cannot find, so a typo is seen before it is
    signed (H-162). Agents are not addresses and are skipped.
    """
    addresses = [a for a in who if "@" in a]
    if not addresses:
        return []
    # One exact lookup (`/people/names`), not the picker: the picker matches
    # by substring and only among active people (S6 review).
    payload = (await get("/projects/people/names", {"emails": ",".join(addresses)})) or {}
    known = {str(k).lower() for k in (payload.get("names") or {})}
    return [a for a in addresses if a.lower() not in known]


def _split(csv: str) -> list[str]:
    return [part.strip() for part in str(csv or "").split(",") if part.strip()]


def _int_or_none(value: Any) -> int | None:
    """An estimate: absent, empty or zero means "not passed"."""
    try:
        return int(value) if value not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        return None


def _importance(value: Any) -> int | None:
    """Importance: -1 (the default) means "not passed". 0 is a real value —
    it is how a member drops the priority — so it must not read as absent."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return None if parsed < 0 else parsed


# ── Tasks ────────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    status: str = "",
    assignees: str = "",
    due: str = "",
    importance: int = -1,
    estimate_mins: int = 0,
    tags: str = "",
    parent_task_id: str = "",
) -> str:
    """Create one task in a project. project_id is a `full_id` from
    projects_tree (a project or subproject, never a folder). status is by
    NAME from that project's vocabulary, or omitted for the default.
    assignees is comma-separated emails, agent:<name>, or people's names.
    due is YYYY-MM-DD. importance is 0 to 4 (omit to leave it). tags is comma-separated.
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
    imp = _importance(importance)
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
        parent_id, _parent = await _task(parent_task_id)
        payload["parent_task_id"] = parent_id
    who = [await _resolve_assignee(a) for a in _split(assignees)]

    card = dict(payload)
    card["status"] = status_label
    if who:
        card["assignees"] = ", ".join(who)
        strangers = await _unknown_addresses(who)
        if strangers:
            card["not in the directory"] = ", ".join(strangers)
    if not await _confirm(
        title="Create this task?",
        detail=f"{data(name)} · status {status_label}" + (f" · {', '.join(who)}" if who else ""),
        context=_fields_block(card),
    ):
        return CANCELLED
    task = await post("/projects/tasks", payload)
    tid = uuid_of(str(task.get("id")), "task_id")
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
    importance: int = -1,
    estimate_mins: int = 0,
    tags: str = "",
    clear: str = "",
) -> str:
    """Change a task's fields. Only the arguments you pass change. status is
    by NAME from the task's project. due and start are YYYY-MM-DD. tags
    REPLACES the tag list. importance 0 to 4 (omit to leave it). clear is a
    comma-separated list of fields to empty: due, start, description,
    estimate, importance. The card shows each change as
    before → after. The timeline records every field change, and the app
    can revert one."""
    tid, task = await _task(task_id)
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
    imp = _importance(importance)
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
    cleared: dict[str, Any] = {}
    for field in _split(clear):
        key = {
            "due": "due_at",
            "start": "start_date",
            "estimate": "estimate_mins",
            "priority": "importance",
        }.get(field.lower(), field.lower())
        if key not in ("due_at", "start_date", "description", "estimate_mins", "importance"):
            return (
                f"clear takes due, start, description, estimate or importance, not {data(field)}."
            )
        cleared[key] = None
        before[key] = task.get(key)
    # Cleared fields go FIRST in the card. The card clips its tail at 4000
    # characters, and a long description must never push "due → None" out of
    # sight: a clear is the change a member most needs to see.
    payload = {**cleared, **payload}
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
    tid, task = await _task(task_id)
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
    strangers = await _unknown_addresses([a for a in who if a not in current])
    if strangers:
        context += f"\nThe people directory does not know {data(', '.join(strangers))}."
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
    tid, task = await _task(task_id)
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
    tid, parent = await _task(task_id)
    pid = str(parent.get("project_id"))
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
    # The parent's id FIRST: the receipt card opens the first `full_id`,
    # and its heading names the parent (H-162).
    out = [f"Added under {_ref(parent)}:", f"  full_id: {tid}"]
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
    tid, task = await _task(task_id)
    oid, other = await _task(other_task_id)
    if tid == oid:
        return "A task cannot be linked to itself."
    if not await _confirm(
        title="Link these tasks?",
        detail=f"{_ref(task)} {kind.replace('_', ' ')} {_ref(other)}",
        context=_fields_block({"link_type": kind, "from": _ref(task), "to": _ref(other)}),
    ):
        return CANCELLED
    row = await post(
        f"/projects/tasks/{tid}/links",
        {"target_task_id": oid, "link_type": kind},
    )
    return (
        f"Linked: {_ref(task)} {kind.replace('_', ' ')} {_ref(other)} (link id {row.get('id')})."
        f"\n  full_id: {tid}"
    )


@_annotate(read_only=False, destructive=False, idempotent=True)
async def unlink_tasks(task_id: str, link_id: str) -> str:
    """Remove a link from a task. link_id comes from task_detail's Links
    list. The card names the task and the link."""
    tid, task = await _task(task_id)
    lid = uuid_of(link_id, "link_id")
    relations = await get(f"/projects/tasks/{tid}/relations")
    links = (relations or {}).get("links") or []
    # The relations route's row (relations.py `_row`): `link_id` is the link
    # row, `id` is the OTHER task. The first version matched on `id` and so
    # never removed a link. The test fake now carries the route's real shape.
    link = next((row for row in links if str(row.get("link_id")) == lid), None)
    if link is None:
        return f"{_ref(task)} has no link with id {lid}. task_detail lists its links."
    label = f"{link.get('direction', '')} {link.get('link_type', 'link')} {_ref(link)}"
    if not await _confirm(
        title="Remove this link?",
        detail=f"{_ref(task)}: {label}",
        context=_fields_block({"link_id": lid, "link": label}),
    ):
        return CANCELLED
    await delete(f"/projects/tasks/{tid}/links/{lid}")
    return f"Removed the link: {label}.\n  full_id: {tid}"


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
        if len(ids) > MAX_BATCH:
            return f"That is {len(ids)} tasks. The limit for one card is {MAX_BATCH}."
        dest = uuid_of(destination_project_id, "destination_project_id")
        dest_node = await get(f"/projects/nodes/{dest}")
        # The card NAMES every task it moves (class B may list many rows),
        # so the member sees which three, not "3 tasks". Read before the
        # card, like every other row a card names.
        tasks = [(await _task(t))[1] for t in ids]
        if all(str(t.get("project_id")) == dest for t in tasks):
            return f"Those tasks are already in {data(dest_node.get('name'))}."
        # The preview WRITES NOTHING (move.py `preview_move`). It computes
        # the plan the apply will use, and it is the read that names what
        # this costs. Two refusals live in the apply alone (same-project,
        # and a status the destination lacks per task), so a 422 after the
        # card is still possible and is relayed as the gateway's own words.
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
        for i, t in enumerate(tasks):
            card[f"task {i + 1}"] = _ref(t)
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
        out = [f"Moved {moved} task{'s' if moved != 1 else ''} to {card['to']}:"]
        for t in tasks:
            out.extend(_task_line({**t, "project_id": dest}))
        return "\n".join(out)
    if parent_task_id.strip():
        if len(ids) != 1:
            return "Re-parenting takes exactly one task id."
        tid, task = await _task(ids[0])
        parent_id, parent = await _task(parent_task_id)
        if not await _confirm(
            title="Make this a subtask?",
            detail=f"{_ref(task)} under {_ref(parent)}",
            context=_fields_block({"task": _ref(task), "parent": _ref(parent)}),
        ):
            return CANCELLED
        row = await post(f"/projects/tasks/{tid}/move", {"parent_task_id": parent_id})
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
        rid, row = await _task(target_id)
        label = _ref(row)
        path = f"/projects/tasks/{rid}/watch"
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
    tid, task = await _task(task_id)
    if task.get("completed_at"):
        return f"{_ref(task)} is already done."
    if not await _confirm(
        title="Mark this task done?",
        detail=_ref(task),
        context=_fields_block({"task": _ref(task), "status": "the project's done lane"}),
    ):
        return CANCELLED
    row = await post(f"/projects/tasks/{tid}/complete")
    merged = {**task, **(row if isinstance(row, dict) else {})}
    return "\n".join(["Done:", *_task_line(merged, "done")])


@_annotate(read_only=False, destructive=False, idempotent=True)
async def defer(task_id: str, until: str) -> str:
    """Hide a task from the member's own inbox until a date (YYYY-MM-DD).
    Mine only: the team's board does not change."""
    when = str(until or "").strip()
    if len(when) != 10:
        return "until is a date, YYYY-MM-DD."
    tid, task = await _task(task_id)
    if not await _confirm(
        title=f"Defer until {when}?",
        detail=_ref(task),
        context=_fields_block({"task": _ref(task), "until": when, "scope": "your inbox only"}),
    ):
        return CANCELLED
    await post(f"/projects/tasks/{tid}/defer", {"until": when})
    return f"Deferred {_ref(task)} until {when} in your inbox.\n  full_id: {tid}"


@_annotate(read_only=False, destructive=False, idempotent=True)
async def unarchive_task(task_id: str) -> str:
    """Bring an archived task back onto its board. This is the undo of
    archiving. list_tasks with include_archived=true finds archived tasks."""
    tid, task = await _task(task_id)
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
    row = await post(f"/projects/tasks/{tid}/unarchive")
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
    scope = "the portfolio"
    if report_id.strip():
        # An UPDATE. The route changes `name` and `config` only, and it
        # REPLACES `config` (reports.py `update_report`), so the tool merges
        # the member's change into the saved config first and never sends a
        # scope: a report's scope cannot change, and the card must not say
        # it can. Save a new report for another scope.
        rid = uuid_of(report_id, "report_id")
        if project_id.strip():
            return (
                "A saved report keeps its scope. Save a new report for another "
                "project, or leave project_id empty to change this one."
            )
        existing = await get(f"/projects/reports/{rid}")
        if config:
            merged = dict(existing.get("config") or {})
            merged.update(config)
            payload["config"] = merged
        if not payload:
            return "Nothing to change."
        card = dict(payload)
        if "config" in card:
            card["config"] = ", ".join(f"{k} {v}" for k, v in payload["config"].items())
        if not await _confirm(
            title="Change this report?",
            detail=data(existing.get("name")),
            context=_fields_block(card),
        ):
            return CANCELLED
        row = await patch(f"/projects/reports/{rid}", payload)
        return f"Updated report {data(row.get('name'))}.\n  full_id: {rid}"
    if config:
        payload["config"] = config
    if project_id.strip():
        pid = uuid_of(project_id, "project_id")
        node = await get(f"/projects/nodes/{pid}")
        payload["project_id"] = pid
        scope = data(node.get("name"))
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


# ── Vocabulary — statuses, types, fields, tags (S2b) ─────────────────────────
#
# A vocabulary write lands on the tree's ROOT (types, fields, tags) or on the
# nearest node that owns a status set. The card names that node, because
# "add a type to Website" reaches every project in Website's tree, and "add a
# status" to a subproject that inherits its lanes changes every sibling's
# board. Authority is the route's: a status write needs the settings
# permission (`admin.assert_can_manage_settings`); a type, field or tag write
# needs only visibility today (board H-4 records the gap). The card is
# consent, never authority. An org-wide row (WS-27bj) is minted only when the
# member says `org_wide=true`, and the route refuses it without the
# organization settings permission.

STATUS_CATEGORIES = ("backlog", "todo", "in_progress", "done", "cancelled", "triage")
FIELD_TYPES = ("text", "number", "date", "select", "multi_select", "boolean", "url")
FREQS = ("daily", "weekly", "monthly", "yearly")
ANCHORS = ("due", "completed")
DISPOSITIONS = ("INBOX", "NEXT", "WAITING", "SOMEDAY", "PROJECT", "REFERENCE", "DONE", "TRASH")
ENERGIES = ("low", "medium", "high")


async def _node(project_id: str) -> tuple[str, dict[str, Any]]:
    pid = uuid_of(project_id, "project_id")
    return pid, await get(f"/projects/nodes/{pid}")


async def _vocab(project_id: str, kind: str) -> list[dict[str, Any]]:
    """One of the four vocabulary lists."""
    pid = uuid_of(project_id, "project_id")
    paths = {
        "statuses": f"/projects/nodes/{pid}/statuses",
        "types": f"/projects/nodes/{pid}/types",
        "fields": f"/projects/nodes/{pid}/fields",
        "tags": f"/projects/nodes/{pid}/tags",
    }
    return ((await get(paths[kind])) or {}).get("rows") or []


async def _root_of(pid: str, node: dict[str, Any]) -> dict[str, Any]:
    """The tree's root, walked up through ``parent_project_id``.

    Types, fields and tags are root-scoped, so the card names the root and
    not the node the member pointed at (S2b review). Capped, because a cycle
    is a database fault and not a reason to loop.
    """
    current = node
    for _ in range(8):
        parent = current.get("parent_project_id")
        if not parent:
            return current
        parent_id = uuid_of(str(parent), "parent_project_id")
        current = await get(f"/projects/nodes/{parent_id}")
    return current


def _tree_scope(root: dict[str, Any], node: dict[str, Any]) -> str:
    """``«Root» and every project in its tree`` for the card."""
    if root.get("id") == node.get("id"):
        return f"{data(root.get('name'))} and every project under it"
    return f"{data(root.get('name'))}, the root of {data(node.get('name'))}, and its whole tree"


def _local(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Root-local rows only. A root-local row may SHADOW an org-wide one of
    the same name (D-PM-16), so a duplicate check compares these alone."""
    return [r for r in rows if r.get("project_id") is not None]


def _org_wide(row: dict[str, Any]) -> bool:
    return row.get("project_id") is None


def _yes_no(value: str, what: str) -> bool | None:
    """``yes``/``no`` → bool, empty → None (leave it), anything else refuses."""
    v = str(value or "").strip().lower()
    if not v:
        return None
    if v in ("yes", "true", "y", "1"):
        return True
    if v in ("no", "false", "n", "0"):
        return False
    raise GatewayRefusal(f"{what} is yes or no.")


def _next_position(rows: list[dict[str, Any]]) -> int:
    taken = [int(r.get("position") or 0) for r in rows]
    return (max(taken) + 10) if taken else 10


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_status(project_id: str, name: str, category: str = "todo", color: str = "") -> str:
    """Add a status lane. category is backlog, todo, in_progress, done,
    cancelled or triage. The lane lands LAST in the set the project uses;
    the card names the project that owns that set, because a subproject
    may inherit its lanes from a parent. Deleting a lane is a guarded act."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    if not label:
        return "A status needs a name."
    cat = str(category or "todo").strip().lower()
    if cat not in STATUS_CATEGORIES:
        return f"category is one of {', '.join(STATUS_CATEGORIES)}."
    owner = (await get(f"/projects/nodes/{pid}/status-set")) or {}
    rows = await _vocab(pid, "statuses")
    if _matches_by_name(rows, label):
        return f"{data(label)} already exists here. The statuses are: {_names(rows)}."
    payload: dict[str, Any] = {"name": label, "category": cat, "position": _next_position(rows)}
    if color.strip():
        payload["color"] = color.strip()
    set_of = "this project" if owner.get("owns") else data(owner.get("owner_name"))
    if not await _confirm(
        title="Add this status?",
        detail=f"{data(label)} [{cat}] in {data(node.get('name'))}",
        context=_fields_block({**payload, "project": data(node.get("name")), "status set": set_of}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/statuses", payload)
    return (
        f"Added status {data(row.get('name') or label)} [{row.get('category') or cat}] "
        f"to {data(node.get('name'))}.\n  status_id: {row.get('id')}"
    )


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_status(
    project_id: str,
    status: str,
    name: str = "",
    category: str = "",
    color: str = "",
    position: int = -1,
) -> str:
    """Rename, recategorise, recolour or reorder one lane, found by NAME in
    the project's set. Only the arguments you pass change. A rename changes
    what every task in the lane reads. The card shows before → after."""
    pid, node = await _node(project_id)
    row = await _resolve_status(pid, status)
    sid = uuid_of(str(row.get("id")), "status_id")
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if name.strip():
        payload["name"] = name.strip()
        before["name"] = row.get("name")
    if category.strip():
        cat = category.strip().lower()
        if cat not in STATUS_CATEGORIES:
            return f"category is one of {', '.join(STATUS_CATEGORIES)}."
        payload["category"] = cat
        before["category"] = row.get("category")
    if color.strip():
        payload["color"] = color.strip()
        before["color"] = row.get("color")
    if position >= 0:
        payload["position"] = int(position)
        before["position"] = row.get("position")
    if not payload:
        return "Nothing to change. Pass at least one field."
    if not await _confirm(
        title="Change this status?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))}",
        context=_fields_block({**payload, "project": data(node.get("name"))}, before=before),
    ):
        return CANCELLED
    updated = await patch(f"/projects/statuses/{sid}", payload)
    return (
        f"Updated status {data(updated.get('name') or row.get('name'))} "
        f"[{updated.get('category') or row.get('category')}].\n  status_id: {sid}"
    )


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_type(
    project_id: str,
    name: str,
    icon: str = "",
    color: str = "",
    is_default: bool = False,
    is_epic: bool = False,
    org_wide: bool = False,
) -> str:
    """Add a task type to the project's root. is_default makes new tasks
    start as it. is_epic makes it a top level in the hierarchy rule.
    org_wide=true mints it for every project (needs the organization
    settings permission, and cannot be the default). Deleting a type is a
    guarded act; tasks keep existing, untyped."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    if not label:
        return "A task type needs a name."
    rows = await _vocab(pid, "types")
    if _matches_by_name(_local(rows), label):
        return f"{data(label)} already exists here. The types are: {_names(rows)}."
    root = await _root_of(pid, node)
    payload: dict[str, Any] = {"name": label}
    if icon.strip():
        payload["icon"] = icon.strip()
    if color.strip():
        payload["color"] = color.strip()
    if is_default and org_wide:
        # The route's rule (admin.create_type): the default is each project's
        # own choice. Refused here, so the card is not shown for a 422.
        return "An organization-wide type cannot be the default. Set one per project."
    if is_default:
        payload["is_default"] = True
    if is_epic:
        payload["is_epic"] = True
    if org_wide:
        payload["scope"] = "org"
    where = "every project (organization-wide)" if org_wide else _tree_scope(root, node)
    if not await _confirm(
        title="Add this task type?",
        detail=f"{data(label)} in {where}",
        context=_fields_block({**payload, "project": data(node.get("name")), "scope": where}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/types", payload)
    return f"Added type {data(row.get('name') or label)} to {where}.\n  type_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_type(
    project_id: str,
    type_name: str,
    name: str = "",
    icon: str = "",
    color: str = "",
    make_default: bool = False,
    epic: str = "",
) -> str:
    """Rename, re-icon, recolour a task type found by NAME, make it the
    project's default (make_default=true), or set epic to yes or no. Only
    the arguments you pass change. The Epic system type cannot be renamed
    or un-flagged; the route says so."""
    pid, node = await _node(project_id)
    rows = await _vocab(pid, "types")
    row = _one_named(rows, type_name, "type")
    tid = uuid_of(str(row.get("id")), "type_id")
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if name.strip():
        payload["name"] = name.strip()
        before["name"] = row.get("name")
    if icon.strip():
        payload["icon"] = icon.strip()
        before["icon"] = row.get("icon")
    if color.strip():
        payload["color"] = color.strip()
        before["color"] = row.get("color")
    demoted = ""
    if make_default:
        payload["is_default"] = True
        before["is_default"] = row.get("is_default")
        # `admin._clear_other_defaults` un-defaults the rest. The card names
        # the one that loses, because that is the other half of the act.
        current = [t for t in rows if t.get("is_default") and t.get("id") != row.get("id")]
        demoted = ", ".join(data(t.get("name")) for t in current)
    flag = _yes_no(epic, "epic")
    if flag is not None:
        payload["is_epic"] = flag
        before["is_epic"] = row.get("is_epic")
    if not payload:
        return "Nothing to change. Pass at least one field."
    if _org_wide(row) and set(payload) - {"name"}:
        # `refuse_org_wide_rescope` allows a rename only on an org-wide type.
        # Said here, before the card, not as a 422 after it.
        return (
            f"{data(row.get('name'))} is organization-wide. Only its name can change "
            "from here. Set an icon, colour or default on a project's own type."
        )
    scope = "organization-wide" if _org_wide(row) else data(node.get("name"))
    card: dict[str, Any] = dict(payload)
    if demoted:
        card["no longer the default"] = demoted
    if _org_wide(row):
        card["scope"] = "organization-wide — every project"
    if not await _confirm(
        title="Change this task type?",
        detail=f"{data(row.get('name'))} in {scope}",
        context=_fields_block({**card, "project": data(node.get("name"))}, before=before),
    ):
        return CANCELLED
    updated = await patch(f"/projects/types/{tid}", payload)
    return f"Updated type {data(updated.get('name') or row.get('name'))}.\n  type_id: {tid}"


def _field_of(rows: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """A field by its name OR its key. Every match, never the first."""
    target = str(wanted or "").strip().lower()
    found = [
        r
        for r in rows
        if target
        and (
            str(r.get("name") or "").strip().lower() == target
            or str(r.get("field_key") or "").strip().lower() == target
        )
    ]
    if len(found) == 1:
        return found[0]
    if not found:
        keys = ", ".join(
            f"{data(r.get('name'))} ({data(r.get('field_key'))})"
            for r in rows
            if r.get("field_key")
        )
        raise GatewayRefusal(
            f"No custom field is called {data(wanted)} here. The fields are: {keys or '(none)'}."
        )
    raise GatewayRefusal(f"{data(wanted)} matches more than one field. Pass its key.")


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_field(
    project_id: str,
    name: str,
    field_type: str = "text",
    options: str = "",
    description: str = "",
    required: bool = False,
    org_wide: bool = False,
) -> str:
    """Add a custom field to the project's root. field_type is text, number,
    date, select, multi_select, boolean or url. options is comma-separated,
    for select and multi_select. required=true means a task cannot MOVE
    into this project without a value. org_wide=true mints it for every
    project. The key is derived from the name and never changes."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    if not label:
        return "A custom field needs a name."
    kind = str(field_type or "text").strip().lower()
    if kind not in FIELD_TYPES:
        return f"field_type is one of {', '.join(FIELD_TYPES)}."
    choices = _split(options)
    if kind in ("select", "multi_select") and not choices:
        return f"A {kind} field needs options."
    if choices and kind not in ("select", "multi_select"):
        # `clean_options` drops them silently for any other type, and the
        # card would have listed them. Refused instead.
        return f"Options belong to a select or multi_select field, not {kind}."
    rows = await _vocab(pid, "fields")
    if _matches_by_name(_local(rows), label):
        return f"A field called {data(label)} already exists here."
    root = await _root_of(pid, node)
    payload: dict[str, Any] = {"name": label, "field_type": kind}
    if choices:
        payload["options"] = choices
    if description.strip():
        payload["description"] = description.strip()
    if org_wide:
        payload["scope"] = "org"
    where = "every project (organization-wide)" if org_wide else _tree_scope(root, node)
    card: dict[str, Any] = {**payload, "project": data(node.get("name")), "scope": where}
    if required:
        # The create route's INSERT carries no `required` column
        # (custom_fields.py `create_field`); only the PATCH sets it. The
        # card shows the flag, and the tool sets it right after the create,
        # under the one card, through `update_field`'s route (COMPOSITE).
        card["required"] = "a task cannot move into this project without a value"
    if not await _confirm(
        title="Add this custom field?",
        detail=f"{data(label)} ({kind}) in {where}" + (" · required" if required else ""),
        context=_fields_block(card),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/fields", payload)
    fid = uuid_of(str(row.get("id")), "field_id")
    if required:
        await patch(f"/projects/fields/{fid}", {"required": True})
    return (
        f"Added field {data(row.get('name') or label)} (key {data(row.get('field_key'))}, {kind}"
        f"{', required' if required else ''}) to {where}.\n  field_id: {fid}"
    )


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_field(
    project_id: str,
    field: str,
    name: str = "",
    description: str = "",
    options: str = "",
    required: str = "",
    field_type: str = "",
) -> str:
    """Change a custom field found by NAME or KEY: its label, description,
    options (comma-separated, REPLACES the list; an option still in use
    cannot be dropped), required (yes or no) or type (only while no task
    holds a value). The key never changes. The card shows before → after."""
    pid, node = await _node(project_id)
    row = _field_of(await _vocab(pid, "fields"), field)
    fid = uuid_of(str(row.get("id")), "field_id")
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if name.strip():
        payload["name"] = name.strip()
        before["name"] = row.get("name")
    if description.strip():
        payload["description"] = description.strip()
        before["description"] = row.get("description")
    if options.strip():
        payload["options"] = _split(options)
        before["options"] = row.get("options")
    flag = _yes_no(required, "required")
    if flag is not None:
        payload["required"] = flag
        before["required"] = row.get("required")
    if field_type.strip():
        kind = field_type.strip().lower()
        if kind not in FIELD_TYPES:
            return f"field_type is one of {', '.join(FIELD_TYPES)}."
        payload["field_type"] = kind
        before["field_type"] = row.get("field_type")
    if not payload:
        return "Nothing to change. Pass at least one field."
    scope = "organization-wide" if _org_wide(row) else data(node.get("name"))
    card: dict[str, Any] = dict(payload)
    if _org_wide(row):
        card["scope"] = "organization-wide — every project"
    if not await _confirm(
        title="Change this custom field?",
        detail=f"{data(row.get('name'))} (key {data(row.get('field_key'))}) in {scope}",
        context=_fields_block({**card, "project": data(node.get("name"))}, before=before),
    ):
        return CANCELLED
    updated = await patch(f"/projects/fields/{fid}", payload)
    return (
        f"Updated field {data(updated.get('name') or row.get('name'))} "
        f"(key {row.get('field_key')}).\n  field_id: {fid}"
    )


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_tag(
    project_id: str, name: str, color: str = "", description: str = "", org_wide: bool = False
) -> str:
    """Register a tag on the project's root, with a colour and a
    description. A tag typed onto a task registers itself; this is for
    naming one before it is used, or giving it a colour. org_wide=true
    mints it for every project. Deleting a tag is a guarded act."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    if not label:
        return "A tag needs a name."
    rows = await _vocab(pid, "tags")
    if _matches_by_name(_local(rows), label):
        return f"{data(label)} already exists here. The tags are: {_names(rows)}."
    root = await _root_of(pid, node)
    payload: dict[str, Any] = {"name": label}
    if color.strip():
        payload["color"] = color.strip()
    if description.strip():
        payload["description"] = description.strip()
    if org_wide:
        payload["scope"] = "org"
    where = "every project (organization-wide)" if org_wide else _tree_scope(root, node)
    if not await _confirm(
        title="Add this tag?",
        detail=f"{data(label)} in {where}",
        context=_fields_block({**payload, "project": data(node.get("name")), "scope": where}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/tags", payload)
    return f"Added tag {data(row.get('name') or label)} to {where}.\n  tag_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False)
async def update_tag(
    project_id: str, tag: str, name: str = "", color: str = "", description: str = ""
) -> str:
    """Rename, recolour or describe a tag found by NAME. A rename rewrites
    every task that wears the tag, and the card says how many. Renaming
    onto an existing tag is refused by the route; merging is a guarded act."""
    pid, node = await _node(project_id)
    row = _one_named(await _vocab(pid, "tags"), tag, "tag")
    gid = uuid_of(str(row.get("id")), "tag_id")
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if name.strip():
        payload["name"] = name.strip()
        before["name"] = row.get("name")
    if color.strip():
        payload["color"] = color.strip()
        before["color"] = row.get("color")
    if description.strip():
        payload["description"] = description.strip()
        before["description"] = row.get("description")
    if not payload:
        return "Nothing to change. Pass at least one field."
    worn = int(row.get("task_count") or 0)
    card: dict[str, Any] = dict(payload)
    size = ""
    if "name" in payload and _org_wide(row):
        # The list's count is scoped to THIS tree on purpose (tags.py
        # `list_tags`), and an org-wide rename rewrites every project's
        # tasks. No number the tool can read is the truth, so the card says
        # the scope and no count (S2b review).
        card = {"scope": "organization-wide — every project's tasks that wear it", **payload}
        size = " · organization-wide"
    elif "name" in payload:
        # The count FIRST on a rename: it is the size of the act. The route
        # rewrites every task that wears the tag (tags.py `_rewrite`).
        card = {"tasks renamed": worn, **payload}
        size = f" · renames {worn} task{'s' if worn != 1 else ''}"
    elif _org_wide(row):
        card["scope"] = "organization-wide — every project"
    scope = "organization-wide" if _org_wide(row) else data(node.get("name"))
    if not await _confirm(
        title="Change this tag?",
        detail=f"{data(row.get('name'))} in {scope}{size}",
        context=_fields_block({**card, "project": data(node.get("name"))}, before=before),
    ):
        return CANCELLED
    updated = await patch(f"/projects/tags/{gid}", payload)
    retagged = updated.get("retagged")
    tail = f" on {retagged} task{'s' if retagged != 1 else ''}" if "name" in payload else ""
    return f"Updated tag {data(updated.get('name') or row.get('name'))}{tail}.\n  tag_id: {gid}"


# ── A comment of the member's own ────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def edit_comment(task_id: str, comment_id: str, body: str) -> str:
    """Reword a comment the member wrote. comment_id comes from task_detail's
    timeline or from comment's receipt. Only the author can edit, and the
    tool checks that before the card. The card shows the old text and the
    new. Newly @mentioned people are notified; the rest are not re-pinged."""
    text_body = str(body or "").strip()
    if not text_body:
        return "A comment needs a body."
    tid, task = await _task(task_id)
    aid = uuid_of(comment_id, "comment_id")
    timeline = await get(
        f"/projects/tasks/{tid}/timeline", {"kind": "comments", "page": 1, "page_size": 50}
    )
    rows = (timeline or {}).get("rows") or []
    old = next((r for r in rows if str(r.get("id")) == aid and r.get("type") == "comment"), None)
    if old is None:
        return (
            f"No comment with id {aid} is in the latest 50 timeline rows of {_ref(task)}. "
            "task_detail lists them."
        )
    from skill_projects.client import current_user_email

    author = str(old.get("created_by") or "").lower()
    if author != current_user_email().lower():
        return f"Only the author can edit a comment. This one is by {data(author)}."
    if not await _confirm(
        title="Edit your comment?",
        detail=f"on {_ref(task)}",
        context=_fields_block({"body": text_body}, before={"body": old.get("body")}),
    ):
        return CANCELLED
    await patch(f"/projects/comments/{aid}", {"body": text_body})
    return f"Edited your comment on {_ref(task)} (comment id {aid}).\n  full_id: {tid}"


# ── The repeat rule ──────────────────────────────────────────────────────────


def _build_rule(
    freq: str,
    interval: int,
    weekdays: str,
    day_of_month: int,
    month_of_year: int,
    anchor: str,
    until: str,
    max_occurrences: int,
) -> dict[str, Any] | str:
    """The rule as the route takes it (``RecurrenceIn``), or the refusal.

    The same checks ``recurrence.validate_rule`` makes, made here so the
    member reads the reason before a card, not a 422 after one.
    """
    kind = str(freq or "").strip().lower()
    if kind not in FREQS:
        return f"freq is one of {', '.join(FREQS)}."
    every = int(interval or 1)
    if not 1 <= every <= 365:
        return "interval is 1 to 365."
    how = str(anchor or "due").strip().lower()
    if how not in ANCHORS:
        return f"anchor is {' or '.join(ANCHORS)}."
    rule: dict[str, Any] = {"freq": kind, "interval": every, "anchor": how}
    parts = _split(weekdays)
    if not all(p.isdigit() for p in parts):
        return "weekdays are numbers, 1 (Monday) to 7 (Sunday)."
    days = [int(p) for p in parts]
    if any(d < 1 or d > 7 for d in days):
        return "weekdays are 1 (Monday) to 7 (Sunday)."
    if kind == "weekly" and not days:
        return "A weekly rule needs weekdays, 1 (Monday) to 7 (Sunday)."
    if kind in ("monthly", "yearly") and not day_of_month:
        return f"A {kind} rule needs day_of_month."
    if days:
        rule["weekdays"] = days
    if day_of_month:
        rule["day_of_month"] = int(day_of_month)
    if month_of_year:
        rule["month_of_year"] = int(month_of_year)
    if until.strip():
        if len(until.strip()) != 10:
            return "until is a date, YYYY-MM-DD."
        rule["until_at"] = until.strip()
    if max_occurrences:
        rule["max_occurrences"] = int(max_occurrences)
    return rule


@_annotate(read_only=False, destructive=False, idempotent=True)
async def set_recurrence(
    task_id: str,
    freq: str = "",
    interval: int = 1,
    weekdays: str = "",
    day_of_month: int = 0,
    month_of_year: int = 0,
    anchor: str = "due",
    until: str = "",
    max_occurrences: int = 0,
    stop: bool = False,
) -> str:
    """Make a task repeat, change its cadence, or stop it (stop=true). freq
    is daily, weekly, monthly or yearly. interval is every N. weekdays is
    comma-separated 1 (Monday) to 7, required for weekly. day_of_month for
    monthly and yearly; month_of_year for yearly. anchor is due (keep the
    schedule) or completed (measure from when the last one was finished).
    until is YYYY-MM-DD. The card shows the current rule and the new one.
    Stopping keeps the task and every occurrence already made."""
    tid, task = await _task(task_id)
    current = ((await get(f"/projects/tasks/{tid}/recurrence")) or {}).get("rule")
    if stop:
        if not current:
            return f"{_ref(task)} does not repeat."
        if not await _confirm(
            title="Stop repeating this task?",
            detail=_ref(task),
            context=_fields_block(
                {"task": _ref(task), "rule": _rule_text(current), "after": "does not repeat"}
            ),
        ):
            return CANCELLED
        await delete(f"/projects/tasks/{tid}/recurrence")
        return f"{_ref(task)} no longer repeats. Existing occurrences stay.\n  full_id: {tid}"
    built = _build_rule(
        freq, interval, weekdays, day_of_month, month_of_year, anchor, until, max_occurrences
    )
    if isinstance(built, str):
        return built
    rule = built
    card = {"task": _ref(task), "rule": _rule_text(rule)}
    before = {"rule": _rule_text(current)} if current else None
    if not await _confirm(
        title="Repeat this task?" if not current else "Change how this task repeats?",
        detail=f"{_ref(task)} · {_rule_text(rule)}",
        context=_fields_block(card, before=before),
    ):
        return CANCELLED
    saved = (await put(f"/projects/tasks/{tid}/recurrence", rule)) or {}
    return f"{_ref(task)} now repeats {_rule_text(saved.get('rule') or rule)}.\n  full_id: {tid}"


# ── The member's own: a private capture, and the overlay ─────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def create_personal_task(
    title: str, notes: str = "", due: str = "", context: str = "", next_action: str = ""
) -> str:
    """Capture a PRIVATE task into the member's own personal project,
    assigned to them. Nobody else can see it. due is YYYY-MM-DD. context
    is a GTD context such as @office. To share it later, move_task it into
    a team project. Archive is the undo."""
    label = str(title or "").strip()
    if not label:
        return "A task needs a title."
    payload: dict[str, Any] = {"title": label}
    if notes.strip():
        payload["notes"] = notes.strip()
    if due.strip():
        if len(due.strip()) != 10:
            return "due is a date, YYYY-MM-DD."
        payload["due_at"] = due.strip()
    if context.strip():
        payload["context"] = context.strip()
    if next_action.strip():
        payload["next_action"] = next_action.strip()
    if not await _confirm(
        title="Capture this private task?",
        detail=data(label),
        context=_fields_block({**payload, "visible to": "you only"}),
    ):
        return CANCELLED
    row = await post("/projects/my/tasks", payload)
    return "\n".join(["Captured (private, yours):", *_task_line(row)])


@_annotate(read_only=False, destructive=False, idempotent=True)
async def set_my_overlay(
    task_id: str,
    disposition: str = "",
    context: str = "",
    energy: str = "",
    next_action: str = "",
    estimate_mins: int = 0,
    two_minute: str = "",
    clear: str = "",
) -> str:
    """Set the member's OWN triage of a task, which the team's board never
    sees: disposition (INBOX, NEXT, WAITING, SOMEDAY, PROJECT, REFERENCE,
    DONE, TRASH), context (@office), energy (low, medium, high),
    two_minute (yes or no). clear empties fields: context, energy,
    next_action. A DONE disposition does not complete the shared task;
    complete does. defer sets a date. The estimate is the TASK's, shared
    with the board since D76: set it with update_task, not here."""
    tid, task = await _task(task_id)
    unread = ""
    try:
        mine = await get(f"/projects/my/tasks/{tid}")
    except GatewayRefusal:
        # The lens lists what is assigned to the member or in their personal
        # project (personal.py `MY_TASKS_FROM`). The overlay route accepts
        # any VISIBLE task, so a triage may exist that this read cannot see.
        # The card must not claim "None →" for a value it never read.
        mine = {}
        unread = "not readable here — the task is not in your lens, so the card cannot show it"
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if disposition.strip():
        state = disposition.strip().upper()
        if state not in DISPOSITIONS:
            return f"disposition is one of {', '.join(DISPOSITIONS)}."
        payload["disposition"] = state
        before["disposition"] = mine.get("disposition")
    if context.strip():
        payload["context"] = context.strip()
        before["context"] = mine.get("context")
    if energy.strip():
        level = energy.strip().lower()
        if level not in ENERGIES:
            return f"energy is one of {', '.join(ENERGIES)}."
        payload["energy"] = level
        before["energy"] = mine.get("energy")
    if next_action.strip():
        payload["next_action"] = next_action.strip()
        before["next_action"] = mine.get("next_action")
    if _int_or_none(estimate_mins) is not None:
        # D76: one estimate, on the task. Refused by name rather than
        # dropped, so the model learns where it goes.
        return ("The estimate is the task's own since D76, shared with the "
                "board and People capacity. Set it with update_task "
                "(estimate_mins).")
    flag = _yes_no(two_minute, "two_minute")
    if flag is not None:
        payload["is_two_minute"] = flag
        before["is_two_minute"] = mine.get("is_two_minute")
    cleared: dict[str, Any] = {}
    for field in _split(clear):
        key = field.lower()
        if key not in ("context", "energy", "next_action"):
            return f"clear takes context, energy or next_action, not {data(field)}."
        cleared[key] = None
        before[key] = mine.get(key)
    payload = {**cleared, **payload}
    if not payload:
        return "Nothing to change. Pass at least one field."
    card: dict[str, Any] = {**payload, "scope": "your overlay only"}
    if unread:
        card["current triage"] = unread
    if not await _confirm(
        title="Update your triage of this task?",
        detail=_ref(task),
        context=_fields_block(card, before=None if unread else before),
    ):
        return CANCELLED
    row = await patch(f"/projects/tasks/{tid}/personal", payload)
    facts = [f"{k} {data(row.get(k))}" for k in payload if row.get(k) not in (None, "", False)]
    return (
        f"Your triage of {_ref(task)}: {' · '.join(facts) if facts else 'cleared'}."
        f"\n  full_id: {tid}"
    )


__all__ = [  # noqa: RUF022 — S2 first, then S2b, the way the spec lists them
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
    # S2b
    "create_field",
    "create_personal_task",
    "create_status",
    "create_tag",
    "create_type",
    "edit_comment",
    "set_my_overlay",
    "set_recurrence",
    "update_field",
    "update_status",
    "update_tag",
    "update_type",
]
