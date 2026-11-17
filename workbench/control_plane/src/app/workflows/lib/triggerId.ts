/**
 * The id of the trigger node of a workflow (task 3382, RUNG 0).
 *
 * The engine keeps `trigger` and `vars` as the roots of its state
 * (`templating.RESERVED_ROOTS`): `{{trigger.body}}` reads the payload, never a
 * node. `graph._parse` refuses a node with one of those ids. The editor named
 * the first node of a new workflow `trigger`, so no new workflow passed
 * Validate, Test or Publish. The first node is now `FIRST_NODE_ID`, and a
 * draft saved before the fix is renamed when the editor loads it.
 *
 * A draft with the old id never ran: a Test run and a publish compile the graph
 * first, and the compile refuses the id. So no run history names the node
 * `trigger`. Fences: `triggerId.test.ts` and
 * `tests/unit/test_workflows_editor_first_node.py`.
 */

import type { WorkflowGraph } from "./types";

/** The id of the first node of a new workflow. Not a root of the engine state. */
export const FIRST_NODE_ID = "start";

/** The node id that the editor gave before the fix. The engine refuses it. */
const OLD_TRIGGER_ID = "trigger";

/** The graph of a new workflow: one manual trigger (RFC §5.2). */
export function newWorkflowGraph(): WorkflowGraph {
  return {
    nodes: [
      {
        id: FIRST_NODE_ID,
        type: "trigger",
        position: { x: 120, y: 60 },
        data: { label: "Trigger", config: { kind: "manual" } },
      },
    ],
    edges: [],
  };
}

/**
 * Rename a node with the id `trigger` to a free id, and each edge that names it.
 *
 * The new id is `FIRST_NODE_ID`, or `start_2`, `start_3` and on when that id is
 * taken. No other node changes. A `{{trigger.*}}` reference stays, because it
 * reads the payload root, and the trigger node gives the same payload. A graph
 * with no such node comes back as it is, so a second call changes nothing.
 */
export function normalizeTriggerId(graph: WorkflowGraph): WorkflowGraph {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes : null;
  if (!nodes || !nodes.some((n) => n?.id === OLD_TRIGGER_ID)) return graph;
  const taken = new Set(nodes.map((n) => n?.id));
  let next = FIRST_NODE_ID;
  for (let i = 2; taken.has(next); i++) next = `${FIRST_NODE_ID}_${i}`;
  const rename = (id: string) => (id === OLD_TRIGGER_ID ? next : id);
  const edges = Array.isArray(graph.edges) ? graph.edges : [];
  return {
    ...graph,
    nodes: nodes.map((n) => (n?.id === OLD_TRIGGER_ID ? { ...n, id: next } : n)),
    edges: edges.map((e) =>
      e && (e.source === OLD_TRIGGER_ID || e.target === OLD_TRIGGER_ID)
        ? { ...e, source: rename(e.source), target: rename(e.target) }
        : e,
    ),
  };
}
