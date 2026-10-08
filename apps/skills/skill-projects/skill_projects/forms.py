"""Forms — edit a row from the chat, and plan a project from a goal (W1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.4 W1, §4.2 (S4).

Each tool here draws an EDITABLE card (``formCard`` or ``planCard`` with
``hitl: true``), INLINE in the transcript — the Projects rail mounts no side
panel host, so a ``surface: "panel"`` card would be a chip that opens
nothing (S4 review) — waits for the member to submit it, and then writes through
the class B tools' own routes under the one confirmation card those tools
use. So an edit from the chat is two gestures, and both are the member's:
the form says WHAT, the card says "yes, write it". The form is not consent
for the write; ``_confirm`` is, and it is the one door class B has.

The submit comes back as ONE string, ``"<label> — <json>"``
(``genUITemplates.tsx::FormCard``). ``_answer`` parses it and refuses
anything else, so a hostile or malformed submit writes nothing.

``manifest.COMPOSITE`` records what each tool reaches: ``edit_task`` writes
through ``update_task``'s route, ``edit_project`` through ``update_project``'s,
and ``propose_plan`` through ``create_project``, ``create_task`` (which
assigns through ``assign``) and ``link_tasks``. Before each card it reads
``POST /projects/plan/preview``, which writes nothing (S7d, §13.6).

``create_tasks`` (WS-46 P13) makes several new tasks in a project that
exists, under ONE confirmation card with one checkbox per task
(``request_confirmation`` rows). The member's one Approve names the ticked
tasks. It writes through ``create_task``'s own write path, one task at a time.

``create_tags`` and ``create_types`` (H-273) do the same for several new
words of the project's vocabulary. Each ticked row is one POST to the route
of ``create_tag`` or ``create_type``, and the server decides each one.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from acb_common import get_logger
from acb_common.priority import importance_for, important_from_importance

from skill_projects.client import GatewayRefusal, data, get, post, put, uuid_of
from skill_projects.priority import (
    card_view,
    flag,
    level_label,
    level_note,
    priority_fields,
)
from skill_projects.reads import WEEKDAYS, _number, _task_line
from skill_projects.views import _emit, _plain, _template
from skill_projects.writes import (
    _WRITE_FAILED,
    CANCELLED,
    CASCADE_DEFAULTS,
    MAX_BATCH,
    AgentAssigneeRefused,
    _confirm,
    _date_arg,
    _fields_block,
    _fits_on_card,
    _follow_new_task,
    _matches_by_name,
    _NewTask,
    _node,
    _post_new_task,
    _prepare_new_task,
    _resolve_assignee,
    _root_of,
    _split,
    _statuses_of,
    _subtask_counts,
    _subtasks_phrase,
    _task,
    _tree_scope,
    _unknown_addresses,
    _vocab,
    _yes_no,
    agent_assignee_refusal_as_text,
    update_project,
    update_task,
)

_log = get_logger("skill_projects.forms")

try:
    from acb_skills.tool_annotations import annotate as _annotate
except Exception:  # pragma: no cover — platform package absent in isolation

    def _annotate(**_hints):  # type: ignore[misc]
        def _wrap(fn):
            return fn

        return _wrap


NOT_SUBMITTED = "The form was not submitted, so nothing was changed."


def _answer(response: Any) -> dict[str, Any] | None:
    """The values a form submitted, or ``None``.

    The frontend sends ``"<label> — <json object>"``. Anything else — no
    response, prose, a list, a string that is not JSON — is not a submit.
    """
    if not isinstance(response, str) or " — " not in response:
        return None
    _, _, raw = response.partition(" — ")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def _ask(spec: dict[str, Any]) -> dict[str, Any] | None:
    result = await _emit({**spec, "hitl": True})
    if not result.get("ok"):
        raise GatewayRefusal(
            "The form could not be drawn (no chat surface to draw into). "
            "Pass the changes as arguments to update_task or update_project instead."
        )
    return _answer(result.get("response"))


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


# ── Edit a task ──────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def edit_task(task_id: str) -> str:
    """Open an editable form for a task in the chat: title, description,
    status (by name), due, start, the Important and Leveraged flags,
    estimate, tags. A task with open subtasks also gets a Subtasks choice,
    for a Done status (D-PM-38). The member
    edits and submits; the changed fields then go through update_task's
    confirmation card, before → after. Nothing is written until that card
    is approved. Assignees are changed with assign."""
    tid, task = await _task(task_id)
    statuses = await _statuses_of(str(task.get("project_id")))
    # WS-46 P6: read BEFORE the form, so the form asks the D-PM-38 question
    # itself. update_task would otherwise ask it after the submit, and the
    # form has no way to answer it.
    subtree = await _subtask_counts(tid)
    names = [str(s.get("name")) for s in statuses]
    current = next(
        (str(s.get("name")) for s in statuses if str(s.get("id")) == str(task.get("status_id"))),
        "",
    )
    fields = [
        {
            "name": "title",
            "label": "Title",
            "type": "text",
            "value": task.get("title") or "",
            "required": True,
        },
        {
            "name": "description",
            "label": "Description",
            "type": "textarea",
            "value": task.get("description") or "",
        },
        {"name": "status", "label": "Status", "type": "select", "options": names, "value": current},
        {"name": "due", "label": "Due", "type": "date", "value": (task.get("due_at") or "")[:10]},
        {
            "name": "start",
            "label": "Start",
            "type": "date",
            "value": (task.get("start_date") or "")[:10],
        },
        # D78 (H-173): the two stated inputs of the priority level. The due
        # date sets Urgent, so the form has no control for it.
        {
            "name": "important",
            "label": "Important",
            "type": "toggle",
            "value": bool(important_from_importance(task.get("importance"))),
        },
        {
            "name": "leveraged",
            "label": "Leveraged",
            "type": "toggle",
            "value": bool(task.get("leveraged")),
        },
        {
            "name": "estimate_mins",
            "label": "Estimate",
            "type": "number",
            "unit": "min",
            "value": task.get("estimate_mins") or 0,
        },
        {
            "name": "tags",
            "label": "Tags (comma-separated)",
            "type": "text",
            "value": ", ".join(str(t) for t in task.get("tags") or []),
        },
    ]
    if subtree.open:
        fields.append(_subtasks_field(subtree.open, subtree.capped))
    values = await _ask(
        _template(
            "formCard",
            {
                "title": f"Edit #{task.get('task_number')} {_plain(task.get('title'))}",
                "description": "Change what you need, then submit. A card confirms the write.",
                "submitLabel": "Review changes",
                "fields": fields,
            },
        )
    )
    if values is None:
        return NOT_SUBMITTED
    changes = _task_changes(task, values, current)
    if isinstance(changes, str):
        return changes
    if not changes:
        return f"Nothing changed on {_ref(task)}."
    cascade = _form_subtasks(values, changes, statuses, task) if subtree.open else {}
    if isinstance(cascade, str):
        return cascade
    changes.update(cascade)
    # `update_task` reads the row again, shows the before → after card, and
    # writes only on approval. The form was the member's wording; the card
    # is their consent.
    return await update_task(tid, **changes)


#: The two answers of the form's Subtasks choice (D-PM-38 decision 2).
ONLY_THIS_TASK = "Only this task"
COMPLETE_THEM_TOO = "Complete them too"


def _subtasks_field(open_count: int, capped: bool) -> dict[str, Any]:
    """The form's Subtasks choice. Its default is the app's
    (``CASCADE_DEFAULTS["complete"]``): only this task."""
    return {
        "name": "subtasks",
        "label": f"If you set a Done status: {_subtasks_phrase(open_count, 'complete', capped)}",
        "type": "select",
        "options": [ONLY_THIS_TASK, COMPLETE_THEM_TOO],
        "value": COMPLETE_THEM_TOO if CASCADE_DEFAULTS["complete"] else ONLY_THIS_TASK,
    }


def _form_subtasks(
    values: dict[str, Any],
    changes: dict[str, Any],
    statuses: list[dict[str, Any]],
    task: dict[str, Any],
) -> dict[str, str] | str:
    """``include_subtasks`` for ``update_task``, from the form's answer.

    It goes only with a status CHANGE into a Done lane, the one act where
    the route completes the subtasks too. Any other edit sends nothing.
    """
    wanted = str(changes.get("status") or "").strip().lower()
    lanes = [s for s in statuses if str(s.get("name") or "").strip().lower() == wanted]
    if len(lanes) != 1 or lanes[0].get("category") != "done":
        return {}
    if str(lanes[0].get("id")) == str(task.get("status_id")):
        return {}
    default = COMPLETE_THEM_TOO if CASCADE_DEFAULTS["complete"] else ONLY_THIS_TASK
    choice = _clean(values.get("subtasks")) or default
    if choice not in (ONLY_THIS_TASK, COMPLETE_THEM_TOO):
        return f"subtasks is {ONLY_THIS_TASK} or {COMPLETE_THEM_TOO}, not {data(choice)}."
    return {"include_subtasks": "yes" if choice == COMPLETE_THEM_TOO else "no"}


def _numeric_changes(
    task: dict[str, Any], values: dict[str, Any], changes: dict[str, Any], clear: list[str]
) -> str:
    """The priority flags and the estimate. A flag that differs from the task
    is sent as "true" or "false". An estimate of zero clears, a change sets.
    Returns the refusal, or an empty string."""
    try:
        est = int(values.get("estimate_mins") or 0)
    except (TypeError, ValueError):
        return "estimate_mins is a number of minutes."
    current = {
        "important": bool(important_from_importance(task.get("importance"))),
        "leveraged": bool(task.get("leveraged")),
    }
    for name, was in current.items():
        if name not in values:
            continue
        now = flag(values.get(name))
        if isinstance(now, str):
            return f"{name}: {now}"
        if bool(now) != was:
            changes[name] = "true" if now else "false"
    if est != int(task.get("estimate_mins") or 0):
        if est:
            changes["estimate_mins"] = est
        else:
            clear.append("estimate")
    return ""


def _task_changes(
    task: dict[str, Any], values: dict[str, Any], current_status: str
) -> dict[str, Any] | str:
    """The `update_task` arguments a submitted form implies, or a refusal.

    Only what differs from the row is sent, so the card shows the member's
    changes and nothing else. An emptied date, estimate or description
    becomes a `clear`. A flag the member turns off is sent as "false".
    """
    changes: dict[str, Any] = {}
    clear: list[str] = []
    title = _clean(values.get("title"))
    if title and title != _clean(task.get("title")):
        changes["title"] = title
    desc = str(values.get("description") or "").strip()
    if desc != str(task.get("description") or "").strip():
        if desc:
            changes["description"] = desc
        else:
            clear.append("description")
    status = _clean(values.get("status"))
    if status and status.lower() != current_status.lower():
        changes["status"] = status
    for key, field in (("due", "due_at"), ("start", "start_date")):
        new = _clean(values.get(key))[:10]
        old = str(task.get(field) or "")[:10]
        if new != old:
            if new:
                changes[key] = new
            else:
                clear.append(key)
    refused = _numeric_changes(task, values, changes, clear)
    if refused:
        return refused
    tags = [t.strip() for t in str(values.get("tags") or "").split(",") if t.strip()]
    if tags and tags != [str(t) for t in task.get("tags") or []]:
        # `update_task` REPLACES the list. An emptied field is left alone,
        # because an empty `tags` reads as "not passed" there.
        changes["tags"] = ", ".join(tags)
    if clear:
        changes["clear"] = ",".join(clear)
    return changes


def _ref(task: dict[str, Any]) -> str:
    number = task.get("task_number")
    return f"#{number} {data(task.get('title'))}" if number is not None else data(task.get("title"))


# ── Edit a project ───────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def edit_project(project_id: str) -> str:
    """Open an editable form for a space, folder or project: name,
    description, run state (active, paused, stopped), lead. The member
    edits and submits; the changes then go through update_project's
    confirmation card. Nothing is written until that card is approved."""
    pid, node = await _node(project_id)
    fields = [
        {
            "name": "name",
            "label": "Name",
            "type": "text",
            "value": node.get("name") or "",
            "required": True,
        },
        {
            "name": "description",
            "label": "Description",
            "type": "textarea",
            "value": node.get("description") or "",
        },
        {
            "name": "status",
            "label": "Run state",
            "type": "select",
            "options": ["active", "paused", "stopped"],
            "value": node.get("status") or "active",
        },
        {
            "name": "lead",
            "label": "Lead (email or name)",
            "type": "text",
            "value": node.get("lead") or "",
        },
    ]
    values = await _ask(
        _template(
            "formCard",
            {
                "title": f"Edit {_plain(node.get('name'))}",
                "description": "Change what you need, then submit. A card confirms the write.",
                "submitLabel": "Review changes",
                "fields": fields,
            },
        )
    )
    if values is None:
        return NOT_SUBMITTED
    changes: dict[str, Any] = {}
    name = _clean(values.get("name"))
    if name and name != _clean(node.get("name")):
        changes["name"] = name
    desc = str(values.get("description") or "").strip()
    if desc and desc != str(node.get("description") or "").strip():
        changes["description"] = desc
    state = _clean(values.get("status")).lower()
    if state and state != str(node.get("status") or "active"):
        changes["status"] = state
    lead = _clean(values.get("lead"))
    if lead and lead.lower() != str(node.get("lead") or "").lower():
        changes["lead"] = lead
    if not changes:
        return f"Nothing changed on {data(node.get('name'))}."
    return await update_project(pid, **changes)


# ── W1 · plan a project from a goal ──────────────────────────────────────────
#
# S7d (spec §13.6) added start dates, dependencies and the capacity preview.
# There are no phases (owner, 2026-09-24): start dates and `blocks` links
# carry the order of a plan, and the Timeline draws it.

PLAN_FIELDS = ("title", "owner", "effort_mins", "due")
#: The three sort scores. They travel through the submit, so the score after
#: the submit is the score the card showed (§13.6 rule 10).
SCORE_KEYS = ("impact", "urgency", "effort")
#: A row key is the model's label for a row, for example ``t1``. The preview
#: route holds the same alphabet (``plan_preview._KEY``).
_KEY = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
PREVIEW_PATH = "/projects/plan/preview"
#: The longest effort one row may carry: 90 days of minutes. The preview route
#: holds the same bound (``plan_preview.MAX_EFFORT_MINS``).
MAX_EFFORT_MINS = 90 * 24 * 60
#: A write whose connection broke may or may not have landed. Both kinds end
#: the batch with the same receipt (§13.6 rule 9, review round 1). The one
#: pair lives in ``writes.py``, which ``create_task`` uses too (WS-46 P1).

#: The words a mark carries on the card (§13.6 rules 1 and 2).
MARK_WORDS = {
    "no_skill_match": "no skill match",
    "short_of_hours": "short of hours",
    "not_in_directory": "not in the directory",
}
#: §13.6 rule 3. A mark and a warning inform. They never block.
WARNS_NOT_BLOCKS = "Marks and warnings do not block the plan. You can still create it."


def _after_of(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, list):
        parts = [str(v) for v in value]
    else:
        return ["?"]
    return [p.strip() for p in parts if p.strip()]


def _plan_row(i: int, item: Any) -> dict[str, Any] | str:
    """One validated row, or the refusal that names it."""
    if not isinstance(item, dict):
        return f"Task {i} is not an object."
    missing = [f for f in PLAN_FIELDS if not _clean(item.get(f))]
    if missing:
        return (
            f"Task {i} ({data(item.get('title') or '?')}) lacks {', '.join(missing)}. "
            "Every task needs all four."
        )
    try:
        effort = int(item.get("effort_mins"))
    except (TypeError, ValueError):
        return f"Task {i}: effort_mins is a number of minutes."
    if not 0 <= effort <= MAX_EFFORT_MINS:
        # The preview route's own bound (`plan_preview.MAX_EFFORT_MINS`).
        # Outside it the preview says 422 and the whole card loses capacity.
        return (
            f"Task {i} ({data(item.get('title'))}): effort_mins is 0 to {MAX_EFFORT_MINS} "
            "minutes. Nothing was created."
        )
    due = _clean(item.get("due"))[:10]
    try:
        due_on = date.fromisoformat(due)
    except ValueError:
        return f"Task {i}: due is a date, YYYY-MM-DD, not {data(item.get('due'))}."
    key = _clean(item.get("key")) or f"t{i}"
    if not _KEY.match(key):
        return f"Task {i}: key is 1 to 40 letters, digits, dots, dashes or underscores."
    row: dict[str, Any] = {
        "key": key,
        "title": _clean(item.get("title")),
        "owner": _clean(item.get("owner")),
        "effort_mins": effort,
        "due": due,
        "after": _after_of(item.get("after")),
    }
    start = _clean(item.get("start"))[:10]
    if start:
        try:
            start_on = date.fromisoformat(start)
        except ValueError:
            return f"Task {i}: start is a date, YYYY-MM-DD, not {data(item.get('start'))}."
        if start_on > due_on:
            return (
                f"Task {i} ({data(row['title'])}) starts on {start}, after its due date {due}. "
                "Nothing was created."
            )
        row["start"] = start
    # D78 (H-173): the row's priority is the level's two stated flags. The
    # level name rides in `level`, because `priority` is this row's score
    # below, and the card sends the score back on submit. A `priority` that
    # is TEXT is a level name the model sent the way every other tool takes
    # it, so it is read as one, never dropped (review 2026-09-28).
    spoken = item.get("priority")
    level = item.get("level") or (
        spoken if isinstance(spoken, str) and not spoken.strip().isdigit() else ""
    )
    # H-196: the 0-4 number is gone. A row that still sends it is refused,
    # never read as no priority.
    flags = priority_fields(
        priority=str(level or ""),
        important=item.get("important", ""),
        leveraged=item.get("leveraged", ""),
        importance=item.get("importance"),
    )
    if isinstance(flags, str):
        return f"Task {i}: {flags}"
    if "importance" in flags:
        row["important"] = bool(important_from_importance(flags["importance"]))
    if "leveraged" in flags:
        row["leveraged"] = flags["leveraged"]
    score = 1
    for name in SCORE_KEYS:
        try:
            value = max(1, min(5, int(item.get(name) or 3)))
        except (TypeError, ValueError):
            value = 3
        row[name] = value
        score *= value
    # A sorting aid, never stored (spec §3.4 W1 step 4).
    row["priority"] = score
    return row


def _block_cycle(rows: list[dict[str, Any]]) -> list[str]:
    """The keys caught in a ``blocks`` cycle, or ``[]`` (§13.6 rule 7).

    Kahn's topological sort: remove every row with no blocker left, until
    none remain. What cannot be removed is on a cycle, or waits on one.
    """
    waiting = {r["key"]: set(r["after"]) for r in rows}
    ready = [k for k, before in waiting.items() if not before]
    while ready:
        done = ready.pop()
        del waiting[done]
        for key, before in waiting.items():
            if done in before:
                before.discard(done)
                if not before and key not in ready:
                    ready.append(key)
    return sorted(waiting)


def _plan_rows(raw: Any, *, dropped: frozenset[str] = frozenset()) -> list[dict[str, Any]] | str:
    """The plan's task rows, validated, or the refusal string.

    W1 rule: a task that lacks a title, an owner, an effort or a date is not
    proposed. §13.6 rule 7 refuses, before any card and with zero writes, a
    start date after the due date, an ``after`` key that names no row, a row
    that blocks itself, and a ``blocks`` cycle. ``dropped`` holds the keys of
    rows the member removed on the card: their links drop with them.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return (
                "tasks is a JSON list of {key, title, owner, effort_mins, start?, due, "
                "after?, level?, important?, leveraged?}."
            )
    if not isinstance(raw, list) or not raw:
        return "tasks is a JSON list with at least one task."
    if len(raw) > MAX_BATCH:
        return f"That is {len(raw)} tasks. The limit for one card is {MAX_BATCH}."
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(raw, start=1):
        row = _plan_row(i, item)
        if isinstance(row, str):
            return row
        rows.append(row)
    keys = [r["key"] for r in rows]
    if len(set(keys)) != len(keys):
        return "Two tasks carry the same key. Give every task its own key."
    known = set(keys)
    for row in rows:
        row["after"] = [k for k in row["after"] if k not in dropped or k in known]
        for blocker in row["after"]:
            if blocker == row["key"]:
                return f"Task {data(row['title'])} cannot block itself. Nothing was created."
            if blocker not in known:
                return (
                    f"Task {data(row['title'])} waits on {data(blocker)}, and no task has "
                    "that key. Nothing was created."
                )
    cycle = _block_cycle(rows)
    if cycle:
        return (
            "These tasks block each other in a circle, so none of them could start: "
            + ", ".join(data(k) for k in cycle)
            + ". Nothing was created."
        )
    rows.sort(key=lambda r: -int(r["priority"]))
    return rows


def _links(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Every ``blocks`` link a plan implies, as ``(blocker key, blocked key)``."""
    return [(a, r["key"]) for r in rows for a in r["after"]]


async def _soft_owner(value: str) -> tuple[str, str]:
    """The owner's address before the card, and why it did not resolve.

    Before the card a name that does not resolve is not a refusal: the member
    can correct it on the card. After the submit it is (``_resolve_assignee``).
    """
    try:
        return await _resolve_assignee(value), ""
    except AgentAssigneeRefused:
        raise  # H-236: refused before the card, never noted on it
    except GatewayRefusal as exc:
        return value.strip().lower(), str(exc)


async def _preview(rows: list[dict[str, Any]], owners: dict[str, str]) -> dict[str, Any] | None:
    """``POST /projects/plan/preview``: fit, hours and dependency warnings.

    A read (``READ_ONLY_POSTS``). A refusal is not fatal: the plan card then
    says that capacity could not be checked, and nothing else changes.
    """
    body = {
        "rows": [
            {
                "key": r["key"],
                "title": r["title"],
                "owner": owners.get(r["owner"], r["owner"]),
                "effort_mins": r["effort_mins"],
                "start": r.get("start"),
                "due": r["due"],
                "after": r["after"],
            }
            for r in rows
        ]
    }
    try:
        answer = await post(PREVIEW_PATH, body)
    except (GatewayRefusal, httpx.TransportError):
        # A refused or unreachable read: capacity is not checked, and the
        # plan goes on (§13.6 As built, review round 1).
        return None
    return answer if isinstance(answer, dict) else None


def _hours_text(hours: dict[str, Any], due: str) -> str:
    if not hours.get("basis"):
        return str(hours.get("note") or "no hours")
    spare = hours.get("spare_hours")
    tail = f", {spare} h spare before the plan" if spare is not None else ""
    if hours.get("fits") is False:
        text_ = (
            f"short {hours.get('shortfall_hours')} h by {due}: needs "
            f"{hours.get('needed_hours')} h, has {hours.get('available_hours')} h"
        )
    elif hours.get("fits") is True:
        text_ = "fits" + tail
    else:
        text_ = str(hours.get("note") or "not checked") + tail
    if hours.get("estimate_note"):
        text_ += f". {hours['estimate_note']}"
    return text_


def _capacity(
    rows: list[dict[str, Any]], answer: dict[str, Any] | None, unresolved: dict[str, str]
) -> tuple[dict[str, dict[str, Any]], list[str], str]:
    """Per row: the display fit, hours and marks. Then the dependency
    warnings as sentences, and the one capacity line for the card."""
    titles = {r["key"]: r["title"] for r in rows}
    due_of = {r["key"]: r["due"] for r in rows}
    # A name that did not resolve is picker data every member reads, not HR
    # data. So the mark shows with or without the grant, and without a
    # preview (review round 1, P2a). The member fixes it before the submit.
    by_key: dict[str, dict[str, Any]] = {
        r["key"]: {"marks": ["owner not resolved"], "warnings": [unresolved[r["owner"]]]}
        for r in rows if r["owner"] in unresolved
    }
    if answer is None:
        return by_key, [], "Capacity could not be checked, so no row carries a fit or hours."
    warnings = [
        f"{data(titles.get(w.get('blocked'), '?'))} starts on {w.get('blocked_starts')}, but "
        f"{data(titles.get(w.get('blocker'), '?'))}, which blocks it, runs until "
        f"{w.get('blocker_ends')}. Both dates stay as planned."
        for w in answer.get("dependency_warnings") or []
    ]
    if not answer.get("hr_visible"):
        # §13.6 rule 4: no fit, no hours, no mark, and one line.
        return by_key, warnings, str(answer.get("hr_note") or "An admin can see capacity.")
    for got in answer.get("rows") or []:
        key = str(got.get("key") or "")
        if key not in titles:
            continue
        fit = got.get("fit") or {}
        marks = [MARK_WORDS.get(str(m), str(m)) for m in got.get("marks") or []]
        fit_text = str(fit.get("text") or "")
        if key in by_key:
            # Resolution failed, so the preview measured a name, not a person.
            by_key[key]["fit"] = "owner not resolved: give an address"
            continue
        by_key[key] = {
            "fit": fit_text,
            "hours": _hours_text(got.get("hours") or {}, due_of[key]),
            "marks": marks,
            "warnings": [str(w) for w in fit.get("warnings") or []],
        }
    window = answer.get("window") or {}
    line = f"Hours cover {window.get('starts_on')} to {window.get('ends_on')}, across all your visible work."
    return by_key, warnings, line


def _card_rows(rows: list[dict[str, Any]], checks: dict[str, dict[str, Any]]) -> list[dict]:
    """The plan card's rows: the editable fields, and the read-only checks."""
    out: list[dict[str, Any]] = []
    for r in rows:
        row = {k: v for k, v in r.items()}
        row.update(checks.get(r["key"], {}))
        out.append(row)
    return out


def _row_flags(row: dict[str, Any]) -> dict[str, Any]:
    """The priority fields a plan row POSTs: ``importance`` and ``leveraged``,
    each only when the row states it."""
    out: dict[str, Any] = {}
    if row.get("important") is not None:
        out["importance"] = importance_for(bool(row["important"]))
    if row.get("leveraged") is not None:
        out["leveraged"] = bool(row["leveraged"])
    return out


def _task_card_line(row: dict[str, Any], owner: str) -> str:
    # Every field the POST carries is on this line (spec §5.3). The priority
    # shows as the flags and the level the app will draw (H-173).
    flags = _row_flags(row)
    shown = card_view(flags)
    return (
        f"{data(row['title'])} · {data(owner)} · {row['effort_mins']} min · "
        + (f"start {row['start']} · " if row.get("start") else "")
        + f"due {row['due']}"
        + "".join(f" · {k} {v}" for k, v in shown.items())
        + (f" · priority {level_label({**flags, 'due_at': row['due']})}" if flags else "")
    )


def _check_line(check: dict[str, Any]) -> str:
    parts = [*check.get("marks", []), *check.get("warnings", [])]
    if "short" in str(check.get("hours", "")):
        parts.append(str(check["hours"]))
    return " · ".join(parts)


def _stopped(
    out: list[str],
    what: str,
    exc: Exception,
    *,
    tasks_left: int,
    owners: tuple[int, int],
    links: tuple[int, int],
) -> str:
    """The partial receipt (§13.6 rule 9). It lists what exists, names the
    row that failed and counts what was not tried. It archives nothing."""
    if isinstance(exc, httpx.TransportError):
        # A broken connection says nothing about the write it carried.
        out.append(
            f"stopped: {what} lost its connection to the gateway "
            f"({type(exc).__name__}). That write may or may not have landed. "
            "Read the project before you try again."
        )
    else:
        out.append(f"stopped: {what} was refused. {exc}")
    out.append(_not_tried(tasks_left, owners, links))
    out.append(
        "Nothing was archived. Archive the project to remove what exists, "
        "or finish the plan by hand."
    )
    return "\n".join(out)


def _not_tried(tasks_left: int, owners: tuple[int, int], links: tuple[int, int]) -> str:
    return (
        f"not tried: {tasks_left} task{'s' if tasks_left != 1 else ''} · "
        f"{owners[0]} of {owners[1]} owners assigned · {links[0]} of {links[1]} links written"
    )


def _project_stopped(label: str, exc: Exception, *, tasks: int, links: int) -> str:
    """The receipt when the FIRST write, the project node, fails (WS-27bm S10,
    §16.2 rule 9). No other write runs, so every task, owner and link is not
    tried. A refusal made nothing. A broken connection may have made the
    project, so the member reads the tree before a retry."""
    left = _not_tried(tasks, (0, tasks), (0, links))
    if isinstance(exc, httpx.TransportError):
        return "\n".join([
            f"stopped: the project {data(label)} lost its connection to the gateway "
            f"({type(exc).__name__}). That write may or may not have landed.",
            left,
            "Read the project tree before you try again, or the retry can make "
            "a second project.",
        ])
    return "\n".join([
        f"stopped: the project {data(label)} was refused. {exc}",
        left,
        "Nothing was created.",
    ])


async def _soft_owners(rows: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """Each owner as written, to an address, and the ones that did not resolve."""
    soft: dict[str, str] = {}
    unresolved: dict[str, str] = {}
    for row in rows:
        if row["owner"] not in soft:
            soft[row["owner"]], why = await _soft_owner(row["owner"])
            if why:
                unresolved[row["owner"]] = why
    return soft, unresolved


def _submitted_rows(submitted: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]] | str:
    """The rows the member submitted. A row they dropped takes its links
    with it (§13.6 rule 6), so its key is not an unknown key."""
    sent: set[str] = set()
    if isinstance(submitted, list):
        sent = {_clean(t.get("key")) for t in submitted if isinstance(t, dict)}
    dropped = frozenset(r["key"] for r in rows if r["key"] not in sent)
    return _plan_rows(submitted, dropped=dropped)


def _confirm_card(
    *,
    label: str,
    parent_label: str,
    about: str,
    final: list[dict[str, Any]],
    owners: dict[str, str],
    checks: dict[str, dict[str, Any]],
    titles: dict[str, str],
    links: list[tuple[str, str]],
    dropped_links: list[tuple[str, str]],
    extra: dict[str, str],
) -> dict[str, Any]:
    """The confirm card's body: every task, every check, every link."""
    card: dict[str, Any] = {"project": data(label), "under": parent_label}
    if about:
        card["description"] = about
    for i, row in enumerate(final, start=1):
        card[f"task {i}"] = _task_card_line(row, owners[row["owner"]])
        check = _check_line(checks.get(row["key"], {}))
        if check:
            card[f"task {i} check"] = check

    def said(pairs: list[tuple[str, str]]) -> str:
        return ", ".join(f"{data(titles[a])} blocks {data(titles[b])}" for a, b in pairs)

    if links:
        card["links"] = said(links)
    if dropped_links:
        card["dropped links"] = said(dropped_links)
    card.update({k: v for k, v in extra.items() if v})
    return card


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
@agent_assignee_refusal_as_text
async def propose_plan(
    name: str,
    tasks: str,
    parent_project_id: str = "",
    description: str = "",
    risks: str = "",
) -> str:
    """W1: propose a project plan as an editable card, then create it as
    ONE batch under one confirmation card. name is the project's name.
    tasks is a JSON list of {key, title, owner, effort_mins, start?, due,
    after?, level?, important?, leveraged?, impact?, urgency?, effort?}.
    level is a priority level name, and sets that level's Important and
    Leveraged flags. important and leveraged are true or false. key is a short label
    such as t1. start and due are YYYY-MM-DD. after lists the keys of the
    tasks that must finish first, and becomes blocks links. Every task needs
    a verb-plus-object title, an owner (email or name), an effort and a due
    date, or it is refused. A start after the due date, an unknown key or a
    circle of blocks is refused before the card. impact, urgency and effort
    (1 to 5) make a priority score the card shows and nothing stores. The
    card shows each owner's skill fit and hours across the plan, and marks a
    row that lacks either. A mark warns and never blocks. risks is the three
    ways the plan fails, one per line. parent_project_id is a full_id from
    projects_tree; empty makes a space. There are no phases."""
    label = _clean(name)
    if not label:
        return "A project needs a name."
    rows = _plan_rows(tasks)
    if isinstance(rows, str):
        return rows
    parent_label = "the top level (a new space)"
    parent_id = ""
    if parent_project_id.strip():
        parent_id, parent = await _node(parent_project_id)
        parent_label = _plain(parent.get("name"))
    soft, unresolved = await _soft_owners(rows)
    checks, warnings, capacity_line = _capacity(rows, await _preview(rows, soft), unresolved)
    values = await _ask(
        _template(
            "planCard",
            {
                "title": f"Plan · {label}",
                "description": f"Under {parent_label}. Edit any row, drop one, then submit.",
                "project": {
                    "name": label,
                    "parent": parent_label,
                    "description": _clean(description),
                },
                "tasks": _card_rows(rows, checks),
                "capacity": capacity_line,
                # A display card shows plain words, never the model's fence
                # (the S4 visual review). The confirm card keeps the fence.
                "warnings": [_plain(w.replace("«", "").replace("»", "")) for w in warnings],
                "risks": [r.strip() for r in str(risks or "").splitlines() if r.strip()][:5],
                "submitLabel": "Review plan",
            },
        )
    )
    if values is None:
        return NOT_SUBMITTED
    final = _submitted_rows(values.get("tasks"), rows)
    if isinstance(final, str):
        return final
    project = values.get("project") if isinstance(values.get("project"), dict) else {}
    label = _clean(project.get("name")) or label
    owners: dict[str, str] = {}
    for row in final:
        who = row["owner"]
        if who not in owners:
            owners[who] = await _resolve_assignee(who)
    strangers = await _unknown_addresses(sorted(set(owners.values())))
    # §13.6 rule 3: the server computes the marks again, for the plan the
    # member submitted, and the confirm card repeats every one.
    checks, warnings, capacity_line = _capacity(final, await _preview(final, owners), {})
    titles = {r["key"]: r["title"] for r in rows}
    titles.update({r["key"]: r["title"] for r in final})
    links = _links(final)
    final_keys = {r["key"] for r in final}
    dropped_links = [
        (a, b) for a, b in _links(rows) if a not in final_keys or b not in final_keys
    ]
    about = _clean(project.get("description") or description)
    card = _confirm_card(
        label=label, parent_label=parent_label, about=about, final=final, owners=owners,
        checks=checks, titles=titles, links=links, dropped_links=dropped_links,
        extra={
            "dependency warnings": " ".join(warnings),
            "not in the directory": ", ".join(strangers),
        },
    )
    card["capacity"] = capacity_line
    if checks or warnings:
        card["note"] = WARNS_NOT_BLOCKS
    if not _fits_on_card(card):
        return (
            f"The plan does not fit on one confirmation card ({len(final)} tasks, "
            f"{len(links)} links). Split it into two plans. Nothing was created."
        )
    n_links = f", {len(links)} link{'s' if len(links) != 1 else ''}" if links else ""
    if not await _confirm(
        title=f"Create {data(label)} with {len(final)} task{'s' if len(final) != 1 else ''}?",
        detail=f"under {parent_label} · one batch, {len(final)} tasks{n_links}",
        context=_fields_block(card),
    ):
        return CANCELLED
    return await _write_plan(label, parent_id, parent_label, about, final, owners, links, titles)


async def _write_plan(
    label: str,
    parent_id: str,
    parent_label: str,
    about: str,
    final: list[dict[str, Any]],
    owners: dict[str, str],
    links: list[tuple[str, str]],
    titles: dict[str, str],
) -> str:
    """Rule 8's order: the project node, the tasks with their start dates,
    the owners, then the links. At the first refusal it stops (rule 9), and
    the project node is a write like any other (S10)."""
    payload: dict[str, Any] = {"name": label, "kind": "project"}
    if parent_id:
        payload["parent_project_id"] = parent_id
    if about:
        payload["description"] = about
    try:
        node = await post("/projects/nodes", payload)
    except _WRITE_FAILED as exc:
        return _project_stopped(label, exc, tasks=len(final), links=len(links))
    pid = uuid_of(str(node.get("id")), "project_id")
    out = [f"Created project {data(node.get('name'))} under {parent_label}.\n  project_id: {pid}"]
    created: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for n, row in enumerate(final):
        body: dict[str, Any] = {
            "project_id": pid,
            "title": row["title"],
            "due_at": row["due"],
            "estimate_mins": row["effort_mins"],
        }
        if row.get("start"):
            body["start_date"] = row["start"]
        body.update(_row_flags(row))
        try:
            task = await post("/projects/tasks", body)
        except _WRITE_FAILED as exc:
            for _, made in created:
                out.extend(_task_line(made))
            return _stopped(
                out, f"task {n + 1} of {len(final)}, {data(row['title'])},", exc,
                tasks_left=len(final) - n - 1, owners=(0, len(final)), links=(0, len(links)),
            )
        created.append((row, task))
    tids: dict[str, str] = {}
    for n, (row, task) in enumerate(created):
        tid = uuid_of(str(task.get("id")), "task_id")
        tids[row["key"]] = tid
        try:
            await put(f"/projects/tasks/{tid}/assignees", {"assignees": [owners[row["owner"]]]})
        except _WRITE_FAILED as exc:
            for _, made in created:
                out.extend(_task_line(made))
            return _stopped(
                out, f"the owner of {data(row['title'])}", exc,
                tasks_left=0, owners=(n, len(final)), links=(0, len(links)),
            )
        task["assignees"] = [owners[row["owner"]]]
    for _, made in created:
        out.extend(_task_line(made))
    for n, (a, b) in enumerate(links):
        link = f"{data(titles[a])} blocks {data(titles[b])}"
        blocker = uuid_of(tids[a], "task_id")
        try:
            await post(
                f"/projects/tasks/{blocker}/links",
                {"target_task_id": tids[b], "link_type": "blocks"},
            )
        except _WRITE_FAILED as exc:
            return _stopped(
                out, f"the link {link}", exc,
                tasks_left=0, owners=(len(final), len(final)), links=(n, len(links)),
            )
        out.append(f"linked: {link}")
    return "\n".join(out)


# ── Several new tasks in one project, as one batch (WS-46 P13) ───────────────
#
# Spec: ``projects_agent_parity.md`` §12, slice P13. On 2026-10-06 a member
# asked for nine tasks in a project that existed. No tool took several
# top-level tasks, so the model made nine create_task calls and nine cards
# at once. This tool takes the batch in ONE call, under ONE confirmation
# card with a checkbox for each row. The member's one Approve names the
# ticked rows. Each row goes through create_task's own write path
# (``writes._prepare_new_task``, then the two halves of
# ``writes._create_new_task``: ``_post_new_task`` and ``_follow_new_task``).

#: The keys of one row. Each one means what the create_task argument of the
#: same name means, and ``tests/unit/test_projects_create_tasks.py`` holds
#: the two lists in step.
ROW_KEYS: tuple[str, ...] = (
    "title",
    "description",
    "status",
    "assignees",
    "due",
    "start",
    "estimate_mins",
    "tags",
    "parent_task_id",
    "priority",
    "important",
    "leveraged",
    "type",
    "fields",
)
#: The create_task arguments a row does not take: the repeat rule (P1) and
#: the retired ``importance``. A repeating task is create_task's alone.
REPEAT_KEYS: tuple[str, ...] = (
    "repeat",
    "repeat_every",
    "repeat_on",
    "repeat_day",
    "repeat_month",
    "repeat_from",
    "repeat_until",
    "repeat_times",
)
REPEAT_REFUSED = (
    "create_tasks makes tasks that do not repeat. Make each repeating task with "
    "create_task and repeat, one call for each."
)
PROJECT_REFUSED = (
    "Every task of one call goes to the project_id of the call. For tasks in "
    "another project, make a second call."
)
TASKS_FORMAT = (
    'tasks is a JSON list with one object per task, for example [{"title": "Book the '
    'venue", "assignees": "Priya", "due": "2026-10-09"}]. The keys are: '
    + ", ".join(ROW_KEYS)
    + ". Only title is required."
)
#: How recent a task with the same title in the same project must be to read
#: as the same task. The create route takes no idempotency key, so this read
#: before the card is what makes a retry safe: such a row starts unticked.
RECENT_MINUTES = 10
#: The read that finds those tasks. The list route sorts by ``created_at``,
#: newest first, by default, so one page holds every recent task.
RECENT_PAGE = 50
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class _BatchRow:
    """One row of a batch: the member's words, and create_task's resolved body."""

    def __init__(self, index: int, title: str, item: dict[str, Any], new: _NewTask) -> None:
        self.index = index
        self.title = title
        self.item = item
        self.new = new
        #: A task with this title made in the last :data:`RECENT_MINUTES`.
        self.recent: dict[str, Any] | None = None
        #: The full line of the confirmation card, fenced.
        self.line = ""
        #: The same facts with no title: the row's hint on the card.
        self.facts = ""


def _batch_items(raw: Any) -> list[dict[str, Any]] | str:
    """The rows as the model sent them, checked for shape, or the refusal."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return TASKS_FORMAT
    if not isinstance(raw, list) or not raw:
        return TASKS_FORMAT
    if len(raw) > MAX_BATCH:
        return (
            f"That is {len(raw)} tasks. The limit for one card is {MAX_BATCH}. Split them "
            "into two calls. Nothing was created."
        )
    seen: dict[str, int] = {}
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return f"Row {i} is not an object. {TASKS_FORMAT}"
        title = _clean(item.get("title"))
        label = f"Row {i}" + (f" ({data(title)})" if title else "")
        for key in item:
            if key in REPEAT_KEYS:
                return f"{label}: {REPEAT_REFUSED} Nothing was created."
            if key == "project_id":
                return f"{label}: {PROJECT_REFUSED} Nothing was created."
            if key not in ROW_KEYS and key != "importance":
                return (
                    f"{label} has no key {data(key)}. The keys are: {', '.join(ROW_KEYS)}. "
                    "Nothing was created."
                )
        if not title:
            return f"Row {i} has no title. Every task needs one. Nothing was created."
        twin = seen.setdefault(title.lower(), i)
        if twin != i:
            return (
                f"Rows {twin} and {i} have the same title, {data(title)}. Give each task its "
                "own title, or drop one. Nothing was created."
            )
    return raw


def _row_text(item: dict[str, Any], key: str) -> str:
    """A text key of a row. A list or an object is refused, never stringified."""
    value = item.get(key)
    if value is None:
        return ""
    if isinstance(value, list | dict):
        raise GatewayRefusal(f"{key} takes text, not a list or an object.")
    return str(value).strip()


def _row_list(item: dict[str, Any], key: str) -> list[str]:
    """``assignees`` or ``tags``: a comma-separated string, or a list."""
    value = item.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, dict):
        raise GatewayRefusal(f"{key} takes a comma-separated list, not an object.")
    return _split(str(value))


def _row_estimate(item: dict[str, Any]) -> int:
    value = item.get("estimate_mins")
    if value in (None, ""):
        return 0
    if isinstance(value, bool) or not str(value).strip().isdigit():
        raise GatewayRefusal(f"estimate_mins is a whole number of minutes, not {data(value)}.")
    return int(str(value).strip())


def _row_fields(item: dict[str, Any]) -> str:
    """``fields`` as create_task takes it: a JSON object string."""
    value = item.get("fields")
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        return json.dumps(value)
    if isinstance(value, str):
        return value
    raise GatewayRefusal(
        'fields is a JSON object keyed by field NAME, for example {"Customer": "Acme"}.'
    )


def _relabel(label: str, exc: GatewayRefusal) -> GatewayRefusal:
    """*exc*, said for one row. A gateway refusal keeps its safe detail only."""
    if getattr(exc, "status", None) is not None:
        hint = "Not found, or not visible to you." if exc.status == 404 else f"({exc.status})"
        said = f"{hint} {data(exc.detail)}" if exc.detail else hint
    else:
        said = " ".join(str(exc).split())
    text_ = f"{label}: {said} Nothing was created."
    if isinstance(exc, AgentAssigneeRefused):
        return AgentAssigneeRefused(text_)
    return GatewayRefusal(text_, fixable=getattr(exc, "fixable", True))


async def _batch_row(
    pid: str, index: int, item: dict[str, Any], statuses: list[dict[str, Any]]
) -> _BatchRow:
    """One row resolved by create_task's own code, or the refusal that names it."""
    title = _clean(item.get("title"))
    label = f"Row {index} ({data(title)})"
    try:
        flags = priority_fields(
            priority=_row_text(item, "priority"),
            important=item.get("important", ""),
            leveraged=item.get("leveraged", ""),
            importance=item.get("importance"),
        )
        if isinstance(flags, str):
            raise GatewayRefusal(flags)
        new = await _prepare_new_task(
            pid,
            title,
            description=_row_text(item, "description"),
            status=_row_text(item, "status"),
            due_at=_date_arg(_row_text(item, "due"), "due"),
            flags=flags,
            estimate_mins=_row_estimate(item),
            tags=_row_list(item, "tags"),
            start=_row_text(item, "start"),
            type_name=_row_text(item, "type"),
            fields=_row_fields(item),
            parent_task_id=_row_text(item, "parent_task_id"),
            assignees=_row_list(item, "assignees"),
            statuses=statuses,
        )
    except GatewayRefusal as exc:
        raise _relabel(label, exc) from None
    return _BatchRow(index, title, item, new)


def _first_lane(statuses: list[dict[str, Any]]) -> str:
    """The lane a task with no status lands in: the first by position, never
    triage (``core.load_default_status``)."""
    lanes = [s for s in statuses if s.get("category") != "triage"]
    if lanes and all(s.get("position") is not None for s in lanes):
        lanes.sort(key=lambda s: (int(s["position"]), str(s.get("name") or "")))
    # With no position on the rows, the route's own order stands.
    return str(lanes[0].get("name") or "") if lanes else ""


def _day_words(value: str) -> str:
    """``2026-10-09`` → ``Fri 9 Oct 2026``."""
    try:
        day = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{WEEKDAYS[day.weekday()][:3]} {day.day} {_MONTHS[day.month - 1]} {day.year}"


async def _names_of(who: list[str]) -> tuple[dict[str, str], list[str]]:
    """The directory name of each address, and the addresses it does not know.

    One read for the whole batch (``/projects/people/names``), as
    ``writes._unknown_addresses`` reads it for one task (H-162).
    """
    addresses = sorted({a for a in who if "@" in a})
    if not addresses:
        return {}, []
    payload = (await get("/projects/people/names", {"emails": ",".join(addresses)})) or {}
    names = {str(k).lower(): str(v) for k, v in (payload.get("names") or {}).items()}
    return names, [a for a in addresses if a.lower() not in names]


def _person(who: str, names: dict[str, str]) -> str:
    """The NAME the directory gives an address, or the address itself."""
    if who.startswith("agent:"):
        return f"agent {who.removeprefix('agent:')}"
    return names.get(who.lower()) or who


def _person_card(who: str, names: dict[str, str]) -> str:
    """An assignee on the confirmation card and the receipt: always
    ``«Name» (address)`` when the directory knows the address. Two people can
    share a name, and the card is consent, so it names the address too
    (review round 2). An address the directory does not know stays bare."""
    name = names.get(who.lower()) if "@" in who else None
    return f"{data(name)} ({who})" if name else data(_person(who, names))


def _created_at(row: dict[str, Any]) -> datetime | None:
    raw = str(row.get("created_at") or "").strip()
    if not raw:
        return None
    try:
        made = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return made if made.tzinfo else made.replace(tzinfo=UTC)


async def _recent_titles(pid: str) -> dict[str, dict[str, Any]]:
    """Each title of a task made in *pid* in the last :data:`RECENT_MINUTES`.

    The create route takes no idempotency key (``TaskIn``), so a retry of a
    batch that half landed would make each landed task twice. This read
    before the card finds them, and the card starts them unticked.
    The read takes the triage lane too, because a create can land a task
    there and the list hides it by default (review round 2).
    """
    query = {"project_id": pid, "page_size": RECENT_PAGE, "include_triage": True}
    page = (await get("/projects/tasks", query)) or {}
    since = datetime.now(UTC) - timedelta(minutes=RECENT_MINUTES)
    out: dict[str, dict[str, Any]] = {}
    for row in page.get("rows") or []:
        made = _created_at(row)
        if made is None or made < since:
            continue
        out.setdefault(_clean(row.get("title")).lower(), row)
    return out


def _minutes_ago(row: dict[str, Any]) -> int:
    made = _created_at(row)
    if made is None:
        return 0
    return max(0, int((datetime.now(UTC) - made).total_seconds() // 60))


def _recent_note(row: dict[str, Any]) -> str:
    ago = _minutes_ago(row)
    when = "less than a minute ago" if ago < 1 else f"{ago} min ago"
    return f"a task with this title was made {when}, as {_number(row)}"


def _describe(
    p: _BatchRow, names: dict[str, str], first_lane: str
) -> None:
    """Fill the row's facts and its confirmation line."""
    payload = p.new.payload
    due = str(payload.get("due_at") or "")
    if "status_id" in payload or "parent_task_id" in payload:
        lane = p.new.status_label
    else:
        lane = f"{first_lane} (the first lane)" if first_lane else "the first lane"
    # `_parent_lane_label` fences the lane name itself. Fence a bare name
    # once, and never a fenced one twice (review round 2).
    lane_card = lane if "«" in lane else data(lane)
    facts = [
        ", ".join(_person_card(w, names) for w in p.new.who) if p.new.who else "nobody assigned",
        f"due {_day_words(due)}" if due else "no due date",
        f"status {lane_card}",
    ]
    if payload.get("start_date"):
        facts.append(f"start {_day_words(payload['start_date'])}")
    if payload.get("estimate_mins"):
        facts.append(f"{payload['estimate_mins']} min")
    if payload.get("tags"):
        facts.append("tags " + ", ".join(data(t) for t in payload["tags"]))
    # The type and each custom value, by NAME. The start is said above.
    facts.extend(
        f"{key} {data(value)}" for key, value in p.new.extra.card.items() if key != "start"
    )
    shown = card_view({k: payload[k] for k in ("importance", "leveraged") if k in payload})
    facts.extend(f"{k} {v}" for k, v in shown.items())
    if payload.get("parent_task_id"):
        facts.append(_parent_words(p.new.parent or {}, p.new.pid))
    if payload.get("description"):
        # The whole text, as the POST sends it. A cut card is not consent
        # (projects_ai_chat.md §13.6 rule 8), so a batch too long for the
        # card is refused by `_fits_on_card` instead (review round 1).
        facts.append("description " + data(payload["description"]))
    p.facts = " · ".join(facts)
    p.line = f"{data(p.title)} · {p.facts}"


def _parent_words(parent: dict[str, Any], pid: str) -> str:
    """``subtask of #8 «Order the nozzle»``, and its project when that is not
    the project of the call (review round 2)."""
    out = f"subtask of {_number(parent)} {data(parent.get('title'))}"
    if parent.get("project_id") and str(parent.get("project_id")) != str(pid):
        where = data(parent.get("project_name")) if parent.get("project_name") else None
        out += f", in {where}" if where else ", in another project"
    return out


def _left_why(p: _BatchRow) -> str:
    """Why a row is not written, in the member's words."""
    if p.recent is not None:
        return f"unticked on the card, because {_recent_note(p.recent)}"
    return "unticked on the card"


def _row_id(p: _BatchRow) -> str:
    """The row's id on the card. The answer names these ids, and nothing else."""
    return f"row-{p.index}"


def _unfenced(text: str) -> str:
    return " ".join(str(text).replace("«", "").replace("»", "").split())


def _card_row(p: _BatchRow) -> dict[str, Any]:
    """One checkbox of the card: the title, every fact the POST sends, and a
    recent twin's reason. A row with a twin starts unticked."""
    hint = _unfenced(p.facts)
    if p.recent is not None:
        hint += f" · {_recent_note(p.recent)}, so it starts unticked"
    return {"id": _row_id(p), "label": _plain(p.title), "hint": hint, "checked": p.recent is None}


def _batch_card(node: dict[str, Any], rows: list[_BatchRow], strangers: list[str]) -> dict[str, Any]:
    """The confirmation card's body. The rows themselves are its checkboxes.

    The title names the project and the count, and the rows carry every
    fact, so the body repeats neither (PR #691 review: "Tasks 4" read wrong
    once a row was unticked). It keeps a warning about an unknown address.
    ``node`` and ``rows`` stay in the signature for the fit measure.
    """
    del node, rows
    card: dict[str, Any] = {}
    if strangers:
        card["not in the directory"] = ", ".join(strangers)
    return card


def _fits(node: dict[str, Any], rows: list[_BatchRow], strangers: list[str]) -> bool:
    """Does the whole batch, every row's facts included, fit on one card?

    A cut card is not consent (``projects_ai_chat.md`` §13.6 rule 8). The
    measure is the body and one line for each row, as ``_fits_on_card``
    counts a card.
    """
    card = _batch_card(node, rows, strangers)
    for p in rows:
        card[f"row {p.index}"] = p.line
        if p.recent is not None:
            card[f"row {p.index} check"] = _recent_note(p.recent)
    return _fits_on_card(card)


#: The answer of a card that named a row it did not offer.
FORGED_ROWS = (
    "Refused: the card answered with a row it did not offer, so nothing was created. "
    "Tell the member, and do not try again."
)


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
@agent_assignee_refusal_as_text
async def create_tasks(project_id: str, tasks: str) -> str:
    """Create several new tasks in ONE project as one batch. Use it for 2 or
    more new tasks, and never call create_task several times in parallel.
    project_id is a `full_id` from projects_tree (a project or subproject,
    never a folder). tasks is a JSON list with one object per task. Each
    object takes the keys of create_task, with the same meaning: title
    (required), description, status (by NAME), assignees (comma-separated
    emails, agent:<name>, or people's names), due and start (YYYY-MM-DD),
    estimate_mins, tags (comma-separated), parent_task_id, priority or
    important and leveraged, type (by NAME), and fields (a JSON object keyed
    by field NAME). A repeating task is create_task with repeat, not this
    tool. Every name is checked before any card, and one bad row refuses the
    whole batch and names the row. The member then sees ONE card with a
    checkbox for each task, and approves the ticked tasks once. A task with
    the same title made in this project in the last 10 minutes starts
    unticked, so a retry makes no second copy. Each row is its own write: a
    row that fails does not stop the others, and the receipt names each task
    made and each row that failed. Never create a task again that the
    receipt lists. Archive is the undo."""
    pid = uuid_of(project_id, "project_id")
    items = _batch_items(tasks)
    if isinstance(items, str):
        return items
    pid, node = await _node(pid)
    if node.get("kind") == "folder":
        return (
            f"{data(node.get('name'))} is a folder. A folder holds projects, not tasks. "
            "Pick a project inside it. Nothing was created."
        )
    statuses = await _statuses_of(pid)
    rows = [await _batch_row(pid, i, item, statuses) for i, item in enumerate(items, start=1)]
    names, strangers = await _names_of([w for p in rows for w in p.new.who])
    recent = await _recent_titles(pid)
    first_lane = _first_lane(statuses)
    for p in rows:
        p.recent = recent.get(p.title.lower())
        _describe(p, names, first_lane)
    from acb_skills.ask_tools import ROW_LABEL_MAX

    long = next((p for p in rows if len(p.title) > ROW_LABEL_MAX), None)
    if long is not None:
        return (
            f"Row {long.index}: a title is at most {ROW_LABEL_MAX} characters, because the "
            "card shows each title whole. Put the rest in description. Nothing was created."
        )
    if not _fits(node, rows, strangers):
        return (
            f"These {len(rows)} tasks do not fit on one confirmation card. Split them into "
            "two calls. Nothing was created."
        )
    n = len(rows)
    ticked = await _confirm(
        title=f"Create {n} task{'s' if n != 1 else ''} in {data(node.get('name'))}?",
        detail=f"one batch in {data(node.get('name'))} · untick a task to leave it out",
        context=_fields_block(_batch_card(node, rows, strangers)),
        rows=[_card_row(p) for p in rows],
    )
    # The card answers with the ticked ids (`request_confirmation` rows). It
    # already refuses an id it did not offer. This checks again, so a door
    # that answers anything else writes nothing.
    if not isinstance(ticked, frozenset) or not ticked:
        return CANCELLED
    offered = {_row_id(p) for p in rows}
    if not ticked <= offered:
        return FORGED_ROWS
    chosen = [p for p in rows if _row_id(p) in ticked]
    left = [p for p in rows if _row_id(p) not in ticked]
    return await _write_batch(pid, node, chosen, left, names)


def _uncertain(exc: Exception) -> bool:
    """A write whose result nobody knows: a lost connection, or an error that
    no gateway sends. A refusal of the gateway is a known result."""
    return not isinstance(exc, GatewayRefusal)


def _failure(exc: Exception) -> str:
    """Why one row's CREATE failed, in words the model can act on."""
    if isinstance(exc, httpx.TransportError):
        return (
            f"lost its connection to the gateway ({type(exc).__name__}), so it may or may "
            f"not exist. Read list_tasks before a retry. Within {RECENT_MINUTES} minutes, "
            "a retry with create_tasks starts it unticked when it exists."
        )
    if isinstance(exc, GatewayRefusal):
        status = getattr(exc, "status", None)
        if status is not None:
            detail = getattr(exc, "detail", "")
            return f"refused ({status})" + (f": {data(detail)}" if detail else "")
        return f"refused: {' '.join(str(exc).split())}"
    return (
        f"failed ({type(exc).__name__}), so it may or may not exist. Read list_tasks "
        f"before a retry. Within {RECENT_MINUTES} minutes, a retry with create_tasks "
        "starts it unticked when it exists."
    )


def _unsaved(task: dict[str, Any], exc: Exception) -> str:
    """Why the assign PUT after a create failed. The task EXISTS, so the words
    are the single path's (``writes._create_stopped``), and they never send
    the model back to create_tasks for it (review round 2)."""
    ref = f"{_number(task)} {data(task.get('title'))}"
    if _uncertain(exc):
        return (
            f"not saved: the task {ref} exists. Its assignees may or may not be saved: the "
            f"write failed ({type(exc).__name__}). Read task_detail for this task first, "
            "then use assign only if they are not there."
        )
    reason = _failure(exc)
    return _said(f"not saved: the task {ref} exists, but its assignees were {reason}") + (
        " Use assign on that task."
    )


def _said(line: str) -> str:
    """*line* with one full stop. A fenced detail often ends with its own."""
    return line if line.endswith((".", ".»")) else f"{line}."


async def _lane_names(pid: str) -> dict[str, str]:
    """``status_id → name``, read after the writes. Never raises."""
    try:
        rows = await _statuses_of(pid)
    except Exception:  # a receipt detail, never a write
        return {}
    return {str(r.get("id")): str(r.get("name") or "") for r in rows}


class _Outcome:
    """What the writes of a batch did, row by row, for the receipt."""

    def __init__(self) -> None:
        self.made: list[tuple[_BatchRow, dict[str, Any]]] = []
        #: Rows the gateway refused: no task exists.
        self.refused: list[str] = []
        #: Rows whose create may or may not have landed.
        self.unknown: list[str] = []
        #: Follow-up writes (the assignees) that did not land on a made task.
        self.unsaved: list[str] = []


def _log_unknown(exc: Exception, step: str) -> None:
    if not isinstance(exc, _WRITE_FAILED):
        # Caught, because a raise after a write makes the model create the
        # tasks again. The trace stays in the log (review round 1).
        _log.warning("projects.batch_row_failed", step=step, error=type(exc).__name__,
                     exc_info=True)


async def _write_batch(
    pid: str,
    node: dict[str, Any],
    chosen: list[_BatchRow],
    left: list[_BatchRow],
    names: dict[str, str] | None = None,
) -> str:
    """One write per ticked row, in order, and the receipt. It never raises.

    A row that fails does not stop the next row. The create and the writes
    after it are apart (``_post_new_task``, ``_follow_new_task``), so an
    error after the POST is a follow-up that failed on a task that exists,
    never a task that "may not exist". A raise here would read to the model
    as "Function failed", and the model would make the tasks again.
    """
    out = _Outcome()
    for p in chosen:
        try:
            task = await _post_new_task(p.new)
        except Exception as exc:  # each row ends in the receipt, never in a raise
            _log_unknown(exc, "create")
            line = _said(f"{'unknown' if _uncertain(exc) else 'failed'}: row {p.index} "
                         f"{data(p.title)} {_failure(exc)}")
            (out.unknown if _uncertain(exc) else out.refused).append(line)
            continue
        out.made.append((p, task))
        try:
            _saved, after = await _follow_new_task(task, p.new)
        except Exception as exc:  # the task exists: a follow-up, never a row
            _log_unknown(exc, "follow-up")
            after = ("assignees", exc)
        if after is not None:
            out.unsaved.append(_unsaved(task, after[1]))
    lanes = await _lane_names(pid)
    return _batch_receipt(node, chosen, left, out, lanes, names or {})


def _head(where: str, many: int, out: _Outcome) -> str:
    """The receipt's first line. It never says "not created" of a row whose
    create may have landed (review round 2)."""
    made, refused, unknown = len(out.made), len(out.refused), len(out.unknown)
    if not refused and not unknown:
        return f"Created {made} task{'s' if made != 1 else ''} in {where}:"
    parts = [f"Created {made} of {many} tasks in {where}." if made else
             f"No task of the {many} is known to exist in {where}."]
    if unknown:
        parts.append(
            f"{unknown} may have been created: read the project before a retry."
        )
    if refused:
        parts.append(f"{refused} {'was' if refused == 1 else 'were'} not created.")
    return " ".join(parts)


def _shown_task(task: dict[str, Any], names: dict[str, str]) -> dict[str, Any]:
    """The created row as the receipt prints it: each assignee by NAME, with
    the address, as the confirmation card shows them (review round 2)."""
    who = [str(a) for a in task.get("assignees") or []]
    if not who:
        return task
    shown = []
    for a in who:
        name = names.get(a.lower()) if "@" in a else None
        shown.append(f"{name} ({a})" if name else a)
    return {**task, "assignees": shown}


def _batch_receipt(
    node: dict[str, Any],
    chosen: list[_BatchRow],
    left: list[_BatchRow],
    out: _Outcome,
    lanes: dict[str, str],
    names: dict[str, str] | None = None,
) -> str:
    many = len(chosen)
    lines = [_head(data(node.get("name")), many, out)]
    for p, task in out.made:
        lines.extend(
            _task_line(_shown_task(task, names or {}), lanes.get(str(task.get("status_id")), ""))
        )
        lines.extend(level_note(_row_text(p.item, "priority"), task))
    lines.extend(out.refused)
    lines.extend(out.unknown)
    lines.extend(out.unsaved)
    failed = len(out.refused) + len(out.unknown)
    if failed or out.unsaved:
        lines.append(
            f"stopped: {failed} of {many} rows failed and {len(out.unsaved)} "
            f"follow-up write{'s' if len(out.unsaved) != 1 else ''} did not land."
        )
    if out.made:
        made = len(out.made)
        lines.append(
            f"The {made} task{'s' if made != 1 else ''} listed above exist. Never "
            "create them again. To retry a failed row, call create_tasks with that row alone."
        )
    lines.extend(
        f"left out: row {p.index} {data(p.title)}, {_left_why(p)}." for p in left
    )
    return "\n".join(lines)


# ── Several new tags or types, as one batch (H-273) ──────────────────────────
#
# Spec: ``projects_ai_chat.md`` §24.5, and the owner directive of 2026-10-07
# (``projects_agent_parity.md`` §16): the member's grants decide, and the
# server checks. On 2026-10-08 the owner asked for several new tags. The chat
# drew a picker, and then one card for each tag. These tools take the words in
# ONE call, under ONE confirmation card with a checkbox for each row, as
# ``create_tasks`` does. Each ticked row is one POST to the route of
# ``create_tag`` or ``create_type``. The tool decides no permission. A 403 of
# the route is quoted in the receipt, row by row.


class _VocabKind:
    """One kind of vocabulary word that a batch can add."""

    def __init__(
        self, *, tool: str, arg: str, kind: str, noun: str, single: str,
        keys: tuple[str, ...], example: str, blank: str,
    ) -> None:
        self.tool = tool
        #: The tool's argument that holds the rows.
        self.arg = arg
        #: The ``writes._vocab`` list, and the last segment of the route.
        self.kind = kind
        self.noun = noun
        #: The single tool that takes the same row, with org_wide.
        self.single = single
        #: The keys of one row. Each one means what the argument of the same
        #: name of the single tool means.
        self.keys = keys
        self.example = example
        #: The hint of a row that gives only a name.
        self.blank = blank

    def nouns(self, n: int) -> str:
        return self.noun if n == 1 else f"{self.noun}s"


TAG_KIND = _VocabKind(
    tool="create_tags", arg="tags", kind="tags", noun="tag", single="create_tag",
    keys=("name", "color", "description"),
    example='[{"name": "q4", "color": "blue"}, {"name": "blocked"}]',
    blank="no colour or description",
)
TYPE_KIND = _VocabKind(
    tool="create_types", arg="types", kind="types", noun="type", single="create_type",
    keys=("name", "icon", "color", "is_epic"),
    example='[{"name": "Chore", "icon": "broom"}, {"name": "Spike"}]',
    blank="no icon or colour",
)
#: The batch tools of the vocabulary, by tool name. The F2 fence reads the
#: row keys from here.
VOCAB_KINDS: dict[str, _VocabKind] = {k.tool: k for k in (TAG_KIND, TYPE_KIND)}
#: The words of a row on the card and on the receipt, by row key.
_VOCAB_WORDS = {"color": "colour", "icon": "icon", "description": "description"}


def _vocab_format(spec: _VocabKind) -> str:
    return (
        f"{spec.arg} is a JSON list with one object per {spec.noun}, for example "
        f"{spec.example}. A plain name is a row too. The keys are: "
        f"{', '.join(spec.keys)}. Only name is required."
    )


def _vocab_name(raw: Any) -> str:
    """A name as the routes store it: trimmed, with the inner spaces collapsed."""
    return " ".join(str(raw or "").split())


def _vocab_items(spec: _VocabKind, raw: Any) -> list[dict[str, Any]] | str:
    """The rows as the model sent them, checked for shape, or the refusal."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return _vocab_format(spec)
    if not isinstance(raw, list) or not raw:
        return _vocab_format(spec)
    if len(raw) > MAX_BATCH:
        return (
            f"That is {len(raw)} {spec.nouns(2)}. The limit for one card is {MAX_BATCH}. "
            "Split them into two calls. Nothing was created."
        )
    items: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for i, item in enumerate(raw, start=1):
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            return f"Row {i} is not an object. {_vocab_format(spec)}"
        name = _vocab_name(item.get("name")) if isinstance(item.get("name"), str) else ""
        label = f"Row {i}" + (f" ({data(name)})" if name else "")
        for key, value in item.items():
            if key in ("org_wide", "scope", "is_default"):
                return (
                    f"{label}: a batch adds {spec.nouns(2)} to this project's tree only, and "
                    f"none is the default. For {data(key)}, call {spec.single} for that "
                    f"{spec.noun} alone. Nothing was created."
                )
            if key not in spec.keys:
                return (
                    f"{label} has no key {data(key)}. The keys are: {', '.join(spec.keys)}. "
                    "Nothing was created."
                )
            if isinstance(value, list | dict):
                return f"{label}: {key} takes text, not a list or an object. Nothing was created."
        if not name:
            return f"Row {i} has no name. Every {spec.noun} needs one. Nothing was created."
        twin = seen.setdefault(name.lower(), i)
        if twin != i:
            return (
                f"Rows {twin} and {i} have the same name, {data(name)}. Drop one. "
                "Nothing was created."
            )
        items.append({**item, "name": name})
    return items


def _vocab_payload(spec: _VocabKind, item: dict[str, Any]) -> dict[str, Any]:
    """The POST body of one row: the body the single tool sends for the same words."""
    payload: dict[str, Any] = {"name": item["name"]}
    for key in spec.keys:
        if key in ("name", "is_epic"):
            continue
        value = str(item.get(key) or "").strip()
        if value:
            payload[key] = value
    if "is_epic" in spec.keys and _yes_no(str(item.get("is_epic") or ""), "is_epic"):
        payload["is_epic"] = True
    return payload


class _VocabRow:
    """One row of a vocabulary batch, with what the card says about it."""

    def __init__(self, index: int, payload: dict[str, Any]) -> None:
        self.index = index
        self.name: str = payload["name"]
        self.payload = payload
        #: Why the row starts unticked, or "" when it starts ticked.
        self.exists = ""

    def facts(self) -> str:
        """The row's values in words. The name is the label, so it is not here."""
        out = [f"{_VOCAB_WORDS[k]} {v}" for k, v in self.payload.items() if k in _VOCAB_WORDS]
        if self.payload.get("is_epic"):
            out.append("a top level (epic)")
        return " · ".join(out)


def _vocab_exists(spec: _VocabKind, rows: list[dict[str, Any]], name: str) -> str:
    """Why a name starts unticked: it is in the project's vocabulary already."""
    found = _matches_by_name(rows, name)
    if not found:
        return ""
    if any(r.get("project_id") is not None for r in found):
        return f"a {spec.noun} with this name exists here already"
    return (
        f"an organization-wide {spec.noun} with this name exists. Tick it only to add a "
        "copy for this tree"
    )


class _VocabPlan:
    """What the card shows, and what Approve writes."""

    def __init__(
        self, spec: _VocabKind, path: str, node: dict[str, Any], where: str,
        rows: list[_VocabRow],
    ) -> None:
        self.spec = spec
        self.path = path
        self.node = node
        self.where = where
        self.rows = rows

    def card(self) -> dict[str, Any]:
        n, spec = len(self.rows), self.spec
        body = {"project": data(self.node.get("name")), "scope": self.where}
        return {
            "title": f"Add {n} {spec.nouns(n)} to {data(self.node.get('name'))}?",
            "detail": f"one batch in {self.where} · untick a {spec.noun} to leave it out",
            "context": _fields_block(body),
            "rows": [_vocab_card_row(spec, p) for p in self.rows],
        }

    def fits(self) -> bool:
        """Does the whole batch fit on one card? A cut card is not consent."""
        body: dict[str, Any] = {"project": data(self.node.get("name")), "scope": self.where}
        for p in self.rows:
            body[f"row {p.index}"] = f"{data(p.name)} · {p.facts()} · {p.exists}"
        return _fits_on_card(body)


def _vocab_row_id(p: _VocabRow) -> str:
    return f"row-{p.index}"


def _vocab_card_row(spec: _VocabKind, p: _VocabRow) -> dict[str, Any]:
    hint = _unfenced(p.facts()) or spec.blank
    if p.exists:
        hint += f" · {_unfenced(p.exists)}, so it starts unticked"
    return {"id": _vocab_row_id(p), "label": _plain(p.name), "hint": hint,
            "checked": not p.exists}


async def _vocab_plan(spec: _VocabKind, project_id: str, raw: Any) -> _VocabPlan | str:
    """Every row resolved and every read done, before the card, or the refusal."""
    uuid_of(project_id, "project_id")
    items = _vocab_items(spec, raw)
    if isinstance(items, str):
        return items
    rows: list[_VocabRow] = []
    for i, item in enumerate(items, start=1):
        try:
            rows.append(_VocabRow(i, _vocab_payload(spec, item)))
        except GatewayRefusal as exc:
            said = " ".join(str(exc).split())
            return f"Row {i} ({data(item['name'])}): {said} Nothing was created."
    pid, node = await _node(project_id)
    paths = {"tags": f"/projects/nodes/{pid}/tags", "types": f"/projects/nodes/{pid}/types"}
    known = await _vocab(pid, spec.kind)
    for p in rows:
        p.exists = _vocab_exists(spec, known, p.name)
    from acb_skills.ask_tools import ROW_LABEL_MAX

    long = next((p for p in rows if len(p.name) > ROW_LABEL_MAX), None)
    if long is not None:
        return (
            f"Row {long.index}: a name is at most {ROW_LABEL_MAX} characters on the card. "
            "Nothing was created."
        )
    where = _tree_scope(await _root_of(pid, node), node)
    plan = _VocabPlan(spec, paths[spec.kind], node, where, rows)
    if not plan.fits():
        return (
            f"These {len(rows)} {spec.nouns(2)} do not fit on one confirmation card. "
            "Split them into two calls. Nothing was created."
        )
    return plan


def _vocab_failure(exc: Exception) -> tuple[bool, str]:
    """``(known, words)`` for one row's POST that failed. A gateway refusal is
    a known result, and its words are the server's (a 403 included)."""
    if isinstance(exc, GatewayRefusal):
        status = getattr(exc, "status", None)
        if status is not None:
            detail = getattr(exc, "detail", "")
            return True, f"refused ({status})" + (f": {data(detail)}" if detail else "")
        return True, f"refused: {' '.join(str(exc).split())}"
    return False, (
        f"failed ({type(exc).__name__}), so it may or may not exist. Read vocabulary "
        "before a retry. A retry starts it unticked when it exists."
    )


async def _vocab_write(plan: _VocabPlan, ticked: Any) -> str:
    """One POST per ticked row, in the card's order, and the receipt.

    It never raises after the card. A raise reads to the model as "Function
    failed", and the model would make the words again.
    """
    spec = plan.spec
    if not isinstance(ticked, frozenset) or not ticked:
        return CANCELLED
    if not ticked <= {_vocab_row_id(p) for p in plan.rows}:
        return FORGED_ROWS
    chosen = [p for p in plan.rows if _vocab_row_id(p) in ticked]
    left = [p for p in plan.rows if _vocab_row_id(p) not in ticked]
    made: list[tuple[_VocabRow, dict[str, Any]]] = []
    refused: list[str] = []
    unknown: list[str] = []
    for p in chosen:
        try:
            row = await post(plan.path, p.payload)
        except Exception as exc:  # each row ends in the receipt, never in a raise
            _log_unknown(exc, spec.tool)
            known, words = _vocab_failure(exc)
            line = _said(f"{'failed' if known else 'unknown'}: row {p.index} {data(p.name)} {words}")
            (refused if known else unknown).append(line)
            continue
        made.append((p, row if isinstance(row, dict) else {}))
    return _vocab_receipt(plan, chosen, left, made, refused, unknown)


def _vocab_receipt(
    plan: _VocabPlan,
    chosen: list[_VocabRow],
    left: list[_VocabRow],
    made: list[tuple[_VocabRow, dict[str, Any]]],
    refused: list[str],
    unknown: list[str],
) -> str:
    spec, many = plan.spec, len(chosen)
    if not refused and not unknown:
        head = f"Added {len(made)} {spec.nouns(len(made))} to {plan.where}:"
    else:
        parts = [f"Added {len(made)} of {many} {spec.nouns(many)} to {plan.where}." if made
                 else f"No {spec.noun} of the {many} is known to exist in {plan.where}."]
        if unknown:
            parts.append(f"{len(unknown)} may have been created: read vocabulary before a retry.")
        if refused:
            parts.append(f"{len(refused)} {'was' if len(refused) == 1 else 'were'} not created.")
        head = " ".join(parts)
    lines = [head]
    for p, row in made:
        facts = p.facts()
        lines.append(f"- {spec.noun} {data(row.get('name') or p.name)}"
                     + (f" · {facts}" if facts else ""))
        lines.append(f"  {spec.noun}_id: {row.get('id')}")
    lines.extend(refused)
    lines.extend(unknown)
    failed = len(refused) + len(unknown)
    if failed:
        lines.append(f"stopped: {failed} of {many} rows failed.")
    if made:
        lines.append(
            f"The {len(made)} {spec.nouns(len(made))} listed above exist. Never create them "
            f"again. To retry a failed row, call {spec.tool} with that row alone."
        )
    lines.extend(
        f"left out: row {p.index} {data(p.name)}, unticked on the card"
        + (f", because {p.exists}." if p.exists else ".")
        for p in left
    )
    return "\n".join(lines)


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def create_tags(project_id: str, tags: str) -> str:
    """Register several new tags on the project's tree as one batch. Use it
    for 2 or more new tags, with no picker first, and never call create_tag
    several times. project_id is a `full_id` from projects_tree. tags is a
    JSON list with one object per tag: name (required), color and
    description, as create_tag takes them. A plain name is a row too. The
    member sees ONE card with a checkbox for each tag, and approves the
    ticked tags once. A name the project has already starts unticked. Each
    row is its own write, and the server decides each one: the receipt names
    each tag made and quotes each refusal. An organization-wide tag is
    create_tag with org_wide=true, one call each."""
    plan = await _vocab_plan(TAG_KIND, project_id, tags)
    if isinstance(plan, str):
        return plan
    ticked = await _confirm(**plan.card())
    return await _vocab_write(plan, ticked)


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def create_types(project_id: str, types: str) -> str:
    """Add several new task types to the project's tree as one batch. Use it
    for 2 or more new types, with no picker first, and never call
    create_type several times. project_id is a `full_id` from projects_tree.
    types is a JSON list with one object per type: name (required), icon,
    color and is_epic (yes or no), as create_type takes them. The member sees
    ONE card with a checkbox for each type, and approves the ticked types
    once. A name the project has already starts unticked. Each row is its own
    write, and the server decides each one. The default type, and an
    organization-wide type, are create_type, one call each."""
    plan = await _vocab_plan(TYPE_KIND, project_id, types)
    if isinstance(plan, str):
        return plan
    ticked = await _confirm(**plan.card())
    return await _vocab_write(plan, ticked)


__all__ = [
    "create_tags", "create_tasks", "create_types", "edit_project", "edit_task", "propose_plan",
]
