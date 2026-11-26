import { describe, expect, it } from "vitest";

import { type GraphOptions, GRAPH_X, layoutGraph, relatedNodes } from "./agentGraph";
import { DEFAULT_STATES, buildLensModel } from "./agentLenses";
import { fleet, mapOf, run, session } from "./agentLensesFixtures";

const options = (over: Partial<GraphOptions> = {}): GraphOptions => ({
  filters: { states: new Set(DEFAULT_STATES), person: "", find: "" },
  collapsed: new Set(),
  opened: new Set(),
  waits: true,
  unit: 1,
  nameOf: (e) => e,
  ...over,
});
const model = buildLensModel(fleet());
const graph = layoutGraph(model, options());
const node = (id: string, g = graph) => g.nodes.find((n) => n.id === id);
const pairs = (g = graph) => g.edges.map((e) => `${e.from}>${e.to}:${e.kind}`).sort();

function expectNoOverlap(g: typeof graph) {
  for (const a of g.nodes) {
    for (const b of g.nodes) {
      if (a.id >= b.id) continue;
      const hit = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      expect(hit, `${a.id} and ${b.id}`).toBe(false);
    }
  }
}

describe("layoutGraph", () => {
  it("places four columns: person, orchestrator, session and what it waits on", () => {
    expect(node("p:ana@x.test")!.x).toBe(GRAPH_X.person);
    expect(node("s:boss")!.x).toBe(GRAPH_X.orch);
    expect(node("s:lane-a")!.x).toBe(GRAPH_X.session);
    expect(node("b:ci")!.x).toBe(GRAPH_X.block);
    expect(node("b:you")!.x).toBe(GRAPH_X.block);
  });
  it("draws the dispatch lines, and holds a loose session from its person", () => {
    expect(pairs()).toEqual(
      expect.arrayContaining([
        "p:ana@x.test>s:boss:tree",
        "s:boss>s:lane-a:dispatch",
        "s:boss>s:lane-b:dispatch",
        "s:lane-a>b:ci:wait",
        "s:lane-b>b:you:you",
      ]),
    );
    expect(pairs()).not.toContain("p:ben@x.test>s:lane-b:tree");
  });
  it("folds the idle sessions of a group into one node", () => {
    const folds = graph.nodes.filter((n) => n.kind === "fold");
    expect(folds).toHaveLength(2);
    expect(node("s:solo")).toBeUndefined();
  });
  it("bends a line between two sessions of the session column", () => {
    const open = layoutGraph(model, options({ opened: new Set(["ana@x.test|boss"]) }));
    expect(open.edges.find((e) => e.to === "s:sub")).toMatchObject({ from: "s:lane-a", side: true, kind: "dispatch" });
    expectNoOverlap(open);
  });
  it("folds an orchestrator, and keeps the lane of another person with its line", () => {
    const folded = layoutGraph(model, options({ collapsed: new Set(["boss"]) }));
    expect(node("s:lane-a", folded)).toBeUndefined();
    expect(pairs(folded)).toContain("s:boss>s:lane-b:dispatch");
    expectNoOverlap(folded);
  });
  it("draws no waits-on column when it is off", () => {
    const off = layoutGraph(model, options({ waits: false }));
    expect(off.nodes.some((n) => n.kind === "block")).toBe(false);
    expect(off.edges.some((e) => e.kind === "you" || e.kind === "wait")).toBe(false);
  });
  it("never invents a line for a wait with no structured field", () => {
    const g = layoutGraph(
      buildLensModel(mapOf([session("w", { state: "waiting" })], [run("r", "w", { state: "waiting" })])),
      options(),
    );
    expect(g.nodes.some((n) => n.kind === "block")).toBe(false);
  });
  it("shows one person, and overlaps no node", () => {
    const one = layoutGraph(model, options({ filters: { states: new Set(DEFAULT_STATES), person: "ben@x.test", find: "" } }));
    expect(one.nodes.filter((n) => n.kind === "person").map((n) => n.id)).toEqual(["p:ben@x.test"]);
    expectNoOverlap(graph);
    expectNoOverlap(one);
  });
  it("scales with the density unit", () => {
    const small = layoutGraph(model, options({ unit: 0.9 }));
    expect(node("s:lane-a", small)!.w).toBeCloseTo(node("s:lane-a")!.w * 0.9);
    expectNoOverlap(small);
  });
  it("overlaps no node when a short group stands beside tall orchestrators", () => {
    const m = buildLensModel(
      mapOf(
        [session("o1"), session("o2"), session("l1", { person: "ben@x.test" }), session("l2", { state: "quiet" })],
        [],
        [{ from: "o1", to: "l1" }, { from: "o2", to: "l2" }],
      ),
    );
    expectNoOverlap(layoutGraph(m, options({ filters: { states: new Set(), person: "", find: "" } })));
    expectNoOverlap(layoutGraph(m, options()));
  });
});

describe("relatedNodes", () => {
  it("lights the chain above a session and what it waits on", () => {
    const lit = relatedNodes(graph, "s:lane-a");
    expect([...lit].sort()).toEqual(["b:ci", "p:ana@x.test", "s:boss", "s:lane-a"].sort());
  });
  it("lights the sessions of a waits-on node and the chain above each", () => {
    const lit = relatedNodes(graph, "b:you");
    expect([...lit].sort()).toEqual(["b:you", "p:ana@x.test", "s:boss", "s:lane-b"].sort());
  });
});
