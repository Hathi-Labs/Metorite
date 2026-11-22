import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentMapView } from "./AgentMapView";
import { AgentsAtWorkView } from "./AgentsAtWork";
import { type AgentMapRead, parseAgentMap } from "../lib/agentMap";
import type { AgentsAtWorkRead } from "../lib/agentsAtWork";

const ready: AgentsAtWorkRead = {
  kind: "ready",
  overview: {
    agents: [
      {
        key: "k",
        name: "Coder",
        job: "",
        source: "org",
        label: null,
        state: "idle",
        runs: 0,
        last_run: null,
        last_error: null,
      },
    ],
    count: 1,
    truncated: false,
  },
};
const map = parseAgentMap({
  sessions: [
    { id: "s1", person: "ana@example.com", name: "Fix login", client: "Claude Code", state: "working" },
  ],
  runs: [{ run_id: "r1", session_id: "s1", task_id: "t1", task_number: 7, title: "Fix it", state: "working" }],
  lines: [],
  count: 1,
  truncated: false,
  viewer: "admin",
});
const mapRead: AgentMapRead = { kind: "ready", map: map! };
const html = (props: Record<string, unknown>) =>
  renderToStaticMarkup(createElement(AgentsAtWorkView, { read: ready, ...props }));

describe("the List | Map switch", () => {
  it("shows the switch, and the list, when the map read answers", () => {
    const out = html({ mapRead });
    expect(out).toContain("Agents view");
    expect(out).toContain(">Map<");
    expect(out).toContain("data-agent-row");
  });
  it("hides the switch when the map read is dark, and the list still draws", () => {
    const out = html({ mapRead: { kind: "dark" } });
    expect(out).not.toContain("Agents view");
    expect(out).toContain("data-agent-row");
  });
  it("draws nothing while the overview is dark", () => {
    expect(html({ read: { kind: "dark" }, mapRead })).toBe("");
  });
  it("shows the map and not the list in Map mode", () => {
    const out = html({ mapRead, initialMode: "map" });
    expect(out).toContain("data-agent-map");
    expect(out).not.toContain("data-agent-row");
  });
});

describe("AgentMapView", () => {
  const view = (over: Record<string, unknown>) =>
    renderToStaticMarkup(
      createElement(AgentMapView, { read: mapRead, showQuiet: false, onShowQuiet: () => {}, phone: true, ...over }),
    );
  it("draws a nested outline on a phone", () => {
    const out = view({});
    expect(out).toContain("data-agent-map-outline");
    expect(out).toContain("data-outline-session");
    expect(out).toContain("Fix it");
    expect(out).not.toContain("data-agent-map-canvas");
  });
  it("names the quiet switch and the error copy", () => {
    expect(view({})).toContain("Show quiet runs");
    expect(view({ read: { kind: "error" } })).toContain("The map did not load");
  });
});
