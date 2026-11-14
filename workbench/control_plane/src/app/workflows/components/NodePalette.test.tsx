/**
 * WFC-2 fence (task 3256, D223 proposed): the palette offers ONE AI step,
 * Ask Mira, and no specialist agent. A dropped Ask Mira node takes the label
 * "Ask Mira", never the runtime name `orchestrator`.
 *
 * The gateway decides the offer (`catalog._agent_entries`, fenced by
 * `tests/unit/test_workflows_catalog_offer.py`). This file holds the palette
 * to the served shape: it draws the label, it keeps no agent list of its own,
 * and its drops carry the runtime name in the config only.
 *
 * Mutation 1: draw `a.name` as the title. "shows Ask Mira" turns red.
 * Mutation 2: set the drop label to the runtime name. "a dropped node" turns red.
 * Mutation 3: put "crm-assistant" back in the source. "keeps no agent list" turns red.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import NodePalette, { dropForAgent, dropForResult } from "./NodePalette";
import type { Catalog, CatalogAgent, SearchResult } from "../lib/types";

const ASK_MIRA: CatalogAgent = {
  name: "orchestrator",
  label: "Ask Mira",
  description: "Give Mira, the Metorite assistant, a message about the data of this run.",
  tags: [],
  integrations: [],
};

/** The shape that the gateway serves since WFC-2. */
const catalog: Catalog = {
  node_types: [],
  condition_ops: ["equals"],
  agents: [ASK_MIRA],
  integrations: [],
  tools: [
    {
      action: "http.request",
      label: "HTTP request",
      description: "Call an external HTTP API.",
      integration: null,
      args_schema: {},
      args: [],
      read_only: false,
      destructive: false,
      open_world: true,
    },
  ],
  modules: [],
};

/** Each specialist of the registry, and the dead CommandCenter names. */
const RETIRED = [
  "task-manager", "apis-config", "email-assistant", "app-builder",
  "whatsapp-assistant", "crm-assistant", "projects-assistant",
  "sales", "delivery", "triage", "reconciler", "billing", "strategy",
];

const draw = () =>
  renderToStaticMarkup(createElement(NodePalette, { catalog, onAdd: () => {} }));

describe("NodePalette", () => {
  it("shows Ask Mira under the AI step, and no runtime name", () => {
    const html = draw();
    expect(html).toContain("Ask Mira");
    expect(html).toContain("AI step");
    expect(html).not.toContain(">Agents<");
    expect(html).not.toContain("orchestrator");
  });

  it("shows no specialist agent", () => {
    const html = draw();
    for (const name of RETIRED) expect(html, name).not.toContain(name);
  });

  it("names the search in plain words", () => {
    const html = draw();
    expect(html).toContain('placeholder="Search steps"');
    expect(html).not.toContain("Search agents");
  });

  it("labels a dropped node Ask Mira, and runs the offered agent", () => {
    expect(dropForAgent(ASK_MIRA)).toEqual({
      nodeType: "agent",
      label: "Ask Mira",
      config: { agent: "orchestrator", message: "" },
    });
  });

  it("labels a node dropped from the search Ask Mira", () => {
    const found: SearchResult = {
      kind: "agent", key: "orchestrator", label: "Ask Mira",
      description: ASK_MIRA.description, category: "agent", score: 6,
    };
    expect(dropForResult(found, catalog)).toEqual({
      nodeType: "agent",
      label: "Ask Mira",
      config: { agent: "orchestrator", message: "" },
    });
  });
});

describe("the source", () => {
  const src = readFileSync(new URL("./NodePalette.tsx", import.meta.url), "utf8");
  it("keeps no agent list of its own", () => {
    for (const name of RETIRED) expect(src, name).not.toContain(`"${name}"`);
    expect(src).not.toContain('"orchestrator"');
  });
});
