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

import json
from datetime import date, timedelta
from typing import Any

from skill_projects.client import GatewayRefusal, data, get, patch, post, uuid_of
from skill_projects.priority import (
    Removed,
    level_note,
    priority_fields,
    takes_priority,
)
from skill_projects.reads import _day, _task_line, legend
from skill_projects.writes import (
    CANCELLED,
    MAX_BATCH,
    _confirm,
    _fields_block,
    _node,
    _priority_card,
    _resolve_assignee,
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
#: What a view may group by, and how it draws a subtask: the server's
#: ``filters.GROUP_BY`` and ``filters.SUBTASK_MODES``. The skill cannot import
#: gateway code, so ``tests/unit/test_projects_project_fields.py`` holds these
#: copies equal to the route's (WS-46 P7).
GROUP_BY = ("status", "category", "assignee", "project", "importance", "tag", "none")
SUBTASK_MODES = ("nested", "separate", "hidden")
#: The status categories a view may filter on: the route's
#: ``filters.STATUS_CATEGORIES``, held equal by the same test.
STATUS_CATEGORIES = ("backlog", "todo", "in_progress", "done", "cancelled", "triage")
#: A view filter, in ``list_tasks``'s words -> the key the app stores. The
#: keys are the app's own (``grouping.ts`` ``toConfig``), so the app opens
#: a view the chat saved with every filter on.
VIEW_FILTERS = {
    "query": "q",
    "status_category": "status_category",
    "assignee": "assignee",
    "unassigned": "unassigned",
    "overdue": "overdue",
    "watching": "watching",
    "archived": "archived_only",
    "tags": "tags",
}
_VIEW_FLAGS = ("unassigned", "overdue", "watching", "archived")
INTAKE_ACTIONS = ("accept", "decline", "duplicate", "snooze")


# ── Reads ────────────────────────────────────────────────────────────────────


@_annotate(read_only=True, idempotent=True, open_world=False)
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


@_annotate(read_only=True, idempotent=True, open_world=False)
async def my_areas(include_archived: bool = False) -> str:
    """The member's own areas: the categories of their private tasks in My
    Tasks, such as Home or Finance, with how much open work each holds and
    its project_id. Read this before you file a private task under an area.
    include_archived=true lists archived areas too. Nobody else sees them,
    and the chat does not create, rename or delete an area."""
    params = {"include_archived": True} if include_archived else None
    rows = ((await get("/projects/my/areas", params)) or {}).get("rows") or []
    if not rows:
        return "You have no areas yet. A person makes one in My Tasks."
    out = [legend(), f"My areas ({len(rows)}):"]
    for row in rows:
        tail = " · archived" if row.get("archived") else ""
        out.append(
            f"- {data(row.get('name'))} · {int(row.get('open_tasks') or 0)} open{tail}"
            f" · project_id {row.get('id')}"
        )
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True, open_world=False)
async def project_views(project_id: str) -> str:
    """A project's saved views: name, type (list or board) and id. save_view
    adds or renames one; delete_view removes one."""
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    out = [legend(), f"Views of {data(node.get('name'))} ({len(rows)}):"]
    for row in rows:
        out.append(f"- {data(row.get('name'))} [{row.get('view_type')}] · view_id {row.get('id')}")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True, open_world=False)
async def calendar(
    start: str,
    end: str,
    project_id: str = "",
    mine: bool = False,
    include_subtree: bool = True,
) -> str:
    """Tasks on the calendar between two dates (YYYY-MM-DD). mine=true reads
    the member's own scheduled blocks (the Calendar app's read); otherwise
    every visible task whose schedule overlaps the window, narrowed by
    project_id. A project_id reads that node with its subprojects, as the
    app's calendar does. include_subtree=false reads the node alone. Dates
    are the server's."""
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
            # WS-46 P5. The route reads the named node ALONE by default, so a
            # space's calendar showed no subproject work. The app's calendar
            # sends include_subtree=true (page.tsx loadMonth), and so does
            # this read, unless the member asks for the node alone.
            params["include_subtree"] = bool(include_subtree)
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


@_annotate(read_only=True, idempotent=True, open_world=False)
async def my_contexts() -> str:
    """The GTD contexts the member uses on their own overlay (@office,
    @calls), with how many open tasks carry each."""
    rows = ((await get("/projects/my/contexts")) or {}).get("rows") or []
    out = [legend(), f"Your contexts ({len(rows)}):"]
    for row in rows:
        out.append(f"- {data(row.get('context'))} · {row.get('total', 0)} tasks")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True, open_world=False)
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


@_annotate(read_only=True, idempotent=True, open_world=False)
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


@_annotate(read_only=True, idempotent=True, open_world=False)
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


@_annotate(read_only=True, idempotent=True, open_world=False)
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


async def _view_filters(raw: str) -> dict[str, Any] | str:
    """*raw*, a JSON object in ``list_tasks``'s words, as the filters the app
    stores, or the refusal. A person is resolved by name, as on every card."""
    try:
        given = json.loads(raw)
    except ValueError:
        given = None
    if not isinstance(given, dict):
        return (
            'filters is a JSON object, for example {"overdue": true, "assignee": "Priya"}. '
            f"Its keys are {', '.join(VIEW_FILTERS)}."
        )
    unknown = sorted(set(given) - set(VIEW_FILTERS))
    if unknown:
        return f"A view filters on {', '.join(VIEW_FILTERS)}, not {', '.join(unknown)}."
    out: dict[str, Any] = {}
    for key, value in given.items():
        if key in _VIEW_FLAGS:
            if not isinstance(value, bool):
                return f"{key} is true or false."
            if value:
                out[VIEW_FILTERS[key]] = True
            continue
        text = ", ".join(value) if isinstance(value, list) else str(value or "").strip()
        if text:
            out[VIEW_FILTERS[key]] = await _filter_value(key, text)
    return out


async def _filter_value(key: str, text: str) -> str:
    """One text filter, as the app stores it. Raises the refusal."""
    if key == "status_category" and text not in STATUS_CATEGORIES:
        raise GatewayRefusal(
            f"status_category is one of {', '.join(STATUS_CATEGORIES)}, for one view."
        )
    if key == "assignee":
        if "," in text:
            raise GatewayRefusal("A view filters on one assignee. Save one view for each person.")
        return await _resolve_assignee(text, dispatch=False)
    if key == "query" and len(text) < 3 and not text.lstrip("#").isdigit():
        raise GatewayRefusal("query needs 3 characters, or a task number such as #7.")
    if key == "tags":
        return ",".join(_split(text))
    return text


def _view_config(
    filters: dict[str, Any] | None, group_by: str, subtasks: str, current: dict[str, Any]
) -> dict[str, Any] | str:
    """The view's ``config``: the saved one with the member's change on it,
    or the refusal. The route REPLACES ``config`` on a PATCH, so a change of
    one part keeps the rest (``views.py`` ``patch_view``)."""
    config = dict(current or {})
    config.setdefault("filters", {})
    config.setdefault("group_by", "status")
    if filters is not None:
        config["filters"] = filters
    axis = str(group_by or "").strip().lower()
    if axis:
        if axis not in GROUP_BY:
            return f"group_by is one of {', '.join(GROUP_BY)}."
        config["group_by"] = axis
        if config.get("sub_group_by") == axis:
            config.pop("sub_group_by")
    mode = str(subtasks or "").strip().lower()
    if mode:
        if mode not in SUBTASK_MODES:
            return f"subtasks is one of {', '.join(SUBTASK_MODES)}."
        config["subtasks"] = mode
    return config


def _config_card(config: dict[str, Any]) -> dict[str, Any]:
    """The view's settings as the card lines a member reads."""
    shown = config.get("filters") or {}
    lines: dict[str, Any] = {
        "filters": ", ".join(f"{k} {v}" for k, v in shown.items()) or "none (every task)",
        "group by": config.get("group_by") or "status",
    }
    if config.get("subtasks"):
        lines["subtasks"] = config["subtasks"]
    return lines


async def _change_view(
    pid: str,
    node: dict[str, Any],
    vid: str,
    label: str,
    change: tuple[dict[str, Any] | None, str, str] | None,
) -> str:
    """``save_view`` with a view_id: a new name, new settings, or both. The
    card shows each before and after."""
    pid = uuid_of(pid, "project_id")
    vid = uuid_of(vid, "view_id")
    rows = ((await get(f"/projects/nodes/{pid}/views")) or {}).get("rows") or []
    row = next((r for r in rows if str(r.get("id")) == vid), None)
    if row is None:
        return f"{data(node.get('name'))} has no view with that id. views lists them."
    if not label and change is None:
        return "Nothing to change. Pass a new name, filters, group_by or subtasks."
    body: dict[str, Any] = {}
    card: dict[str, Any] = {"project": data(node.get("name"))}
    before: dict[str, Any] = {}
    if label:
        body["name"] = card["name"] = label
        before["name"] = row.get("name")
    if change is not None:
        config = _view_config(*change, row.get("config") or {})
        if isinstance(config, str):
            return config
        body["config"] = config
        card.update(_config_card(config))
        before.update(_config_card(row.get("config") or {}))
    if not await _confirm(
        title="Change this view?" if change is not None else "Rename this view?",
        detail=f"{data(row.get('name'))} in {data(node.get('name'))}",
        context=_fields_block(card, before=before),
    ):
        return CANCELLED
    await patch(f"/projects/views/{vid}", body)
    return f"Saved view {data(label or row.get('name'))}.\n  view_id: {vid}"


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def save_view(
    project_id: str,
    name: str = "",
    view_type: str = "list",
    view_id: str = "",
    filters: str = "",
    group_by: str = "",
    subtasks: str = "",
) -> str:
    """Save a view on a project (name and type: list or board), or change
    one (pass view_id, and a new name or new settings). filters is a JSON
    object in list_tasks's words: query, status_category (one), assignee
    (one person), unassigned, overdue, watching, archived (true or false) and
    tags (comma-separated), for example {"overdue": true, "assignee": "Priya"}.
    A changed view takes the new filters as a whole. group_by is status,
    category, assignee, project, importance, tag or none. subtasks is
    nested, separate or hidden. The app opens the view with the same filters.
    The card names the project. delete_view is the guarded undo."""
    pid, node = await _node(project_id)
    label = str(name or "").strip()
    kind = str(view_type or "list").strip().lower()
    if kind not in VIEW_TYPES:
        return f"view_type is {' or '.join(VIEW_TYPES)}."
    wanted: dict[str, Any] | None = None
    if str(filters or "").strip():
        found = await _view_filters(filters)
        if isinstance(found, str):
            return found
        wanted = found
    configured = wanted is not None or bool(str(group_by or "").strip()) or bool(
        str(subtasks or "").strip()
    )
    if view_id.strip():
        change = (wanted, group_by, subtasks) if configured else None
        return await _change_view(pid, node, uuid_of(view_id, "view_id"), label, change)
    if not label:
        return "A view needs a name."
    payload: dict[str, Any] = {"name": label, "view_type": kind}
    if configured:
        config = _view_config(wanted, group_by, subtasks, {})
        if isinstance(config, str):
            return config
        payload["config"] = config
    shown = {k: v for k, v in payload.items() if k != "config"}
    if configured:
        shown.update(_config_card(payload["config"]))
    if not await _confirm(
        title="Save this view?",
        detail=f"{data(label)} [{kind}] in {data(node.get('name'))}",
        context=_fields_block({**shown, "project": data(node.get("name"))}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/views", payload)
    return f"Saved view {data(row.get('name') or label)} [{kind}] in {data(node.get('name'))}.\n  view_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
@takes_priority
async def capture_intake(
    title: str,
    project_id: str = "",
    description: str = "",
    due: str = "",
    priority: str = "",
    important: str = "",
    leveraged: str = "",
    importance: Removed = None,
) -> str:
    """Capture a task into a project's intake queue for someone to triage,
    rather than straight onto its board. project_id empty captures into
    the member's own personal project, when they have one. The card shows
    the title, the priority and where it lands. Omit every priority argument
    to leave the task unjudged."""
    label = str(title or "").strip()
    if not label:
        return "A task needs a title."
    # H-196: `IntakeIn` takes `leveraged`, and writes it the way create does.
    flags = priority_fields(
        priority=priority, important=important, leveraged=leveraged, importance=importance
    )
    if isinstance(flags, str):
        return flags
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
    payload.update(flags)
    if not await _confirm(
        title="Capture this into intake?",
        detail=f"{data(label)} → {where}",
        context=_fields_block(
            {**_priority_card(payload), "lands in": f"the intake queue of {where}"}
        ),
    ):
        return CANCELLED
    result = (await post("/projects/intake", payload)) or {}
    task = result.get("task") or {}
    return "\n".join(
        [
            f"Captured into the intake queue of {where}:",
            *_task_line(task),
            *level_note(priority, task),
        ]
    )


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
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
