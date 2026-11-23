import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentMapView, SubAgentToggle } from "./AgentMapView";
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
  it("shows the machine and the model on the outline, and Not reported for none", () => {
    const placed = parseAgentMap({
      sessions: [
        {
          id: "s1", person: "ana@example.com", name: "box-loop", client: "Claude Code", state: "working",
          host: "metorite-home", host_kind: "server", host_source: "checked", model: "Sonnet 5.5",
        },
        { id: "s2", person: "ana@example.com", name: "bare", client: "Claude Code", state: "working" },
      ],
      runs: [],
      lines: [],
      count: 0,
      truncated: false,
      viewer: "admin",
    });
    const out = view({ read: { kind: "ready", map: placed! } });
    expect(out).toContain("metorite-home");
    expect(out).toContain("Sonnet 5.5");
    expect(out).toContain('data-map-source="checked"');
    expect(out).toContain("Not reported");
  });
  it("shows the Group by machine switch beside Show quiet runs on the canvas, and not on a phone", () => {
    expect(view({ phone: false })).toContain("Group by machine");
    expect(view({ phone: false })).toContain("data-agent-map-group");
    expect(view({ phone: true })).not.toContain("Group by machine");
  });
});

describe("the sub-agents of a session (SW-110)", () => {
  const kids = parseAgentMap({
    sessions: [
      { id: "s1", person: "ana@example.com", name: "Fix login", client: "Claude Code", state: "working", model: "Opus 5.5" },
    ],
    runs: [
      { run_id: "r1", session_id: "s1", task_id: "t1", task_number: 7, title: "Fix it", state: "working" },
      { run_id: "r2", session_id: "s1", task_id: "t2", task_number: 8, title: "Test it", state: "working" },
    ],
    children: [
      { child_id: "c1", run_id: "r1", session_id: "s1", name: "implementer", state: "working", model: "Haiku 5", age: 120 },
      { child_id: "c2", run_id: "r1", session_id: "s1", name: "verifier", state: "working", model: null, age: null },
    ],
    lines: [],
    count: 2,
    truncated: false,
    viewer: "admin",
  });
  const render = (phone: boolean, map = kids!) =>
    renderToStaticMarkup(
      createElement(AgentMapView, {
        read: { kind: "ready", map },
        showQuiet: false,
        onShowQuiet: () => {},
        phone,
        nowMs: Date.now(),
      }),
    );
  const toggle = (expanded: boolean, count = 2) =>
    renderToStaticMarkup(createElement(SubAgentToggle, { count, expanded, onToggle: () => {} }));

  it("the control is a real button with its count, its act and aria-expanded", () => {
    const closed = toggle(false);
    expect(closed).toMatch(/^<button /);
    expect(closed).toContain('type="button"');
    expect(closed).toContain('aria-expanded="false"');
    expect(closed).toContain("Show 2 sub-agents");
    expect(closed).toContain("pointer-events-auto");
    const opened = toggle(true, 1);
    expect(opened).toContain('aria-expanded="true"');
    expect(opened).toContain("Hide 1 sub-agent");
  });

  it("the outline of a phone lists each sub-agent under its task, with no model text for none", () => {
    const out = render(true);
    expect(out.match(/data-outline-child=""/g)).toHaveLength(2);
    expect(out).toContain("implementer");
    expect(out).toContain("Haiku 5");
    expect(out).toContain("Started 2m ago");
    // The verifier has no model: its row holds no model text, and never the model of its session.
    const verifier = out.slice(out.indexOf("verifier"));
    expect(verifier.slice(0, verifier.indexOf("</li>"))).not.toContain("Opus 5.5");
    expect(out).not.toContain("data-map-children-toggle");
  });

  it("a map with no sub-agent draws no sub-agent row and no control", () => {
    const out = render(true, map!);
    expect(out).not.toContain("data-outline-child");
    expect(out).not.toContain("sub-agent");
  });
});

describe("AgentsCanvas (SW-112)", () => {
  it("says no agent works on the project when the map has no session", async () => {
    const { AgentsCanvas } = await import("./AgentsCanvas");
    const empty = parseAgentMap({ sessions: [], runs: [], lines: [], count: 0, truncated: false, viewer: "admin" });
    const read: AgentMapRead = empty ? { kind: "ready", map: empty } : { kind: "loading" };
    const html = renderToStaticMarkup(
      createElement(AgentsCanvas, { read, showQuiet: false, onShowQuiet: () => {} }),
    );
    expect(empty).not.toBeNull();
    expect(html).toContain("No agent works on this project yet.");
  });
});
