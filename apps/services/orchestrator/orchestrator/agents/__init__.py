"""Specialist sub-agents: Sales, Delivery, HR, Strategy, Triage, Annealer."""
# Re-export the main orchestrator builder from agents.py so callers that do
# `from orchestrator.agents import build_orchestrator_agent` still work even
# though this package shadows the same-named module file.
import importlib
import importlib.util
from pathlib import Path as _Path

# Load orchestrator/agents.py as a distinct module (bypasses this package).
_agents_file = _Path(__file__).parent.parent / "agents.py"
_spec = importlib.util.spec_from_file_location("orchestrator._agents_module", _agents_file)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

build_orchestrator_agent = _mod.build_orchestrator_agent
build_agents = _mod.build_agents
retrieve_entity_context = _mod.retrieve_entity_context
retrieve_sales_context = _mod.retrieve_sales_context
spawn_copilot_agent = _mod.spawn_copilot_agent
delegate_to_agent = _mod.delegate_to_agent
enrich_instructions_with_memory = _mod.enrich_instructions_with_memory
search_timeline = _mod.search_timeline
# WS-62 CF-3: the orchestrator's page tool (tests/unit/test_show_in_app.py).
show_in_app = _mod.show_in_app
# Private by convention, but it is the ONE gateway-/v1/ client factory — the
# declarative builder (orchestrator.declarative) is a second legitimate consumer
# inside this package, and re-exporting beats duplicating the token-precedence
# and identity-header logic it encapsulates.
_make_openai_client = _mod._make_openai_client

__all__ = [
    "build_agents",
    "build_orchestrator_agent",
    "delegate_to_agent",
    "enrich_instructions_with_memory",
    "retrieve_entity_context",
    "retrieve_sales_context",
    "search_timeline",
    "show_in_app",
    "spawn_copilot_agent",
]
