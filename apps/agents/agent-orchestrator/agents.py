"""agent-orchestrator — wraps the orchestrator as a dynamically loadable agent.

The orchestrator is the central routing agent for Metorite.  It:
- Routes user requests to the appropriate specialist agent via MAF tools
- Retrieves company data (ClickUp, Zoho, Odoo) through retrieval tools
- Spawns Copilot SDK agents for creation/improvement tasks
- Maintains cross-session memory via Mem0 + Graphiti

This thin wrapper lets the orchestrator go through the same run_agent_stream()
path that all other named agents use, eliminating the separate /copilot/chat
endpoint path in main.py and the isOrchestrator branching in route.ts.

Exports:
    build_agents() -> list[Agent]
"""
from __future__ import annotations

from orchestrator.agents import build_orchestrator_agent


def build_agents():
    """Return the orchestrator MAF Agent, built without Redis history
    (the executor's run_agent_stream handles session continuity separately)."""
    return [build_orchestrator_agent(with_history=False)]
