# WFC-3

Tier: HIGH

Gate: AGENT-SAFE. After WFC-0 and WFC-2. The flag flip is owner only

## What

**The `ask_mira` node (task to file).** Rung 2. A new node type with `message` and `packs[]`. The `agent` type stays as it is (expand). The node is read only by default (owner Q2), runs with no egress when nobody watches, and has a credit ceiling for each node (Q4). It has no `run_workflow` tool: `DEFAULT_PROFILE` gives that tool to every agent, and no guard against a nested run was found in `run_published_workflow`, so this slice checks it first. `packs` is a subset of `packs_on(org)`. **The technical choice:** only `_tool_injection._pack_tools` builds a pack tool, and injection never offers it, so a batch `orchestrator` run holds no pack tool today. The slice picks one: a pinned pack set for a batch `orchestrator` run, or a pack build for a worker job (`headless_turn.run_worker`). Both are HIGH and touch the WS-67 seam. A new flag, `WORKFLOW_ASK_MIRA_ORGS`, is empty by default. `TOOL_PACKS_ORGS` stays owner only

## Done when

The slice names its test for each point above. A run with no pack holds no pack tool. A run that asks for a pack the org lacks refuses. A nested `run_workflow` refuses
