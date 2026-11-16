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

Fences (R7): ``tests/unit/test_focus_item.py`` and
``evals/trajectories/test_focus_item_trajectory.py``.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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


# ── The record of one run ───────────────────────────────────────────────────


class RunFocus:
    """The items that the calls of ONE run named (fix round 1, P1-a).

    The binding of the run holds it, so each pack call and each hand-off of
    the run reads the same record, and a new run starts a new one.
    :attr:`lock` puts the decision and the Redis act of each write in one
    order, so calls in parallel always end in the same stored state."""

    __slots__ = ("_guard", "_lock", "cleared", "items")

    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []
        self.cleared = False
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
    new :class:`RunFocus`. Only the executor calls it, after its own gates."""
    return {FOCUS_KEY: (str(run_id), str(org), str(session),
                        str(member).strip().lower(), clean_focus(item), RunFocus())}


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
        items, cleared = named_items(owner, list(calls))
        if not items and not cleared:
            return False
        run: RunFocus = ctx[FOCUS_KEY][5]
        async with run.lock:
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


def orchestrator_block(item: Any) -> str:
    """The block of the item for the orchestrator's per-turn context, or
    ``""``. Ids, the kind and the link only: no title and no text that a
    member or a stranger wrote."""
    focus = clean_focus(item)
    if focus is None:
        return ""
    kind, fid = focus["kind"], focus["id"]
    return "\n".join([
        BLOCK_HEAD,
        "The email or task that a tool of this chat read or acted on last. "
        "It is an id, not a title.",
        f"- kind: {kind}",
        f"- {ID_ARGS[kind]}: {fid}",
        f"- in-app link: {LINKS[kind].format(id=fid)}",
        *RULE_LINES,
    ])


__all__ = [
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
    "clean_focus",
    "drop_focus",
    "focus_of",
    "focus_on",
    "named_items",
    "open_binding",
    "orchestrator_block",
    "read_focus",
    "record_calls",
    "save_focus",
]
