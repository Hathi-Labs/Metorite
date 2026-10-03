"""The per-run context of a native MAF run (WS-43t1).

Spec: ``project-docs/specs/maf_coding_engine.md`` §15.9.5. Behind
``MAF_NATIVE_SESSIONS`` (default OFF), so nothing imports this module while the
flag is off.

Two rules live here:

1. **The per-turn context never becomes an input message.** ``system_context``,
   ``memory_context`` and the persona (which rides inside ``system_context``)
   reach the model through :class:`RunContextProvider`, as instructions. MAF's
   ``InMemoryHistoryProvider`` stores every input message in
   ``session.state``, and WS-43t2 stores that session. Instructions are never
   stored by any history provider, so the member's memory never lands in a
   stored session.
2. **The provider is attached to ONE run, never to a shared agent object.**
   ``Agent.run`` in MAF 1.19 has no per-run ``context_providers`` argument: the
   framework reads ``self.context_providers``. One agent object can serve two
   runs at once, so appending to its list would hand one member's memory to
   the other run. :func:`agent_for_run` returns a shallow copy of the agent
   that carries its own provider list (``SerializationMixin.__copy__``), and
   the shared object never changes.

Fence: ``tests/unit/test_native_session_persistence.py`` (WS43-F20).
"""
from __future__ import annotations

import copy
from typing import Any

from agent_framework import AgentSession, ContextProvider, SessionContext, SupportsAgentRun

#: The ``source_id`` of the per-run provider. MAF keys provider state and
#: message attribution on it.
RUN_CONTEXT_SOURCE_ID = "metorite-run-context"


class RunContextProvider(ContextProvider):
    """Add the run's own context to the instructions of ONE ``agent.run``.

    *text* is the fitted block that the string prompt would carry before its
    history: the connected and missing integrations, ``memory_context`` and
    ``system_context``. The caller puts the prompt-cache sentinel at its head,
    so the agent's stable instructions stay a byte-stable prefix.
    """

    def __init__(self, text: str) -> None:
        super().__init__(RUN_CONTEXT_SOURCE_ID)
        self.text = text

    async def before_run(
        self,
        *,
        agent: SupportsAgentRun,
        session: AgentSession,
        context: SessionContext,
        state: dict[str, Any],
    ) -> None:
        """Append the run's context to the instructions, never to the messages."""
        if self.text:
            context.extend_instructions(self.source_id, self.text)


def agent_for_run(agent: Any, provider: ContextProvider | None) -> Any:
    """Return the object to call ``run`` on for this run.

    No provider: *agent* itself, so a flag-off run is unchanged. A provider:
    a shallow copy of *agent* whose ``context_providers`` is a NEW list, the
    agent's own providers plus *provider*. The copy shares the client, the
    options and the tools, so it sends what the agent would send. The list
    of the shared agent is never mutated.
    """
    if provider is None:
        return agent
    view = copy.copy(agent)
    view.context_providers = [
        *list(getattr(agent, "context_providers", None) or []),
        provider,
    ]
    return view
