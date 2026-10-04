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

from collections.abc import Iterable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider

from acb_skills.tool_guard import RefuseTools, WithholdTools, tool_name

_log = get_logger("acb_skills.egress")

__all__ = [
    "DELEGATION_TOOLS",
    "EGRESS_WITHHELD_ANSWER",
    "NO_EGRESS_KEY",
    "STORE_WRITES",
    "EgressGuardProvider",
    "RefuseEgressTools",
    "WithholdEgressTools",
    "egress_tool_names",
    "has_explicit_open_world",
    "is_egress_tool",
    "no_egress_for_this_run",
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


def _from_mcp(item: Any) -> bool:
    """A MAF MCP tool, or a FunctionTool that MAF made from an MCP server."""
    if isinstance(item, _mcp_classes()):
        return True
    props = getattr(item, "additional_properties", None)
    return isinstance(props, Mapping) and bool(props.get("_mcp_is_tool"))


#: The packages whose callables ARE the platform's tools. A registry entry
#: that the platform made (not an agent's ``annotate`` call) is trusted for a
#: tool object only when the object's callable comes from one of these.
_PLATFORM_PACKAGES = ("acb_skills.", "orchestrator.")
#: MAF's own provider tools (the file and skill tools of a sandboxed run).
#: Their callables are closures inside ``agent_framework``, so their names
#: differ from the tool names, and only the sandbox names are trusted there.
_MAF_PROVIDER_PACKAGE = "agent_framework."


def _base_callable(item: Any) -> Any:
    """The callable under a tool object, through its wrappers."""
    fn = getattr(item, "func", item)
    for _ in range(16):
        inner = getattr(fn, "__wrapped__", None)
        if inner is None:
            break
        fn = inner
    return fn


def _platform_owned(item: Any, name: str) -> bool:
    """True when the tool object *item*, called *name*, is the platform's own.

    Module and name (H-236, fix round 2): the callable comes from a platform
    package and carries the tool's name, as every injected tool, sandbox tool,
    workflow tool and app tool does. Or it is one of MAF's provider tools of a
    sandboxed run. A tool of another repo that only borrows a platform name
    (its own ``run_diagnostics`` or ``write_artifact``) fails this test.
    """
    from acb_skills.tool_annotations import SANDBOX_TOOL_NAMES

    fn = _base_callable(item)
    module = str(getattr(fn, "__module__", "") or "")
    if module.startswith(_PLATFORM_PACKAGES):
        return str(getattr(fn, "__name__", "") or "") == name
    return module.startswith(_MAF_PROVIDER_PACKAGE) and name in SANDBOX_TOOL_NAMES


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
    delegation tool may stay. An MCP tool, a store write, and a tool with no
    explicit ``open_world=False`` may not.
    """
    if not isinstance(item, str) and _from_mcp(item):
        return True
    name = item if isinstance(item, str) else tool_name(item)
    if name in DELEGATION_TOOLS:
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
