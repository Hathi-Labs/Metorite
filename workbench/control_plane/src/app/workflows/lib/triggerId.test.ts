/**
 * Task 3382 fence (RUNG 0): the id of the first node of a workflow.
 *
 * The engine keeps `trigger` and `vars` as the roots of its state
 * (`templating.RESERVED_ROOTS`), and `graph._parse` refuses a node with one of
 * those ids. The editor named the first node of a new workflow `trigger`, so
 * no new workflow passed Validate, Test or Publish. A draft that a member
 * saved before the fix still holds that id, and the editor renames it on load.
 * `tests/unit/test_workflows_editor_first_node.py` holds `FIRST_NODE_ID`
 * against the real `RESERVED_ROOTS` and runs `validate_graph` on the shape.
 *
 * Mutations (each turned this file red):
 * - Set `FIRST_NODE_ID` back to `"trigger"`: "a new workflow" turns red.
 * - Drop the edge rename of `normalizeTriggerId`: the rename cases turn red.
 */
import { describe, expect, it } from "vitest";

import { FIRST_NODE_ID, newWorkflowGraph, normalizeTriggerId } from "./triggerId";
import type { WorkflowGraph, WorkflowGraphNode } from "./types";

/** The roots of the engine state. A node id here fails `validate_graph`. */
const RESERVED = ["trigger", "vars"];

const node = (
  id: string,
  type: WorkflowGraphNode["type"],
  config: Record<string, unknown> = {},
): WorkflowGraphNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { label: id, config },
});

describe("a new workflow", () => {
  it("starts from one trigger node whose id is not a reserved root", () => {
    const graph = newWorkflowGraph();
    expect(graph.nodes).toHaveLength(1);
    expect(graph.edges).toEqual([]);
    const [first] = graph.nodes;
    expect(first.type).toBe("trigger");
    expect(first.id).toBe(FIRST_NODE_ID);
    expect(RESERVED).not.toContain(first.id);
  });

  it("keeps the id in the body that a save sends", () => {
    // The page sends the graph as JSON. The id must come through unchanged.
    const body = JSON.parse(JSON.stringify({ graph: newWorkflowGraph() })) as {
      graph: WorkflowGraph;
    };
    for (const n of body.graph.nodes) expect(RESERVED).not.toContain(n.id);
  });

  it("is a fresh graph each time", () => {
    const a = newWorkflowGraph();
    a.nodes[0].data.config.kind = "schedule";
    expect(newWorkflowGraph().nodes[0].data.config).toEqual({ kind: "manual" });
  });
});

describe("normalizeTriggerId", () => {
  const stored = (): WorkflowGraph => ({
    nodes: [
      node("trigger", "trigger", { kind: "manual" }),
      node("agent_1", "agent", { agent: "a", message: "{{trigger.body}}" }),
      node("output_1", "output", { value: "{{agent_1.text}}" }),
    ],
    edges: [
      { id: "e_trigger_out_agent_1", source: "trigger", target: "agent_1" },
      { id: "e_agent_1_out_output_1", source: "agent_1", target: "output_1" },
    ],
  });

  it("renames the node with the id trigger, and each edge that names it", () => {
    const out = normalizeTriggerId(stored());
    expect(out.nodes.map((n) => n.id)).toEqual([FIRST_NODE_ID, "agent_1", "output_1"]);
    expect(out.nodes[0].type).toBe("trigger");
    expect(out.edges[0]).toMatchObject({ source: FIRST_NODE_ID, target: "agent_1" });
    for (const n of out.nodes) expect(RESERVED).not.toContain(n.id);
    for (const e of out.edges) {
      expect(RESERVED).not.toContain(e.source);
      expect(RESERVED).not.toContain(e.target);
    }
  });

  it("touches no other node, and no reference to the trigger root", () => {
    const before = stored();
    const out = normalizeTriggerId(before);
    expect(out.nodes[1]).toBe(before.nodes[1]);
    expect(out.nodes[2]).toBe(before.nodes[2]);
    expect(out.edges[1]).toBe(before.edges[1]);
    // `{{trigger.body}}` reads the payload root, never the node, so it stays.
    expect(out.nodes[1].data.config.message).toBe("{{trigger.body}}");
    // The input is not changed in place.
    expect(before.nodes[0].id).toBe("trigger");
    expect(before.edges[0].source).toBe("trigger");
  });

  it("renames an edge INTO the node too", () => {
    const g = stored();
    g.edges.push({ id: "e_back", source: "output_1", target: "trigger" });
    const out = normalizeTriggerId(g);
    expect(out.edges[2]).toMatchObject({ source: "output_1", target: FIRST_NODE_ID });
  });

  it("is idempotent, and gives back a graph with no such node as it is", () => {
    const once = normalizeTriggerId(stored());
    expect(normalizeTriggerId(once)).toBe(once);
    const clean = newWorkflowGraph();
    expect(normalizeTriggerId(clean)).toBe(clean);
  });

  it("picks a free id when the graph holds both ids", () => {
    const g = stored();
    g.nodes.push(node(FIRST_NODE_ID, "set", { assignments: {} }));
    const out = normalizeTriggerId(g);
    const renamed = `${FIRST_NODE_ID}_2`;
    expect(out.nodes.map((n) => n.id)).toEqual([
      renamed, "agent_1", "output_1", FIRST_NODE_ID,
    ]);
    expect(out.nodes[3]).toBe(g.nodes[3]);
    expect(out.edges[0]).toMatchObject({ source: renamed, target: "agent_1" });
    expect(new Set(out.nodes.map((n) => n.id)).size).toBe(out.nodes.length);

    g.nodes.push(node(renamed, "wait", { seconds: 60 }));
    const next = normalizeTriggerId(g);
    expect(next.nodes[0].id).toBe(`${FIRST_NODE_ID}_3`);
  });

  it("gives back a graph with no node list as it is", () => {
    const bad = { nodes: null, edges: [] } as unknown as WorkflowGraph;
    expect(normalizeTriggerId(bad)).toBe(bad);
    expect(normalizeTriggerId(undefined as unknown as WorkflowGraph)).toBeUndefined();
  });
});
