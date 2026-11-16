"""The per-turn budget of each read tool (HRD-5, task 2984).

Spec: ``project-docs/specs/chat_failure_modes.md`` §4 row B5 and §6 HRD-5.

The rule and its counter live in ``orchestrator.watchdog``
(:class:`~orchestrator.watchdog.ToolTurnBudget`). This module is only the
function middleware that applies it, as the one place that can ANSWER a call
without running the tool. ``LoopDetector`` in the executor sees a result after
the fact, so it cannot do that.

Rules that bind:

1. **One budget for each turn.** :class:`ToolTurnBudgetProvider` makes a new
   :class:`ToolTurnBudget` in ``before_run``, so the state dies with the turn
   and is never shared across runs, members or organizations.
2. **An allow-list of reads.** Only a tool in ``watchdog.BUDGET_TOOLS`` that is
   marked read-only is counted or stopped. A delegation tool and a tool that
   waits for the member are exempt too, and a write is never touched.
   **Parallel calls:** MAF runs the calls of one model response together
   (``asyncio.gather``), so all start before any answer. The limit binds from
   the next model round, not inside one round.
3. **The stop does not run the tool.** After 3 empty or refused answers from
   one tool, the next call gets ``watchdog.stop_text`` as its result.
4. **Dark.** ``TOOL_TURN_BUDGET_ORGS`` (a comma list of organization ids, or
   ``*``) opens it. Empty is the default, and then
   ``_tool_injection._apply_tool_turn_budget`` attaches nothing. The org is the
   bound tenant of the run, never request input (R5).

Fence: ``tests/unit/test_tool_turn_budget.py``.
"""
from __future__ import annotations

from typing import Any

from acb_common import get_logger
from agent_framework import ContextProvider, FunctionMiddleware

from orchestrator import watchdog
from orchestrator.watchdog import ToolTurnBudget

_log = get_logger("orchestrator.tool_turn_budget")

SOURCE_ID = "metorite-tool-turn-budget"


def tool_turn_budget_on(org: object = None) -> bool:
    """True when ``TOOL_TURN_BUDGET_ORGS`` names *org*. THE reader.

    *org* defaults to the bound tenant of the run. A comma list of org ids,
    or ``*`` for each one. Empty means off. It reads the settings on each
    call, so a flag reload applies a change. It fails closed.
    """
    try:
        from acb_common import get_settings

        raw = str(getattr(get_settings(), "tool_turn_budget_orgs", "") or "")
    except Exception:
        return False
    entries = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not entries:
        return False
    if "*" in entries:
        return True
    if org is None:
        try:
            from acb_common.db import current_tenant

            org = current_tenant()
        except Exception:
            return False
    return str(org or "").strip().lower() in entries


def _budgeted(function: Any) -> bool:
    """True when *function* is a read tool that the budget may count and stop."""
    from acb_skills.progress_notice import _exempt, _is_read
    from acb_skills.tool_guard import tool_name

    try:
        return (
            tool_name(function) in watchdog.BUDGET_TOOLS
            and not _exempt(function)
            and _is_read(function)
        )
    except Exception:
        return False


def _answer_text(result: Any) -> str | None:
    from acb_skills.tool_result_spill import _plain_text

    return _plain_text(result)


class ToolTurnBudgetMiddleware(FunctionMiddleware):
    """Applies ONE turn's :class:`ToolTurnBudget` to each tool call."""

    def __init__(self, budget: ToolTurnBudget | None = None) -> None:
        self.budget = budget if budget is not None else ToolTurnBudget()

    async def process(self, context: Any, call_next: Any) -> None:
        function = getattr(context, "function", None)
        if function is None or not _budgeted(function):
            await call_next()
            return
        from acb_skills.tool_guard import tool_name

        name = tool_name(function)
        stop = self.budget.stop_for(name)
        if stop is not None:
            context.result = stop
            _log.info("tool_turn_budget.stopped", tool=name)
            return
        await call_next()
        try:
            counted = self.budget.record(
                name, getattr(context, "arguments", None),
                _answer_text(getattr(context, "result", None)),
            )
            if counted:
                _log.info("tool_turn_budget.counted", tool=name)
        except Exception as exc:  # a count fault never costs the call
            _log.warning("tool_turn_budget.record_failed", error=type(exc).__name__)


class ToolTurnBudgetProvider(ContextProvider):
    """Adds :class:`ToolTurnBudgetMiddleware` to ONE turn of a native MAF agent.

    ``_tool_injection._apply_tool_turn_budget`` attaches it. It adds no tool
    and no instruction. Each ``before_run`` makes a new budget, so the count
    starts at zero on each turn.
    """

    def __init__(self) -> None:
        super().__init__(SOURCE_ID)

    async def before_run(
        self, *, agent: Any, session: Any, context: Any, state: dict[str, Any],
    ) -> None:
        context.extend_middleware(SOURCE_ID, [ToolTurnBudgetMiddleware(ToolTurnBudget())])


__all__ = [
    "SOURCE_ID",
    "ToolTurnBudgetMiddleware",
    "ToolTurnBudgetProvider",
    "tool_turn_budget_on",
]
