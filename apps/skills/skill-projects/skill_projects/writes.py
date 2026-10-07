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

import calendar
import functools
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

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
from skill_projects.priority import (
    CLEAR_PRIORITY,
    Removed,
    card_view,
    level_label,
    level_note,
    priority_fields,
    takes_priority,
)
from skill_projects.reads import WEEKDAYS, _number, _rule_text, _task_line, clock_of
from skill_projects.refusals import REFUSED

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


# ── The card ─────────────────────────────────────────────────────────────────


def _fields_block(payload: dict[str, Any], *, before: dict[str, Any] | None = None) -> str:
    """The payload as the card body, rendered from what goes on the wire.

    ``before`` adds the current value beside each changed field, so a member
    approving an update sees the change and not only the result.
    """
    body = _card_lines(payload, before=before)
    budget = CARD_CONTEXT_LIMIT - len(CARD_NOTE) - 1
    if len(body) <= budget:
        return f"{CARD_NOTE}\n{body}"
    keep = max(0, budget - len(_TRUNCATED) - 1)
    return f"{CARD_NOTE}\n{body[:keep]}\n{_TRUNCATED}"


def _fits_on_card(payload: dict[str, Any]) -> bool:
    """Does the whole payload fit under the clip, with no truncation?

    A tool whose card IS the list of what it creates refuses instead of
    cutting it (WS-27bm S7d, §13.6 rule 8). A cut card is not consent.
    """
    return len(_card_lines(payload)) <= CARD_CONTEXT_LIMIT - len(CARD_NOTE) - 1


def _card_lines(payload: dict[str, Any], *, before: dict[str, Any] | None = None) -> str:
    """The card body before the clip: one ``key: value`` line per field."""

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
    return "\n".join(lines)


async def _confirm(
    title: str, detail: str, context: str, rows: list[dict[str, Any]] | None = None
) -> bool | frozenset[str]:
    """One door for every card. Imported inside so a test can stub the gate.

    With ``rows`` (WS-46 P13 one-card), the card draws one checkbox per row,
    and the answer is the ``frozenset`` of the ticked row ids: empty when the
    member did not approve. With no rows, the call is exactly as before.
    """
    from acb_skills.ask_tools import request_confirmation

    if rows is None:
        return await request_confirmation(title=title, detail=detail, context=context)
    return await request_confirmation(title=title, detail=detail, context=context, rows=rows)


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


#: H-236: the refusal of an agent assignee in a run that may not send.
AGENT_ASSIGNEE_REFUSED = (
    "This chat works with member data from a sandboxed turn, so it cannot "
    "assign a task to an agent: an assigned agent starts a run of its own, "
    "outside this chat's controls. Assign a person, or ask the member to "
    "assign the agent in the Projects app."
)


class AgentAssigneeRefused(GatewayRefusal):
    """H-236 refused an agent assignee. :func:`agent_assignee_refusal_as_text`
    turns it into the tool's own answer, so the model reads why."""


def agent_assignee_refusal_as_text(
    fn: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    """A Projects tool that answers an H-236 refusal with its text.

    The refusal is raised deep in :func:`_resolve_assignee`. A raised error
    reaches the model as "Error: Function failed.", so the tools that can
    assign return :data:`AGENT_ASSIGNEE_REFUSED` instead (fix round 2).
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        try:
            return await fn(*args, **kwargs)
        except AgentAssigneeRefused as exc:
            return str(exc)

    return wrapper


def _refuse_agent_assignee(assignee: str) -> str:
    """*assignee*, unless it names an agent and this run may not send (H-236).

    Assigning ``agent:<name>`` starts that agent's run in the gateway
    (``routes/projects/agent_dispatch.py``), where this run's ``no_egress``
    does not reach. So a run that holds the flag refuses the agent before
    any write. The flag comes from the run binding
    (``acb_skills.egress.no_egress_for_this_run``, which fails closed),
    never from the request.
    """
    if not assignee.startswith("agent:"):
        return assignee
    try:
        from acb_skills.egress import no_egress_for_this_run
    except ImportError:  # no platform package: no run can be checked
        raise AgentAssigneeRefused(AGENT_ASSIGNEE_REFUSED) from None
    if no_egress_for_this_run():
        raise AgentAssigneeRefused(AGENT_ASSIGNEE_REFUSED)
    return assignee


async def _resolve_assignee(value: str, *, dispatch: bool = True) -> str:
    """An email or ``agent:<name>`` passes through. A person's name resolves
    through the picker, and only when exactly one person matches.

    With *dispatch* (an assignee to ADD), an agent is refused in a run that
    may not send (:func:`_refuse_agent_assignee`, H-236). A removal passes
    ``dispatch=False``, because taking an agent off a task starts nothing.
    """
    resolved = await _resolve_assignee_name(value)
    return _refuse_agent_assignee(resolved) if dispatch else resolved


async def _resolve_assignee_name(value: str) -> str:
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


#: `update_task`'s clear words that are not priority words → the field each
#: one empties.
_CLEAR_WORDS: dict[str, str] = {
    "due": "due_at",
    "start": "start_date",
    "description": "description",
    "estimate": "estimate_mins",
    # WS-46 P6 (G6): the task's type.
    "type": "type_id",
}


def _clears(clear: str, words: dict[str, str], taken: dict[str, Any]) -> dict[str, Any] | str:
    """The fields a ``clear`` list empties, or the refusal.

    ``words`` maps the plain words to their fields. The priority words
    (``CLEAR_PRIORITY``) are accepted everywhere. A field in ``taken`` is
    being set by the same call, so clearing it too is refused.
    """
    out: dict[str, Any] = {}
    for field in _split(clear):
        word = field.lower()
        empties = CLEAR_PRIORITY.get(word) or (
            {words[word]: None} if word in words else None
        )
        if empties is None:
            names = ", ".join([*words, *CLEAR_PRIORITY])
            return f"clear takes {names}, not {data(field)}."
        for key, value in empties.items():
            if key in taken:
                return f"{data(field)} is both set and cleared. Pass one or the other."
            out[key] = value
    return out


def _priority_card(
    payload: dict[str, Any],
    *,
    before: dict[str, Any] | None = None,
    task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The card's view of a task payload: the priority as a member reads it.

    The stored ``importance`` number becomes ``important: yes``, and a
    ``priority`` line names the level the app will draw (H-173). ``before``
    gains the same keys, so an update card shows each flag and the level as
    before → after.
    """
    # In payload order, so a cleared flag stays at the top of the card.
    card: dict[str, Any] = {}
    for key, value in payload.items():
        if key in ("importance", "leveraged"):
            card.update(card_view({key: value}))
        else:
            card[key] = value
    touched = {"importance", "leveraged"} & set(payload)
    if not touched and not ("due_at" in payload and task is not None):
        return card
    card["priority"] = level_label({**(task or {}), **payload})
    if before is not None and task is not None:
        # `leveraged` keeps its key on the card, `importance` becomes
        # `important`. Drop the stored number first, then add the flags.
        before.pop("importance", None)
        before.update(card_view({k: task.get(k) for k in touched}))
        before["priority"] = level_label(task)
    return card


# ── WS-46 P6 — the task fields the screen sets (gaps G5 to G9) ───────────────
#
# Spec: ``project-docs/specs/projects_agent_parity.md`` §3.3 and slice P6.
# The start date at the create (G5), the task type by name (G6), custom field
# values by name (G7), the destination's required fields on a move (G8), and
# the subtasks of a lifecycle act (G9, D-PM-38). Each one is resolved and
# checked before the card, shown on the card, and sent on the wire. The route
# still decides: the checks here only move a refusal in front of the card.

#: D-PM-38 (``project_management_app.md`` §12.9): what each lifecycle door
#: does with the subtasks when the member has not said. Decision 2: a
#: complete takes this task only. Decision 4: a move and an archive take the
#: subtree along. A mirror of ``CASCADE_DEFAULTS`` in
#: ``workbench/control_plane/src/lib/subtaskCascade.ts``.
#: ``tests/unit/test_projects_task_fields.py`` fails when the two drift.
CASCADE_DEFAULTS: dict[str, bool] = {"complete": False, "move": True, "archive": True}

#: The word for each door, in the question and on the card.
_CASCADE_VERB = {"complete": "completed", "move": "moved", "archive": "archived"}

#: The example the model reads when ``fields`` does not parse.
FIELDS_FORMAT = (
    'fields is a JSON object keyed by field NAME, for example {"Customer": "Acme", '
    '"Cost centre": 4200}. A null value empties that field.'
)


def _subtasks_wanted(value: str) -> bool | None:
    """``include_subtasks`` as the member said it: yes, no, or not said."""
    return _yes_no(value, "include_subtasks")


def _subtasks_phrase(count: int, door: str, capped: bool = False) -> str:
    """``3 open subtasks (every level)``: the count the cascade acts on.

    The gateway's cascade walks every level below the task
    (``cascade.load_subtree``), so the count does too (:func:`_subtask_counts`).
    """
    noun = "open subtask" if door == "complete" else "subtask"
    lead = "at least " if capped else ""
    return f"{lead}{count} {noun}{'' if count == 1 else 's'} (every level)"


def ask_about_subtasks(
    task: dict[str, Any], count: int, door: str, capped: bool = False
) -> str:
    """The one question D-PM-38 asks, before any card and before any write.

    The app asks it in a prompt (complete) or with a ticked box (move and
    archive). A chat card has no box, so the tool asks first, once, and the
    card then shows the answer. The app's own default is named, so the
    member knows what the app would do.
    """
    many = _subtasks_phrase(count, door, capped)
    them = "it" if count == 1 else "them"
    default = "they go with it" if CASCADE_DEFAULTS[door] else "only this task"
    return (
        f"{_ref(task)} has {many}. Ask the member once: {door} {them} too, or only this "
        f"task? In the app, the default is: {default}. Then call this tool again with "
        "include_subtasks=yes or include_subtasks=no. Nothing was changed."
    )


#: The most ``/relations`` reads one subtree count makes. Past it the card
#: says "at least", so a huge tree costs a bounded number of reads.
SUBTREE_READS = 100

_CLOSED_CATEGORIES = ("done", "cancelled")


class SubtreeCount:
    """The descendants a cascade reaches, counted at every level.

    ``total`` is every visible descendant that is not archived, which is the
    set ``cascade.archive_subtree`` shelves. ``open`` is the part of it that
    is not closed, which is the set ``cascade.complete_subtree`` completes.
    ``capped`` says the walk stopped at :data:`SUBTREE_READS`.
    """

    def __init__(self, total: int = 0, open_: int = 0, capped: bool = False) -> None:
        self.total = total
        self.open = open_
        self.capped = capped


async def _subtask_counts(task_id: str) -> SubtreeCount:
    """Every level below *task_id*, through ``/relations``, top down.

    ``/relations`` lists the direct children that the member can see and
    that are not archived. A walk of it reaches the descendants the
    gateway's cascade acts on (``cascade.load_subtree``). One read per task
    in the tree, to :data:`SUBTREE_READS`.
    """
    out = SubtreeCount()
    queue = [task_id]
    seen = {str(task_id)}
    reads = 0
    while queue:
        if reads >= SUBTREE_READS:
            out.capped = True
            break
        tid = uuid_of(queue.pop(0), "task_id")
        relations = (await get(f"/projects/tasks/{tid}/relations")) or {}
        reads += 1
        for child in relations.get("subtasks") or []:
            cid = str(child.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            out.total += 1
            if child.get("category") not in _CLOSED_CATEGORIES:
                out.open += 1
            queue.append(cid)
    return out


def _subtask_line(door: str, wanted: bool, count: int, capped: bool = False) -> dict[str, str]:
    """The card's ``subtasks`` line: what the act does to them."""
    if not wanted and not count:
        return {}
    if wanted:
        noun = "open subtask" if door == "complete" else "subtask"
        many = _subtasks_phrase(count, door, capped) if count else f"every {noun}"
        return {"subtasks": f"{many} {_CASCADE_VERB[door]} too"}
    many = _subtasks_phrase(count, door, capped)
    return {"subtasks": f"{many} stay as they are (include_subtasks=no)"}


def _subtask_receipt(reply: Any, key: str, door: str) -> list[str]:
    """The receipt's line from the route's own count, when it sent one."""
    count = reply.get(key) if isinstance(reply, dict) else None
    if not isinstance(count, int):
        return []
    return [f"Subtasks {_CASCADE_VERB[door]} too: {count}."]


async def _resolve_type(project_id: str, name: str) -> dict[str, Any]:
    """The one task type a spoken name means, or a refusal that lists them."""
    return _one_named(await _vocab(project_id, "types"), name, "task type")


def _epic_refusal(row: dict[str, Any], has_parent: bool) -> str:
    """The route's epic rule (``core.assert_epic_has_no_parent``), said first."""
    if row.get("is_epic") and has_parent:
        return (
            f"{data(row.get('name'))} is an epic type, and an epic is a top-level task, "
            "so it cannot be a subtask. Pick another type, or leave out the parent."
        )
    return ""


def _date_arg(value: str, what: str) -> str:
    """A ``YYYY-MM-DD`` argument, or ``""``. Raises the refusal for a bad one."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) != 10 or _date_of(raw) is None:
        raise GatewayRefusal(f"{what} is a date, YYYY-MM-DD, not {data(raw)}.")
    return raw


def _parse_fields(raw: str) -> dict[str, Any]:
    """``fields`` as the model sent it: a JSON object keyed by field name."""
    try:
        parsed = json.loads(str(raw or "").strip())
    except ValueError:
        raise GatewayRefusal(FIELDS_FORMAT) from None
    if not isinstance(parsed, dict) or not parsed:
        raise GatewayRefusal(FIELDS_FORMAT)
    return parsed


def _option(name: str, value: Any, options: list[str]) -> str:
    """One choice, matched to an option without regard to case."""
    wanted = str(value or "").strip().lower()
    found = [o for o in options if o.strip().lower() == wanted]
    if len(found) == 1:
        return found[0]
    listed = ", ".join(data(o) for o in options) or "(none)"
    raise GatewayRefusal(f"{name} is one of {listed}, not {data(value)}.")


def _number_value(name: str, value: Any, _options: list[str]) -> int | float:
    if isinstance(value, bool):
        raise GatewayRefusal(f"{name} takes a number, not {data(value)}.")
    if isinstance(value, int | float):
        return value
    text = str(value or "").strip()
    try:
        return int(text) if re.fullmatch(r"-?\d+", text) else float(text)
    except ValueError:
        raise GatewayRefusal(f"{name} takes a number, not {data(value)}.") from None


def _many_value(name: str, value: Any, options: list[str]) -> list[str]:
    parts = value if isinstance(value, list) else _split(str(value))
    return list(dict.fromkeys(_option(name, p, options) for p in parts))


def _boolean_value(name: str, value: Any, _options: list[str]) -> bool:
    answer = value if isinstance(value, bool) else _yes_no(str(value), name)
    if answer is None:
        raise GatewayRefusal(f"{name} is yes or no.")
    return answer


def _date_value(name: str, value: Any, _options: list[str]) -> str:
    day = _date_arg(str(value), name)
    if not day:
        raise GatewayRefusal(f"{name} is a date, YYYY-MM-DD.")
    return day


def _text_value(name: str, value: Any, _options: list[str]) -> str:
    if isinstance(value, list | dict):
        raise GatewayRefusal(f"{name} takes text, not a list or an object.")
    return str(value).strip()


def _url_value(name: str, value: Any, _options: list[str]) -> str:
    text_value = _text_value(name, value, _options)
    if text_value and not re.match(r"^https?://\S+$", text_value):
        raise GatewayRefusal(f"{name} takes a http(s) address, not {data(text_value)}.")
    return text_value


#: One shape per field type, as ``custom_fields._COERCERS`` holds one check
#: per type. The tool turns the model's words into the wire shape (an option
#: in its own case, ``yes`` into ``true``). The PATCH route still checks each
#: value, so a drift here is a refusal after the card, never a bad value.
_FIELD_SHAPES: dict[str, Callable[[str, Any, list[str]], Any]] = {
    "select": _option,
    "multi_select": _many_value,
    "boolean": _boolean_value,
    "number": _number_value,
    "date": _date_value,
    "text": _text_value,
    "url": _url_value,
}


def _field_value(definition: dict[str, Any], value: Any) -> Any:
    """One value in the shape the route stores, ``None`` to empty it, or the refusal."""
    if value is None:
        return None
    shape = _FIELD_SHAPES.get(str(definition.get("field_type") or "text"), _text_value)
    options = [str(o) for o in definition.get("options") or []]
    return shape(data(definition.get("name")), value, options)


def _shown(value: Any) -> str:
    """A field value as the card prints it."""
    if value is None:
        return "(empty)"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "(empty)"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


async def field_values(
    project_id: str, raw: str, *, current: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """``fields`` resolved against the project's own fields.

    Returns ``(wire, card, before)``. ``wire`` is keyed by ``field_key``,
    as the route takes it. ``card`` and ``before`` are keyed ``field <Name>``,
    as the member reads them. A name the project does not define is refused
    with the real names (``_field_of``). ``current`` is the task's stored
    values, for an edit, so the card shows before → after.
    """
    given = _parse_fields(raw)
    rows = await _vocab(project_id, "fields")
    wire: dict[str, Any] = {}
    card: dict[str, Any] = {}
    before: dict[str, Any] = {}
    for name, value in given.items():
        definition = _field_of(rows, str(name))
        key = str(definition.get("field_key"))
        label = f"field {definition.get('name')}"
        wire[key] = _field_value(definition, value)
        card[label] = _shown(wire[key])
        if current is not None:
            before[label] = _shown(current.get(key))
    return wire, card, before


# ── Tasks ────────────────────────────────────────────────────────────────────


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
@takes_priority
@agent_assignee_refusal_as_text
async def create_task(
    project_id: str,
    title: str,
    description: str = "",
    status: str = "",
    assignees: str = "",
    due: str = "",
    estimate_mins: int = 0,
    tags: str = "",
    parent_task_id: str = "",
    priority: str = "",
    important: str = "",
    leveraged: str = "",
    importance: Removed = None,
    repeat: str = "",
    repeat_every: int = 1,
    repeat_on: str = "",
    repeat_day: int = 0,
    repeat_month: int = 0,
    repeat_from: str = "",
    repeat_until: str = "",
    repeat_times: int = 0,
    start: str = "",
    type: str = "",
    fields: str = "",
) -> str:
    """Create one task in a project. project_id is a `full_id` from
    projects_tree (a project or subproject, never a folder). status is by
    NAME from that project's vocabulary, or omitted for the default.
    assignees is comma-separated emails, agent:<name>, or people's names.
    due is YYYY-MM-DD. tags is comma-separated. Omit every priority argument
    to leave the task unjudged.
    parent_task_id makes it a subtask. A subtask with no status lands in the
    parent's status when that status is open. The member approves a card first;
    nothing is created if they decline. Archive is the undo.
    A repeating task is ONE call: pass repeat (daily, weekly, monthly or
    yearly), and never put "weekly" in the title. repeat_every is every N
    (1 to 365, default 1). repeat_on is comma-separated weekdays, 1 (Monday)
    to 7 (Sunday). A weekly task with no repeat_on takes the due date's
    weekday, else today's, and the card names the day. repeat_day is the day
    of the month, needed for monthly and yearly. repeat_month is the month
    for yearly. repeat_from is due (keep the schedule, the default) or
    completed. repeat_until is YYYY-MM-DD. repeat_times is the most copies.
    With no due, due becomes the first day the rule lands on. The next copy
    appears when this one is done. set_recurrence changes or stops it.
    start is YYYY-MM-DD, the day the work begins. type is a task type by
    NAME (vocabulary lists them). fields sets custom field values: a JSON
    object keyed by field NAME, for example {"Customer": "Acme"}. A choice
    field takes one of its options, and the card shows every value."""
    pid = uuid_of(project_id, "project_id")
    name = str(title or "").strip()
    if not name:
        return "A task needs a title."
    flags = priority_fields(
        priority=priority, important=important, leveraged=leveraged, importance=importance
    )
    if isinstance(flags, str):
        return flags
    repeating = await _repeat_for_create(
        repeat=repeat,
        every=repeat_every,
        on=repeat_on,
        day=repeat_day,
        month=repeat_month,
        anchor=repeat_from,
        until=repeat_until,
        times=repeat_times,
        due=due,
    )
    if isinstance(repeating, str):
        return repeating
    new = await _prepare_new_task(
        pid,
        name,
        description=description,
        status=status,
        due_at=repeating.due_at(due),
        flags=flags,
        estimate_mins=estimate_mins,
        tags=_split(tags),
        start=start,
        type_name=type,
        fields=fields,
        parent_task_id=parent_task_id,
        assignees=_split(assignees),
    )
    card = _priority_card(new.payload)
    # The card names the type and each field by NAME (`extra.card`).
    card.pop("type_id", None)
    card.pop("custom_fields", None)
    card["status"] = new.status_label
    card.update(new.extra.card)
    card.update(await _assignee_card_lines(new.who))
    card.update(repeating.card_lines())
    if not await _confirm(
        title=repeating.card_title,
        detail=f"{data(name)} · status {new.status_label}"
        + (f" · {', '.join(new.who)}" if new.who else "")
        + repeating.detail(),
        context=_fields_block(card),
    ):
        return CANCELLED
    task, saved, failed = await _create_new_task(new, repeating)
    status_label = new.status_label
    if "status_id" not in new.payload:
        # The receipt names the lane the row really landed in, read off the
        # created row, as `add_subtasks` does. The card was a forecast. The
        # read runs after every write, and `_lane_name` never raises.
        status_label = await _lane_name(pid, task.get("status_id")) or status_label
    lines = [*_task_line(task, status_label), *level_note(priority, task)]
    return repeating.receipt(task, lines, saved, failed, new.extra)


class _NewTask:
    """One new task, resolved before its card: the POST body and its follow-ups.

    ``create_task`` makes one and ``create_tasks`` (``forms.py``, WS-46 P13)
    makes one per row, so both send the same body for the same words.
    ``status_label`` is what the card says the status will be. ``who`` is
    the resolved assignees, which the assign PUT sends after the create.
    """

    def __init__(
        self,
        pid: str,
        payload: dict[str, Any],
        status_label: str,
        who: list[str],
        extra: _NewFields,
    ) -> None:
        self.pid = pid
        self.payload = payload
        self.status_label = status_label
        self.who = who
        self.extra = extra
        #: The parent task's row, read before the card, for a subtask. The
        #: batch card names it (WS-46 P13 review round 2).
        self.parent: dict[str, Any] | None = None


async def _prepare_new_task(
    pid: str,
    name: str,
    *,
    description: str = "",
    status: str = "",
    due_at: str = "",
    flags: dict[str, Any] | None = None,
    estimate_mins: Any = 0,
    tags: list[str] | None = None,
    start: str = "",
    type_name: str = "",
    fields: str = "",
    parent_task_id: str = "",
    assignees: list[str] | None = None,
    statuses: list[dict[str, Any]] | None = None,
) -> _NewTask:
    """Every name of one new task resolved, before any card. Raises the refusal.

    Only reads happen here. ``statuses`` is the project's lane list when the
    caller read it already, so a batch reads it once and not once per row.
    """
    payload: dict[str, Any] = {"project_id": pid, "title": name}
    if description.strip():
        payload["description"] = description.strip()
    if status.strip():
        rows = statuses if statuses is not None else await _statuses_of(pid)
        row = _one_named(rows, status, "status", "statuses")
        payload["status_id"] = str(row.get("id"))
        status_label = str(row.get("name"))
    else:
        status_label = "the default"
    if due_at:
        payload["due_at"] = due_at
    payload.update(flags or {})
    est = _int_or_none(estimate_mins)
    if est is not None:
        payload["estimate_mins"] = est
    if tags:
        payload["tags"] = list(tags)
    extra = await _new_task_fields(
        pid, start=start, type_name=type_name, fields=fields, has_parent=bool(parent_task_id.strip())
    )
    payload.update(extra.payload)
    parent: dict[str, Any] | None = None
    if parent_task_id.strip():
        parent_id, parent = await _task(parent_task_id)
        payload["parent_task_id"] = parent_id
        if "status_id" not in payload:
            # The gateway puts a step with no stated status in the parent's
            # lane (`core.parent_lane_status`, §11.42), so "the default" on
            # the card would be false.
            status_label = await _parent_lane_label(pid, parent)
    who = [await _resolve_assignee(a) for a in assignees or []]
    new = _NewTask(pid, payload, status_label, who, extra)
    new.parent = parent
    return new


async def _create_new_task(
    new: _NewTask, repeating: _Repeat | None = None
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, Exception] | None]:
    """The one write path of a new task: the POST, then its follow-ups.

    The POST may raise, because nothing exists before it. After it the task
    exists. Each later write that fails comes back as ``failed`` and never as
    a raise: a raise reads to the model as "Function failed", and a second
    create makes a second task.
    """
    task = await _post_new_task(new)
    saved, failed = await _follow_new_task(task, new, repeating)
    return task, saved, failed


async def _post_new_task(new: _NewTask) -> dict[str, Any]:
    """The create itself. It may raise, because nothing exists before it."""
    return await post("/projects/tasks", new.payload)


async def _follow_new_task(
    task: dict[str, Any], new: _NewTask, repeating: _Repeat | None = None
) -> tuple[dict[str, Any], tuple[str, Exception] | None]:
    """The writes after the create: the assignees, then the repeat rule.

    ``create_tasks`` calls the two halves apart, so an error after the POST
    is a follow-up that failed, never a task that may not exist.
    """
    tid = uuid_of(str(task.get("id")), "task_id")
    return await _after_create(tid, task, new.who, new.extra, repeating or NO_REPEAT)


#: A write whose connection broke may or may not have landed. Both kinds end
#: a run of writes with a `stopped:` receipt (``forms.py`` uses the same pair).
_WRITE_FAILED = (GatewayRefusal, httpx.TransportError)

#: The words of a create's partial receipt, for each write after the create:
#: the subject, its verb, the read that checks it, and the tool that finishes it.
_AFTER_CREATE: dict[str, tuple[str, str, str, str]] = {
    "assignees": ("assignees", "were", "task_detail", "assign"),
    "rule": ("repeat rule", "was", "recurrence", "set_recurrence"),
}


class _NewFields:
    """The P6 fields of a create (G5, G6, G7), resolved before the card.

    ``payload`` goes in the POST: the start date, the type and the custom
    values (``custom_fields``, keyed by ``field_key``). ``values`` is that
    last part again, for the receipt. The create route checks each value
    through ``custom_fields.apply_values`` (#679), in the transaction of the
    insert. So a value the route refuses leaves no task behind, and the
    create is one atomic write. ``card`` is what the member reads.
    """

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.card: dict[str, Any] = {}
        self.values: dict[str, Any] = {}

    def receipt_lines(self) -> list[str]:
        return [f"  {k}: {v}" for k, v in self.card.items() if k.startswith("field ")]


async def _new_task_fields(
    project_id: str, *, start: str, type_name: str, fields: str, has_parent: bool
) -> _NewFields:
    """The start, the type and the custom values of a new task, or the refusal.

    Every name resolves against the project's own words before the card. An
    epic type under a parent is refused here, as the route would refuse it.
    """
    out = _NewFields()
    day = _date_arg(start, "start")
    if day:
        out.payload["start_date"] = day
        out.card["start"] = day
    if str(type_name or "").strip():
        row = await _resolve_type(project_id, type_name)
        refused = _epic_refusal(row, has_parent)
        if refused:
            raise GatewayRefusal(refused)
        out.payload["type_id"] = str(row.get("id"))
        out.card["type"] = row.get("name")
    if str(fields or "").strip():
        out.values, lines, _before = await field_values(project_id, fields)
        if any(v is None for v in out.values.values()):
            raise GatewayRefusal(
                "A new task has no value to empty. Leave that field out of fields."
            )
        out.payload["custom_fields"] = out.values
        out.card.update(lines)
    return out


async def _after_create(
    task_id: str,
    task: dict[str, Any],
    who: list[str],
    extra: _NewFields,
    repeating: _Repeat,
) -> tuple[dict[str, Any], tuple[str, Exception] | None]:
    """The writes after the create, in order, under its one card.

    The assignees, then the repeat rule (P1). The first one that fails stops
    the rest, and the receipt names it. The custom values are not here: they
    are in the create itself (:class:`_NewFields`).
    """
    failed = await _assign_after_create(task_id, task, who)
    if failed is not None:
        return {}, failed
    return await repeating.save(task_id)


async def _assign_after_create(
    task_id: str, task: dict[str, Any], who: list[str]
) -> tuple[str, Exception] | None:
    """The assign PUT after a create, or the failure that stops the receipt."""
    if not who:
        return None
    tid = uuid_of(task_id, "task_id")  # the writes fence: a canonical id
    try:
        await put(f"/projects/tasks/{tid}/assignees", {"assignees": who})
    except _WRITE_FAILED as exc:
        return "assignees", exc
    task["assignees"] = who
    return None


def _create_stopped(
    task: dict[str, Any], lines: list[str], step: str, exc: Exception, left: str
) -> str:
    """The receipt of a create whose later write failed (§8.1 item 6).

    The task exists, so the receipt carries its ``full_id`` and a
    ``stopped:`` line, and the receipt card shows a partial result. A broken
    connection says nothing about the write it carried (``forms._stopped``),
    so the model reads the row before it tries once more.
    """
    noun, verb, check, fix = _AFTER_CREATE[step]
    n = _number(task)
    if isinstance(exc, httpx.TransportError):
        head = f"Created {n}. The {noun} may or may not be saved."
        stop = (
            f"stopped: the write of the {noun} lost its connection to the gateway "
            f"({type(exc).__name__}). "
            f"That write may or may not have landed. Read {check} for this task "
            f"first, and use {fix} only if it is not there."
        )
    else:
        head = f"Created {n}. The {noun} {verb} NOT saved: {data(str(exc))}."
        stop = f"stopped: the {noun} {verb} refused. {fix} on this task can finish it."
    out = [head, *lines, stop]
    if left:
        out.append(f"not tried: {left}")
    out.append(f"Never call create_task again for this task. It exists as {n}.")
    return "\n".join(out)


#: What a member who looks for next week's copy today must be told. There is
#: no scheduler (``recurrence.py``): the successor is made when a task closes.
NEXT_COPY = (
    "The next one appears when this one is done. No copy exists before then, "
    "and none is made while the project is paused."
)


async def _assignee_card_lines(who: list[str]) -> dict[str, Any]:
    """The create card's assignee lines, with the addresses the directory
    does not know (H-162). Empty when nobody is assigned."""
    if not who:
        return {}
    lines = {"assignees": ", ".join(who)}
    strangers = await _unknown_addresses(who)
    if strangers:
        lines["not in the directory"] = ", ".join(strangers)
    return lines


#: The card's words for a step whose lane the preview cannot name.
PARENT_LANE_FALLBACK = "the parent's lane when it is open, else the default"


async def _parent_lane_label(project_id: str, parent: dict[str, Any]) -> str:
    """What the card says a new step's status will be.

    A forecast of ``core.parent_lane_status`` from the same statuses read
    ``_lane_name`` uses: the parent's lane when it is in this project's set,
    open and not triage, else the first lane. The gateway decides, and the
    receipt reads the real row, so a forecast that drifts cannot make the
    receipt false. When the read fails, the card states the rule instead.
    """
    try:
        rows = await _statuses_of(project_id)
    except Exception:  # a card detail, never a write
        return PARENT_LANE_FALLBACK
    lane = next(
        (r for r in rows if str(r.get("id")) == str(parent.get("status_id"))), None
    )
    if lane is not None and lane.get("category") not in ("done", "cancelled", "triage"):
        return f"{data(lane.get('name'))} (the parent's lane)"
    return "the default (the parent's lane is closed or in another set)"


class _EditFields:
    """The P6 half of an edit (G6, G7, G9), resolved before the card.

    ``payload`` joins the PATCH body. ``params`` is its query string
    (``include_subtasks``). ``question`` is D-PM-38's question, when the
    member has not said what the subtasks do.
    """

    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}
        self.card: dict[str, Any] = {}
        self.before: dict[str, Any] = {}
        self.params: dict[str, Any] = {}
        self.question = ""


def _type_name(rows: list[dict[str, Any]], type_id: Any) -> str:
    """The name of the task's current type, for the card's before value."""
    if not type_id:
        return "(none)"
    found = next((r for r in rows if str(r.get("id")) == str(type_id)), None)
    return str(found.get("name")) if found else "(a type of another project)"


async def _edit_type(out: _EditFields, task: dict[str, Any], type_name: str, clear: bool) -> None:
    """The type half of an edit: a type by name, or ``clear=type``."""
    named = bool(str(type_name or "").strip())
    if not named and not clear:
        return
    if named and clear:
        raise GatewayRefusal("type is both set and cleared. Pass one or the other.")
    rows = await _vocab(str(task.get("project_id")), "types")
    out.before["type"] = _type_name(rows, task.get("type_id"))
    if clear:
        out.card["type"] = "(none)"
        return
    row = _one_named(rows, type_name, "task type")
    refused = _epic_refusal(row, bool(task.get("parent_task_id")))
    if refused:
        raise GatewayRefusal(refused)
    out.payload["type_id"] = str(row.get("id"))
    out.card["type"] = row.get("name")


async def _edit_subtasks(
    out: _EditFields, task: dict[str, Any], lane: dict[str, Any] | None, include_subtasks: str
) -> None:
    """``include_subtasks`` on an edit: it means something only with a move
    into a Done lane, where the route completes the open subtasks too."""
    wanted = _subtasks_wanted(include_subtasks)
    # The PATCH route cascades on a status CHANGE into a Done lane only
    # (`tasks.patch_task`), so a task already in that lane closes nothing.
    closing = (
        lane is not None
        and lane.get("category") == "done"
        and str(lane.get("id")) != str(task.get("status_id"))
    )
    if not closing:
        if wanted is not None:
            raise GatewayRefusal(
                "include_subtasks goes with a status change into a Done lane. Pass that "
                "status for a task that is not in it yet, or use complete."
            )
        return
    count = await _subtask_counts(str(task.get("id")))
    if wanted is None and count.open:
        out.question = ask_about_subtasks(task, count.open, "complete", count.capped)
        return
    if wanted:
        out.params["include_subtasks"] = True
    out.card.update(_subtask_line("complete", bool(wanted), count.open, count.capped))


async def _edit_task_fields(
    task: dict[str, Any],
    *,
    type_name: str,
    fields: str,
    clear_type: bool,
    lane: dict[str, Any] | None,
    include_subtasks: str,
) -> _EditFields:
    """The type, the custom values and the subtasks of an edit, or the refusal."""
    out = _EditFields()
    await _edit_type(out, task, type_name, clear_type)
    if str(fields or "").strip():
        values, card, before = await field_values(
            str(task.get("project_id")), fields, current=task.get("custom_fields") or {}
        )
        out.payload["custom_fields"] = values
        out.card.update(card)
        out.before.update(before)
    await _edit_subtasks(out, task, lane, include_subtasks)
    return out


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
@takes_priority
async def update_task(
    task_id: str,
    title: str = "",
    description: str = "",
    status: str = "",
    due: str = "",
    start: str = "",
    estimate_mins: int = 0,
    tags: str = "",
    clear: str = "",
    priority: str = "",
    important: str = "",
    leveraged: str = "",
    importance: Removed = None,
    type: str = "",
    fields: str = "",
    include_subtasks: str = "",
) -> str:
    """Change a task's fields. Only the arguments you pass change. status is
    by NAME from the task's project. due and start are YYYY-MM-DD. tags
    REPLACES the tag list. clear is a comma-separated list of fields to
    empty: due, start, description, estimate, type, important (the task is
    then unjudged), leveraged, or priority (both flags). type is a task type
    by NAME (vocabulary lists them). fields sets custom field values: a JSON
    object keyed by field NAME, for example {"Customer": "Acme"}, and a null
    value empties one field. include_subtasks (yes or no) goes with a status
    in a Done lane: yes completes the open subtasks too. With open subtasks
    and no answer, the tool asks first. The card shows each change as before
    → after, and the timeline records every change."""
    tid, task = await _task(task_id)
    flags = priority_fields(
        priority=priority,
        important=important,
        leveraged=leveraged,
        importance=importance,
        current=task,
    )
    if isinstance(flags, str):
        return flags
    # Every flag the member STATED, including one the task already holds.
    # `flags` drops those, and a clear of a stated flag must still be refused.
    stated = priority_fields(priority=priority, important=important, leveraged=leveraged)
    payload: dict[str, Any] = {}
    before: dict[str, Any] = {}
    if title.strip():
        payload["title"] = title.strip()
        before["title"] = task.get("title")
    if description.strip():
        payload["description"] = description.strip()
        before["description"] = (task.get("description") or "")[:200]
    lane: dict[str, Any] | None = None
    if status.strip():
        lane = await _resolve_status(str(task.get("project_id")), status)
        payload["status_id"] = str(lane.get("id"))
    if due.strip():
        payload["due_at"] = due.strip()
        before["due_at"] = (task.get("due_at") or "")[:10]
    if start.strip():
        payload["start_date"] = _date_arg(start, "start")
        before["start_date"] = (task.get("start_date") or "")[:10]
    payload.update(flags)
    est = _int_or_none(estimate_mins)
    if est is not None:
        payload["estimate_mins"] = est
        before["estimate_mins"] = task.get("estimate_mins")
    if tags.strip():
        payload["tags"] = _split(tags)
        before["tags"] = task.get("tags")
    extra = await _edit_task_fields(
        task,
        type_name=type,
        fields=fields,
        clear_type="type" in {w.lower() for w in _split(clear)},
        lane=lane,
        include_subtasks=include_subtasks,
    )
    if extra.question:
        return extra.question
    payload.update(extra.payload)
    taken = {**(stated if isinstance(stated, dict) else flags), **extra.payload}
    cleared = _edit_clears(clear, taken, task, before)
    if isinstance(cleared, str):
        return cleared
    # Cleared fields go FIRST in the card. The card clips its tail at 4000
    # characters, and a long description must never push "due → None" out of
    # sight: a clear is the change a member most needs to see.
    payload = {**cleared, **payload}
    if not payload:
        return "Nothing to change. Pass at least one field."
    card, detail = _edit_card(payload, before, extra, lane, task)
    if not await _confirm(
        title="Update this task?", detail=detail, context=_fields_block(card, before=before)
    ):
        return CANCELLED
    updated = await patch(f"/projects/tasks/{tid}", payload, params=extra.params)
    updated["assignees"] = task.get("assignees") or []
    return "\n".join(
        [
            "Updated:",
            *_task_line(updated, str(lane.get("name")) if lane else ""),
            *level_note(priority, updated),
            *_subtask_receipt(updated, "subtasks_completed", "complete"),
        ]
    )


def _edit_clears(
    clear: str, taken: dict[str, Any], task: dict[str, Any], before: dict[str, Any]
) -> dict[str, Any] | str:
    """The fields ``clear`` empties, each with its value before, or the refusal."""
    cleared = _clears(clear, _CLEAR_WORDS, taken)
    if isinstance(cleared, str):
        return cleared
    for key in cleared:
        before[key] = task.get(key)
    return cleared


def _edit_card(
    payload: dict[str, Any],
    before: dict[str, Any],
    extra: _EditFields,
    lane: dict[str, Any] | None,
    task: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """``update_task``'s card and its detail line. *before* gains the P6 lines.

    The card names a status, a type and a field by NAME, never by id, so the
    id keys of the wire body are not shown.
    """
    status_label = str(lane.get("name")) if lane else ""
    card = _priority_card(payload, before=before, task=task)
    for key in ("status_id", "type_id", "custom_fields"):
        card.pop(key, None)
        before.pop(key, None)
    if status_label:
        card["status"] = status_label
    card.update(extra.card)
    before.update(extra.before)
    detail = _ref(task) + (f" · status → {status_label}" if status_label else "")
    return card, detail


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
@agent_assignee_refusal_as_text
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def add_subtasks(task_id: str, titles: str) -> str:
    """Break a task into steps. titles is one subtask per line, or
    comma-separated. ONE card lists every subtask; the member approves the
    batch once. Each subtask lands in the parent's project, in the parent's
    status when that status is open, else in the project's first status.
    The receipt names the status. Archive undoes any of them."""
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
    # No status is sent: the gateway puts each step in the parent's lane
    # when that lane is open (`core.parent_lane_status`, D-PM-38).
    rows = [
        await post(
            "/projects/tasks", {"project_id": pid, "title": title, "parent_task_id": tid}
        )
        for title in parts
    ]
    lane = await _lane_name(pid, rows[0].get("status_id"))
    # The parent's id FIRST: the receipt card opens the first `full_id`,
    # and its heading names the parent (H-162). The heading also names the
    # lane the steps landed in, as every status receipt does (D79).
    head = f"Added to {data(lane)} under {_ref(parent)}:" if lane else f"Added under {_ref(parent)}:"
    out = [head, f"  full_id: {tid}"]
    for row in rows:
        out.extend(_task_line(row, lane))
    return "\n".join(out)


async def _lane_name(project_id: str, status_id: Any) -> str:
    """The name of the lane a new row landed in, or ``""``.

    Read after the writes, so a failed read must not hide them: the rows
    exist, and a raised error would tell the model that nothing was written.
    """
    if not status_id:
        return ""
    try:
        rows = await _statuses_of(project_id)
    except Exception:  # a receipt detail, never a write
        return ""
    return next(
        (str(r.get("name") or "") for r in rows if str(r.get("id")) == str(status_id)),
        "",
    )


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def move_task(
    task_ids: str,
    destination_project_id: str = "",
    parent_task_id: str = "",
    fields: str = "",
    include_subtasks: str = "",
) -> str:
    """Move tasks. With destination_project_id, moves the listed tasks (all
    from ONE source project, comma-separated ids) into another project: the
    server previews what carries over and what drops, the card shows that,
    and the move applies only the drops the member saw. With
    parent_task_id alone, re-parents one task under another in the same
    project. Moving back is the undo.
    fields answers the destination's REQUIRED fields, for ONE task: a JSON
    object keyed by field NAME, for example {"Cost centre": 4200}. When the
    destination requires a field the task lacks, the tool names each one.
    include_subtasks (yes or no) takes the subtasks along. When the tasks
    have subtasks and no answer is given, the tool asks first."""
    ids = [uuid_of(t, "task_id") for t in _split(task_ids)]
    if not ids:
        return "Give at least one task id."
    if destination_project_id.strip():
        move = await _plan_move(ids, destination_project_id, fields, include_subtasks)
    elif fields.strip() or include_subtasks.strip():
        return (
            "fields and include_subtasks go with destination_project_id. They are about a "
            "move into another project."
        )
    elif parent_task_id.strip():
        move = await _plan_reparent(ids, parent_task_id)
    else:
        return (
            "Pass destination_project_id to move between projects, or parent_task_id to "
            "re-parent."
        )
    if isinstance(move, str):
        return move
    if not await _confirm(title=move.title, detail=move.detail, context=move.context):
        return CANCELLED
    return await move.apply()


class _Move:
    """A move the member has not yet approved: its card, and the one write.

    ``path`` is the route, ``body`` its JSON. ``receipt`` turns the route's
    answer into what the member reads.
    """

    def __init__(
        self,
        title: str,
        detail: str,
        context: str,
        path: str,
        body: dict[str, Any],
        receipt: Callable[[Any], str],
    ) -> None:
        self.title = title
        self.detail = detail
        self.context = context
        self.path = path
        self.body = body
        self.receipt = receipt

    async def apply(self) -> str:
        return self.receipt(await post(self.path, self.body))


async def _plan_reparent(ids: list[str], parent_task_id: str) -> _Move | str:
    """Make one task a subtask of another, in the same project."""
    if len(ids) != 1:
        return "Re-parenting takes exactly one task id."
    tid, task = await _task(ids[0])
    parent_id, parent = await _task(parent_task_id)
    return _Move(
        title="Make this a subtask?",
        detail=f"{_ref(task)} under {_ref(parent)}",
        context=_fields_block({"task": _ref(task), "parent": _ref(parent)}),
        path=f"/projects/tasks/{tid}/move",
        body={"parent_task_id": parent_id},
        receipt=lambda row: "\n".join([f"Now a subtask of {_ref(parent)}:", *_task_line(row)]),
    )


def _move_subtask_refusal(plan: dict[str, Any], wanted: bool | None) -> str:
    """Why a move that takes its subtasks cannot run, before the card.

    The gateway refuses a move whose subtree holds a task hidden from the
    member (409), and a subtask that D62 keeps out of the destination (422).
    """
    subtasks = plan.get("subtasks") or {}
    if not wanted:
        return ""
    hidden = int(subtasks.get("hidden") or 0)
    if hidden:
        return (
            f"{hidden} subtask{'' if hidden == 1 else 's'} of these tasks "
            f"{'is' if hidden == 1 else 'are'} hidden from you, so the move cannot take "
            "them along. Pass include_subtasks=no to move only the tasks."
        )
    refused = subtasks.get("refused") or []
    if refused:
        return f"{data(refused[0].get('reason'))} Pass include_subtasks=no to move only the tasks."
    return ""


async def _required_refusal(dest: str, missing: list[str], many: bool) -> str:
    """The destination's required fields that the move does not answer.

    Each one is named with its type and its options, so the member can give
    a value in one answer, as the app's promote dialog asks for them.
    """
    rows = await _vocab(dest, "fields")
    wanted = {m.strip().lower() for m in missing}
    described = []
    for row in rows:
        if {str(row.get("name") or "").lower(), str(row.get("field_key") or "").lower()} & wanted:
            kind = str(row.get("field_type") or "text")
            options = row.get("options") or []
            hint = f"one of {', '.join(data(o) for o in options)}" if options else kind
            described.append(f"{data(row.get('name'))} ({hint})")
    names = ", ".join(described) or ", ".join(data(m) for m in missing)
    out = (
        f"The destination requires {names}, which these tasks do not carry. Ask the member "
        "for each value, then pass fields, for example "
        '{"<field name>": "<value>"}. Nothing was changed.'
    )
    if many:
        out += " fields takes one task, so move the tasks one at a time."
    return out


def _blank(value: Any) -> bool:
    """Absent, as ``custom_fields._is_blank`` judges it: ``None``, blank text,
    an empty list. ``0`` and ``False`` are answers."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, list | dict) and not value


def _unanswered(missing: list[str], card: dict[str, Any], values: dict[str, Any]) -> list[str]:
    """The required fields among *missing* that ``fields`` gives no value.

    *card* and *values* are filled in one loop (:func:`field_values`), so
    the n-th label names the n-th key. A blank answer is no answer, as the
    route's required check judges it.
    """
    given: set[str] = set()
    for label, (key, value) in zip(card, values.items(), strict=False):
        if not _blank(value):
            given |= {key.lower(), label.removeprefix("field ").lower()}
    return [m for m in missing if m.strip().lower() not in given]


async def _plan_move(
    ids: list[str], destination: str, fields: str, include_subtasks: str
) -> _Move | str:
    """The move into another project: the preview, then its card and its write."""
    if len(ids) > MAX_BATCH:
        return f"That is {len(ids)} tasks. The limit for one card is {MAX_BATCH}."
    answers = bool(str(fields or "").strip())
    if answers and len(ids) != 1:
        return (
            "fields takes one task, as the app asks for a destination's fields one task "
            "at a time. Move the tasks one by one, each with its fields."
        )
    wanted = _subtasks_wanted(include_subtasks)
    dest = uuid_of(destination, "destination_project_id")
    dest_node = await get(f"/projects/nodes/{dest}")
    # The card NAMES every task it moves (class B may list many rows),
    # so the member sees which three, not "3 tasks". Read before the
    # card, like every other row a card names.
    tasks = [(await _task(t))[1] for t in ids]
    if all(str(t.get("project_id")) == dest for t in tasks):
        return f"Those tasks are already in {data(dest_node.get('name'))}."
    # The preview WRITES NOTHING (move.py `preview_move`). It computes the
    # plan the apply will use, and it is the read that names what this
    # costs, the subtasks' cost too when they go along (D-PM-29). Two
    # refusals live in the apply alone (same-project, and a status the
    # destination lacks per task), so a 422 after the card is still possible.
    ask: dict[str, Any] = {"task_ids": ids, "destination_project_id": dest}
    if wanted:
        ask["include_subtasks"] = True
    plan = await post("/projects/tasks/move/preview", ask)
    count = int((plan.get("subtasks") or {}).get("count") or 0)
    if wanted is None and count:
        head = tasks[0] if len(tasks) == 1 else {"title": f"This selection of {len(tasks)}"}
        return ask_about_subtasks(head, count, "move")
    refused = _move_subtask_refusal(plan, wanted)
    if refused:
        return refused
    values: dict[str, Any] = {}
    field_card: dict[str, Any] = {}
    if answers:
        if not plan.get("crosses_root"):
            return (
                "This move stays inside one space, so the destination asks for no field. "
                "Move without fields, then set values with update_task fields."
            )
        values, field_card, _before = await field_values(dest, fields)
    missing = _unanswered(plan.get("required_missing") or [], field_card, values)
    if missing:
        return await _required_refusal(dest, missing, len(ids) > 1)
    card = _move_card(plan, tasks, dest_node, field_card, bool(wanted), count)
    drops = sorted(plan.get("drops") or [])
    if answers:
        # One task, with the answers: the promote door's route, which lands
        # the values under the destination's keys and checks each required
        # field as it lands (`tasks.move_task_in`).
        tid = uuid_of(ids[0], "task_id")
        path = f"/projects/tasks/{tid}/move"
        body: dict[str, Any] = {"project_id": dest, "custom_fields": values}
    else:
        path = "/projects/tasks/move"
        body = {"task_ids": ids, "destination_project_id": dest}
        if drops:
            # The member saw exactly these drops. The apply refuses with 409
            # if the destination changed and would now drop more (D-PM-29).
            body["accept_drops"] = True
            body["accepted_drops"] = drops
    if wanted:
        body["include_subtasks"] = True
    return _Move(
        title=f"Move {card['tasks']} task{'s' if card['tasks'] != 1 else ''}?",
        detail=f"to {card['to']}" + (f" · drops {', '.join(drops)}" if drops else ""),
        context=_fields_block(card),
        path=path,
        body=body,
        receipt=lambda result: _move_receipt(result, tasks, dest, card["to"], answers),
    )


def _move_card(
    plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    dest_node: dict[str, Any],
    field_card: dict[str, Any],
    wanted: bool,
    count: int,
) -> dict[str, Any]:
    """The move card: the tasks, what re-points, what drops, the answers given."""
    card: dict[str, Any] = {
        "tasks": plan.get("task_count", len(tasks)),
        "to": data(dest_node.get("name")),
    }
    for i, t in enumerate(tasks):
        card[f"task {i + 1}"] = _ref(t)
    if plan.get("crosses_status_set"):
        card["statuses"] = "re-pointed to the destination's lanes by category"
    drops = sorted(plan.get("drops") or [])
    if drops:
        card["drops (values with no field in the destination)"] = ", ".join(drops)
    card.update(field_card)
    card.update(_subtask_line("move", wanted, count))
    return card


def _move_receipt(
    result: Any, tasks: list[dict[str, Any]], dest: str, where: str, one: bool
) -> str:
    """What the member reads after the move."""
    moved = 1 if one else (result.get("moved") if isinstance(result, dict) else None)
    moved = moved if isinstance(moved, int) else len(tasks)
    out = [f"Moved {moved} task{'s' if moved != 1 else ''} to {where}:"]
    for t in tasks:
        out.extend(_task_line({**t, "project_id": dest}))
    out.extend(_subtask_receipt(result, "subtasks_moved", "move"))
    return "\n".join(out)


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
async def complete(task_id: str, include_subtasks: str = "") -> str:
    """Mark a task done. This moves the task's SHARED status to its
    project's done lane, for everyone. To reopen, set a status by name with
    update_task. include_subtasks (yes or no): yes completes the open
    subtasks too, each in its own Done lane. When the task has open subtasks
    and no answer is given, the tool asks first (D-PM-38)."""
    wanted = _subtasks_wanted(include_subtasks)
    tid, task = await _task(task_id)
    if task.get("completed_at"):
        return f"{_ref(task)} is already done."
    count = await _subtask_counts(tid)
    if wanted is None and count.open:
        return ask_about_subtasks(task, count.open, "complete", count.capped)
    card = {"task": _ref(task), "status": "the project's done lane"}
    card.update(_subtask_line("complete", bool(wanted), count.open, count.capped))
    if not await _confirm(
        title="Mark this task done?",
        detail=_ref(task),
        context=_fields_block(card),
    ):
        return CANCELLED
    params = {"include_subtasks": True} if wanted else None
    row = await post(f"/projects/tasks/{tid}/complete", params=params)
    merged = {**task, **(row if isinstance(row, dict) else {})}
    return "\n".join(
        [
            "Done:",
            *_task_line(merged, "done"),
            *_subtask_receipt(row, "subtasks_completed", "complete"),
        ]
    )


#: What a defer writes on the gateway (`personal.defer_task`).
_DEFER_DISPOSITION = "SOMEDAY"


def _defer_scope(task: dict[str, Any]) -> str:
    """The defer card's scope, chosen the way `_overlay_card` chooses its own.

    F2: the card must not promise "your inbox only" when the write would
    move the board. A defer writes SOMEDAY, and under D77 choice 3 SOMEDAY
    does not reopen a finished task, so today this answers "your inbox only"
    every time. It reads `completed_at` and `_REOPENING` anyway, so the card
    stays honest if the reopen rule ever changes.
    """
    if task.get("completed_at") and _DEFER_DISPOSITION in _REOPENING:
        return "reopens the task on the board, then hides it from your inbox"
    return "your inbox only"


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
async def defer(task_id: str, until: str) -> str:
    """Hide a task from the member's own inbox until a date (YYYY-MM-DD).
    Mine only: the team's board does not change, even on a finished task."""
    when = str(until or "").strip()
    if len(when) != 10:
        return "until is a date, YYYY-MM-DD."
    tid, task = await _task(task_id)
    if not await _confirm(
        title=f"Defer until {when}?",
        detail=_ref(task),
        context=_fields_block({
            "task": _ref(task), "until": when, "scope": _defer_scope(task),
        }),
    ):
        return CANCELLED
    await post(f"/projects/tasks/{tid}/defer", {"until": when})
    return f"Deferred {_ref(task)} until {when} in your inbox.\n  full_id: {tid}"


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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
        payload["lead"] = await _resolve_assignee(lead, dispatch=False)
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


#: The icons Space Settings offers (``lib/tree.ts`` ``SPACE_ICON_CHOICES``).
#: ``tests/unit/test_projects_project_fields.py`` holds the two lists equal,
#: so the chat never stores an icon the picker cannot draw.
SPACE_ICONS = (
    "Boxes", "Layers", "LayoutGrid", "Package",
    "Rocket", "Target", "Flag", "Star",
    "Building2", "Briefcase", "Users", "Globe",
    "Cpu", "Code", "Wrench", "Zap",
    "Palette", "Camera", "Megaphone", "ShoppingCart",
    "BookOpen", "Lightbulb", "Shield", "Truck",
    "Headphones", "Coffee", "Gem", "Puzzle",
    "Factory", "FlaskConical", "Printer", "Hammer",
    "Cog", "Database", "Server", "Cloud",
    "Home", "Landmark", "Mountain", "Waves",
    "Wallet", "CreditCard", "TrendingUp", "Activity",
    "Mail", "Phone", "MessageSquare", "Video",
    "Radio", "Mic", "PenTool", "Newspaper",
    "Calendar", "Clock", "Timer", "Sun",
    "Moon", "Flame", "Car", "Stethoscope",
    "Bot", "Brain", "Sparkles", "Monitor",
)
#: The colour slots of the categorical ramp, 1-based on the wire
#: (``core.ICON_SLOT_RANGE``, held equal by the same test).
ICON_SLOT_RANGE = (1, 12)
#: The settings a space keeps for its whole subtree (``core.LIFECYCLE_FIELDS``).
_ROOT_ONLY = ("archive_after_months", "close_after_months", "timezone")
#: ``update_project``'s clear words: each empties one setting.
_PROJECT_CLEAR = {
    "icon": ("icon",),
    "icon_slot": ("icon_slot",),
    "archive_after_months": ("archive_after_months",),
    "close_after_months": ("close_after_months",),
}


def _zone_refusal(name: str) -> str:
    """``""`` for an IANA zone, else the refusal (the route's own check,
    ``core.validate_lifecycle_settings``, said before the card)."""
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        # OSError: "America" is a directory of the zone database, not a zone.
        return f"timezone is an IANA name such as Asia/Kolkata, not {data(name)}."
    return ""


def _project_settings(
    node: dict[str, Any],
    icon: str,
    icon_slot: int,
    archive_after_months: int,
    close_after_months: int,
    timezone: str,
    clear: str,
) -> tuple[dict[str, Any], dict[str, Any]] | str:
    """The settings half of ``update_project``: ``(payload, before)``, or the
    refusal. Each check is the route's, said before the card."""
    payload: dict[str, Any] = {}
    if icon.strip():
        found = [i for i in SPACE_ICONS if i.lower() == icon.strip().lower()]
        if not found:
            return f"icon is one of the Space Settings icons: {', '.join(SPACE_ICONS)}."
        payload["icon"] = found[0]
    if _int_or_none(icon_slot) is not None:
        low, high = ICON_SLOT_RANGE
        if not low <= int(icon_slot) <= high:
            return f"icon_slot is a colour slot from {low} to {high}, not {icon_slot}."
        payload["icon_slot"] = int(icon_slot)
    for key, months in (
        ("archive_after_months", archive_after_months),
        ("close_after_months", close_after_months),
    ):
        if _int_or_none(months) is not None:
            if int(months) < 1:
                return f"{key} is a whole number of months, 1 or more. clear switches it off."
            payload[key] = int(months)
    if timezone.strip():
        refusal = _zone_refusal(timezone.strip())
        if refusal:
            return refusal
        payload["timezone"] = timezone.strip()
    refusal = _clear_into(
        payload, clear, _PROJECT_CLEAR,
        {"timezone": "A timezone cannot be cleared. Set timezone=UTC instead."},
    ) or _level_refusal(node, payload)
    return refusal or (payload, {k: node.get(k) for k in payload})


def _clear_into(
    payload: dict[str, Any],
    clear: str,
    words: dict[str, tuple[str, ...]],
    refused: dict[str, str] | None = None,
) -> str:
    """Put a ``None`` in *payload* for each field a ``clear`` word empties,
    and ``""``, or the refusal. A word that is also set is refused, never
    guessed. *refused* answers a word that may not be cleared."""
    for word in _split(clear):
        key = word.lower()
        if refused and key in refused:
            return refused[key]
        fields = words.get(key)
        if fields is None:
            return f"clear takes {', '.join(words)}, not {data(word)}."
        for field in fields:
            if field in payload:
                return f"{word} is both set and cleared. Pass one of the two."
            payload[field] = None
    return ""


def _level_refusal(node: dict[str, Any], payload: dict[str, Any]) -> str:
    """The two level rules of the node routes, before the card: an icon
    belongs to a space, and the lifecycle policy to a root (``tree.py``
    ``_refuse_identity_off_a_space`` and ``_refuse_lifecycle_on_child``)."""
    if node.get("parent_project_id") is None:
        return ""
    if {"icon", "icon_slot"} & set(payload):
        return (
            f"The icon and its colour belong to a space. {data(node.get('name'))} is not a "
            "space, so its marker is its run state or a folder."
        )
    if set(_ROOT_ONLY) & set(payload):
        return (
            "The lifecycle months and the timezone are settings of the space, and its "
            f"whole subtree takes them. {data(node.get('name'))} is not a space. Set them "
            "on its space."
        )
    return ""


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def update_project(
    project_id: str,
    name: str = "",
    description: str = "",
    status: str = "",
    lead: str = "",
    icon: str = "",
    icon_slot: int = 0,
    archive_after_months: int = 0,
    close_after_months: int = 0,
    timezone: str = "",
    clear: str = "",
) -> str:
    """Rename a node, change its description, its run state (active,
    paused, stopped) or its lead. Only the arguments you pass change. The
    card shows before → after. Re-parenting is a move (S3), and archiving
    is its own act.
    The settings of a SPACE (a node with no parent), as Space Settings and
    the Lifecycle panel set them: icon is a Space Settings icon name, such
    as Rocket. icon_slot is its colour, 1 to 12. archive_after_months
    archives done and cancelled work untouched for that many months.
    close_after_months closes open work untouched for that many months.
    timezone is an IANA name, such as Asia/Kolkata, for the midnight those
    months are measured from. clear switches settings off: icon, icon_slot,
    archive_after_months, close_after_months (comma-separated)."""
    pid = uuid_of(project_id, "project_id")
    node = await get(f"/projects/nodes/{pid}")
    settings = _project_settings(
        node, icon, icon_slot, archive_after_months, close_after_months, timezone, clear
    )
    if isinstance(settings, str):
        return settings
    payload: dict[str, Any] = dict(settings[0])
    before: dict[str, Any] = dict(settings[1])
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
        payload["lead"] = await _resolve_assignee(lead, dispatch=False)
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
    shown = [
        f"{key} {'off' if row.get(key) is None else data(row.get(key))}"
        for key in settings[0]
    ]
    tail = f" Settings: {' · '.join(shown)}." if shown else ""
    return f"Updated {data(row.get('name'))}.{tail}\n  full_id: {pid}"


# ── Reports ──────────────────────────────────────────────────────────────────

#: The route's `reports.SECTIONS`, held equal by
#: `tests/unit/test_projects_report_sections_lockstep.py`. `outlook`,
#: `capacity`, `pulse`, `hygiene`, `conflicts` and `rebalance` are opt-in:
#: a report carries one only when the member asks.
REPORT_SECTIONS = (
    "finished", "throughput", "outlook", "load", "capacity", "pulse", "stuck",
    "hygiene", "conflicts", "rebalance",
)

#: WS-27bn R5d (§9 Q13). The words of the server's 403, in
#: ``reports.CHANGE_REFUSED``. A test pins the two as one sentence.
REPORT_CHANGE_REFUSED = "Only the author of this report or an admin may change it."


async def _report_change(
    report_id: str, project_id: str, payload: dict[str, Any], config: dict[str, Any],
) -> str:
    """The UPDATE path of ``report_save``.

    The route changes `name` and `config` only, and it REPLACES `config`
    (reports.py `update_report`), so the tool merges the member's change into
    the saved config first and never sends a scope: a report's scope cannot
    change, and the card must not say it can. Save a new report for another
    scope.
    """
    rid = uuid_of(report_id, "report_id")
    if project_id.strip():
        return (
            "A saved report keeps its scope. Save a new report for another "
            "project, or leave project_id empty to change this one."
        )
    existing = await get(f"/projects/reports/{rid}") or {}
    if existing.get("can_edit") is False:
        # WS-27bn R5d (§9 Q13). The server computed the rule. Say it, and
        # show no card that the PATCH would refuse.
        return f"{REPORT_CHANGE_REFUSED}\n  report_id: {rid}"
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
async def report_save(
    name: str, project_id: str = "", sections: str = "", weeks: int = 0, report_id: str = ""
) -> str:
    """Save a report definition, or change one (pass report_id). A report
    stores the question: scope (project_id, or empty for the portfolio),
    sections (comma-separated from finished, throughput, outlook, load,
    capacity, pulse, stuck, hygiene, conflicts, rebalance; outlook,
    capacity, pulse, hygiene, conflicts and rebalance are never there
    unless asked for) and
    weeks. Render it
    with report_render. Delivery and schedules stay in the Reports app."""
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
        return await _report_change(report_id, project_id, payload, config)
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
#
# Owner directive 2026-10-07 (`projects_agent_parity.md` §16): the tool never
# decides a permission. It calls the write, and the server allows or refuses
# it. The ONE check before a card is :func:`status_edit_refusal`, and it reads
# the server's own answer (`may_edit` and `edit_refusal` on the status-set
# read, from `core.can_manage_settings`, the write's predicate). Nothing here
# infers a permission from a role, an owner node or where a row lives.

STATUS_CATEGORIES = ("backlog", "todo", "in_progress", "done", "cancelled", "triage")
FIELD_TYPES = ("text", "number", "date", "select", "multi_select", "boolean", "url")
FREQS = ("daily", "weekly", "monthly", "yearly")
ANCHORS = ("due", "completed")
DISPOSITIONS = ("INBOX", "NEXT", "WAITING", "SOMEDAY", "PROJECT", "REFERENCE", "DONE", "TRASH")
ENERGIES = ("low", "medium", "high")


def status_edit_refusal(status_set: dict[str, Any], node_name: Any) -> str:
    """The server's "no" before a status card, or ``""`` to go on.

    *status_set* is ``GET /projects/nodes/{id}/status-set``. Its ``may_edit``
    is ``core.can_manage_settings``, the predicate the status write checks,
    and its ``edit_refusal`` is the write's own 403 words. So a member is not
    shown a card that the write will refuse, and the words are the server's.

    ⚠️ Only an explicit ``False`` stops the tool. A read that does not carry
    the flag (an older gateway) lets the tool go on to the card, and the
    write then decides. Fence: ``tests/unit/test_projects_agent_grants.py``.
    """
    if status_set.get("may_edit") is not False:
        return ""
    said = str(status_set.get("edit_refusal") or "").strip()
    lines = [
        f"{REFUSED} The server says that this member may not edit the statuses "
        f"of {data(node_name)}. Nothing was done."
    ]
    if said:
        lines.append(f"Gateway said: {data(said)}")
    lines.append(
        "Next: Tell the member what the gateway said. Do not try again, and do "
        "not try another tool for the same act."
    )
    return "\n".join(lines)


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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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
    refused = status_edit_refusal(owner, node.get("name"))
    if refused:
        return refused
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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
    refused = status_edit_refusal(
        (await get(f"/projects/nodes/{pid}/status-set")) or {}, node.get("name")
    )
    if refused:
        return refused
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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
    default_weekday: int = 0,
    today: date | None = None,
) -> dict[str, Any] | str:
    """The rule as the route takes it (``RecurrenceIn``), or the refusal.

    The same checks ``recurrence.validate_rule`` makes, made here so the
    member reads the reason before a card, not a 422 after one.
    ``default_weekday`` is the day a weekly rule with no weekdays takes
    (Q1, :func:`_default_weekday`). Zero keeps the refusal. ``today`` is the
    member's date (:func:`_member_clock`), which an end date may not be
    before. ``None`` is the UTC date.
    """
    kind = str(freq or "").strip().lower()
    if kind not in FREQS:
        return f"freq is one of {', '.join(FREQS)}."
    try:
        # Absent is every 1. An explicit 0 is a mistake, never "every 1"
        # (the gateway's `validate_rule` says the same).
        every = 1 if interval in (None, "") else int(interval)
        numbers = [int(day_of_month or 0), int(month_of_year or 0), int(max_occurrences or 0)]
    except (TypeError, ValueError):
        return "interval, day_of_month, month_of_year and max_occurrences take whole numbers."
    day_of_month, month_of_year, max_occurrences = numbers
    out_of_range = _range_refusal(
        every, day_of_month, month_of_year, max_occurrences, until, today or _today()
    )
    if out_of_range:
        return out_of_range
    how = str(anchor or "due").strip().lower()
    if how not in ANCHORS:
        return f"anchor is {' or '.join(ANCHORS)}."
    rule: dict[str, Any] = {"freq": kind, "interval": every, "anchor": how}
    parts = _split(weekdays)
    if not all(p.isdigit() for p in parts):
        return "weekdays takes numbers, 1 (Monday) to 7 (Sunday)."
    days = [int(p) for p in parts]
    if any(d < 1 or d > 7 for d in days):
        return "weekdays takes 1 (Monday) to 7 (Sunday)."
    if kind == "weekly" and not days and 1 <= default_weekday <= 7:
        days = [default_weekday]
    if kind == "weekly" and not days:
        return "A weekly rule needs weekdays, 1 (Monday) to 7 (Sunday)."
    if kind in ("monthly", "yearly") and not day_of_month:
        return f"A {kind} rule needs day_of_month."
    if days:
        rule["weekdays"] = days
    if day_of_month:
        rule["day_of_month"] = day_of_month
    if month_of_year:
        rule["month_of_year"] = month_of_year
    if str(until or "").strip():
        rule["until_at"] = str(until).strip()
    if max_occurrences:
        rule["max_occurrences"] = max_occurrences
    return rule


def _range_refusal(
    every: int,
    day_of_month: int,
    month_of_year: int,
    max_occurrences: int,
    until: str,
    today: date,
) -> str:
    """The refusal for a number or a date out of range, else ``""``.

    Checked before the card (WS-46 P1 review), so a member never approves a
    rule the route then refuses, and ``_first_due`` never meets a bad month.
    Zero in the three optional numbers means "not given".
    """
    if not 1 <= every <= 365:
        return f"interval is 1 to 365, not {every}."
    if day_of_month and not 1 <= day_of_month <= 31:
        return f"day_of_month is 1 to 31, not {day_of_month}."
    if month_of_year and not 1 <= month_of_year <= 12:
        return f"month_of_year is 1 to 12, not {month_of_year}."
    if max_occurrences < 0:
        return f"max_occurrences is 1 or more, not {max_occurrences}."
    end_text = str(until or "").strip()
    if not end_text:
        return ""
    end = _date_of(end_text) if len(end_text) == 10 else None
    if end is None:
        return f"until is a date, YYYY-MM-DD, not {data(end_text)}."
    if end < today:
        return f"until is {end_text}, which is in the past. Give today or a later date."
    return ""


def _today() -> date:
    """The UTC date: what :func:`_member_clock` falls back to, and the date
    every read's legend states to the model."""
    return datetime.now(UTC).date()


async def _member_clock() -> tuple[date, str, str]:
    """``(today, zone, label)`` in the member's own zone (WS-46 P7, §8.1 item 4).

    From ``GET /projects/my/today``, which reads ``user_settings.timezone``,
    the zone the Tasks and the Calendar clients save. P1 took the UTC date,
    which is the wrong day for five and a half hours every evening in India.
    A gateway that does not serve the read yet (R6, during a deploy) answers
    UTC here, and a member with no saved zone gets UTC from the route. Either
    way the card names the zone, so the member sees the guess. Read only when
    a tool needs a day, so a call that guesses nothing costs no extra read.
    """
    try:
        day, zone, label = clock_of(await get("/projects/my/today"))
    except GatewayRefusal:
        return _today(), "UTC", "UTC"
    return (day, zone, label) if day is not None else (_today(), "UTC", "UTC")


def _date_of(value: Any) -> date | None:
    """``2026-10-09`` or ``2026-10-09T00:00:00+00:00`` → a date. Else ``None``."""
    raw = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _default_weekday(due: date | None, today: date, zone: str = "UTC") -> tuple[int, str]:
    """The day a weekly rule with no weekdays takes, and the card's reason.

    Q1, the owner, 2026-10-06: the due date's weekday, else today's. Today
    is the member's date in *zone* (WS-46 P7). The card names the day and
    the zone, so the member sees the guess and can decline.
    """
    if due is not None:
        return due.isoweekday(), f"{WEEKDAYS[due.isoweekday() - 1]}, the due date's weekday"
    day = today.isoweekday()
    return day, f"{WEEKDAYS[day - 1]}, today's weekday ({zone}), because no due date was given"


def _first_due(rule: dict[str, Any], today: date) -> date:
    """The first day on or after *today* that *rule* lands on (§8.1 item 5)."""
    freq = rule["freq"]
    if freq == "weekly":
        days = set(rule.get("weekdays") or [])
        ahead = (today + timedelta(days=n) for n in range(7))
        return next(d for d in ahead if d.isoweekday() in days)
    if freq == "daily":
        return today
    wanted = int(rule["day_of_month"])
    month = rule.get("month_of_year")
    if freq == "yearly" and month:
        for year in (today.year, today.year + 1):
            day = min(wanted, calendar.monthrange(year, int(month))[1])
            if date(year, int(month), day) >= today:
                return date(year, int(month), day)
    # Monthly, or yearly with no month: this month's day, else next month's.
    year, mon = today.year, today.month
    for _ in range(2):
        day = min(wanted, calendar.monthrange(year, mon)[1])
        if date(year, mon, day) >= today:
            return date(year, mon, day)
        year, mon = (year + 1, 1) if mon == 12 else (year, mon + 1)
    return today  # unreachable: next month's day is always ahead


#: `create_task`'s argument → the `set_recurrence` word `_build_rule` names.
_REPEAT_WORDS = {
    "freq": "repeat",
    "interval": "repeat_every",
    "weekdays": "repeat_on",
    "day_of_month": "repeat_day",
    "month_of_year": "repeat_month",
    "max_occurrences": "repeat_times",
    "anchor": "repeat_from",
    "until": "repeat_until",
}
_REPEAT_WORD = re.compile(r"\b(" + "|".join(_REPEAT_WORDS) + r")\b")


def _weekday_numbers(spoken: str) -> str:
    """``fri, 1`` → ``5,1``. A day name is accepted as well as its number."""
    out: list[str] = []
    for part in _split(spoken):
        word = part.lower()
        named = [
            i + 1 for i, d in enumerate(WEEKDAYS) if len(word) >= 3 and d.lower().startswith(word)
        ]
        out.append(str(named[0]) if len(named) == 1 else part)
    return ",".join(out)


class _Repeat:
    """The rule ``create_task`` sets after the create, under the same card.

    ``rule=None`` is a task that does not repeat. It adds nothing to the
    card and sends exactly what ``create_task`` sent before P1 (§8.2 item 5).
    """

    def __init__(
        self, rule: dict[str, Any] | None = None, first_due: str = "", day_note: str = ""
    ) -> None:
        self.rule = rule
        self.first_due = first_due
        self.day_note = day_note

    def due_at(self, due: str) -> str:
        """The member's due date, else the rule's first day (§8.1 item 5).
        "Every Friday" expects a Friday on the task."""
        return str(due or "").strip() or self.first_due

    @property
    def card_title(self) -> str:
        return "Create this repeating task?" if self.rule else "Create this task?"

    def detail(self) -> str:
        return f" · repeats {_rule_text(self.rule)}" if self.rule else ""

    def card_lines(self) -> dict[str, Any]:
        if not self.rule:
            return {}
        lines: dict[str, Any] = {"repeats": _rule_text(self.rule)}
        if self.day_note:
            lines["repeat day"] = f"{self.day_note}. Decline if you meant another day."
        if self.first_due:
            lines["first due"] = f"{self.first_due}, the first day the rule lands on"
        lines["next copy"] = NEXT_COPY
        return lines

    async def save(self, task_id: str) -> tuple[dict[str, Any], tuple[str, Exception] | None]:
        """The second write of the one approval (§8.1 item 3): the route's
        answer, or the failure that stops the receipt. Never a raise."""
        if not self.rule:
            return {}, None
        tid = uuid_of(task_id, "task_id")  # the writes fence: a canonical id
        try:
            return (await put(f"/projects/tasks/{tid}/recurrence", self.rule)) or {}, None
        except _WRITE_FAILED as exc:
            return {}, ("rule", exc)

    def receipt(
        self,
        task: dict[str, Any],
        lines: list[str],
        saved: dict[str, Any],
        failed: tuple[str, Exception] | None,
        extra: _NewFields | None = None,
    ) -> str:
        """What the member reads. A failed write is named, the task stays
        (item 6), and each write it did not try is listed."""
        if extra is not None and extra.values:
            # Saved by the create itself, so they hold even if a later write fails.
            lines = [*lines, "Fields set:", *extra.receipt_lines()]
        if failed is not None:
            left = (
                "the repeat rule. Use set_recurrence on this task to add it."
                if self.rule and failed[0] != "rule"
                else ""
            )
            return _create_stopped(task, lines, failed[0], failed[1], left)
        if not self.rule:
            return "\n".join(["Created:", *lines])
        rule = saved.get("rule") or self.rule
        return "\n".join(["Created:", *lines, f"It repeats {_rule_text(rule)}.", NEXT_COPY])


NO_REPEAT = _Repeat()


async def _repeat_for_create(
    *,
    repeat: str,
    every: int,
    on: str,
    day: int,
    month: int,
    anchor: str,
    until: str,
    times: int,
    due: str,
) -> _Repeat | str:
    """The rule ``create_task`` sets, :data:`NO_REPEAT`, or the refusal.

    A repeat argument with no ``repeat`` is refused, never dropped: the
    member asked for a rule, and a task with none is the failure P1 fixes.
    """
    if not str(repeat or "").strip():
        stray = [
            name
            for name, value in (
                ("repeat_on", on),
                ("repeat_day", day),
                ("repeat_month", month),
                ("repeat_from", anchor),
                ("repeat_until", until),
                ("repeat_times", times),
            )
            if str(value or "").strip() not in ("", "0")
        ]
        if every not in (None, "", 0, 1) or stray:
            names = ", ".join(stray or ["repeat_every"])
            return f"{names} needs repeat (one of {', '.join(FREQS)}). Nothing was created."
        return NO_REPEAT
    due_day = _date_of(due) if str(due or "").strip() else None
    if str(due or "").strip() and due_day is None:
        return "due is a date, YYYY-MM-DD."
    today, _zone, label = await _member_clock()
    weekday, note = _default_weekday(due_day, today, label)
    built = _build_rule(
        repeat, every, _weekday_numbers(on), day, month, anchor or "due", until, times,
        default_weekday=weekday, today=today,
    )
    if isinstance(built, str):
        # `_build_rule` speaks `set_recurrence`'s words. Name this tool's.
        return _REPEAT_WORD.sub(lambda m: _REPEAT_WORDS[m.group(1)], built)
    guessed = built["freq"] == "weekly" and not _split(on)
    first = ""
    if due_day is None and built["anchor"] == "due":
        try:
            first = _first_due(built, today).isoformat()
        except (ValueError, StopIteration):  # IllegalMonthError is a ValueError
            return "The rule gives no first date. Pass due as YYYY-MM-DD."
    return _Repeat(built, first, note if guessed else "")


def _weekly_days(
    freq: str,
    weekdays: str,
    current: dict[str, Any] | None,
    due_at: Any,
    clock: tuple[date, str, str],
) -> tuple[str, str]:
    """The weekdays ``set_recurrence`` sends, and the card's note for a guess.

    Days the member named win. A weekly rule that exists keeps its days, so
    "make it every 2 weeks" does not move the day (WS-46 P1 review). Only a
    task with no weekly rule takes the Q1 default (§8.1 item 8), the same
    default ``create_task`` takes.
    """
    given = _weekday_numbers(weekdays)
    if given or str(freq or "").strip().lower() != "weekly":
        return given, ""
    if current and current.get("freq") == "weekly" and current.get("weekdays"):
        return ",".join(str(d) for d in current["weekdays"]), ""
    day, note = _default_weekday(_date_of(due_at), clock[0], clock[2])
    return str(day), note


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
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
    comma-separated 1 (Monday) to 7. A weekly rule with no weekdays takes
    the task's due weekday, else today's, and the card names the day.
    day_of_month for monthly and yearly; month_of_year for yearly. anchor is
    due (keep the schedule) or completed (measure from when the last one was
    finished). until is YYYY-MM-DD. The card shows the current rule and the
    new one. Stopping keeps the task and every occurrence already made. To
    make a NEW task that repeats, use create_task with repeat instead."""
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
    clock = await _member_clock()
    days, note = _weekly_days(freq, weekdays, current, task.get("due_at"), clock)
    built = _build_rule(
        freq, interval, days, day_of_month, month_of_year, anchor, until, max_occurrences,
        today=clock[0],
    )
    if isinstance(built, str):
        return built
    rule = built
    card = {"task": _ref(task), "rule": _rule_text(rule)}
    if note:
        card["repeat day"] = f"{note}. Decline if you meant another day."
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


@_annotate(read_only=False, destructive=False, idempotent=False, open_world=False)
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


#: The dispositions that reopen a finished task on the board when stated
#: (D77 choice 3, `personal.OPEN_DISPOSITIONS`): only the actionable ones.
#: SOMEDAY, REFERENCE and PROJECT file a finished task without reopening it.
_REOPENING = frozenset({"INBOX", "NEXT", "WAITING"})

#: DONE is refused by `set_my_overlay`, not routed. Its card is about the
#: member's own triage (manifest class B on /personal); a shared completion
#: behind that card would move the board without asking about the board.
#: `complete` has its own card and asks.
_DONE_REFUSAL = (
    "DONE is not your own triage any more: a task is done when its shared "
    "lane is done (D77). Use complete to finish it for everyone."
)


def _overlay_disposition(disposition: str) -> tuple[str | None, str | None]:
    """``(state, refusal)`` for the disposition argument. Both None when the
    caller passed none."""
    if not disposition.strip():
        return None, None
    state = disposition.strip().upper()
    if state not in DISPOSITIONS:
        return None, f"disposition is one of {', '.join(DISPOSITIONS)}."
    if state == "DONE":
        return None, _DONE_REFUSAL
    return state, None


def _overlay_card(
    payload: dict[str, Any], task: dict[str, Any], unread: str,
) -> dict[str, Any]:
    """What the confirmation card says the write will do.

    ⚠️ D77: an actionable disposition (INBOX, NEXT, WAITING) on a FINISHED
    task reopens it for everybody (`personal.reopen_if_closed`), so "your
    overlay only" would be false. SOMEDAY, REFERENCE and PROJECT do not.
    The task read this tool already made carries `completed_at`, which
    `apply_status_transition` keeps equal to "the lane is closed".
    """
    reopens = bool(task.get("completed_at")) and payload.get("disposition") in _REOPENING
    scope = (
        "reopens the task on the board, then sets your overlay"
        if reopens else "your overlay only"
    )
    card: dict[str, Any] = {**payload, "scope": scope}
    if unread:
        card["current triage"] = unread
    return card


#: ``set_my_overlay``'s argument -> the overlay field it sets, for the times
#: and the flags of WS-46 P7 (G15). The member's word is the key (§7.2).
_OVERLAY_TIMES = {
    "block_start": "scheduled_start",
    "block_end": "scheduled_end",
    "actual_start": "actual_start",
    "actual_end": "actual_end",
    "waiting_since": "delegated_at",
}
_OVERLAY_FLAGS = {"flexible": "flexible", "hard_date": "is_hard_date", "deep_work": "deep_work"}
#: The clear words -> the overlay fields each one empties.
_OVERLAY_CLEAR = {
    "context": ("context",),
    "energy": ("energy",),
    "next_action": ("next_action",),
    "block": ("scheduled_start", "scheduled_end"),
    "actual": ("actual_start", "actual_end"),
    "flexible": ("flexible",),
    "hard_date": ("is_hard_date",),
    "deep_work": ("deep_work",),
    "waiting_on": ("waiting_on",),
    "expected_by": ("expected_by",),
}
#: Every argument the overlay builder takes, so ``bulk_update``'s ``personal``
#: object is refused by name for a key the single tool does not take.
OVERLAY_ARGUMENTS = (
    "disposition", "context", "energy", "next_action", "two_minute",
    *_OVERLAY_TIMES, *_OVERLAY_FLAGS, "waiting_on", "expected_by", "clear",
)
_NAIVE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$")


def _instant(value: str, what: str, zone: str) -> str:
    """A time the member gave -> the ISO instant the route stores.

    ``2026-10-07 14:00`` is a time in the member's own zone (WS-46 P7), as the
    Calendar shows it. A time with an offset or a ``Z`` is kept as given. A
    date alone is refused: a block or an actual time is a time of day.
    """
    raw = str(value or "").strip()
    if _NAIVE_TIME.match(raw):
        local = datetime.fromisoformat(raw.replace(" ", "T"))
        return local.replace(tzinfo=ZoneInfo(zone)).isoformat()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None or len(raw) <= 10:
        raise GatewayRefusal(
            f"{what} is a date and a time, YYYY-MM-DD HH:MM in your own zone, not {data(raw)}."
        )
    return parsed.isoformat()


def _expected_by(value: str) -> str:
    """The promised date, ``YYYY-MM-DD``, as the Tasks app's date picker
    sends it (``ItemDetail.tsx``)."""
    raw = str(value or "").strip()
    if _date_of(raw) is None or len(raw) != 10:
        raise GatewayRefusal(f"expected_by is a date, YYYY-MM-DD, not {data(raw)}.")
    return raw


async def _waiting_person(value: str) -> dict[str, Any]:
    """``{name, email}`` for the person the member waits on: the shape the
    Tasks app's Delegate dialog stores (``lens.ts`` ``lensDelegateItem``)."""
    email = await _resolve_assignee_name(value)
    if email.startswith("agent:"):
        raise GatewayRefusal("waiting_on is a person. An agent is assigned, not chased.")
    names = ((await get("/projects/people/names", {"emails": email})) or {}).get("names") or {}
    name = next((str(v) for k, v in names.items() if str(k).lower() == email), "")
    return {"name": name or email.split("@", 1)[0], "email": email}


def _block_refusal(values: dict[str, Any], mine: dict[str, Any]) -> str:
    """The route's merged-row check (``personal._reject_impossible_block``),
    said before the card: a block ends after it starts."""
    def at(key: str) -> datetime | None:
        raw = values[key] if key in values else mine.get(key)
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
        except ValueError:
            return None

    start, end = at("scheduled_start"), at("scheduled_end")
    if start and end and start.tzinfo and end.tzinfo and end <= start:
        return "block_end must be after block_start."
    return ""


async def _overlay_values(args: dict[str, Any], mine: dict[str, Any]) -> dict[str, Any] | str:
    """The overlay body for *args*, keyed by ``set_my_overlay``'s argument
    names, or the refusal. One builder for the single tool and for
    ``bulk_update``'s ``personal`` action, so the two refuse the same words.

    *mine* is the member's current overlay, or ``{}``. It decides the block
    check and nothing else.
    """
    unknown = sorted(set(args) - set(OVERLAY_ARGUMENTS))
    if unknown:
        return f"The overlay takes {', '.join(OVERLAY_ARGUMENTS)}, not {', '.join(unknown)}."
    given = {k: "" if v is None else str(v).strip() for k, v in args.items()}
    values = _overlay_words(given)
    if isinstance(values, str):
        return values
    await _overlay_times(given, values)
    if given.get("waiting_on"):
        values["waiting_on"] = await _waiting_person(given["waiting_on"])
        # Migration 188: a chase has a since-when. The Delegate dialog
        # stamps now, and so does the chat, unless the member gave one. A
        # chase of the same person keeps its age (review round 1): the age
        # is the column a person scans before a nudge.
        if not _same_chase(values["waiting_on"], mine):
            values.setdefault(
                "delegated_at", datetime.now(UTC).replace(microsecond=0).isoformat()
            )
    if given.get("expected_by"):
        values["expected_by"] = _expected_by(given["expected_by"])
    refusal = _clear_into(values, given.get("clear", ""), _OVERLAY_CLEAR)
    return refusal or _block_refusal(values, mine) or values


def _same_chase(person: dict[str, Any], mine: dict[str, Any]) -> bool:
    """Does the member already wait on *person*, with a since-when stored?"""
    stored = mine.get("waiting_on") if isinstance(mine.get("waiting_on"), dict) else {}
    email = str(stored.get("email") or "").lower()
    return bool(email) and email == person.get("email") and bool(mine.get("delegated_at"))


def _overlay_words(given: dict[str, str]) -> dict[str, Any] | str:
    """The triage words and the yes-or-no flags of the overlay, or the refusal."""
    values: dict[str, Any] = {}
    state, refusal = _overlay_disposition(given.get("disposition", ""))
    if refusal:
        return refusal
    if state:
        values["disposition"] = state
    for key in ("context", "next_action"):
        if given.get(key):
            values[key] = given[key]
    if given.get("energy"):
        level = given["energy"].lower()
        if level not in ENERGIES:
            return f"energy is one of {', '.join(ENERGIES)}."
        values["energy"] = level
    for arg, field in {"two_minute": "is_two_minute", **_OVERLAY_FLAGS}.items():
        flag = _yes_no(given.get(arg, ""), arg)
        if flag is not None:
            values[field] = flag
    return values


async def _overlay_times(given: dict[str, str], values: dict[str, Any]) -> None:
    """The times of the overlay into *values*. A time with no offset is in
    the member's own zone, read only when a time needs it (WS-46 P7). A
    ``waiting_since`` may be a date alone, as the Tasks app keeps it."""
    asked = [arg for arg in _OVERLAY_TIMES if given.get(arg)]
    if not asked:
        return
    _day, zone, _label = await _member_clock()
    for arg in asked:
        raw = given[arg]
        if arg == "waiting_since" and len(raw) == 10 and _date_of(raw):
            values[_OVERLAY_TIMES[arg]] = raw
        else:
            values[_OVERLAY_TIMES[arg]] = _instant(raw, arg, zone)


@_annotate(read_only=False, destructive=False, idempotent=True, open_world=False)
async def set_my_overlay(
    task_id: str,
    disposition: str = "",
    context: str = "",
    energy: str = "",
    next_action: str = "",
    estimate_mins: int = 0,
    two_minute: str = "",
    block_start: str = "",
    block_end: str = "",
    flexible: str = "",
    hard_date: str = "",
    actual_start: str = "",
    actual_end: str = "",
    deep_work: str = "",
    waiting_on: str = "",
    waiting_since: str = "",
    expected_by: str = "",
    clear: str = "",
) -> str:
    """Set the member's OWN triage of a task: disposition (INBOX, NEXT,
    WAITING, SOMEDAY, PROJECT, REFERENCE, TRASH), context (@office), energy
    (low, medium, high), two_minute (yes or no). clear empties fields:
    context, energy, next_action. DONE is refused here: completion is the
    task's shared lane (D77), so finish a task with complete. INBOX,
    NEXT or WAITING on a finished task reopens it for the board. defer sets a
    date. The estimate is the TASK's, shared with the board since D77: set
    it with update_task, not here.
    The member's own time for the task, which nobody else sees:
    block_start and block_end are a block on their calendar, YYYY-MM-DD
    HH:MM in their own zone. flexible (yes or no) says the block may move,
    and hard_date (yes or no) says the date may not. actual_start and
    actual_end are when the work really started and ended. deep_work (yes or
    no) marks focus work. waiting_on is the person (name or email) the
    member waits on. waiting_since is when the wait began, and it is now
    when not given. expected_by (YYYY-MM-DD) is the date that person
    promised. clear also takes block, actual, flexible, hard_date, deep_work,
    waiting_on and expected_by."""
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
    if _int_or_none(estimate_mins) is not None:
        # D77: one estimate, on the task. Refused by name rather than
        # dropped, so the model learns where it goes.
        return ("The estimate is the task's own since D77, shared with the "
                "board and People capacity. Set it with update_task "
                "(estimate_mins).")
    given = {
        "disposition": disposition, "context": context, "energy": energy,
        "next_action": next_action, "two_minute": two_minute, "block_start": block_start,
        "block_end": block_end, "flexible": flexible, "hard_date": hard_date,
        "actual_start": actual_start, "actual_end": actual_end, "deep_work": deep_work,
        "waiting_on": waiting_on, "waiting_since": waiting_since, "expected_by": expected_by,
        "clear": clear,
    }
    payload = await _overlay_values(given, mine if isinstance(mine, dict) else {})
    if isinstance(payload, str):
        return payload
    if not payload:
        return "Nothing to change. Pass at least one field."
    before = {key: (mine or {}).get(key) for key in payload}
    card = _overlay_card(payload, task, unread)
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
