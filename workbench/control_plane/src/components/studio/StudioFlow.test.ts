/**
 * STU-D8 fence: the Process flow block draws a read-only graph with the state of each step, in
 * plain words, and shows no config, no hook token and no hook URL. The renderer owns the
 * loading, error and empty states.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/studio/blocks-preview",
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));

import GenerativeUINode from "@/components/GenerativeUINode";
import { FlowGraph, FlowStatus, FlowSummary } from "@/components/studio/StudioFlow";
import { StudioDataContext } from "@/lib/studio/dataContext";
import type { StudioDataDoor } from "@/lib/studio/dataDoor";
import { applyRunEvent, flowGraph, paintFlow, statesFromRun } from "@/lib/studio/flowModel";

beforeEach(() => vi.clearAllMocks());

const GRAPH = {
  nodes: [
    { id: "n1", type: "trigger", position: { x: 0, y: 0 }, data: { label: "New order", config: { secret: "CONFIG-SECRET" } } },
    { id: "n2", type: "agent", position: { x: 0, y: 140 }, data: { label: "Check stock", config: {} } },
    { id: "n3", type: "condition", position: { x: 0, y: 280 }, data: { label: "In stock?", config: {} } },
    { id: "n4", type: "output", position: { x: 0, y: 420 }, data: { label: "Confirm", config: {} } },
  ],
  edges: [
    { id: "e1", source: "n1", target: "n2" },
    { id: "e2", source: "n2", target: "n3" },
    { id: "e3", source: "n3", target: "n4", sourceHandle: "true" },
  ],
};

const draw = (states: Parameters<typeof paintFlow>[1], graph: unknown = GRAPH) => {
  const p = paintFlow(flowGraph(graph), states);
  return renderToStaticMarkup(createElement(FlowGraph, { nodes: p.nodes, edges: p.edges }));
};

describe("FlowGraph", () => {
  it("draws every step by its label and its type, and each wire", () => {
    const html = draw({});
    for (const label of ["New order", "Check stock", "In stock?", "Confirm"]) expect(html).toContain(label);
    expect(html.match(/react-flow__node /g)?.length ?? 0).toBe(4);
  });

  it("says each step state in words", () => {
    const html = draw({ n1: { status: "ok", durationMs: 820 }, n2: { status: "running" }, n3: { status: "error" } });
    expect(html).toContain("done");
    expect(html).toContain("running");
    expect(html).toContain("failed");
    expect(html).toContain("idle");
    expect(html).toContain("820ms");
  });

  it("shows no config text and no hook text (mutation: spread node.data into the card)", () => {
    const answer = { ...GRAPH, hook_token: "HOOK-TOKEN-VALUE", hook_url: "https://x.example/hook/HOOK-TOKEN-VALUE", hook_path: "/hooks/abc" };
    const html = draw({}, answer);
    for (const bad of ["CONFIG-SECRET", "HOOK-TOKEN-VALUE", "hook", "/hooks/abc"]) expect(html).not.toContain(bad);
  });

  it("offers no insert button on a wire", () => {
    expect(draw({})).not.toContain("Insert a block here");
  });

  it("a node frame of the stream changes one step", () => {
    let states = statesFromRun({ n1: { status: "ok" } });
    expect(draw(states)).not.toContain("failed");
    states = applyRunEvent(states, { event: "node", node_id: "n2", status: "error" });
    expect(draw(states)).toContain("failed");
  });
});

describe("FlowSummary", () => {
  const nodes = paintFlow(flowGraph(GRAPH), { n1: { status: "ok" }, n2: { status: "running" } }).nodes;

  it("words the run status and the time, and counts the steps by state", () => {
    const html = renderToStaticMarkup(createElement(FlowSummary, {
      title: "Order flow",
      run: { status: "running", started_at: "2026-11-14T11:55:00Z" },
      nodes, nowMs: Date.parse("2026-11-14T12:00:00Z"),
    }));
    expect(html).toContain("Order flow");
    expect(html).toContain("Running");
    expect(html).toContain("Started 5m ago");
    expect(html).toContain("Done");
    expect(html).toContain("Not run");
  });

  it("says a workflow has not run, with no error", () => {
    const html = renderToStaticMarkup(createElement(FlowSummary, { run: null, nodes }));
    expect(html).toContain("This workflow has not run yet");
  });
});

describe("FlowStatus (the renderer owns the states)", () => {
  it("loading is a busy skeleton", () => {
    const html = renderToStaticMarkup(createElement(FlowStatus, { phase: "loading" }));
    expect(html).toContain('aria-busy="true"');
  });
  it("an error says its sentence once, with a retry", () => {
    const html = renderToStaticMarkup(createElement(FlowStatus, { phase: "error", error: "This workflow no longer exists.", onRetry: () => {} }));
    expect(html).toContain('role="alert"');
    expect(html).toContain("This workflow no longer exists.");
    expect(html).toContain("Try again");
  });
  it("empty says there are no steps", () => {
    const html = renderToStaticMarkup(createElement(FlowStatus, { phase: "empty" }));
    expect(html).toContain("This workflow has no steps yet");
  });
});

describe("the renderer", () => {
  const door = { scope: "pkg", read: vi.fn(), run: vi.fn() } as unknown as StudioDataDoor;
  const node = { type: "template", props: { name: "flow", source: { action: "workflow", args: { id: "w1" } } } };

  it("binds a flow node on a page and reads nothing through the package door", () => {
    const html = renderToStaticMarkup(
      createElement(StudioDataContext.Provider, { value: door }, createElement(GenerativeUINode, { spec: node as never })),
    );
    expect(html).toContain('data-flow-state="loading"');
    expect(door.read).not.toHaveBeenCalled();
    expect(door.run).not.toHaveBeenCalled();
  });

  it("a flow node with no workflow id does not bind", () => {
    const bad = { type: "template", props: { name: "flow", source: { action: "workflow", args: {} } } };
    const html = renderToStaticMarkup(
      createElement(StudioDataContext.Provider, { value: door }, createElement(GenerativeUINode, { spec: bad as never })),
    );
    expect(html).not.toContain("data-flow-state");
  });
});
