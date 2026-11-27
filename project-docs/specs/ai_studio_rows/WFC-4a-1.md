# WFC-4a-1

Tier: STD

Gate: AGENT-SAFE. D145 owner preview

## What

🔨 **BUILT (2026-11-14, task 3287), owner preview pending.** **The editor makes a human step.** Rung 3. The opus audit of 2026-11-14 split WFC-4a into three rows. The palette has a "Human step" item in Logic. The search and the Copilot shortlist offer it through its `NODE_TYPE_META` entry. The inspector shows a form with three fields. The person picker reads the active members of the org (`GET /chat/directory`, through `fetchDirectory`). The step needs a title, and the description is optional. The form writes `assignee`, `title` and `description`, and no other key. It refuses `{{` in each field with plain words, and it shows no `{{ref}}` hint. The editor draws each type of `graph.NODE_TYPES`: `types.ts`, `nodeVisuals.ts`, `categoryForType` and the card summary have each type. A `pm_task`, `pm_lifecycle` or `studio_write` node shows a read-only note. `catalog.UNOFFERED_NODE_TYPES` keeps those three out of the search and the shortlist, and their data stays. The words of the two task types now say that they cannot run yet. The Copilot prompt names `human_step` with its three keys, and it forbids `pm_task` and `pm_lifecycle`. **Non-goals:** no change to the engine, a run door, a route, a flag, a migration or a permission. No palette item for `pm_task`, `pm_lifecycle` or `studio_write`. The step stays dark until the owner lists the org in `WORKFLOW_UNATTENDED_ORGS`. **Known limit:** the directory gives at most 100 people, and the picker says so

## Done when

The palette offers the human step and none of the other three. The form writes only the three keys, and never a value with `{{`. Each `NODE_TYPES` name has an icon and a label. A graph as the form writes it passes `validate_graph`, and a `{{` in any field fails. Fences: `tests/unit/test_workflows_node_offer.py`, and in `src/app/workflows/` the files `lib/nodeTypes.test.ts`, `lib/humanStep.test.ts`, `components/NodePalette.test.tsx` and `components/NodeInspector.test.tsx`
