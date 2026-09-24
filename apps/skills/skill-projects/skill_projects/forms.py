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
"""

from __future__ import annotations

import json
import re
from datetime import date
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
    _fits_on_card,
    _node,
    _resolve_assignee,
    _statuses_of,
    _task,
    _unknown_addresses,
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
                "after?, importance?}."
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
    except GatewayRefusal:
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
    by_key: dict[str, dict[str, Any]] = {}
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
        owner_name = next((r["owner"] for r in rows if r["key"] == key), "")
        if owner_name in unresolved:
            fit_text = "owner not resolved: give an address"
            marks = ["owner not resolved"]
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


def _task_card_line(row: dict[str, Any], owner: str) -> str:
    # Every field the POST carries is on this line (spec §5.3).
    return (
        f"{data(row['title'])} · {data(owner)} · {row['effort_mins']} min · "
        + (f"start {row['start']} · " if row.get("start") else "")
        + f"due {row['due']}"
        + (f" · importance {row['importance']}" if row.get("importance") is not None else "")
    )


def _check_line(check: dict[str, Any]) -> str:
    parts = [*check.get("marks", []), *check.get("warnings", [])]
    if "short" in str(check.get("hours", "")):
        parts.append(str(check["hours"]))
    return " · ".join(parts)


def _stopped(
    out: list[str],
    what: str,
    exc: GatewayRefusal,
    *,
    tasks_left: int,
    owners: tuple[int, int],
    links: tuple[int, int],
) -> str:
    """The partial receipt (§13.6 rule 9). It lists what exists, names the
    row that failed and counts what was not tried. It archives nothing."""
    out.append(f"stopped: {what} was refused. {exc}")
    out.append(
        f"not tried: {tasks_left} task{'s' if tasks_left != 1 else ''} · "
        f"{owners[0]} of {owners[1]} owners assigned · {links[0]} of {links[1]} links written"
    )
    out.append(
        "Nothing was archived. Archive the project to remove what exists, "
        "or finish the plan by hand."
    )
    return "\n".join(out)


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
    tasks is a JSON list of {key, title, owner, effort_mins, start?, due,
    after?, importance?, impact?, urgency?, effort?}. key is a short label
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
    the owners, then the links. At the first refusal it stops (rule 9)."""
    payload: dict[str, Any] = {"name": label, "kind": "project"}
    if parent_id:
        payload["parent_project_id"] = parent_id
    if about:
        payload["description"] = about
    node = await post("/projects/nodes", payload)
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
        if row.get("importance") is not None:
            body["importance"] = row["importance"]
        try:
            task = await post("/projects/tasks", body)
        except GatewayRefusal as exc:
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
        except GatewayRefusal as exc:
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
        except GatewayRefusal as exc:
            return _stopped(
                out, f"the link {link}", exc,
                tasks_left=0, owners=(len(final), len(final)), links=(n, len(links)),
            )
        out.append(f"linked: {link}")
    return "\n".join(out)


__all__ = ["edit_project", "edit_task", "propose_plan"]
