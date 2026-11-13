/**
 * STU-D8 fence: the pure half of the Process flow block. Each test names the mutation it catches.
 */
import { describe, expect, it } from "vitest";

import { FLOW_STATES, accentForHue, flowStateChip } from "@/lib/statusAccent";
import {
  applyRunEvent, flowGraph, flowCanRetry, flowReadError, flowStateOf, isFlowNode, isLive, nodeStatus, paintFlow,
  parseFlowSource, runState, runStatusOf, runWhen, stateCounts, statesFromRun,
} from "./flowModel";

const GRAPH = {
  nodes: [
    { id: "t", type: "trigger", position: { x: 0, y: 0 }, data: { label: "Start", config: { token: "SECRET-CONFIG" } } },
    { id: "a", type: "agent", position: { x: 0, y: 120 }, data: { label: "Draft", config: { prompt: "SECRET-PROMPT" } } },
    { id: "c", type: "condition", position: { x: 0, y: 240 }, data: { label: "Ok?", config: {} } },
    { id: "o", type: "output", position: { x: 0, y: 360 }, data: { label: "End", config: {} } },
  ],
  edges: [
    { id: "e1", source: "t", target: "a" },
    { id: "e2", source: "a", target: "c" },
    { id: "e3", source: "c", target: "o", sourceHandle: "true" },
    { id: "e4", source: "c", target: "ghost" },
  ],
};

describe("parseFlowSource", () => {
  it("reads the workflow and the run", () => {
    expect(parseFlowSource({ action: "workflow", args: { id: " w1 ", run: "r9" } })).toEqual({ workflow: "w1", run: "r9" });
    expect(parseFlowSource({ action: "workflow", args: { id: "w1" } })).toEqual({ workflow: "w1" });
  });
  it("refuses a source with no workflow id (mutation: accept an empty id)", () => {
    expect(parseFlowSource({ action: "workflow", args: {} })).toBeNull();
    expect(parseFlowSource({ action: "workflow" })).toBeNull();
    expect(parseFlowSource("w1")).toBeNull();
  });
  it("marks only the flow template", () => {
    const source = { action: "workflow", args: { id: "w1" } };
    expect(isFlowNode("template", { name: "flow", source })).toBe(true);
    expect(isFlowNode("template", { name: "chart", source })).toBe(false);
    expect(isFlowNode("template", { name: "flow" })).toBe(false);
  });
});

describe("flowGraph", () => {
  it("keeps 4 nodes and 3 edges, and drops the edge to a missing node", () => {
    const g = flowGraph(GRAPH);
    expect(g.nodes).toHaveLength(4);
    expect(g.edges.map((e) => e.id)).toEqual(["e1", "e2", "e3"]);
  });
  it("copies the label and the type, and nothing of the config (mutation: spread node.data)", () => {
    const text = JSON.stringify(flowGraph(GRAPH));
    expect(text).not.toContain("SECRET-CONFIG");
    expect(text).not.toContain("SECRET-PROMPT");
    expect(text).not.toContain("config");
    expect(flowGraph(GRAPH).nodes[1].data).toEqual({ label: "Draft", nodeType: "agent" });
  });
  it("is read-only: no node drags, connects or selects", () => {
    for (const n of flowGraph(GRAPH).nodes) {
      expect([n.draggable, n.connectable, n.selectable]).toEqual([false, false, false]);
    }
  });
  it("marks the branch of a condition", () => {
    expect(flowGraph(GRAPH).edges[2]).toMatchObject({ sourceHandle: "true", data: { branch: "true" } });
  });
  it("reads a bad answer as an empty graph", () => {
    expect(flowGraph(null)).toEqual({ nodes: [], edges: [] });
    expect(flowGraph({ nodes: [{ id: 3 }, { type: "agent" }], edges: "x" })).toEqual({ nodes: [], edges: [] });
  });
  it("draws an unknown type as a tool step and drops a repeated id", () => {
    const g = flowGraph({ nodes: [{ id: "a", type: "weird", data: {} }, { id: "a", type: "agent" }], edges: [] });
    expect(g.nodes).toHaveLength(1);
    expect(g.nodes[0].data.nodeType).toBe("tool");
  });
});

describe("run state", () => {
  it("maps the words of the gateway to the words of the card", () => {
    expect(nodeStatus("ok")).toBe("ok");
    expect(nodeStatus("failed")).toBe("error");
    expect(nodeStatus("paused")).toBe("waiting");
    expect(nodeStatus("queued")).toBe("pending");
    expect(nodeStatus("strange")).toBeUndefined();
  });
  it("reads node_results of a recorded run", () => {
    const s = statesFromRun({ t: { status: "ok", duration_ms: 12 }, a: { status: "error" }, z: { status: "??" } });
    expect(s).toEqual({ t: { status: "ok", durationMs: 12 }, a: { status: "error" } });
  });
  it("a node frame sets one step and any other frame changes nothing", () => {
    const s0 = { t: { status: "ok" as const } };
    const s1 = applyRunEvent(s0, { event: "node", node_id: "a", status: "running" });
    expect(s1).toEqual({ t: { status: "ok" }, a: { status: "running" } });
    expect(applyRunEvent(s1, { event: "run", status: "succeeded" })).toBe(s1);
    expect(applyRunEvent(s1, { event: "node", status: "ok" })).toBe(s1);
    expect(applyRunEvent(s1, "junk")).toBe(s1);
    expect(applyRunEvent(s1, { event: "node", node_id: "a", status: "ok", duration_ms: 5 }).a).toEqual({ status: "ok", durationMs: 5 });
  });
  it("reads the run frame and tells a live run", () => {
    expect(runStatusOf({ event: "run", status: "failed" })).toBe("failed");
    expect(runStatusOf({ event: "node", status: "ok" })).toBeNull();
    expect(isLive("running")).toBe(true);
    expect(isLive("queued")).toBe(true);
    expect(isLive("succeeded")).toBe(false);
    expect(isLive("paused")).toBe(false);
  });
  it("paints each step and lights a wire from a done step to a running one", () => {
    const g = flowGraph(GRAPH);
    const p = paintFlow(g, { t: { status: "ok" }, a: { status: "running" } });
    expect(p.nodes.find((n) => n.id === "a")?.data.runStatus).toBe("running");
    expect(p.nodes.find((n) => n.id === "o")?.data.runStatus).toBeUndefined();
    expect(p.edges.find((e) => e.id === "e1")?.data.hot).toBe(true);
    expect(p.edges.find((e) => e.id === "e2")?.data.hot).toBe(false);
  });
});

describe("states and accents", () => {
  it("a step with no result is idle, a pending step is queued", () => {
    expect(flowStateOf(undefined)).toBe("idle");
    expect(flowStateOf("pending")).toBe("queued");
    expect(flowStateOf("error")).toBe("failed");
  });
  it("maps each run status to a state", () => {
    expect(["queued", "running", "succeeded", "failed", "cancelled", "paused", "x"].map(runState))
      .toEqual(["queued", "running", "ok", "failed", "skipped", "waiting", "idle"]);
  });
  it("every state draws from the one palette of statusAccent (mutation: a literal colour)", () => {
    for (const state of Object.keys(FLOW_STATES) as Array<keyof typeof FLOW_STATES>) {
      expect(flowStateChip(state)).not.toMatch(/#[0-9a-f]{3,6}|rgb\(/i);
    }
    expect(flowStateChip("ok")).toBe(accentForHue("green").chip);
    expect(flowStateChip("failed")).toBe(accentForHue("red").chip);
    expect(flowStateChip("running")).toBe(accentForHue("blue").chip);
    expect(flowStateChip("waiting")).toBe(accentForHue("amber").chip);
  });
  it("counts the steps by state, most urgent first", () => {
    const p = paintFlow(flowGraph(GRAPH), { t: { status: "ok" }, a: { status: "running" } });
    expect(stateCounts(p.nodes)).toEqual([
      { state: "running", count: 1 }, { state: "ok", count: 1 }, { state: "idle", count: 2 },
    ]);
  });
});

describe("words", () => {
  const now = Date.parse("2026-11-14T12:00:00Z");
  it("says when a run started or finished", () => {
    expect(runWhen({ status: "running", started_at: "2026-11-14T11:55:00Z" }, now)).toBe("Started 5m ago");
    expect(runWhen({ status: "succeeded", started_at: "2026-11-14T09:00:00Z", finished_at: "2026-11-14T10:00:00Z" }, now))
      .toBe("Finished 2h ago");
    expect(runWhen({ status: "running", started_at: null }, now)).toBe("");
  });
  it("words a failed read and never shows the raw fault", () => {
    expect(flowReadError(404)).toBe("This workflow no longer exists.");
    expect(flowReadError(403)).toBe("You cannot see this workflow.");
    expect(flowReadError(500)).toBe("Could not load this workflow. Try again.");
    expect(flowReadError(undefined)).toBe("Could not load this workflow. Try again.");
    expect(flowCanRetry(flowReadError(500))).toBe(true);
    expect(flowCanRetry(flowReadError(404))).toBe(false);
  });
});
