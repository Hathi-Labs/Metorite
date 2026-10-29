"""One per-run middleware pair that withholds and refuses tools by a rule.

Two controls take tools away from ONE run: the host floor tools of a
sandboxed run (``sandbox_tools``, WS-43d) and the egress tools of a
``no_egress`` run (``egress``, H-236). They differ only in the rule that
picks a tool and in the answer the model reads. So this module holds the
pair once, and each control passes its rule.

* :class:`WithholdTools` takes the picked tools out of each model request.
  It changes a copy of the request's options, never an agent object.
* :class:`RefuseTools` answers a call to a picked tool, in case the model
  names one anyway, and the tool never runs.

Fences: ``tests/unit/test_projects_sandbox_tools.py`` (WS43-F21) and
``tests/unit/test_delegation_no_egress.py`` (WS43-F24).
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from acb_common import get_logger
from agent_framework import ChatMiddleware, ContextProvider, FunctionMiddleware

__all__ = [
    "LogRequestTools", "LogRequestToolsProvider", "RefuseTools", "WithholdTools",
    "tool_digest", "tool_name",
]

_log = get_logger("acb_skills.tool_guard")

#: Picks a tool. It gets the tool object, as MAF holds it.
Picker = Callable[[Any], bool]
#: The answer the model reads for a refused call. It gets the tool's name.
Answer = Callable[[str], str]


def tool_name(item: Any) -> str:
    """The name of a tool in each shape that the agents hold one."""
    if isinstance(item, Mapping):
        fn = item.get("function")
        return str((fn or {}).get("name") or item.get("name") or "")
    return str(
        getattr(item, "name", None)
        or getattr(item, "__name__", None)
        or getattr(getattr(item, "func", item), "__name__", "")
        or ""
    )


class WithholdTools(ChatMiddleware):
    """Takes every tool that *picks* says yes to out of each model request."""

    def __init__(self, picks: Picker) -> None:
        self._picks = picks

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        options = context.options
        tools = options.get("tools") if isinstance(options, dict) else None
        if tools:
            kept = [t for t in tools if not self._picks(t)]
            if len(kept) != len(tools):
                context.options = {**options, "tools": kept}
        await call_next()


class RefuseTools(FunctionMiddleware):
    """Answers a call to a tool that *picks* says yes to, with *answer*."""

    def __init__(self, picks: Picker, answer: Answer) -> None:
        self._picks = picks
        self._answer = answer

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        function = getattr(context, "function", None)
        if function is not None and self._picks(function):
            context.result = self._answer(tool_name(function))
            return
        await call_next()


def tool_digest(tools: Any) -> tuple[int, int, str]:
    """``(sent, distinct, hash)`` of a tool list as a model request holds it.

    *sent* is the number of items, *distinct* the number of different names,
    and *hash* the first 10 hex digits of the SHA-256 of the sorted names.
    The hash shows that two requests hold the same tools without the names.
    """
    items = list(tools or [])
    names = sorted({tool_name(t) for t in items if tool_name(t)})
    digest = hashlib.sha256("\n".join(names).encode("utf-8")).hexdigest()[:10]
    return len(items), len(names), digest


class LogRequestTools(ChatMiddleware):
    """Logs the tools of each model request of ONE run (WS-67 OA-8f).

    It sees the request after MAF merged the agent's tools, the tools of a
    context provider (the pack loader) and the tools of an MCP server. So the
    count is what the model gets, not an estimate. It changes nothing.
    """

    def __init__(self, agent_name: str) -> None:
        self._agent = agent_name
        self._request = 0

    async def process(self, context: Any, call_next: Callable[[], Any]) -> None:
        options = context.options
        tools = options.get("tools") if isinstance(options, dict) else None
        self._request += 1
        try:
            sent, distinct, digest = tool_digest(tools)
            _log.info(
                "executor.request_tools", agent=self._agent, request=self._request,
                tools=sent, distinct=distinct, names_hash=digest,
            )
        except Exception:  # a log never costs the request
            pass
        await call_next()


class LogRequestToolsProvider(ContextProvider):
    """Adds :class:`LogRequestTools` to ONE run of a native MAF agent."""

    SOURCE_ID = "request_tools_log"

    def __init__(self, agent_name: str) -> None:
        super().__init__(self.SOURCE_ID)
        self._agent = agent_name

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(self.SOURCE_ID, [LogRequestTools(self._agent)])
