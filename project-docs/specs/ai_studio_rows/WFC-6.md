# WFC-6

Tier: LOW

Gate: AGENT-SAFE. After WFC-2

## What

**The clean-up of the old names (task to file).** Rung 3. Remove the `STATIC_FALLBACK` of `/api/agent/list` (`route.ts`: sales, triage, delivery, reconciler, strategy). Remove the six dead CommandCenter entries of `agent_registry.json` (five tests read the file, so they change too). Fix the stale texts in `routes/agent.py` and the eval fixtures. Remove the drift lines in the specs

## Done when

`rg` finds none of the old names in a runtime path. The five tests and the readers of `/api/agent/list` pass
