# E7f

Tier: STD

Gate: AGENT-SAFE. RUNG 0 fix task 3382: the editor first node id

## What

🔨 **BUILT (2026-11-15, task 3382).** **The workflow editor names the first node of a new workflow `start`.** Before this fix, the editor gave that node the id `trigger`. The engine keeps `trigger` and `vars` as the roots of its state (`templating.RESERVED_ROOTS`). `graph._parse` refuses a node with one of those ids, and `validate_graph` then returns `reserved_id` only. So no workflow from New workflow passed Validate, Test or Publish.

**As built:** `app/workflows/lib/triggerId.ts` holds `FIRST_NODE_ID`, `newWorkflowGraph()` and `normalizeTriggerId()`. The editor takes its first node from `newWorkflowGraph()`. `flowFromGraph` calls `normalizeTriggerId()` on a load, on a Copilot apply and on an undo. That call renames a stored node `trigger` to `start`, or to `start_2` and on when another node holds that id. It renames each edge that names the node, and it changes no other node.

A `{{trigger.*}}` reference stays, because it reads the payload root and never the node. "Available data" leaves out the trigger node by its type, not by its id. No old run names the node `trigger`, because a Test run and a publish compile the graph first. No route, flag or migration changes.

**Known edge (found 2026-11-15, not in this row):** the PUT answer holds no `versions`, and the editor reads `detail.versions.length` after each Save. So on `main` each Save shows "This page did not load".

## Done when

The PUT body of a new workflow holds the id `start`. With one Output step, `validate_graph` returns no issue for that body. A stored draft with the id `trigger` loads as `start`, and its next Save holds `start`. Fences: `src/app/workflows/lib/triggerId.test.ts` and `tests/unit/test_workflows_editor_first_node.py`
