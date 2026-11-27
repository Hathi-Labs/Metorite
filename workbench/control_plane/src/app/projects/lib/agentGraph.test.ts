import { describe, expect, it } from "vitest";

import { type Graph, type GraphNode, type GraphOptions, GRAPH_X, ROOT_ID, layoutGraph, relatedNodes } from "./agentGraph";
import { type SubRow, buildLensModel } from "./agentLenses";
import { BEN, fleet, mapOf, run, session, splitFleet, workingFleet } from "./agentLensesFixtures";
import { CARDS_MAX, NEW_VIEW, SUB_ROWS_MAX, bucketView, foldView, restView } from "./agentScale";

const options = (over: Partial<GraphOptions> = {}): GraphOptions => ({
  ...NEW_VIEW,
  mode: "active",
  person: "",
  find: "",
  pin: null,
  unit: 1,
  nameOf: (e) => e,
  ...over,
});
const model = buildLensModel(fleet(), new Map(), BEN);
const graph = layoutGraph(model, options());
const split = buildLensModel(splitFleet(), new Map(), { email: "ana@x.test" });
const node = (id: string, g: Graph = graph) => g.nodes.find((n) => n.id === id);
const pairs = (g: Graph = graph) => g.edges.map((e) => `${e.from}>${e.to}:${e.kind}`).sort();
const kind = <K extends GraphNode["kind"]>(g: Graph, k: K) =>
  g.nodes.filter((n): n is Extract<GraphNode, { kind: K }> => n.kind === k);
/** Every node that is one agent: the cards of the bound. */
const cards = (g: Graph) => g.nodes.flatMap((n) => (n.kind === "session" || n.kind === "orch" ? [n.session.id] : []));

function expectNoOverlap(g: Graph) {
  for (const a of g.nodes) {
    for (const b of g.nodes) {
      if (a.id >= b.id) continue;
      const hit = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
      expect(hit, `${a.id} and ${b.id}`).toBe(false);
    }
  }
}

/** Every line ends on a node that is drawn. */
function expectWholeLines(g: Graph) {
  const ids = new Set(g.nodes.map((n) => n.id));
  for (const e of g.edges) expect([ids.has(e.from), ids.has(e.to)], e.id).toEqual([true, true]);
}

describe("the map", () => {
  it("places the four columns: scope, person, orchestrator and agent", () => {
    expect(node(ROOT_ID)!.x).toBe(GRAPH_X.root);
    expect(node("p:ana@x.test")!.x).toBe(GRAPH_X.person);
    expect(node("s:boss")!.x).toBe(GRAPH_X.orch);
    expect(node("s:lane-a")!.x).toBe(GRAPH_X.session);
    expectNoOverlap(graph);
    expectWholeLines(graph);
  });
  it("gives the agents the orchestrator column when no orchestrator draws", () => {
    const g = layoutGraph(buildLensModel(mapOf([session("a")])), options());
    expect(node("s:a", g)!.x).toBe(GRAPH_X.orch);
  });
  it("draws the dispatch lines, across people too", () => {
    expect(pairs()).toEqual(
      expect.arrayContaining([
        `${ROOT_ID}>p:ana@x.test:tree`,
        "p:ana@x.test>s:boss:tree",
        "s:boss>s:lane-a:dispatch",
        "s:boss>s:lane-b:dispatch",
        "p:ben@x.test>s:lane-b:tree",
      ]),
    );
  });
  it("draws no wait line and no waits-on node (decision G1)", () => {
    const ids = new Set(graph.nodes.map((n) => n.kind as string));
    expect(ids.has("block")).toBe(false);
    expect([...new Set(graph.edges.map((e) => e.kind))].sort()).toEqual(["dispatch", "tree"]);
  });
  it("keeps the dispatch line when a person folds", () => {
    const g = layoutGraph(model, options({ ...foldView(NEW_VIEW, "p:ben@x.test", false, "ben@x.test") }));
    expect(node("s:lane-b", g)).toBeUndefined();
    expect(pairs(g)).toContain("s:boss>p:ben@x.test:dispatch");
    expectNoOverlap(g);
    expectWholeLines(g);
  });
  it("folds an orchestrator in place, and keeps the agent of another person with its line", () => {
    const g = layoutGraph(model, options({ ...foldView(NEW_VIEW, "s:boss", false, "ana@x.test") }));
    expect(node("s:lane-a", g)).toBeUndefined();
    expect(node("s:boss", g)).toMatchObject({ folded: true, canFold: true });
    expect(pairs(g)).toContain("s:boss>s:lane-b:dispatch");
    expectNoOverlap(g);
    expectWholeLines(g);
  });
  it("folds the root in place", () => {
    const g = layoutGraph(model, options({ ...foldView(NEW_VIEW, ROOT_ID, false, null) }));
    expect(g.nodes.map((n) => n.id)).toEqual([ROOT_ID]);
    expect(g.edges).toEqual([]);
  });
  it("scales with the density unit", () => {
    const small = layoutGraph(model, options({ unit: 0.9 }));
    expect(node("s:lane-a", small)!.w).toBeCloseTo(node("s:lane-a")!.w * 0.9);
    expectNoOverlap(small);
  });
  it("draws one person only for one person", () => {
    const g = layoutGraph(model, options({ person: "ben@x.test" }));
    expect(kind(g, "person").map((n) => n.person)).toEqual(["ben@x.test"]);
    expect(cards(g)).toEqual(["lane-b"]);
    expect(node(ROOT_ID, g)).toMatchObject({ person: "ben@x.test" });
    expectWholeLines(g);
  });
  it("shows the matches of a find, whatever the switch and the folds", () => {
    const g = layoutGraph(model, options({ find: "solo", ...foldView(NEW_VIEW, "p:ana@x.test", false, "ana@x.test") }));
    expect(cards(g)).toEqual(["solo"]);
    expect(kind(g, "person").map((n) => n.person)).toEqual(["ana@x.test"]);
    expect(kind(g, "rest")).toHaveLength(0);
  });
});

describe("64 sessions of two people (the owner's view)", () => {
  const g = layoutGraph(split, options());
  it("draws one map: the root, both people, the 4 active agents and one idle or done node", () => {
    expect(kind(g, "root")[0].sessions).toHaveLength(64);
    expect(kind(g, "person").map((n) => [n.person, n.folded])).toEqual([
      ["ana@x.test", false],
      ["ben@x.test", true],
    ]);
    expect(cards(g).sort()).toEqual(["boss", "lane-1", "lane-2", "lane-3"]);
    expect(kind(g, "rest").map((n) => [n.person, n.count, n.open])).toEqual([["ana@x.test", 43, false]]);
    expect(kind(g, "bucket")).toHaveLength(0);
    expect(kind(g, "person")[1].sessions).toHaveLength(17);
    expectNoOverlap(g);
    expectWholeLines(g);
  });
  it("opens the idle and done node in place, bounded", () => {
    const open = layoutGraph(split, options({ ...restView(NEW_VIEW, "ana@x.test") }));
    expect(cards(open)).toHaveLength(CARDS_MAX);
    expect(kind(open, "rest").map((n) => [n.count, n.open])).toEqual([[43, true]]);
    expect(kind(open, "bucket").length).toBeGreaterThan(0);
    expectNoOverlap(open);
  });
  it("draws at most 40 agent cards with All, and the most urgent first", () => {
    const all = layoutGraph(split, options({ mode: "all" }));
    expect(cards(all)).toHaveLength(CARDS_MAX);
    expect(cards(all)).toEqual(expect.arrayContaining(["boss", "lane-1", "lane-2", "lane-3"]));
    expect(kind(all, "person").map((n) => n.folded)).toEqual([false, true]);
    expect(kind(all, "rest")).toHaveLength(0);
    expect(kind(all, "bucket").reduce((n, b) => n + b.sessions.length, 0)).toBe(47 - CARDS_MAX);
    expectNoOverlap(all);
    expectWholeLines(all);
  });
});

/** Walks the map by clicks only, as a member does, and returns every agent that drew. */
function walk(m: ReturnType<typeof buildLensModel>, mode: "active" | "all", pin: string | null = null): Set<string> {
  let view = NEW_VIEW;
  const seen = new Set<string>();
  const draw = () => {
    const g = layoutGraph(m, options({ ...view, mode, pin }));
    const drawn = cards(g);
    expect(drawn.length).toBeLessThanOrEqual(CARDS_MAX);
    if (pin) expect(drawn).toContain(pin);
    drawn.forEach((id) => seen.add(id));
    return g;
  };
  for (const p of m.people) {
    const own = () => draw().nodes.find((n) => n.id === `p:${p.person}`);
    const head = own();
    if (head?.kind === "person" && head.folded) view = foldView(view, head.id, true, p.person);
    if (mode === "active" && kind(draw(), "rest").some((n) => n.person === p.person && !n.open)) {
      view = restView(view, p.person);
    }
    const mine = m.sessions.filter((s) => s.person === p.person);
    for (let click = 0; click < 600 && mine.some((s) => !seen.has(s.id)); click += 1) {
      const buckets = kind(draw(), "bucket").filter((b) => b.person === p.person);
      const next = buckets.find((b) => b.sessions.some((s) => !seen.has(s.id)));
      if (!next) break;
      view = bucketView(view, p.person, next.state, next.next);
      draw();
    }
  }
  return seen;
}

describe("every agent is reachable by clicks", () => {
  for (const mode of ["active", "all"] as const) {
    it(`the 64, with ${mode}`, () => {
      expect(walk(split, mode).size).toBe(64);
    });
    it(`the 64, with ${mode}, while an agent is selected`, () => {
      expect(walk(split, mode, "b3").size).toBe(64);
    });
  }
  it("500 working agents", () => {
    expect(walk(buildLensModel(workingFleet(500)), "active").size).toBe(500);
  });
});

describe("the bound of the map", () => {
  const many = buildLensModel(workingFleet(500));
  const of = (sizes: Record<string, number>) =>
    buildLensModel(
      mapOf(Object.entries(sizes).flatMap(([who, n]) => Array.from({ length: n }, (_, i) => session(`${who}${i}`, { person: `${who}@x.test` })))),
    );
  it("draws at most 40 agent cards of 500 working ones", () => {
    const g = layoutGraph(many, options());
    expect(cards(g).length).toBeLessThanOrEqual(CARDS_MAX);
    expect(cards(g).length).toBeGreaterThan(20);
    expect(kind(g, "person").filter((n) => !n.folded)).toHaveLength(1);
    expectNoOverlap(g);
    expectWholeLines(g);
  });
  it("folds the person with the most agents first", () => {
    const g = layoutGraph(of({ ana: 30, ben: 20, cy: 5 }), options());
    expect(kind(g, "person").map((n) => [n.person.slice(0, 3), n.folded]).sort()).toEqual([
      ["ana", true],
      ["ben", false],
      ["cy@", false],
    ]);
    expect(cards(g)).toHaveLength(25);
    expect(kind(g, "bucket")).toHaveLength(0);
  });
  it("keeps the person that the member opened, and folds another", () => {
    const view = foldView(NEW_VIEW, "p:ana@x.test", true, "ana@x.test");
    const g = layoutGraph(of({ ana: 30, ben: 20 }), options({ ...view }));
    expect(kind(g, "person").map((n) => n.folded)).toEqual([false, true]);
  });
  it("draws the state node of AMR-4 for one person past 40", () => {
    const g = layoutGraph(of({ ana: 55 }), options());
    expect(cards(g)).toHaveLength(CARDS_MAX);
    expect(kind(g, "bucket").map((b) => [b.state, b.sessions.length])).toEqual([["working", 15]]);
  });
  it("always draws the selected agent, in a folded person and under a folded orchestrator", () => {
    const fleetOf = of({ ana: 60, ben: 50 });
    for (const pin of ["ana59", "ben49"]) {
      const g = layoutGraph(fleetOf, options({ pin }));
      expect(cards(g)).toContain(pin);
      expect(cards(g).length).toBeLessThanOrEqual(CARDS_MAX);
    }
    const folded = layoutGraph(model, options({ pin: "lane-a", ...foldView(NEW_VIEW, "s:boss", false, "ana@x.test") }));
    expect(cards(folded)).toContain("lane-a");
    expectWholeLines(folded);
  });
  it("lays out 500 agents well inside one second", () => {
    const from = performance.now();
    layoutGraph(buildLensModel(workingFleet(500)), options());
    expect(performance.now() - from).toBeLessThan(1000);
  });
});

describe("the subagents of one agent", () => {
  const map = fleet();
  map.sessions[1] = { ...map.sessions[1], subagents: { total: 20, working: 1, failed: 1, done: 18, names: [] } };
  const m = buildLensModel(map, new Map(), BEN);
  const rows: SubRow[] = Array.from({ length: 20 }, (_, i) => ({ id: `c${i}`, name: `c${i}`, state: "done", model: null, age: i }));
  it("opens as at most 8 rows and a row for the rest", () => {
    const g = layoutGraph(m, options({ subs: { sessionId: "lane-a", rows } }));
    expect(kind(g, "sub")).toHaveLength(SUB_ROWS_MAX);
    expect(kind(g, "submore")[0]).toMatchObject({ count: 12 });
    expectNoOverlap(g);
  });
  it("draws one note while the rows load", () => {
    const g = layoutGraph(m, options({ subs: { sessionId: "lane-a", rows: null } }));
    expect(kind(g, "subnote")).toHaveLength(1);
  });
});

describe("a fold never loses an agent or a line (the verifier's repair)", () => {
  /** 35 working agents, an orchestrator with an agent that asks, and 10 that wait. */
  const m = buildLensModel(
    mapOf(
      [
        ...Array.from({ length: 35 }, (_, i) => session(`w${i}`)),
        session("aboss", { state: "waiting" }),
        session("ask", { state: "waiting_on_person" }),
        ...Array.from({ length: 10 }, (_, i) => session(`z${i}`, { state: "waiting" })),
      ],
      [run("r", "ask", { state: "awaiting" })],
      [{ from: "aboss", to: "ask" }],
    ),
    new Map(),
    { email: "ana@x.test" },
  );
  it("keeps each line whole when the folded orchestrator joins a state node", () => {
    let view = foldView(NEW_VIEW, "s:aboss", false, "ana@x.test");
    const bucket = kind(layoutGraph(m, options({ ...view })), "bucket").find((b) => b.state === "waiting")!;
    view = bucketView(view, "ana@x.test", "waiting", bucket.next);
    const walked = layoutGraph(m, options({ ...view }));
    expectWholeLines(walked);
  });
  it("counts the agents under a folded orchestrator", () => {
    const g = layoutGraph(model, options({ ...foldView(NEW_VIEW, "s:boss", false, "ana@x.test") }));
    // lane-a and the idle agent under it. lane-b is of another person.
    expect(node("s:boss", g)).toMatchObject({ hidden: 2 });
  });
  it("draws the selected agent of a person that the member folded", () => {
    const view = foldView(NEW_VIEW, "p:ben@x.test", false, "ben@x.test");
    expect(cards(layoutGraph(model, options({ ...view, pin: "lane-b" })))).toContain("lane-b");
  });
  it("offers no fold that a find would ignore", () => {
    const g = layoutGraph(model, options({ find: "solo" }));
    expect(node(ROOT_ID, g)).toMatchObject({ canFold: false });
    expect(node("p:ana@x.test", g)).toMatchObject({ canFold: false });
    const bound = layoutGraph(buildLensModel(workingFleet(500)), options({ find: "w" }));
    expect(kind(bound, "person").filter((n) => n.folded).every((n) => n.canFold)).toBe(true);
    expect(kind(bound, "person").some((n) => n.folded)).toBe(true);
  });
});

describe("the layout with lines between two agents", () => {
  const map = mapOf(
    [session("o1"), session("o2"), session("l1", { person: "ben@x.test" }), session("l2"), session("l3")],
    [run("r", "o1")],
    [
      { from: "o1", to: "l1" },
      { from: "o2", to: "l2" },
      { from: "l2", to: "l3" },
    ],
  );
  it("bends a line between two agents of the same column, and overlaps no node", () => {
    const g = layoutGraph(buildLensModel(map), options());
    expect(g.edges.find((e) => e.to === "s:l3")).toMatchObject({ from: "s:l2", side: true, kind: "dispatch" });
    expectNoOverlap(g);
    expectWholeLines(g);
  });
});

describe("relatedNodes", () => {
  it("lights the chain above an agent, and what it dispatched", () => {
    const lit = relatedNodes(graph, "s:lane-a");
    // The idle agent that lane-a dispatched sits in Ana's "idle or done" node.
    expect([...lit].sort()).toEqual([ROOT_ID, "p:ana@x.test", "s:boss", "s:lane-a", "r:ana@x.test"].sort());
  });
});
