"""The item that a chat read or acted on last (HRD-11a, task 2990).

Spec ``project-docs/specs/chat_failure_modes.md`` §4.5 E1 and §6 row HRD-11a.
Outside email, nothing held the item that the chat read or acted on last. So
"mark it done" after a read of a task had no item, and the model guessed or
asked. This module keeps ONE item for each chat and member: the email or the
Projects task that the tool calls of a run read or acted on.

* :data:`FOCUS_TOOLS` is the closed table of the tools that set the item, as
  ``tool -> (kind, id argument)``. The email rows are the tools of
  ``email_chat_context._EMAIL_ID_TOOLS``. The task rows are the tools of the
  core ``projects`` pack that read or act on ONE task by its ``task_id``. A
  list read (``find_tasks``, ``list_tasks``, ``query_inbox``) is not in it,
  so it sets nothing. ``tests/unit/test_focus_item.py`` fails when a tool of
  the table lacks its id argument in its real signature.
* :data:`CLEARS` is the closed table of the Projects tools that make, archive,
  delete or move a task or a project. A call of one of them moves the
  member's attention to another item, so it CLEARS the item. The fence reads
  each name in the real packs of its owner.
* Only a call that acted counts, as a setter or as a clear: a call that
  succeeded, whose result does not start with a lead of :data:`NO_ACT`
  (``email_chat_context._NO_ACT`` and three more). An id that fails
  ``email_chat_context._ok_id`` sets nothing. A task id must also read as a
  UUID, because each Projects tool takes only a UUID and the link needs one.
* ONE RUN, ONE ITEM (fix round 1, P1-a). The binding of a run holds a
  :class:`RunFocus`, which keeps each different item that the calls of the run
  named. When the calls of a run name two or more different items, or a call
  clears, the run deletes the stored item and writes none. The model then
  reads the chat, as before HRD-11a. Calls in parallel finish in any order,
  so "the last call" is no rule. Many calls that name the same item are fine.
* FRESH ONLY (fix round 1, P1-b). The stored value holds the time of its
  write. A read gives an item only for :data:`FOCUS_FRESH_S` (2 hours) after
  that time, and Redis drops the value at the same time (:data:`FOCUS_TTL_S`).
* Two seams write it (:func:`record_calls`), each from the calls of a tool
  and never from the text of the model: ``orchestrator._tool_injection
  ._record_pack_call`` after a pack call, and the sub-run hook of
  ``orchestrator.executor._run_sub_agent_streaming`` after a hand-off.
* The executor reads the item once for each turn (:func:`read_focus`), binds
  it into the run (:data:`FOCUS_KEY`), and shows it in the per-turn context
  after the OA-4 block (:func:`orchestrator_block`).

THE BLOCK holds the kind, the id and the in-app link, and no title (Q1). A
title is text that a stranger wrote. Each id passes :func:`clean_focus` before
it is stored, after it is read back, and before it shows. The rule of the
block yields: the member's own words and a newer item win over it.

GATES (done-when 7). Only a top-level orchestrator turn in the member's own
web chat opens the item: a solo room, a verified member, a run that shows
cards, not a batch run (WhatsApp) and not a delegated run. The executor
decides that, and binds :data:`FOCUS_KEY` with the run id. A write and a read
need that binding of THIS run (:func:`open_binding`). A delegated run never
holds it, so it reads and writes nothing.

R5. The key is ``cc:<org>:chat-focus:<session>:<member hash>`` through the
tenant Redis wrapper, inside ``organization_scope``. The org is the run's bound
tenant (``current_tenant()``). The session and the member come from the run's
bound artifact context. No part comes from a request field, a tool argument or
the model. A Redis fault costs the item, never the turn.

DARK. ``CHAT_FOCUS_ORGS`` (a comma list of organization ids, or ``*``) turns
it on, and :func:`focus_on` is the one reader. Empty is off, the default. Off,
no read starts, nothing is bound or written, and each prompt keeps its bytes.

THE NEXT ONE (HRD-11b, task 3480). The chat also keeps the LAST LIST that a
tool read, under the same flag, at its own key
``cc:<org>:chat-focus-list:<session>:<member hash>``. A second key, so the
item and the list each write with no read back, and a drop of the item keeps
the list.

* :data:`LIST_TOOLS` is the closed table of the list reads, as ``tool ->
  kind``. A list holds only the ids that the tool result printed, in the
  printed order: the ``• id=<uuid> `` lines of an email list and the
  ``  full_id: <uuid>`` lines of a task list (:data:`_LIST_LINES`). Each id
  must read as a UUID (:func:`_list_id`), so a list line that a mail
  subject forges with a free token gives nothing. The list keeps the first
  :data:`LIST_MAX` ids.
* A list read that acted REPLACES the stored list, of any kind, and puts the
  cursor before the first item. A list read that printed no id deletes it.
* THE CURSOR is the highest place in the list of an item that the calls of
  ONE run read or acted on (:data:`FOCUS_TOOLS`). So after "mark it done" on
  item 3, the cursor is at 3, and the next item is 4. The server moves it from
  the tool calls only, never from the text of the model.
* The block shows the place of the cursor and the NEXT item, as ``3 of 7``,
  the kind, the id and the link. At the end of the list it says that no next
  item exists. It never goes back to the first item. When the model then
  calls a tool of :data:`FOCUS_TOOLS` on the next id, that call moves the
  item and the cursor.
* A list older than :data:`FOCUS_FRESH_S` after its read shows nothing. A
  move of the cursor keeps the time of the read.

Fences (R7): ``tests/unit/test_focus_item.py``,
``tests/unit/test_focus_list.py`` and
``evals/trajectories/test_focus_item_trajectory.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

import structlog

from acb_skills.email_chat_context import (
    _EMAIL_ID_TOOLS,
    _NO_ACT,
    EMAIL_AGENT,
    _args_of,
    _ok_id,
)

_log = structlog.get_logger(__name__)

#: The Redis namespace of the items.
NAMESPACE = "chat-focus"
#: How long an item stays in focus after its write (fix round 1, P1-b). An
#: older item shows no block, and a read ignores it.
FOCUS_FRESH_S = 2 * 3600
#: The life of a stored value, slid by each write. It is the fresh time, so
#: Redis drops a value when it can no longer show.
FOCUS_TTL_S = FOCUS_FRESH_S
#: The most that the time of a write may be ahead of this clock, for a write
#: of another process whose clock runs a little fast.
_CLOCK_SKEW_S = 300
#: The longest wait for Redis. A slow Redis costs the item, never the turn.
REDIS_TIMEOUT_S = 1.0
#: The version of the stored value. A later slice (HRD-11b) adds fields, and
#: a reader of this version reads the kind, the id and the time only.
VERSION = 1

#: The kind of an email.
EMAIL = "email"
#: The kind of a Projects task.
TASK = "task"
#: The kinds of an item.
KINDS: tuple[str, ...] = (EMAIL, TASK)

#: The agent that owns the tools of each kind. A tool of the table sets the
#: item only in a call of its own owner, so a tool of another agent with the
#: same name never sets it.
PROJECTS_AGENT = "projects-assistant"
OWNERS: Mapping[str, str] = MappingProxyType({EMAIL: EMAIL_AGENT, TASK: PROJECTS_AGENT})
#: The id argument of each kind.
ID_ARGS: Mapping[str, str] = MappingProxyType({EMAIL: "email_id", TASK: "task_id"})
#: The in-app link of each kind. The email link is the link of the email
#: working set (``email_chat_context.working_set_lines``), and the task link
#: is the link of ``open_in_app`` (``skill_projects.nav``).
LINKS: Mapping[str, str] = MappingProxyType({
    EMAIL: "/email?email={id}", TASK: "/projects?task={id}",
})

#: The tools of the core ``projects`` pack that read or act on ONE task by
#: its ``task_id``. ``move_task`` takes a list (``task_ids``), and
#: ``create_task`` makes a new id that no argument holds, so neither is here.
_TASK_ID_TOOLS: tuple[str, ...] = (
    "add_subtasks", "assign", "comment", "complete", "defer", "my_task",
    "set_my_overlay", "task_detail", "update_task",
)

#: THE closed table: each tool that sets the item, and ``(kind, id
#: argument)``. Fence: ``tests/unit/test_focus_item.py`` reads the real
#: signature of each tool.
FOCUS_TOOLS: Mapping[str, tuple[str, str]] = MappingProxyType({
    **{name: (EMAIL, ID_ARGS[EMAIL]) for name in sorted(_EMAIL_ID_TOOLS)},
    **{name: (TASK, ID_ARGS[TASK]) for name in _TASK_ID_TOOLS},
})

#: THE closed table of the tools that CLEAR the item (fix round 1, P1-b), and
#: the owner of each. Each one makes, archives, deletes or moves a task or a
#: project, so the member's next "it" can mean an item that no argument
#: names. Fence: each name is a real pack tool of its owner.
CLEARS: Mapping[str, str] = MappingProxyType({name: PROJECTS_AGENT for name in (
    "archive_project", "archive_task", "capture_intake", "create_personal_task",
    "create_project", "create_project_from_template", "create_task", "create_tasks",
    "delete_project", "delete_task", "file_tasks", "merge_tasks", "move_task",
    "unarchive_project", "unarchive_task",
)})

#: The first words of a result that did not act. The leads of the email
#: working set, and three more that a Projects tool or the permission gate
#: gives: the gate's refusal, the date check of ``defer`` and the body check
#: of ``comment`` and ``edit_comment`` (``skill_projects.writes``).
NO_ACT: tuple[str, ...] = (
    *_NO_ACT, "[blocked by permission policy", "until is a date",
    "A comment needs a body",
)

#: THE closed table of the list reads (HRD-11b), as ``tool -> kind``. Each
#: one prints one line for each item, with its id. The owner of the kind
#: (:data:`OWNERS`) must make the call. Fence: ``tests/unit/test_focus_list.py``
#: reads each name in the real packs of its owner.
LIST_TOOLS: Mapping[str, str] = MappingProxyType({
    "find_priority": EMAIL, "query_inbox": EMAIL,
    "find_tasks": TASK, "list_tasks": TASK, "my_work": TASK,
})
#: The line of one item in a list result, by kind. SECURITY (independent read
#: of HRD-11b): the email tools print the subject and the snippet of the
#: sender with no line-break strip. So a subject can forge a whole
#: ``• id=<token> `` line. Each id of a list must therefore read as a UUID
#: (:func:`_list_id`), because each ``email_messages.id`` is one. A forged
#: line with a free token goes into no list. The residual: a forged line with
#: a real UUID still goes in. The sender must know the id of a mail in the
#: member's own mailbox, and the email tools read only that mailbox. The email lists print
#: ``• id=<id> `` at the start of a line (``agent-email-assistant``
#: ``query_inbox`` and ``find_priority``). The task lists print
#: ``  full_id: <uuid>`` on a line of its own (``skill_projects.reads
#: ._task_line``). An id that a cut result ends in has no space or no end of
#: line after it, so it does not match.
_LIST_LINES: Mapping[str, re.Pattern[str]] = MappingProxyType({
    EMAIL: re.compile(r"^\u2022 id=([0-9A-Fa-f-]{36}) ", re.MULTILINE),
    TASK: re.compile(r"^  full_id: ([0-9A-Fa-f-]{36})$", re.MULTILINE),
})
#: The most ids that a stored list keeps.
LIST_MAX = 10
#: The Redis namespace of the lists.
LIST_NAMESPACE = "chat-focus-list"
#: The version of a stored list.
LIST_VERSION = 1
#: The cursor before the first item.
BEFORE_FIRST = -1
#: The most bytes of a stored list. A larger value is not written.
LIST_MAX_BYTES = 4096

#: The artifact-context key of the binding of ONE run:
#: ``(run_id, org, session, member, item or None, RunFocus)``. Only the
#: executor binds it, for a run that may read and write the item. A delegated
#: run drops it.
FOCUS_KEY = "chat_focus"

#: The heading of the block in the per-turn context.
BLOCK_HEAD = "## The item in focus in this chat"


def focus_on(org: object = None) -> bool:
    """True when ``CHAT_FOCUS_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant of the run. A comma list of org ids, or
    ``*`` for each one. Empty means off, the default. It reads the settings on
    each call, so a flag reload (``docs/flag_reload.md``) applies a change
    with no restart. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "chat_focus_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        org = _tenant()
    return bool(str(org or "").strip()) and str(org or "").strip().lower() in entries


def _now() -> float:
    """The wall clock. A seam for the tests."""
    return time.time()


# ── The item ────────────────────────────────────────────────────────────────


def _valid_id(kind: str, value: Any) -> str:
    """*value* as the id of an item of *kind*, or ``""``.

    It must be a string that passes ``email_chat_context._ok_id``: a plain
    token with no space, slash, bracket or quote. A task id must also read as
    a UUID, and it comes back in the canonical form."""
    if not isinstance(value, str):
        return ""
    text = _ok_id(value)
    if not text or kind != TASK:
        return text
    try:
        return _ok_id(str(uuid.UUID(text)))
    except ValueError:
        return ""


def clean_focus(raw: Any) -> dict[str, str] | None:
    """*raw* as ``{"kind", "id"}``, or None. THE check of an item.

    The kind must be one of :data:`KINDS`, and the id must pass
    :func:`_valid_id`. Each write, each read back and each block calls it.
    Any other key of *raw* is dropped."""
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        return None
    fid = _valid_id(kind, raw.get("id"))
    return {"kind": kind, "id": fid} if fid else None


def _fresh(at: Any) -> bool:
    """True when the time *at* of a write is in the last
    :data:`FOCUS_FRESH_S`. A value with no time, or a time that is not a
    number, is not fresh."""
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    age = _now() - float(at)
    return -_CLOCK_SKEW_S <= age <= FOCUS_FRESH_S


def _acted(call: Mapping[str, Any]) -> bool:
    """True when *call* succeeded and its result does not start with a lead
    of :data:`NO_ACT`."""
    if call.get("success") is False:
        return False
    return not str(call.get("result") or "").lstrip().startswith(NO_ACT)


def _item_of(owner: str, call: Mapping[str, Any]) -> dict[str, str] | None:
    """The item that one call of *owner* names, or None.

    Only a tool of :data:`FOCUS_TOOLS`, called by the owner of its kind, in a
    call that acted (:func:`_acted`). Only an id argument that passes
    :func:`clean_focus`."""
    row = FOCUS_TOOLS.get(str(call.get("name") or ""))
    if row is None:
        return None
    kind, arg = row
    if OWNERS.get(kind) != owner or not _acted(call):
        return None
    return clean_focus({"kind": kind, "id": _args_of(call.get("args")).get(arg)})


def _clears(owner: str, call: Mapping[str, Any]) -> bool:
    """True when *call* is a call of a tool of :data:`CLEARS`, by its owner,
    that acted."""
    return CLEARS.get(str(call.get("name") or "")) == owner and _acted(call)


def named_items(
    owner: str, calls: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, str]], bool]:
    """``(each different item that *calls* name, in order; True when a call
    clears)``. Each call is ``{"name", "args", "result", "success"}``, from the
    tool call itself, never from the text of the model."""
    items: list[dict[str, str]] = []
    cleared = False
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        cleared = cleared or _clears(owner, call)
        item = _item_of(owner, call)
        if item is not None and item not in items:
            items.append(item)
    return items, cleared


def focus_of(owner: str, calls: Iterable[Mapping[str, Any]]) -> dict[str, str] | None:
    """The ONE item that *calls* name, or None (fix round 1, P1-a).

    None when no call names an item, when the calls name two or more
    different items, or when a call clears. Many calls of the same item give
    that item."""
    items, cleared = named_items(owner, calls)
    return items[0] if len(items) == 1 and not cleared else None


# ── The last list (HRD-11b) ─────────────────────────────────────────────────


def _list_id(kind: str, value: Any) -> str:
    """*value* as the id of a list item of *kind*, or ``""``. It passes
    :func:`_valid_id`, and it must read as a UUID for EACH kind, so a forged
    list line with a free token gives nothing. It comes back in the
    canonical lower-case form."""
    fid = _valid_id(kind, value)
    if not fid:
        return ""
    try:
        return _ok_id(str(uuid.UUID(fid)))
    except ValueError:
        return ""


def clean_list(raw: Any) -> dict[str, Any] | None:
    """*raw* as ``{"kind", "ids", "cursor", "cut"}``, or None. THE check of a
    list.

    The kind must be one of :data:`KINDS`. Each id must pass
    :func:`_list_id` (a UUID), and a bad id makes the whole list None. The ids are
    different, at least one and at most :data:`LIST_MAX`. The cursor is
    :data:`BEFORE_FIRST` or a place in the list. Any other key is dropped."""
    if not isinstance(raw, Mapping):
        return None
    kind = raw.get("kind")
    ids = raw.get("ids")
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(ids, list):
        return None
    out: list[str] = []
    for value in ids:
        fid = _list_id(kind, value)
        if not fid or fid in out:
            return None
        out.append(fid)
    if not out or len(out) > LIST_MAX:
        return None
    cursor = raw.get("cursor", BEFORE_FIRST)
    if isinstance(cursor, bool) or not isinstance(cursor, int):
        return None
    if not BEFORE_FIRST <= cursor < len(out):
        return None
    return {"kind": kind, "ids": out, "cursor": cursor, "cut": raw.get("cut") is True}


def list_of(owner: str, call: Mapping[str, Any]) -> dict[str, Any] | None:
    """The list that one list read of *owner* printed, or None when *call* is
    no list read that acted.

    ``{"kind", "ids", "cursor", "cut"}`` with the cursor before the first
    item. ``ids`` is empty when the result printed no id. Only the lines of
    :data:`_LIST_LINES` count, and each id passes :func:`_list_id` (a UUID).
    A line with a bad id is skipped."""
    kind = LIST_TOOLS.get(str(call.get("name") or ""))
    if kind is None or OWNERS.get(kind) != owner or not _acted(call):
        return None
    ids: list[str] = []
    cut = False
    for match in _LIST_LINES[kind].finditer(str(call.get("result") or "")):
        fid = _list_id(kind, match.group(1))
        if not fid or fid in ids:
            continue
        if len(ids) == LIST_MAX:
            cut = True
            break
        ids.append(fid)
    return {"kind": kind, "ids": ids, "cursor": BEFORE_FIRST, "cut": cut}


def next_of(listing: Any) -> tuple[int, str] | None:
    """``(place, id)`` of the item after the cursor, or None at the end of
    the list. The place counts from 1. It never goes back to the first
    item."""
    lst = clean_list(listing)
    if lst is None:
        return None
    at = lst["cursor"] + 1
    return (at + 1, lst["ids"][at]) if at < len(lst["ids"]) else None


# ── The record of one run ───────────────────────────────────────────────────


class RunFocus:
    """The items that the calls of ONE run named (fix round 1, P1-a).

    The binding of the run holds it, so each pack call and each hand-off of
    the run reads the same record, and a new run starts a new one.
    :attr:`lock` puts the decision and the Redis act of each write in one
    order, so calls in parallel always end in the same stored state."""

    __slots__ = ("_guard", "_lock", "bound_list", "cleared", "items", "listing", "top")

    def __init__(self, listing: Any = None) -> None:
        self.items: list[dict[str, str]] = []
        self.cleared = False
        #: HRD-11b. The list that the run read at its start, for the block.
        #: It does not change in the run.
        self.bound_list: dict[str, Any] | None = clean_list(listing)
        #: The list as the calls of this run left it, for the writes. It
        #: holds the time of its list read under ``"at"``.
        self.listing: dict[str, Any] | None = None
        at = listing.get("at") if isinstance(listing, Mapping) else None
        if self.bound_list is not None and isinstance(at, int) and not isinstance(at, bool):
            self.listing = {**self.bound_list, "at": at}
        #: The highest place of an item of :attr:`listing` that the calls of
        #: this run named, or :data:`BEFORE_FIRST`.
        self.top = BEFORE_FIRST
        self._guard = threading.Lock()
        self._lock: asyncio.Lock | None = None

    @property
    def lock(self) -> asyncio.Lock:
        """The lock of the writes of this run, made on first use."""
        with self._guard:
            if self._lock is None:
                self._lock = asyncio.Lock()
            return self._lock

    def note(self, items: list[dict[str, str]], cleared: bool) -> dict[str, str] | None:
        """Add the items and the clear of some calls. Returns the ONE item
        that the run named, or None when the run must keep no item: it named
        two or more different items, or a call cleared."""
        with self._guard:
            self.cleared = self.cleared or cleared
            for item in items:
                if item not in self.items:
                    self.items.append(dict(item))
            if self.cleared or len(self.items) != 1:
                return None
            return dict(self.items[0])

    def note_list(self, owner: str, calls: list[Mapping[str, Any]]) -> str:
        """Add the list reads and the items of some calls, in their order
        (HRD-11b). Returns ``"save"`` when :attr:`listing` changed and must be
        written, ``"drop"`` when the run read a list with no id, else ``""``.

        A list read replaces the list and puts the cursor before the first
        item. An item of the list moves the cursor to the highest place that
        the calls of this run named."""
        out = ""
        with self._guard:
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                read = list_of(owner, call)
                if read is not None:
                    self.top = BEFORE_FIRST
                    self.listing = clean_list(read)
                    if self.listing is not None:
                        self.listing["at"] = int(_now())
                    out = "save" if self.listing is not None else "drop"
                    continue
                item = _item_of(owner, call)
                lst = self.listing
                if item is None or lst is None or item["kind"] != lst["kind"]:
                    continue
                if item["id"] in lst["ids"]:
                    self.top = max(self.top, lst["ids"].index(item["id"]))
                    if lst["cursor"] != self.top:
                        lst["cursor"] = self.top
                        out = "save"
            return out


# ── The binding of a run ────────────────────────────────────────────────────


def _tenant() -> str:
    """The bound tenant of this frame, or ``""``."""
    try:
        from acb_common.db import current_tenant

        return str(current_tenant() or "").strip()
    except Exception:
        return ""


def binding(run_id: str, org: str, session: str, member: str,
            item: Any) -> dict[str, Any]:
    """The change of the run context that binds the item of ONE run, with a
    new :class:`RunFocus`. Only the executor calls it, after its own gates.

    *item* is the record that :func:`read_record` gave. Its ``"list"`` key, if
    any, is the last list of the chat (HRD-11b), and the :class:`RunFocus`
    keeps it."""
    listing = item.get("list") if isinstance(item, Mapping) else None
    return {FOCUS_KEY: (str(run_id), str(org), str(session),
                        str(member).strip().lower(), clean_focus(item), RunFocus(listing))}


def open_binding(ctx: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    """``(org, session, member)`` when *ctx* holds the binding of its OWN
    run, else None.

    The binding must name the run id, the session and the member of *ctx*,
    and the bound tenant of this frame. A batch run and an org off
    ``CHAT_FOCUS_ORGS`` get None. A delegated run never holds the binding."""
    if not isinstance(ctx, Mapping):
        return None
    entry = ctx.get(FOCUS_KEY)
    if not isinstance(entry, tuple) or len(entry) != 6 or not isinstance(entry[5], RunFocus):
        return None
    run_id, org, session, member, _item, _run = entry
    if not run_id or run_id != str(ctx.get("run_id") or ""):
        return None
    if ctx.get("batch_thread") is True:
        return None
    if not session or session != str(ctx.get("session_id") or ""):
        return None
    if not member or member != str(ctx.get("member") or "").strip().lower():
        return None
    if not org or org.lower() != _tenant().lower() or not focus_on(org):
        return None
    return org, session, member


def bound_focus(ctx: Mapping[str, Any] | None, member: str) -> dict[str, str] | None:
    """The item that THIS run bound at its start, or None.

    *member* is the verified member of the run, from the run boundary, and
    it must be the member of the binding. The item passes
    :func:`clean_focus` again."""
    if open_binding(ctx) is None or ctx is None:
        return None
    entry = ctx[FOCUS_KEY]
    if entry[3] != str(member or "").strip().lower():
        return None
    return clean_focus(entry[4])


def bound_list(ctx: Mapping[str, Any] | None, member: str) -> dict[str, Any] | None:
    """The last list that THIS run bound at its start, or None (HRD-11b).
    The same gates as :func:`bound_focus`."""
    if open_binding(ctx) is None or ctx is None:
        return None
    entry = ctx[FOCUS_KEY]
    if entry[3] != str(member or "").strip().lower():
        return None
    return clean_list(entry[5].bound_list)


# ── The store ───────────────────────────────────────────────────────────────


def get_tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis as _client

    return _client()


def _member_part(member: str) -> str:
    """A hash of the member, so the key holds no address."""
    return hashlib.sha256(member.encode("utf-8")).hexdigest()[:24]


async def read_focus(org: str, session: str, member: str) -> dict[str, str] | None:
    """The stored item of this chat and member, or None. Never raises.

    *org* must be the bound tenant of this frame, or nothing is read. The
    value read back passes :func:`clean_focus` again, and its time must be
    fresh (:data:`FOCUS_FRESH_S`). A miss, a bad or old value or a Redis fault
    gives None, and the turn goes on with no item."""
    member = str(member or "").strip().lower()
    if not (org and session and member) or str(org).lower() != _tenant().lower():
        return None
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            raw = await asyncio.wait_for(
                get_tenant_redis().get(key(NAMESPACE, session, _member_part(member))),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("chat_focus.read_failed", error=type(exc).__name__)
        return None
    if not raw:
        return None
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(stored, Mapping) or not _fresh(stored.get("at")):
        return None
    return clean_focus(stored)


async def read_list(org: str, session: str, member: str) -> dict[str, Any] | None:
    """The stored last list of this chat and member, or None (HRD-11b).
    Never raises.

    The same gates as :func:`read_focus`. The value read back passes
    :func:`clean_list` again, and the time of its list read must be fresh
    (:data:`FOCUS_FRESH_S`)."""
    member = str(member or "").strip().lower()
    if not (org and session and member) or str(org).lower() != _tenant().lower():
        return None
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            raw = await asyncio.wait_for(
                get_tenant_redis().get(key(LIST_NAMESPACE, session, _member_part(member))),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:
        _log.warning("chat_focus.list_read_failed", error=type(exc).__name__)
        return None
    if not raw or (isinstance(raw, (str, bytes)) and len(raw) > LIST_MAX_BYTES):
        return None
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(stored, Mapping) or not _fresh(stored.get("at")):
        return None
    lst = clean_list(stored)
    if lst is not None:
        lst["at"] = int(stored["at"])
    return lst


async def read_record(org: str, session: str, member: str) -> dict[str, Any] | None:
    """The record of this chat and member: the item, and the last list under
    the key ``"list"`` (HRD-11b). None when neither is stored. Both reads run
    at once. Never raises. The executor's early read calls it."""
    item, listing = await asyncio.gather(
        read_focus(org, session, member), read_list(org, session, member))
    if item is None and listing is None:
        return None
    out: dict[str, Any] = dict(item or {})
    if listing is not None:
        out["list"] = listing
    return out


async def save_list(listing: Any, ctx: Mapping[str, Any] | None) -> bool:
    """Keep *listing* as the last list of the chat of *ctx* (HRD-11b).

    The value holds the time of its list read, the ``"at"`` of *listing*. A
    move of the cursor keeps that time. The TTL is what is left of
    :data:`FOCUS_FRESH_S`. Returns False when nothing was kept: no open
    binding, a bad list, no time or an old one, a value over
    :data:`LIST_MAX_BYTES`, or a Redis fault. Never raises."""
    slot = open_binding(ctx)
    lst = clean_list(listing)
    if slot is None or lst is None:
        return False
    at = listing.get("at") if isinstance(listing, Mapping) else None
    if isinstance(at, bool) or not isinstance(at, int):
        return False
    ttl = FOCUS_TTL_S - max(0, int(_now()) - at)
    if ttl <= 0 or not _fresh(at):
        return False
    value = json.dumps({"v": LIST_VERSION, **lst, "at": at}, sort_keys=True)
    if len(value) > LIST_MAX_BYTES:
        return False
    org, session, member = slot
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await asyncio.wait_for(
                get_tenant_redis().setex(
                    key(LIST_NAMESPACE, session, _member_part(member)), ttl, value),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:
        _log.warning("chat_focus.list_write_failed", error=type(exc).__name__)
        return False
    return True


async def drop_list(ctx: Mapping[str, Any] | None) -> bool:
    """Delete the last list of the chat of *ctx* (HRD-11b). Returns False
    when nothing was deleted. Never raises."""
    slot = open_binding(ctx)
    if slot is None:
        return False
    org, session, member = slot
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await asyncio.wait_for(
                get_tenant_redis().delete(key(LIST_NAMESPACE, session, _member_part(member))),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:
        _log.warning("chat_focus.list_drop_failed", error=type(exc).__name__)
        return False
    return True


async def save_focus(item: Any, ctx: Mapping[str, Any] | None) -> bool:
    """Keep *item* for the chat of *ctx*, with the time of the write. Returns
    False when nothing was kept: no open binding (:func:`open_binding`), a
    bad item, or a Redis fault. Never raises."""
    slot = open_binding(ctx)
    focus = clean_focus(item)
    if slot is None or focus is None:
        return False
    org, session, member = slot
    value = json.dumps({"v": VERSION, "kind": focus["kind"], "id": focus["id"],
                        "at": int(_now())}, sort_keys=True)
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await asyncio.wait_for(
                get_tenant_redis().setex(
                    key(NAMESPACE, session, _member_part(member)), FOCUS_TTL_S, value),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:
        _log.warning("chat_focus.write_failed", error=type(exc).__name__)
        return False
    return True


async def drop_focus(ctx: Mapping[str, Any] | None) -> bool:
    """Delete the stored item of the chat of *ctx*. Returns False when
    nothing was deleted: no open binding, or a Redis fault. Never raises."""
    slot = open_binding(ctx)
    if slot is None:
        return False
    org, session, member = slot
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await asyncio.wait_for(
                get_tenant_redis().delete(key(NAMESPACE, session, _member_part(member))),
                timeout=REDIS_TIMEOUT_S,
            )
    except Exception as exc:
        _log.warning("chat_focus.drop_failed", error=type(exc).__name__)
        return False
    return True


async def record_calls(
    owner: str, calls: Iterable[Mapping[str, Any]], ctx: Mapping[str, Any] | None,
) -> bool:
    """Write the item of the run after *calls*, for the chat of *ctx*. THE
    writer of both seams. *owner* is the agent whose tools made the calls.

    The run's :class:`RunFocus` adds the items and the clears of *calls*.
    One item for the whole run writes that item. Two or more different
    items, or a clear, delete the stored item, so no item shows. Calls that
    name no item and clear nothing change nothing. A run with no open
    binding writes nothing. Returns True when Redis kept an item. Never
    raises."""
    try:
        if open_binding(ctx) is None or ctx is None:
            return False
        calls = list(calls)
        items, cleared = named_items(owner, calls)
        run: RunFocus = ctx[FOCUS_KEY][5]
        async with run.lock:
            # HRD-11b: the last list and its cursor, from the same calls.
            act = run.note_list(owner, calls)
            if act == "save":
                await save_list(run.listing, ctx)
            elif act == "drop":
                await drop_list(ctx)
            if not items and not cleared:
                return False
            item = run.note(items, cleared)
            if item is None:
                await drop_focus(ctx)
                _log.info("chat_focus.dropped", reason="cleared" if run.cleared else "many")
                return False
            kept = await save_focus(item, ctx)
    except Exception as exc:
        _log.warning("chat_focus.record_failed", error=type(exc).__name__)
        return False
    if kept:
        _log.info("chat_focus.kept", kind=item["kind"])
    return kept


# ── The block ───────────────────────────────────────────────────────────────

#: The lines of the rule, after the ids. The model reads them each turn. The
#: rule yields: the member's own words and a newer item win over the item.
RULE_LINES: tuple[str, ...] = (
    '"It", "this" and "mark it done" can mean this item.',
    "Use this item only when the words of the member do not name another item.",
    "An item that the member names wins over this item.",
    "A newer item in this chat wins over this item.",
    'For "this task" or "this email", the open page item under '
    '"## Current context" wins.',
)


#: The heading of the list part of the block (HRD-11b).
LIST_HEAD = "## The last list in this chat"
#: The lines of the rule of "the next one", after the list lines.
LIST_RULE_LINES: tuple[str, ...] = (
    '"The next one" means the item on the "next" line.',
    'Use the id on the "next" line. Do not guess an id.',
    'When the "next" line says that no next item exists, say so. '
    "Do not go back to the first item.",
    "A list or an item that the member names wins over this list.",
)


def _list_lines(listing: Any) -> list[str]:
    """The list part of the block, or ``[]``. The kind, the count, the place
    of the cursor and the next item: ids and links only (HRD-11b)."""
    lst = clean_list(listing)
    if lst is None:
        return []
    kind, ids, cursor = lst["kind"], lst["ids"], lst["cursor"]
    total = len(ids)
    where = ("before the first item" if cursor == BEFORE_FIRST
             else f"{cursor + 1} of {total}")
    nxt = next_of(lst)
    if nxt is not None:
        place, fid = nxt
        next_line = (f"- next: {place} of {total}, {ID_ARGS[kind]} {fid}, "
                     f"in-app link {LINKS[kind].format(id=fid)}")
    elif lst["cut"]:
        next_line = (f"- next: no next item in the {total} items that this chat "
                     "keeps. Read the list again for more.")
    else:
        next_line = f"- next: no next item. Item {total} of {total} is the end of the list."
    return [
        LIST_HEAD,
        "The list that a tool of this chat read last, as ids in the order of "
        "the tool result. It holds no titles.",
        f"- kind: {kind}",
        f"- items: {total}",
        f"- position: {where}",
        next_line,
        *LIST_RULE_LINES,
    ]


def orchestrator_block(item: Any, listing: Any = None) -> str:
    """The block of the item for the orchestrator's per-turn context, or
    ``""``. Ids, the kind and the link only: no title and no text that a
    member or a stranger wrote.

    HRD-11b: *listing* is the last list of the chat. With a list, the block
    adds its part after the item, or shows only that part when no item is
    in focus. With no list, the text is the text of HRD-11a."""
    lines: list[str] = []
    focus = clean_focus(item)
    if focus is not None:
        kind, fid = focus["kind"], focus["id"]
        lines = [
            BLOCK_HEAD,
            "The email or task that a tool of this chat read or acted on last. "
            "It is an id, not a title.",
            f"- kind: {kind}",
            f"- {ID_ARGS[kind]}: {fid}",
            f"- in-app link: {LINKS[kind].format(id=fid)}",
            *RULE_LINES,
        ]
    return "\n".join([*lines, *_list_lines(listing)])


__all__ = [
    "BEFORE_FIRST",
    "BLOCK_HEAD",
    "CLEARS",
    "EMAIL",
    "FOCUS_FRESH_S",
    "FOCUS_KEY",
    "FOCUS_TOOLS",
    "FOCUS_TTL_S",
    "ID_ARGS",
    "KINDS",
    "LINKS",
    "LIST_HEAD",
    "LIST_MAX",
    "LIST_MAX_BYTES",
    "LIST_NAMESPACE",
    "LIST_RULE_LINES",
    "LIST_TOOLS",
    "LIST_VERSION",
    "NAMESPACE",
    "NO_ACT",
    "OWNERS",
    "PROJECTS_AGENT",
    "RULE_LINES",
    "TASK",
    "VERSION",
    "RunFocus",
    "binding",
    "bound_focus",
    "bound_list",
    "carry_focus",
    "clean_focus",
    "clean_list",
    "drop_focus",
    "drop_list",
    "focus_of",
    "focus_on",
    "list_of",
    "named_items",
    "next_of",
    "open_binding",
    "orchestrator_block",
    "read_focus",
    "read_list",
    "read_record",
    "record_calls",
    "save_focus",
    "save_list",
]


async def carry_focus(org: str, source: str, target: str, member: str) -> bool:
    """Copy the item in focus of *member* in chat *source* to the new chat
    *target* (HRD-19a, ``chat_failure_modes.md`` E3). True when it copied.

    It copies the member's own record key to key and writes no text into the
    new chat. The item passes :func:`clean_focus` again, it must still be
    fresh, and it keeps its ORIGINAL time, so it is no younger in the new
    chat and its life there is what is left of :data:`FOCUS_FRESH_S`. It
    copies nothing while ``CHAT_FOCUS_ORGS`` leaves *org* out. It never
    raises. The caller gives the org and the member from the verified
    session, never from a request field.
    """
    org, source, target = str(org or ""), str(source or ""), str(target or "")
    member = str(member or "").strip().lower()
    if not (org and source and target and member) or source == target or not focus_on(org):
        return False
    try:
        from acb_common.tenant_redis import key, organization_scope

        part = _member_part(member)
        with organization_scope(org):
            redis = get_tenant_redis()
            raw = await asyncio.wait_for(
                redis.get(key(NAMESPACE, source, part)), timeout=REDIS_TIMEOUT_S)
            if not raw:
                return False
            stored = json.loads(raw)
            if not isinstance(stored, Mapping) or not _fresh(stored.get("at")):
                return False
            item = clean_focus(stored)
            if item is None:
                return False
            left = max(1, int(FOCUS_FRESH_S - (_now() - float(stored["at"]))))
            value = json.dumps({"v": VERSION, "kind": item["kind"], "id": item["id"],
                                "at": int(float(stored["at"]))}, sort_keys=True)
            await asyncio.wait_for(
                redis.setex(key(NAMESPACE, target, part), min(left, FOCUS_TTL_S), value),
                timeout=REDIS_TIMEOUT_S)
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("chat_focus.carry_failed", error=type(exc).__name__)
        return False
    return True
