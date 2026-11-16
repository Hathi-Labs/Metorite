"""The item that a chat read or acted on last (HRD-11a, task 2990).

Spec ``project-docs/specs/chat_failure_modes.md`` §4.5 E1 and §6 row HRD-11a.
Outside email, nothing held the item that the chat read or acted on last. So
"mark it done" after a read of a task had no item, and the model guessed or
asked. This module keeps ONE item for each chat and member: the email or the
Projects task of the last tool call that read it or acted on it.

* :data:`FOCUS_TOOLS` is the closed table of the tools that set the item, as
  ``tool -> (kind, id argument)``. The email rows are the tools of
  ``email_chat_context._EMAIL_ID_TOOLS``. The task rows are the tools of the
  core ``projects`` pack that read or act on ONE task by its ``task_id``. A
  list read (``find_tasks``, ``list_tasks``, ``query_inbox``) is not in it,
  so it sets nothing. ``tests/unit/test_focus_item.py`` fails when a tool of
  the table lacks its id argument in its real signature.
* Only a call that succeeded sets the item, and only when its result does not
  start with a lead of ``email_chat_context._NO_ACT``. An id that fails
  ``email_chat_context._ok_id`` sets nothing. A task id must also read as a
  UUID, because each Projects tool takes only a UUID and the link needs one.
* Two seams write it (:func:`record_calls`), each from the calls of a tool
  and never from the text of the model: ``orchestrator._tool_injection
  ._record_pack_call`` after a pack call, and the sub-run hook of
  ``orchestrator.executor._run_sub_agent_streaming`` after a hand-off.
* The executor reads the item once for each turn (:func:`read_focus`), binds
  it into the run (:data:`FOCUS_KEY`), and shows it in the per-turn context
  after the OA-4 block (:func:`orchestrator_block`).

THE BLOCK holds the kind, the id and the in-app link, and no title (Q1). A
title is text that a stranger wrote. Each id passes :func:`clean_focus` before
it is stored, after it is read back, and before it shows.

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
#: The life of an item, slid by each write. A chat has no retention limit, so
#: the item lasts while the chat is in use and then goes (as the OA-4 list).
FOCUS_TTL_S = 30 * 24 * 3600
#: The longest wait for Redis. A slow Redis costs the item, never the turn.
REDIS_TIMEOUT_S = 1.0
#: The version of the stored value. A later slice (HRD-11b) adds fields, and
#: a reader of this version reads the kind and the id only.
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

#: The artifact-context key of the binding of ONE run:
#: ``(run_id, org, session, member, item or None)``. Only the executor binds
#: it, for a run that may read and write the item. A delegated run drops it.
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


def _item_of(owner: str, call: Mapping[str, Any]) -> dict[str, str] | None:
    """The item that one call of *owner* sets, or None.

    Only a tool of :data:`FOCUS_TOOLS`, called by the owner of its kind. Only
    a call that succeeded, whose result does not start with a lead of
    ``_NO_ACT``. Only an id argument that passes :func:`clean_focus`."""
    row = FOCUS_TOOLS.get(str(call.get("name") or ""))
    if row is None:
        return None
    kind, arg = row
    if OWNERS.get(kind) != owner or call.get("success") is False:
        return None
    if str(call.get("result") or "").lstrip().startswith(_NO_ACT):
        return None
    return clean_focus({"kind": kind, "id": _args_of(call.get("args")).get(arg)})


def focus_of(owner: str, calls: Iterable[Mapping[str, Any]]) -> dict[str, str] | None:
    """The item of the LAST call of *calls* that sets one, or None.

    Each call is ``{"name", "args", "result", "success"}``, from the tool
    call itself, never from the text of the model."""
    found: dict[str, str] | None = None
    for call in calls:
        if not isinstance(call, Mapping):
            continue
        item = _item_of(owner, call)
        if item is not None:
            found = item
    return found


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
    """The change of the run context that binds the item of ONE run. Only
    the executor calls it, after its own gates."""
    return {FOCUS_KEY: (str(run_id), str(org), str(session),
                        str(member).strip().lower(), clean_focus(item))}


def open_binding(ctx: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    """``(org, session, member)`` when *ctx* holds the binding of its OWN
    run, else None.

    The binding must name the run id, the session and the member of *ctx*,
    and the bound tenant of this frame. A batch run and an org off
    ``CHAT_FOCUS_ORGS`` get None. A delegated run never holds the binding."""
    if not isinstance(ctx, Mapping):
        return None
    entry = ctx.get(FOCUS_KEY)
    if not isinstance(entry, tuple) or len(entry) != 5:
        return None
    run_id, org, session, member, _item = entry
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
    value read back passes :func:`clean_focus` again. A miss, a bad value or
    a Redis fault gives None, and the turn goes on with no item."""
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
        return clean_focus(json.loads(raw))
    except (TypeError, ValueError):
        return None


async def save_focus(item: Any, ctx: Mapping[str, Any] | None) -> bool:
    """Keep *item* for the chat of *ctx*. Returns False when nothing was
    kept: no open binding (:func:`open_binding`), a bad item, or a Redis
    fault. Never raises."""
    slot = open_binding(ctx)
    focus = clean_focus(item)
    if slot is None or focus is None:
        return False
    org, session, member = slot
    value = json.dumps({"v": VERSION, "kind": focus["kind"], "id": focus["id"]},
                       sort_keys=True)
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


async def record_calls(
    owner: str, calls: Iterable[Mapping[str, Any]], ctx: Mapping[str, Any] | None,
) -> bool:
    """Write the item of the last call of *calls* that sets one, for the chat
    of *ctx*. THE writer of both seams. *owner* is the agent whose tools
    made the calls. A run with no open binding, or calls that set no item,
    write nothing. Returns True when Redis kept an item. Never raises."""
    try:
        if open_binding(ctx) is None:
            return False
        item = focus_of(owner, calls)
        if item is None:
            return False
        kept = await save_focus(item, ctx)
    except Exception as exc:
        _log.warning("chat_focus.record_failed", error=type(exc).__name__)
        return False
    if kept:
        _log.info("chat_focus.kept", kind=item["kind"])
    return kept


# ── The block ───────────────────────────────────────────────────────────────

#: The lines of the rule, after the ids. The model reads them each turn.
RULE_LINES: tuple[str, ...] = (
    '"It", "this" and "mark it done" mean this item.',
    "An item that the member names wins over this item.",
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
    "EMAIL",
    "FOCUS_KEY",
    "FOCUS_TOOLS",
    "FOCUS_TTL_S",
    "ID_ARGS",
    "KINDS",
    "LINKS",
    "NAMESPACE",
    "OWNERS",
    "PROJECTS_AGENT",
    "RULE_LINES",
    "TASK",
    "VERSION",
    "binding",
    "bound_focus",
    "clean_focus",
    "focus_of",
    "focus_on",
    "open_binding",
    "orchestrator_block",
    "read_focus",
    "record_calls",
    "save_focus",
]
