"""The network control on the agents that a covered run delegates to (H-236).

Spec ``project-docs/specs/maf_coding_engine.md`` §16.3, the owner decision
"Keep delegation" of 2026-10-03, and the prerequisite of WS-43w. Fence
WS43-F24, ``tests/unit/test_delegation_no_egress.py``.

**The threat.** A covered run of projects-assistant holds member data, in
``/workspace/.run/`` and in the model's context. The owner kept delegation,
so the run may call another agent. That agent runs OUTSIDE the sandbox, on
the host, with its own tools. A model that an injection steers could hand it
member data and ask it to send the data out.

**The control.** Every run that a covered run delegates to, directly or
through another delegation, carries ``no_egress=True`` in its artifact
context. The orchestrator decides it, from the PARENT's binding and never
from input (``orchestrator._tool_injection._delegated_no_egress``). A child
cannot clear it, because the artifact context is an immutable mapping per
run, and a run inherits the flag before it computes anything of its own.

A ``no_egress`` run:

1. gets no egress-capable tool at injection (:func:`is_egress_tool`), and no
   MCP server or MCP tool;
2. refuses a call to one at call time: :class:`RefuseEgressTools` on the MAF
   path (as ``sandbox_tools.RefuseHostTools`` does), and
   ``permission_policy.guard_shared_agent_shell`` on the Copilot path.

Delegation itself stays: :data:`DELEGATION_TOOLS` are never egress tools,
because the flag travels with them. So a delegated agent can still read and
compute, and it cannot send.

**One source of truth.** A tool is egress-capable when its risk annotation
says ``open_world`` (``acb_skills.tool_annotations``: "the tool reaches
outside Metorite"). The four delegation tools are the one exception. A tool
that can carry data off the platform MUST set ``open_world=True``, or this
control cannot see it.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, ContextProvider, FunctionMiddleware

_log = get_logger("acb_skills.egress")

__all__ = [
    "DELEGATION_TOOLS",
    "EGRESS_WITHHELD_ANSWER",
    "NO_EGRESS_KEY",
    "EgressGuardProvider",
    "RefuseEgressTools",
    "WithholdEgressTools",
    "egress_tool_names",
    "is_egress_tool",
    "no_egress_for_this_run",
    "tool_name",
]

#: The artifact-context key. ``True`` means this run holds no egress tool.
NO_EGRESS_KEY = "no_egress"
SOURCE_ID = "metorite-no-egress"

#: The tools that hand a task to another agent. The owner kept them in a
#: covered run on 2026-10-03, so they are never egress tools. The sub-run
#: inherits ``no_egress`` instead. The orchestrator's specialist tools
#: (one per registered agent) carry no annotation, so they are never egress
#: tools either, and they delegate through the same executor seam.
DELEGATION_TOOLS: frozenset[str] = frozenset({
    "call_agent", "call_agents_parallel", "call_agent_background", "delegate_to_agent",
})

#: The answer to a call that the control refuses. It names what is true of
#: this run and claims nothing about the whole platform.
EGRESS_WITHHELD_ANSWER = (
    "{name} is off in this run. Another agent called you during a sandboxed "
    "turn, so this run may not send data off the platform. Read and compute "
    "with your other tools, and give the answer back to the agent that called you."
)


def tool_name(item: Any) -> str:
    """The name of a tool in each shape the agents hold one."""
    if isinstance(item, Mapping):
        fn = item.get("function")
        return str((fn or {}).get("name") or item.get("name") or "")
    return str(
        getattr(item, "name", None)
        or getattr(item, "__name__", None)
        or getattr(getattr(item, "func", item), "__name__", "")
        or ""
    )


def _mcp_classes() -> tuple[type, ...]:
    try:
        from agent_framework import MCPStdioTool, MCPStreamableHTTPTool, MCPWebsocketTool
    except ImportError:  # pragma: no cover — MAF ships all three
        return ()
    return (MCPStdioTool, MCPStreamableHTTPTool, MCPWebsocketTool)


def _risk_of(item: Any, name: str) -> Mapping[str, Any] | None:
    for obj in (item, getattr(item, "func", None)):
        hints = getattr(obj, "__tool_risk__", None)
        if isinstance(hints, Mapping):
            return hints
    from acb_skills.tool_annotations import TOOL_ANNOTATIONS

    return TOOL_ANNOTATIONS.get(name)


def is_egress_tool(item: Any) -> bool:
    """True when *item* can send data off the platform (H-236).

    *item* is a tool object, a plain function, a dict spec or a bare name. An
    MCP tool is always one: it reaches a server outside the platform. Else the
    risk annotation decides (``open_world``), and a delegation tool never is.
    """
    if not isinstance(item, str) and isinstance(item, _mcp_classes()):
        return True
    name = item if isinstance(item, str) else tool_name(item)
    if not name or name in DELEGATION_TOOLS:
        return False
    hints = None if isinstance(item, str) else _risk_of(item, name)
    if hints is None:
        from acb_skills.tool_annotations import TOOL_ANNOTATIONS

        hints = TOOL_ANNOTATIONS.get(name)
    return bool(hints and hints.get("open_world"))


def egress_tool_names(tools: Iterable[Any] | None = None) -> frozenset[str]:
    """The names of the egress tools among *tools*.

    With no *tools*, every registered name that :func:`is_egress_tool` says
    yes to. The fence pins that set.
    """
    if tools is None:
        from acb_skills.tool_annotations import TOOL_ANNOTATIONS

        tools = list(TOOL_ANNOTATIONS)
    return frozenset(tool_name(t) if not isinstance(t, str) else t
                     for t in tools if is_egress_tool(t))


def no_egress_for_this_run() -> bool:
    """True when the run on this frame holds no egress tool (H-236).

    Only a missing key, or an explicit ``False``, reads as "may send". Any
    other value reads as ``no_egress``, so a damaged flag fails closed.
    """
    try:
        from acb_skills.write_artifact import artifact_context

        return artifact_context().get(NO_EGRESS_KEY, False) is not False
    except Exception:
        return True


class WithholdEgressTools(ChatMiddleware):
    """Takes every egress tool out of each model request of ONE run.

    Injection already left them out. This catches a tool that joined the run
    later, for example through a context provider. It changes a copy of the
    request's options, never an agent object.
    """

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        options = context.options
        tools = options.get("tools") if isinstance(options, dict) else None
        if tools:
            kept = [t for t in tools if not is_egress_tool(t)]
            if len(kept) != len(tools):
                context.options = {**options, "tools": kept}
        await call_next()


class RefuseEgressTools(FunctionMiddleware):
    """Refuses a call to an egress tool, in case the model names one."""

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        function = getattr(context, "function", None)
        if function is not None and is_egress_tool(function):
            name = tool_name(function)
            _log.info("egress.call_refused", tool=name)
            context.result = EGRESS_WITHHELD_ANSWER.format(name=name)
            return
        await call_next()


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
