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

from acb_common.paging import bounds, page_line

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
    _access_lines,
    _confirm,
    _fields_block,
    _held_chat,
    _node,
    _priority_card,
    _resolve_assignee,
    _resolve_status,
    _split,
    _task,
    is_team_space,
    mailbox_line,
    space_refusal,
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
# WS-55 EA-13e: `agent` groups by the agent state of a row (EA3-43), as the board does.
GROUP_BY = ("status", "category", "assignee", "project", "importance", "tag", "agent", "none")
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
    """Who may see a project: its grants, each org, a `group:<slug>` or a
    member address, with its grant_id. grant_access adds a grant and
    revoke_access removes one, each behind a card (WS-64 OP-6b)."""
    pid, node = await _node(project_id)
    rows = ((await get(f"/projects/nodes/{pid}/grants")) or {}).get("rows") or []
    out = [legend(), f"Access to {data(node.get('name'))} ({len(rows)} grants):"]
    for row in rows:
        out.append(
            f"- {subject_words(str(row.get('subject') or ''))} ({data(row.get('subject'))})"
            f" · granted by {data(row.get('created_by') or '?')} · grant_id {row.get('id')}"
        )
    if not rows:
        out.append("(no grants of its own; visibility comes from its Center or the org)")
    return "\n".join(out)


@_annotate(read_only=True, idempotent=True, open_world=False)
async def project_authorities(project_id: str, include_removed: bool = False) -> str:
    """The Knowledge pages pinned to a project as its authority (the pages
    that hold its rules), each with its pin_id and its state: active or
    stale. A page the member cannot read shows no title. pin_authority pins a
    page and unpin_authority removes a pin, each behind a card.
    include_removed=true lists removed pins too."""
    pid, node = await _node(project_id)
    params = {"include_removed": True} if include_removed else None
    try:
        got = (await get(f"/projects/nodes/{pid}/authorities", params)) or {}
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 404:
            return "Authority pages are not on for this organization yet."
        raise
    rows = got.get("rows") or []
    out = [legend(), f"Authority pages of {data(node.get('name'))} ({len(rows)} pins):"]
    for row in rows:
        hidden = row.get("page_hidden") is True
        name = "a page hidden from you" if hidden else data(row.get("title"))
        state = str(row.get("state") or "?")
        if state == "stale" and row.get("stale_reason"):
            state = f"stale ({data(row.get('stale_reason'))})"
        out.append(
            f"- {name} · {state} · pinned by {data(row.get('pinned_by') or '?')}"
            f" · pin_id {row.get('id')} · page_id {row.get('page_id')}"
        )
    if not rows:
        out.append("(no page is pinned as its authority)")
    return "\n".join(out)


#: What the member reads while PROJECTS_TEMPLATES_ORGS is off for the
#: organization (WS-39 TPL-1a). The routes answer 404, and a tool makes no card.
TEMPLATES_OFF = "Project templates are not on for this organization yet. Nothing was changed."


def _template_line(row: dict[str, Any]) -> str:
    """One template as one line: the name, what it holds, and its id."""
    counts = row.get("counts") or {}
    line = (
        f"- {data(row.get('name'))} · {int(counts.get('tasks') or 0)} starter tasks"
        f" · {int(counts.get('statuses') or 0)} statuses"
        f" · {int(counts.get('types') or 0)} task types"
        f" · {int(counts.get('fields') or 0)} fields"
        f" · {int(counts.get('tags') or 0)} tags · template_id {row.get('id')}"
    )
    if row.get("description"):
        line += f"\n  {data(row.get('description'))}"
    return line


@_annotate(read_only=True, idempotent=True, open_world=False)
async def list_templates() -> str:
    """The project templates of the organization, by name (WS-39 TPL-1a).
    Each row has its template_id and what it holds: the statuses, task types,
    fields, tags and starter tasks. Every member of the organization sees
    the same list. create_project_from_template makes a new project from one,
    and save_as_template saves a project as a new one, each behind a card."""
    try:
        got = (await get("/projects/templates")) or {}
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 404:
            return TEMPLATES_OFF
        raise
    rows = got.get("rows") or []
    out = [legend(), f"Project templates ({len(rows)}):"]
    out.extend(_template_line(row) for row in rows)
    if not rows:
        out.append("(no template yet. save_as_template saves a project as one)")
    if got.get("more"):
        out.append("(more templates exist. This list shows the first ones by name)")
    return "\n".join(out)


#: What the member reads while DECISIONS_ORGS is off for the organization.
DECISIONS_OFF = "Decisions are not on for this organization yet."


@_annotate(read_only=True, idempotent=True, open_world=False)
async def my_decisions() -> str:
    """The questions that wait for the member's own answer: a question, two to
    four options, and the task each one is about. Each has a decision_id.
    answer_decision answers one, behind a card. The member sees only the
    decisions that name them."""
    try:
        rows = ((await get("/projects/decisions")) or {}).get("decisions") or []
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 404:
            return DECISIONS_OFF
        raise
    out = [legend(), f"Decisions that wait for you ({len(rows)}):"]
    for row in rows:
        options = " | ".join(
            f"{o.get('key')} = {data(o.get('label'))}" for o in row.get("options") or []
        )
        out.append(
            f"- {data(row.get('question'))} · options: {options}"
            f" · task_id {row.get('subject_id')} · decision_id {row.get('id')}"
            f" · expires {row.get('expires_at')}"
        )
    if not rows:
        out.append("(no decision waits for you)")
    return chr(10).join(out)


def subject_words(subject: str) -> str:
    """A grant subject in plain words, for a card, a receipt and a read."""
    if subject == "org":
        return "everyone in the organization"
    if subject.startswith("group:"):
        return f"every member of the group {data(subject[len('group:'):])}"
    return data(subject)


@_annotate(read_only=True, idempotent=True, open_world=False)
async def my_areas(include_archived: bool = False) -> str:
    """The member's own areas: the categories of their private tasks in My
    Tasks, such as Home or Finance, with how much open work each holds and
    its project_id. Read this before you file a private task under an area.
    include_archived=true lists archived areas too. Nobody else sees them.
    create_area, rename_area and delete_area change them."""
    params = {"include_archived": True} if include_archived else None
    rows = ((await get("/projects/my/areas", params)) or {}).get("rows") or []
    if not rows:
        return "You have no areas yet. create_area makes one."
    out = [legend(), f"My areas ({len(rows)}):"]
    for row in rows:
        tail = " · archived" if row.get("archived") else ""
        out.append(
            f"- {data(row.get('name'))} · {int(row.get('open_tasks') or 0)} open{tail}"
            f" · project_id {row.get('id')}"
        )
    return "\n".join(out)


AREA_NAME_MAX = 120


def _area_key(name: Any) -> str:
    """A name with case, spacing and punctuation removed. Two areas that
    share a key are near-equal, and the chat does not pick between them."""
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


async def _my_area(area: str) -> dict[str, Any]:
    """The one live area of the member that ``area`` means, or a refusal.

    ``area`` is an id from ``my_areas`` or an exact name. The route finds an
    area only among the member's own, so this list is the member's list. A name
    that is near-equal to another area ("Home" and "home!") is a question for
    the member, not a coin toss.
    """
    wanted = str(area or "").strip()
    if not wanted:
        raise GatewayRefusal("Name the area, or pass its project_id from my_areas.")
    rows = ((await get("/projects/my/areas")) or {}).get("rows") or []
    live = [r for r in rows if not r.get("archived")]
    try:
        aid = uuid_of(wanted, "area")
    except GatewayRefusal:
        aid = ""
    if aid:
        hit = next((r for r in live if str(r.get("id")) == aid), None)
        if hit is None:
            raise GatewayRefusal("You have no live area with that id. my_areas lists them.")
        return hit
    key = _area_key(wanted)
    near = [r for r in live if key and _area_key(r.get("name")) == key]
    if len(near) > 1:
        quoted = ", ".join(data(r.get("name")) for r in near)
        raise GatewayRefusal(
            f"{data(wanted)} matches more than one area ({quoted}). "
            "Ask the member which one they mean, then pass its project_id."
        )
    if not near:
        names = ", ".join(data(r.get("name")) for r in live) or "(none yet)"
        raise GatewayRefusal(f"No area is called {data(wanted)}. Your areas are: {names}.")
    return near[0]


def _clean_area_name(name: str) -> str:
    """The route's rule, said before the card: a name, at most 120 characters."""
    clean = " ".join(str(name or "").split())
    if not clean:
        raise GatewayRefusal("An area needs a name.")
    if len(clean) > AREA_NAME_MAX:
        raise GatewayRefusal(f"An area name is at most {AREA_NAME_MAX} characters.")
    return clean


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
async def create_area(name: str) -> str:
    """Make a new area: a category for the member's private tasks in My
    Tasks, such as Home or Finance. Nobody else sees it. The card shows the
    name. rename_area and delete_area change an area later."""
    clean = _clean_area_name(name)
    rows = ((await get("/projects/my/areas")) or {}).get("rows") or []
    key = _area_key(clean)
    twin = next((r for r in rows if not r.get("archived") and _area_key(r.get("name")) == key), None)
    if twin is not None:
        return (
            f"You already have an area called {data(twin.get('name'))}. "
            f"Use it, or choose a different name.\n  area_id: {twin.get('id')}"
        )
    if not await _confirm(
        title="Make this area?",
        detail=data(clean),
        context=_fields_block({"area": data(clean), "visible to": "you only"}),
    ):
        return CANCELLED
    row = (await post("/projects/my/areas", {"name": clean})) or {}
    return f"Made the area {data(row.get('name') or clean)}.\n  area_id: {row.get('id')}"


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
async def intake_queue(project_id: str = "", offset: int = 0) -> str:
    """The intake queue: captured tasks waiting for a decision (accept,
    decline, duplicate, snooze). project_id narrows it. triage_intake
    decides one. 50 a page: offset reads the next."""
    start, size = _page(offset)
    params: dict[str, Any] = {"page": start // size + 1, "page_size": size}
    if project_id.strip():
        params["project_id"] = uuid_of(project_id, "project_id")
    payload = await get("/projects/intake", params)
    rows = (payload or {}).get("rows") or []
    total = int((payload or {}).get("total") or len(rows))
    out = [legend(), f"Intake ({total} waiting):"]
    for row in rows:
        lines = _task_line(row)
        lines[0] += " · " + _intake_facts(row)
        out.extend(lines)
    if line := page_line(total, start, len(rows)):
        out.append(line)
    return "\n".join(out)


def _page(offset: Any) -> tuple[int, int]:
    """A page of ``MAX_BATCH`` on a page-numbered route. The offset rounds
    down to a page boundary, so the start the hint names is the real one."""
    start, size = bounds(offset, MAX_BATCH, default=MAX_BATCH)
    return start // size * size, size


@_annotate(read_only=True, idempotent=True, open_world=False)
async def notifications(unread_only: bool = True, offset: int = 0) -> str:
    """The member's notifications, newest first: mentions, assignments,
    comments on watched tasks. The bell counts are the server's.
    mark_notifications_read clears them. 50 a page: offset reads the next."""
    start, size = _page(offset)
    payload = await get(
        "/projects/notifications",
        {"unread_only": bool(unread_only), "page": start // size + 1,
         "page_size": size},
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
    # The route counts the unread rows only, so a full page is the one sign
    # that another exists. A wrong guess costs one empty page, never a row.
    if line := page_line(None, start, len(rows), more=len(rows) >= size):
        out.append(line)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
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
        detail=f"{data(label)} in {data(node.get('name'))}",
        context=_fields_block({**shown, "project": data(node.get("name"))}),
    ):
        return CANCELLED
    row = await post(f"/projects/nodes/{pid}/views", payload)
    return f"Saved view {data(row.get('name') or label)} [{kind}] in {data(node.get('name'))}.\n  view_id: {row.get('id')}"


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
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
    source: str = "",
    source_ref: str = "",
) -> str:
    """Capture a task into a project's intake queue for someone to triage,
    rather than straight onto its board. project_id empty captures into
    the member's own personal project, when they have one. The card shows
    the title, the priority and where it lands. Omit every priority argument
    to leave the task unjudged. source says where the capture came from, such
    as email, and source_ref is the reference there, such as the message id.
    For a mail, the card then says that the text leaves the mailbox."""
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
        stop = await space_refusal(pid, node)
        if stop:
            return stop
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
    # WS-46 P9 (G11): the route keeps both words on the capture. They are
    # free text there, so the card shows them whole.
    card: dict[str, Any] = {}
    origin = source.strip()[:80]
    reference = source_ref.strip()[:200]
    held = await _held_chat()
    if origin:
        payload["source"] = origin
    if held or origin.lower() == "email":
        # RX-1: a held chat says it from the server's answer, because a steered
        # model would leave out `source`.
        card["mailbox"] = mailbox_line(where)
    if held:
        card.update(await _access_lines(str(payload["project_id"])))
    if reference:
        payload["source_ref"] = reference
    if not await _confirm(
        title="Capture this into intake?",
        detail=f"{data(label)} → {where}",
        context=_fields_block(
            {
                **card,
                **_priority_card(payload),
                "lands in": f"the intake queue of {where}",
            }
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
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
        card["note"] = "marking a duplicate archives this task. Restore it from the archive to undo"
    elif verb == "snooze":
        when = str(until or "").strip()[:10]
        if len(when) != 10:
            return "snooze needs until, a date YYYY-MM-DD."
        body["until"] = when
        card["until"] = when
    else:
        card["note"] = "declining archives the task. Restore it from the archive to undo"
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


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False, card_write=True)
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


# ── WS-39 LP-1a: the updates of a project, with their health ─────────────────

#: The three values of the health of a project update, and their words. The
#: gateway's ``updates.HEALTH_LABELS`` holds the same, and a test pins them.
UPDATE_HEALTHS: dict[str, str] = {
    "on_track": "On track",
    "at_risk": "At risk",
    "off_track": "Off track",
}

#: The cap of the text, as the gateway's ``updates.MAX_UPDATE_CHARS``.
MAX_UPDATE_CHARS = 20_000

#: What the member reads while PROJECTS_UPDATES_ORGS is off for the organization.
UPDATES_OFF = "Project updates are not on for this organization yet."

#: The default page of ``list_project_updates``, and the route's cap.
UPDATES_PAGE = 5
UPDATES_PAGE_MAX = 50


def _health_key(value: str) -> str | None:
    """``on_track``, ``at_risk`` or ``off_track`` from the member's words, or None."""
    key = "_".join(str(value or "").strip().lower().replace("-", " ").split())
    return key if key in UPDATE_HEALTHS else None


def _not_a_project(node: dict[str, Any]) -> bool:
    """A team space or a folder holds no update. The gateway refuses it too."""
    return is_team_space(node) or str(node.get("kind") or "project") == "folder"


@_annotate(read_only=True, idempotent=True, open_world=False)
async def list_project_updates(project_id: str, limit: int = UPDATES_PAGE, before: str = "") -> str:
    """The updates of a project, newest first. Each has its health (On track,
    At risk or Off track), its text, the member who posted it, the time and
    its update_id. The first line is the newest update. To read older
    updates, pass the update_id of the last line as before.
    post_project_update posts a new update behind a card."""
    pid, node = await _node(project_id)
    params: dict[str, Any] = {"limit": max(1, min(int(limit or UPDATES_PAGE), UPDATES_PAGE_MAX))}
    if str(before or "").strip():
        params["before"] = uuid_of(before, "before")
    try:
        got = (await get(f"/projects/nodes/{pid}/updates", params)) or {}
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 404:
            return UPDATES_OFF
        raise
    rows = got.get("rows") or []
    out = [legend(), f"Updates of {data(node.get('name'))}, newest first ({len(rows)} shown):"]
    for row in rows:
        health = UPDATE_HEALTHS.get(str(row.get("health")), str(row.get("health")))
        out.append(
            f"- {health} · by {data(row.get('author') or '?')} · {row.get('posted_at')}"
            f" · update_id {row.get('id')}"
        )
        out.append(f"  text: {data(str(row.get('body') or '')[:1200])}")
    if got.get("next_before"):
        out.append(f"(older updates exist. Pass before={got['next_before']} to read them)")
    if not rows:
        out.append("(no update yet)")
    return "\n".join(out)


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False, card_write=True)
async def post_project_update(project_id: str, health: str, body: str) -> str:
    """Post an update on a project: its health and a short text, in the
    member's own words. health is on_track, at_risk or off_track. The member
    picks it, so ask the member when the words do not say it. The card shows
    the project, the health and the exact text. The newest update shows on
    the project, so a later update takes the place of this one there."""
    wanted = _health_key(health)
    if wanted is None:
        return (
            "health is on_track, at_risk or off_track. Ask the member which one "
            "fits, and post nothing until the member says."
        )
    text_body = str(body or "").strip()
    if not text_body or "\x00" in text_body:
        return "An update needs a text. Ask the member what the update says."
    if len(text_body) > MAX_UPDATE_CHARS:
        return (
            f"The text is {len(text_body)} characters, and an update holds at most "
            f"{MAX_UPDATE_CHARS}. Make it shorter."
        )
    pid, node = await _node(project_id)
    if _not_a_project(node):
        return (
            f"{data(node.get('name'))} is a space or a folder, and it has no project "
            "updates. Ask the member which project inside it the update is for."
        )
    # A filed project takes no new update. Say so before the card, as
    # `writes._file_destinations` does for a task.
    if node.get("archived_at") or node.get("archived"):
        return (
            f"{data(node.get('name'))} is archived, so it takes no new update. "
            "Restore it first, or post on another project."
        )
    # The read before the card: it also says when the feature is off, so the
    # member never answers a card that cannot post.
    try:
        await get(f"/projects/nodes/{pid}/updates", {"limit": 1})
    except GatewayRefusal as exc:
        if getattr(exc, "status", None) == 404:
            return UPDATES_OFF
        raise
    label = UPDATE_HEALTHS[wanted]
    if not await _confirm(
        title="Post this project update?",
        detail=f"on {data(node.get('name'))}",
        context=_fields_block(
            {"project": data(node.get("name")), "health": label, "body": text_body}
        ),
    ):
        return CANCELLED
    row = (await post(f"/projects/nodes/{pid}/updates", {"health": wanted, "body": text_body})) or {}
    return (
        f"Posted the update on {data(node.get('name'))}: {label}"
        f" (update id {row.get('id')}).\n  full_id: {pid}"
    )


__all__ = [  # noqa: RUF022 — reads first, then the writes
    "calendar",
    "intake_queue",
    "my_contexts",
    "my_led_projects",
    "notifications",
    "project_access",
    "project_authorities",
    "project_views",
    "watchers",
    "my_decisions",
    "list_project_updates",
    "capture_intake",
    "mark_notifications_read",
    "save_view",
    "triage_intake",
    "post_project_update",
]
