# WFC-2

Tier: STD

Gate: AGENT-SAFE. After D223 is confirmed

## What

**One AI step in the palette (task 3256).** Rung 1. The palette, the catalog, the search and the Copilot offer one AI step, "Ask Mira" (the `orchestrator` agent, relabelled), and no specialist. `known_agent_names()` and `_AGENT_REGISTRY` do not change, so every old graph still validates, publishes and runs (R6 expand). The inspector shows a legacy node read only with an upgrade note, and it drops the free-text "Model tier" field (D90). The palette hides an integration row that has no action (owner Q7). **Files:** `routes/workflows/catalog.py`, `routes/workflows/search.py`, `routes/workflows/copilot.py`, `NodePalette.tsx`, `NodeInspector.tsx`, `types.ts`

## Done when

`tests/unit/test_workflows_catalog_offer.py` (new): the catalog agents and the `agent` entries hold only Ask Mira. The Copilot offers no legacy name, refuses a NEW legacy node and keeps an old one. `known_agent_names()` still holds each `_AGENT_REGISTRY` name. No CommandCenter name of `agent_registry.json` appears. `NodePalette.test.tsx` (new, vitest). Update `test_studio_workflow_tools.py` (node types), `test_workflows_author_gate.py` (`EXPECTED_ROUTES`), `test_workflows_copilot.py` (the `{"triage"}` stub) and the `lead_triage_*.json` fixtures. The owner previews it with four captures (D145)
