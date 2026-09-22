"""Forms — edit a row from the chat, and plan a project from a goal (W1).

Spec: ``project-docs/specs/projects_ai_chat.md`` §3.4 W1, §4.2 (S4).

Each tool here draws an EDITABLE card (``formCard`` or ``planCard`` with
``hitl: true``), waits for the member to submit it, and then writes through
the class B tools' own routes under the one confirmation card those tools
use. So an edit from the chat is two gestures, and both are the member's:
the form says WHAT, the card says "yes, write it". The form is not consent
for the write; ``_confirm`` is, and it is the one door class B has.

The submit comes back as ONE string, ``"<label> — <json>"``
(``genUITemplates.tsx::FormCard``). ``_answer`` parses it and refuses
anything else, so a hostile or malformed submit writes nothing.

``manifest.COMPOSITE`` records what each tool reaches: ``edit_task`` writes
through ``update_task``'s route, ``edit_project`` through ``update_project``'s,
and ``propose_plan`` through ``create_project`` and ``create_task`` (which
assigns through ``assign``).
"""

from __future__ import annotations

import json
from typing import Any

from skill_projects.client import GatewayRefusal, data, post, put, uuid_of
from skill_projects.reads import _task_line
from skill_projects.views import _emit, _plain, _template
from skill_projects.writes import (
    CANCELLED,
    IMPORTANCE,
    MAX_BATCH,
    _confirm,
    _fields_block,
    _node,
    _resolve_assignee,
    _statuses_of,
    _task,
    update_project,
    update_task,
)

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


@_annotate(read_only=False, destructive=False, idempotent=False)
async def edit_task(task_id: str) -> str:
    """Open an editable form for a task in the chat: title, description,
    status (by name), due, start, importance, estimate, tags. The member
    edits and submits; the changed fields then go through update_task's
    confirmation card, before → after. Nothing is written until that card
    is approved. Assignees are changed with assign."""
    tid, task = await _task(task_id)
    statuses = await _statuses_of(str(task.get("project_id")))
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
        {
            "name": "importance",
            "label": "Importance (0 to 4)",
            "type": "slider",
            "min": 0,
            "max": 4,
            "step": 1,
            "value": task.get("importance") or 0,
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
    values = await _ask(
        _template(
            "formCard",
            {
                "title": f"Edit #{task.get('task_number')} {_plain(task.get('title'))}",
                "description": "Change what you need, then submit. A card confirms the write.",
                "submitLabel": "Review changes",
                "fields": fields,
            },
            surface="panel",
        )
    )
    if values is None:
        return NOT_SUBMITTED
    changes = _task_changes(task, values, current)
    if isinstance(changes, str):
        return changes
    if not changes:
        return f"Nothing changed on {_ref(task)}."
    # `update_task` reads the row again, shows the before → after card, and
    # writes only on approval. The form was the member's wording; the card
    # is their consent.
    return await update_task(tid, **changes)


def _numeric_changes(
    task: dict[str, Any], values: dict[str, Any], changes: dict[str, Any], clear: list[str]
) -> str:
    """Importance and estimate: a zero clears, a change sets. Returns the
    refusal, or an empty string."""
    try:
        imp = int(values.get("importance") or 0)
        est = int(values.get("estimate_mins") or 0)
    except (TypeError, ValueError):
        return "importance is 0 to 4, and estimate_mins is a number of minutes."
    if imp not in IMPORTANCE:
        return "importance is 0 to 4."
    if imp != int(task.get("importance") or 0):
        if imp == 0:
            clear.append("importance")
        else:
            changes["importance"] = imp
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
    changes and nothing else. An emptied date, estimate, importance or
    description becomes a `clear`.
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


@_annotate(read_only=False, destructive=False, idempotent=False)
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
            surface="panel",
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

PLAN_FIELDS = ("title", "owner", "effort_mins", "due")


def _plan_rows(raw: Any) -> list[dict[str, Any]] | str:
    """The plan's task rows, validated. W1 rule: a task that lacks a title,
    an owner, an effort or a date is not proposed. Returns the refusal
    string when one does."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return "tasks is a JSON list of {title, owner, effort_mins, due, importance?}."
    if not isinstance(raw, list) or not raw:
        return "tasks is a JSON list with at least one task."
    if len(raw) > MAX_BATCH:
        return f"That is {len(raw)} tasks. The limit for one card is {MAX_BATCH}."
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            return f"Task {i} is not an object."
        missing = [f for f in PLAN_FIELDS if not _clean(item.get(f))]
        if missing:
            return f"Task {i} ({data(item.get('title') or '?')}) lacks {', '.join(missing)}. Every task needs all four."
        try:
            effort = int(item.get("effort_mins"))
        except (TypeError, ValueError):
            return f"Task {i}: effort_mins is a number of minutes."
        due = _clean(item.get("due"))[:10]
        if len(due) != 10:
            return f"Task {i}: due is a date, YYYY-MM-DD."
        row: dict[str, Any] = {
            "title": _clean(item.get("title")),
            "owner": _clean(item.get("owner")),
            "effort_mins": effort,
            "due": due,
        }
        imp = item.get("importance")
        if imp not in (None, ""):
            try:
                imp = int(imp)
            except (TypeError, ValueError):
                return f"Task {i}: importance is 0 to 4."
            if imp not in IMPORTANCE:
                return f"Task {i}: importance is 0 to 4."
            row["importance"] = imp
        score = 1
        for key in ("impact", "urgency", "effort"):
            try:
                score *= max(1, min(5, int(item.get(key) or 3)))
            except (TypeError, ValueError):
                score *= 3
        # A sorting aid, never stored (spec §3.4 W1 step 4).
        row["priority"] = score
        rows.append(row)
    rows.sort(key=lambda r: -int(r["priority"]))
    return rows


@_annotate(read_only=False, destructive=False, idempotent=False)
async def propose_plan(
    name: str,
    tasks: str,
    parent_project_id: str = "",
    description: str = "",
    risks: str = "",
) -> str:
    """W1: propose a project plan as an editable card, then create it as
    ONE batch under one confirmation card. name is the project's name.
    tasks is a JSON list of {title, owner, effort_mins, due, importance?,
    impact?, urgency?, effort?} — every task needs a verb-plus-object title,
    an owner (email or name), an effort and a date, or it is refused. impact,
    urgency and effort (1 to 5) make a priority score the card shows and
    nothing stores. risks is the three ways the plan fails, one per line,
    shown on the card before the member approves. parent_project_id is a
    full_id from projects_tree; empty makes a space."""
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
                "tasks": rows,
                "risks": [r.strip() for r in str(risks or "").splitlines() if r.strip()][:5],
                "submitLabel": "Review plan",
            },
            surface="panel",
        )
    )
    if values is None:
        return NOT_SUBMITTED
    final = _plan_rows(values.get("tasks"))
    if isinstance(final, str):
        return final
    project = values.get("project") if isinstance(values.get("project"), dict) else {}
    label = _clean(project.get("name")) or label
    owners: dict[str, str] = {}
    for row in final:
        who = row["owner"]
        if who not in owners:
            owners[who] = await _resolve_assignee(who)
    card: dict[str, Any] = {"project": data(label), "under": parent_label}
    for i, row in enumerate(final, start=1):
        card[f"task {i}"] = (
            f"{data(row['title'])} · {data(owners[row['owner']])} · {row['effort_mins']} min · "
            f"due {row['due']}"
        )
    if not await _confirm(
        title=f"Create {data(label)} with {len(final)} task{'s' if len(final) != 1 else ''}?",
        detail=f"under {parent_label} · one batch, {len(final)} tasks",
        context=_fields_block(card),
    ):
        return CANCELLED
    payload: dict[str, Any] = {"name": label, "kind": "project"}
    if parent_id:
        payload["parent_project_id"] = parent_id
    if _clean(project.get("description") or description):
        payload["description"] = _clean(project.get("description") or description)
    node = await post("/projects/nodes", payload)
    pid = uuid_of(str(node.get("id")), "project_id")
    out = [f"Created project {data(node.get('name'))} under {parent_label}.\n  project_id: {pid}"]
    for row in final:
        body: dict[str, Any] = {
            "project_id": pid,
            "title": row["title"],
            "due_at": row["due"],
            "estimate_mins": row["effort_mins"],
        }
        if row.get("importance") is not None:
            body["importance"] = row["importance"]
        task = await post("/projects/tasks", body)
        tid = uuid_of(str(task.get("id")), "task_id")
        await put(f"/projects/tasks/{tid}/assignees", {"assignees": [owners[row["owner"]]]})
        task["assignees"] = [owners[row["owner"]]]
        out.extend(_task_line(task))
    return "\n".join(out)


__all__ = ["edit_project", "edit_task", "propose_plan"]
