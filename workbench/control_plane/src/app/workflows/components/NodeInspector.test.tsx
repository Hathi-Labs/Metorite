/**
 * WFC-2 fence (task 3256, D223 proposed): the inspector of the AI step.
 *
 * An Ask Mira node shows the step and its message, with nothing to choose. A
 * node that names a retired agent (a graph of before) shows that name read
 * only, with the upgrade note, and no select. No node shows the free-text
 * "Model tier" field, because the platform picks the model (D90).
 *
 * Mutation 1: draw a `<select>` of the agents again. "no select" turns red.
 * Mutation 2: put the "Model tier" field back. "no model field" turns red.
 * Mutation 3: drop the note of a retired node. "the upgrade note" turns red.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NodeInspector, { retiredAgentNote } from "./NodeInspector";
import type { Catalog, WorkflowGraphNode } from "../lib/types";

const catalog: Catalog = {
  node_types: [],
  condition_ops: ["equals"],
  agents: [
    {
      name: "orchestrator",
      label: "Ask Mira",
      description: "Give Mira, the Metorite assistant, a message about the data of this run.",
      tags: [],
      integrations: [],
    },
  ],
  integrations: [],
  tools: [],
  modules: [],
};

const agentNode = (config: Record<string, unknown>, label = "Step"): WorkflowGraphNode => ({
  id: "step_1",
  type: "agent",
  position: { x: 0, y: 0 },
  data: { label, config },
});

const draw = (node: WorkflowGraphNode) =>
  renderToStaticMarkup(
    createElement(NodeInspector, {
      node,
      catalog,
      upstreamIds: [],
      issues: [],
      triggerKinds: [],
      onConfig: () => {},
      onLabel: () => {},
      onDelete: () => {},
    }),
  );

describe("NodeInspector, the AI step", () => {
  it("shows Ask Mira and its message, with nothing to choose", () => {
    const html = draw(agentNode({ agent: "orchestrator", message: "Sort {{trigger.body}}" }));
    expect(html).toContain("Ask Mira");
    expect(html).toContain("<textarea");
    expect(html).not.toContain("<select");
    expect(html).not.toContain(retiredAgentNote("Ask Mira"));
  });

  it("shows a retired agent read only, with the upgrade note and no select", () => {
    const html = draw(agentNode({ agent: "crm-assistant", message: "Score this lead" }));
    expect(html).toContain("crm-assistant");
    expect(html).toContain(
      "This step uses an assistant that Metorite retired. Replace it with Ask Mira.",
    );
    expect(html).toContain("Score this lead");
    expect(html).not.toContain("<select");
    expect(html).not.toContain("<textarea");
  });

  it("shows no model field, even when an old node stores a model", () => {
    for (const agent of ["orchestrator", "crm-assistant"]) {
      const html = draw(agentNode({ agent, message: "", model: "tier-balanced" }));
      expect(html, agent).not.toContain("Model tier");
      expect(html, agent).not.toContain("tier-balanced");
    }
  });

  it("offers Ask Mira to a node with no agent", () => {
    const html = draw(agentNode({ agent: "", message: "" }));
    expect(html).toContain("Use Ask Mira");
    expect(html).not.toContain(retiredAgentNote("Ask Mira"));
  });
});
