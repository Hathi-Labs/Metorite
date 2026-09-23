"""The rest of the manifest — views, calendar, contexts, watchers, intake,
notifications, grants (S5).

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.1, §3.2, §10 (S5).

With this module every ``/projects`` route the manifest carries is either
built or excluded by name, and ``manifest.PLANNED`` is empty. The reads are
class A in the row convention the cards read. The four writes are class B
over ``writes._confirm``, the one card door: a saved view, an intake
capture, an intake decision, and clearing the bell. Each is undone by the
app's own next act (rename or delete the view, unarchive a declined task,
the bell refills).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from skill_projects.client import GatewayRefusal, data, get, patch, post, uuid_of
from skill_projects.reads import _day, _task_line, legend
from skill_projects.writes import (
    CANCELLED,
    MAX_BATCH,
    _confirm,
    _fields_block,
    _node,
    _resolve_status,
    _split,
    _task,
)

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


VIEW_TYPES = ("list", "board")
INTAKE_ACTIONS = ("accept", "decline", "duplicate", "snooze")


# ── Reads ────────────────────────────────────────────────────────────────────


@_annotate(read_only=True, idempotent=True)
async def project_access(project_id: str) -> str:
    """Who may see a project: its grants, each a member address or a
    `group:<slug>`. Reading only; a grant write is not on the chat surface
    (spec §12, question 2)."""
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/grants")) or {}).get("rows") or []
    out = [legend(), f"Access to {data(node.get('name'))} ({len(rows)} grants):"]
    for row in rows:
        out.append(
            f"- {data(row.get('subject'))} · granted by {data(row.get('created_by') or '?')}"
        )
    if not rows:
        out.append("(no grants of its own; visibility comes from its Center or the org)")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def project_views(project_id: str) -> str:
    """A project's saved views: name, type (list or board) and id. save_view
    adds or renames one; delete_view removes one."""
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    out = [legend(), f"Views of {data(node.get('name'))} ({len(rows)}):"]
    for row in rows:
        out.append(f"- {data(row.get('name'))} [{row.get('view_type')}] · view_id {row.get('id')}")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def calendar(start: str, end: str, project_id: str = "", mine: bool = False) -> str:
    """Tasks on the calendar between two dates (YYYY-MM-DD). mine=true reads
    the member's own scheduled blocks (the Calendar app's read); otherwise
    every visible task whose schedule overlaps the window, narrowed by
    project_id. Dates are the server's."""
    frm, to = str(start or "").strip()[:10], str(end or "").strip()[:10]
    try:
        first, last = date.fromisoformat(frm), date.fromisoformat(to)
    except ValueError:
        return "start and end are dates, YYYY-MM-DD."
    if last <= first:
        # The routes take a half-open window and refuse end <= start. "Today"
        # is one day, so the end moves to the next morning.
        to = (first + timedelta(days=1)).isoformat()
    if mine:
        payload = await get("/projects/my/calendar", {"start": frm, "end": to})
        title = f"My calendar {frm} to {to}"
    else:
        params: dict[str, Any] = {"from": frm, "to": to}
        if project_id.strip():
            params["project_id"] = uuid_of(project_id, "project_id")
        payload = await get("/projects/calendar", params)
        title = f"Calendar {frm} to {to}"
    rows = (payload or {}).get("rows") or []
    # `/projects/calendar` returns no `total`: a window is capped
    # (`truncated`), and the cap is what the member must hear about.
    capped = " · the window is capped; narrow the dates" if (payload or {}).get("truncated") else ""
    out = [legend(), f"{title} ({len(rows)} tasks{capped}):"]
    for row in rows:
        out.extend(_task_line(row))
        if not row.get("due_at") and row.get("start_date"):
            out[-2] += f" · starts {_day(row.get('start_date'))}"
        # A scheduled block lives on the member's overlay, so only the
        # `mine=true` rows carry it (`personal.py`); the company rows never do.
        block = row.get("scheduled_start") if mine else None
        if block:
            out[-2] += f" · block {str(block)[:16].replace('T', ' ')}"
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def my_contexts() -> str:
    """The GTD contexts the member uses on their own overlay (@office,
    @calls), with how many open tasks carry each."""
    rows = ((await get("/projects/my/contexts")) or {}).get("rows") or []
    out = [legend(), f"Your contexts ({len(rows)}):"]
    for row in rows:
        out.append(f"- {data(row.get('context'))} · {row.get('total', 0)} tasks")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def my_led_projects() -> str:
    """The projects the member LEADS, with how much open work each holds and
    the member's own open tasks in it first (WS-39 S6e). A project with no
    task assigned to the member still lists — leading it is the fact."""
    rows = ((await get("/projects/my/led")) or {}).get("rows") or []
    out = [legend(), f"Projects you lead ({len(rows)}):"]
    for row in rows:
        mine = row.get("my_tasks") or []
        out.append(
            f"- {data(row.get('name'))} · {row.get('open_tasks', 0)} open"
            f" · {len(mine)} assigned to you",
        )
        for task in mine:
            out.extend("  " + line for line in _task_line(task))
    if not rows:
        out.append("(none — nobody has named you lead of a project)")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def watchers(target_id: str, kind: str = "task") -> str:
    """Who watches a task or a project, and whether the member does. For a
    project, `inherited` means an ancestor's watch already covers it."""
    which = str(kind or "task").strip().lower()
    if which not in ("task", "project"):
        return "kind is task or project."
    if which == "task":
        tid, task = await _task(target_id)
        payload = await get(f"/projects/tasks/{tid}/watchers")
        head = f"Watchers of #{task.get('task_number')} {data(task.get('title'))}"
    else:
        pid, node = await _node(target_id)
        payload = await get(f"/projects/nodes/{pid}/watchers")
        head = f"Watchers of {data(node.get('name'))}"
    names = [data(w) for w in (payload or {}).get("watchers") or []]
    out = [legend(), f"{head} ({len(names)}): " + (", ".join(names) or "nobody")]
    out.append("You watch it." if (payload or {}).get("watching") else "You do not watch it.")
    if (payload or {}).get("inherited"):
        out.append("An ancestor's watch already covers it.")
    return "\n".join(out)


def _intake_facts(row: dict[str, Any]) -> str:
    wrapper = row.get("intake") or {}
    facts = [f"intake {wrapper.get('status') or '?'}"]
    if wrapper.get("source"):
        facts.append(f"from {data(wrapper.get('source'))}")
    if wrapper.get("snoozed_until"):
        facts.append(f"snoozed until {_day(wrapper.get('snoozed_until'))}")
    if wrapper.get("duplicate_of_task_id"):
        facts.append(f"duplicate of {wrapper.get('duplicate_of_task_id')}")
    return " · ".join(facts)


@_annotate(read_only=True, idempotent=True)
async def intake_queue(project_id: str = "") -> str:
    """The intake queue: captured tasks waiting for a decision (accept,
    decline, duplicate, snooze). project_id narrows it. triage_intake
    decides one."""
    params: dict[str, Any] = {"page": 1, "page_size": MAX_BATCH}
    if project_id.strip():
        params["project_id"] = uuid_of(project_id, "project_id")
    payload = await get("/projects/intake", params)
    rows = (payload or {}).get("rows") or []
    out = [legend(), f"Intake ({(payload or {}).get('total', len(rows))} waiting):"]
    for row in rows:
        lines = _task_line(row)
        lines[0] += " · " + _intake_facts(row)
        out.extend(lines)
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True)
async def notifications(unread_only: bool = True) -> str:
    """The member's notifications, newest first: mentions, assignments,
    comments on watched tasks. The bell counts are the server's.
    mark_notifications_read clears them."""
    payload = await get(
        "/projects/notifications",
        {"unread_only": bool(unread_only), "page": 1, "page_size": MAX_BATCH},
    )
    rows = (payload or {}).get("rows") or []
    unread = (payload or {}).get("unread") or {}
    out = [
        legend(),
        f"Notifications ({len(rows)} shown · unread {unread.get('total', 0)}, "
        f"mentions {unread.get('mentions', 0)}):",
    ]
    for row in rows:
        # The row grammar the list card parses: `- #<n> «title» · facts`,
        # then `full_id:`. A line that led with the date drew no rows.
        out.append(
            f"- #{row.get('task_number')} {data(row.get('task_title'))} · "
            f"{row.get('kind')} by {data(row.get('actor'))} · {_day(row.get('created_at'))}"
            + (f" · {data(row.get('excerpt'))}" if row.get("excerpt") else "")
            + f" · notification id {row.get('id')}"
        )
        out.append(f"  full_id: {row.get('task_id')}")
    return "\n".join(out)


# ── Writes (class B) ─────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False)
async def save_view(project_id: str, name: str, view_type: str = "list", view_id: str = "") -> str:
    """Save a view on a project (name and type: list or board), or rename
    one (pass view_id). The card names the project. delete_view is the
    guarded undo."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    if not label:
        return "A view needs a name."
    kind = str(view_type or "list").strip().lower()
    if kind not in VIEW_TYPES:
        return f"view_type is {' or '.join(VIEW_TYPES)}."
    if view_id.strip():
        vid = uuid_of(view_id, "view_id")
        rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
        row = next((r for r in rows if str(r.get("id")) == vid), None)
        if row is None:
            return f"{data(node.get('name'))} has no view with that id. views lists them."
        if not await _confirm(
            title="Rename this view?",
            detail=f"{data(row.get('name'))} → {data(label)} in {data(node.get('name'))}",
            context=_fields_block(
                {"name": label, "project": data(node.get("name"))}, before={"name": row.get("name")}
            ),
        ):
            return CANCELLED
        await patch(f"/projects/views/{vid}", {"name": label})
        return f"Renamed view to {data(label)}.\n  view_id: {vid}"
    payload: dict[str, Any] = {"name": label, "view_type": kind}
    if not await _confirm(
        title="Save this view?",
        detail=f"{data(label)} [{kind}] in {data(node.get('name'))}",
        context=_fields_block({**payload, "project": data(node.get("name"))}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/views", payload)
    return f"Saved view {data(row.get('name') or label)} [{kind}] in {data(node.get('name'))}.\n  view_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False)
async def capture_intake(
    title: str, project_id: str = "", description: str = "", due: str = "", importance: int = -1
) -> str:
    """Capture a task into a project's intake queue for someone to triage,
    rather than straight onto its board. project_id empty captures into
    the member's own personal project, when they have one. The card shows
    the title and where it lands."""
    label = str(title or "").strip()
    if not label:
        return "A task needs a title."
    payload: dict[str, Any] = {"title": label}
    if project_id.strip():
        pid, node = await _node(project_id)
        payload["project_id"] = pid
        where = data(node.get("name"))
    else:
        # The route refuses a capture with no project (intake.py, 422).
        # The member's own project is the one default the app has, and it
        # exists once they have captured anything (`/my/project`).
        try:
            mine = (await get("/projects/my/project")) or {}
        except GatewayRefusal:
            mine = {}
        if not mine.get("id"):
            return (
                "Intake needs a project, and you have no personal project yet. "
                "projects_tree lists the projects; create_personal_task makes yours."
            )
        payload["project_id"] = uuid_of(str(mine.get("id")), "project_id")
        where = f"{data(mine.get('name'))} (your personal project)"
    if description.strip():
        payload["description"] = description.strip()
    if due.strip():
        if len(due.strip()) != 10:
            return "due is a date, YYYY-MM-DD."
        payload["due_at"] = due.strip()
    if importance >= 0:
        if importance > 4:
            return "importance is 0 to 4."
        payload["importance"] = int(importance)
    if not await _confirm(
        title="Capture this into intake?",
        detail=f"{data(label)} → {where}",
        context=_fields_block({**payload, "lands in": f"the intake queue of {where}"}),
    ):
        return CANCELLED
    result = (await post("/projects/intake", payload)) or {}
    task = result.get("task") or {}
    return "\n".join([f"Captured into the intake queue of {where}:", *_task_line(task)])


@_annotate(read_only=False, destructive=False, idempotent=False)
async def triage_intake(
    task_id: str, action: str, status: str = "", duplicate_of: str = "", until: str = ""
) -> str:
    """Decide one intake task. action is accept (onto the board, in status
    by NAME or the default lane), decline (archives it; unarchive_task
    restores), duplicate (of duplicate_of, another task id; this archives
    it too), or snooze (until YYYY-MM-DD). The card names the task and the
    decision."""
    verb = str(action or "").strip().lower()
    if verb not in INTAKE_ACTIONS:
        return f"action is one of {', '.join(INTAKE_ACTIONS)}."
    tid, task = await _task(task_id)
    body: dict[str, Any] = {}
    card: dict[str, Any] = {
        "task": f"#{task.get('task_number')} {data(task.get('title'))}",
        "decision": verb,
    }
    if verb == "accept" and status.strip():
        row = await _resolve_status(str(task.get("project_id")), status)
        body["status_id"] = str(row.get("id"))
        card["status"] = data(row.get("name"))
    elif verb == "accept":
        card["status"] = "the project's first lane"
    elif verb == "duplicate":
        if not duplicate_of.strip():
            return "duplicate needs duplicate_of, the task it repeats."
        oid, other = await _task(duplicate_of)
        body["duplicate_of_task_id"] = oid
        card["duplicate of"] = f"#{other.get('task_number')} {data(other.get('title'))}"
        card["note"] = "marking a duplicate archives this task; unarchive_task restores it"
    elif verb == "snooze":
        when = str(until or "").strip()[:10]
        if len(when) != 10:
            return "snooze needs until, a date YYYY-MM-DD."
        body["until"] = when
        card["until"] = when
    else:
        card["note"] = "declining archives the task; unarchive_task restores it"
    titles = {
        "accept": "Accept this task onto the board?",
        "decline": "Decline this task?",
        "duplicate": "Mark this task a duplicate?",
        "snooze": "Snooze this task?",
    }
    if not await _confirm(title=titles[verb], detail=card["task"], context=_fields_block(card)):
        return CANCELLED
    # One literal path per verb: a path segment is an id from `uuid_of` or a
    # literal, never a value (the writes' path fence).
    paths = {
        "accept": f"/projects/intake/{tid}/accept",
        "decline": f"/projects/intake/{tid}/decline",
        "duplicate": f"/projects/intake/{tid}/duplicate",
        "snooze": f"/projects/intake/{tid}/snooze",
    }
    row = (await post(paths[verb], body)) or {}
    merged = {**task, **(row.get("task") if isinstance(row.get("task"), dict) else row or {})}
    return "\n".join([f"Intake: {verb}.", *_task_line(merged)])


@_annotate(read_only=False, destructive=False, idempotent=True)
async def mark_notifications_read(ids: str = "", all_unread: bool = False) -> str:
    """Clear the bell: mark the listed notification ids read
    (comma-separated, from notifications), or every unread one with
    all_unread=true. Idempotent, and only the member's own rows."""
    wanted = [uuid_of(i, "notification_id") for i in _split(ids)]
    if not wanted and not all_unread:
        return "Pass ids, or all_unread=true."
    body: dict[str, Any] = {"all": True} if all_unread else {"ids": wanted}
    if all_unread:
        bell = (
            await get("/projects/notifications", {"unread_only": True, "page": 1, "page_size": 1})
        ) or {}
        unread = int(((bell.get("unread") or {}).get("total")) or 0)
        if not unread:
            return "Nothing is unread."
        what = f"every unread notification ({unread})"
    else:
        what = f"{len(wanted)} notification{'s' if len(wanted) != 1 else ''}"
    if not await _confirm(
        title="Mark as read?",
        detail=what,
        context=_fields_block({"marks read": what, "scope": "your own notifications only"}),
    ):
        return CANCELLED
    result = (await post("/projects/notifications/read", body)) or {}
    marked = int(result.get("marked") or 0)
    if not marked:
        return "Nothing was marked; those notifications were not yours or were read already."
    # No row to open. `done:` is the receipt line the card reads for a write
    # that touches no single row (`classifyActionResult`, this tool only).
    return f"Marked {marked} read.\n  done: {marked} marked"


__all__ = [  # noqa: RUF022 — reads first, then the writes
    "calendar",
    "intake_queue",
    "my_contexts",
    "my_led_projects",
    "notifications",
    "project_access",
    "project_views",
    "watchers",
    "capture_intake",
    "mark_notifications_read",
    "save_view",
    "triage_intake",
]
