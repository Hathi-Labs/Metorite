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

from collections.abc import Callable, Mapping
from typing import Any

from agent_framework import ChatMiddleware, FunctionMiddleware

__all__ = ["RefuseTools", "WithholdTools", "tool_name"]

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
