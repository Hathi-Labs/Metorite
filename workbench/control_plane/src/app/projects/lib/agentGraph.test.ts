import { describe, expect, it } from "vitest";

import { type Graph, type GraphNode, type GraphOptions, GRAPH_X, ROOT_ID, layoutGraph, relatedNodes } from "./agentGraph";
import { type SubRow, buildLensModel } from "./agentLenses";
import { BEN, fleet, mapOf, run, session, splitFleet, workingFleet } from "./agentLensesFixtures";
import { CARDS_MAX, NEW_VIEW, SUB_ROWS_MAX, bucketView, foldView, rememberStarts, restView } from "./agentScale";

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
  it("keeps the four columns when no orchestrator draws, so a refresh moves no node", () => {
    const g = layoutGraph(buildLensModel(mapOf([session("a")])), options());
    expect(node("s:a", g)!.x).toBe(GRAPH_X.session);
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
  it("folds the people with the most agents first, inside one urgency (G3)", () => {
    const g = layoutGraph(of({ ana: 30, ben: 20, cy: 5 }), options());
    const of_ = (who: string) => cards(g).filter((id) => id.startsWith(who)).length;
    expect([of_("ana"), of_("ben"), of_("cy")]).toEqual([15, 20, 5]);
    expect(kind(g, "bucket").map((b) => [b.person.slice(0, 3), b.sessions.length])).toEqual([["ana", 15]]);
  });
  it("draws a person with no card left as their folded card", () => {
    const g = layoutGraph(of({ ana: 60, ben: 40 }), options());
    expect(kind(g, "person").map((n) => [n.person.slice(0, 3), n.folded])).toEqual([
      ["ana", true],
      ["ben", false],
    ]);
  });
  it("keeps the person that the member opened, and folds another", () => {
    const view = foldView(NEW_VIEW, "p:ana@x.test", true, "ana@x.test");
    const g = layoutGraph(of({ ana: 60, ben: 40 }), options({ ...view }));
    expect(cards(g).filter((id) => id.startsWith("ana")).length).toBe(CARDS_MAX);
    expect(kind(g, "person").find((n) => n.person === "ben@x.test")!.folded).toBe(true);
  });
  it("fills the canvas group by group, so an orchestrator keeps its agents", () => {
    const g = layoutGraph(buildLensModel(workingFleet(500)), options({ person: "ana@x.test" }));
    const orchs = kind(g, "orch");
    expect(orchs.length).toBeLessThanOrEqual(5);
    expect(kind(g, "session").length).toBeGreaterThan(30);
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

describe("urgency wins every fold (decision G3)", () => {
  const states = (list: Array<[string, string, string]>) =>
    buildLensModel(
      mapOf(list.map(([id, who, state]) => session(id, { person: `${who}@x.test`, state }))),
      new Map(),
      { email: "ana@x.test" },
    );
  const many = (who: string, n: number, state: string) =>
    Array.from({ length: n }, (_, i): [string, string, string] => [`${who}-${state}-${i}`, who, state]);
  it("draws the one agent that needs you among 60", () => {
    const m = states([...many("ana", 59, "working"), ["ask", "ana", "waiting_on_person"]]);
    expect(cards(layoutGraph(m, options()))).toContain("ask");
  });
  it("draws each Needs you and Failed agent of every person while the cap has room", () => {
    const m = states([
      ...many("ana", 100, "working"),
      ...many("ben", 6, "failed"),
      ...many("cy", 70, "waiting"),
      ...many("cy", 3, "failed"),
    ]);
    const drawn = cards(layoutGraph(m, options()));
    expect(drawn.filter((id) => id.includes("failed"))).toHaveLength(9);
    expect(drawn).toHaveLength(CARDS_MAX);
    // Working folds last: Waiting goes first.
    expect(drawn.filter((id) => id.includes("waiting"))).toHaveLength(0);
  });
  it("folds Done first, then Idle, then Waiting", () => {
    const m = states([
      ...many("ana", 20, "working"),
      ...many("ana", 15, "waiting"),
      ...many("ana", 10, "quiet"),
      ...many("ben", 10, "done"),
      ...many("ben", 1, "working"),
    ]);
    const drawn = cards(layoutGraph(m, options({ mode: "all" })));
    const n = (word: string) => drawn.filter((id) => id.includes(word)).length;
    expect([n("working"), n("waiting"), n("quiet"), n("done")]).toEqual([21, 15, 4, 0]);
  });
});

describe("urgency wins after a click too (decision G3)", () => {
  const fleetOf = (list: Array<[string, number, string]>) =>
    buildLensModel(
      mapOf(
        list.flatMap(([who, n, state]) =>
          Array.from({ length: n }, (_, i) => session(`${who}-${state}-${i}`, { person: `${who}@x.test`, state })),
        ),
      ),
      new Map(),
      { email: "ana@x.test" },
    );
  const n = (g: Graph, word: string) => cards(g).filter((id) => id.includes(word)).length;
  const click = (m: ReturnType<typeof fleetOf>, mode: "active" | "all", state: string, who = "ana@x.test") => {
    const first = layoutGraph(m, options({ mode }));
    const bucket = kind(first, "bucket").find((b) => b.state === state && b.person === who)!;
    return layoutGraph(m, options({ mode, ...bucketView(NEW_VIEW, who, bucket.state, bucket.next) }));
  };
  it("leaves no slot empty: a click on one Done agent keeps every card that fits", () => {
    const m = fleetOf([
      ["ana", 34, "waiting_on_person"],
      ["ana", 10, "working"],
      ["ana", 1, "done"],
    ]);
    const g = click(m, "all", "done");
    expect(cards(g)).toHaveLength(CARDS_MAX);
    expect(n(g, "done")).toBe(1);
    expect(n(g, "waiting_on_person")).toBe(34);
  });
  it("folds an urgent card only for the room that the click needs", () => {
    const m = fleetOf([
      ["ana", 35, "waiting_on_person"],
      ["ana", 20, "working"],
    ]);
    const g = click(m, "active", "working");
    expect(cards(g)).toHaveLength(CARDS_MAX);
    expect(n(g, "working")).toBe(12);
    expect(n(g, "waiting_on_person")).toBe(28);
    // 3 hidden working agents fit beside all 35 urgent ones, so none folds.
    const few = fleetOf([
      ["ana", 35, "waiting_on_person"],
      ["ana", 8, "working"],
    ]);
    const after = click(few, "active", "working");
    expect(n(after, "waiting_on_person")).toBe(35);
    for (const id of [5, 6, 7]) expect(cards(after)).toContain(`ana-working-${id}`);
  });
  it("takes from another person only the room that the click needs", () => {
    const m = fleetOf([
      ["ben", 40, "failed"],
      ["ana", 20, "working"],
      ["ana", 1, "done"],
    ]);
    let view = foldView(NEW_VIEW, "p:ana@x.test", true, "ana@x.test");
    const open = layoutGraph(m, options({ mode: "all", ...view }));
    expect([n(open, "ben-failed"), n(open, "ana-working")]).toEqual([28, 12]);
    const bucket = kind(open, "bucket").find((b) => b.state === "done" && b.person === "ana@x.test")!;
    view = bucketView(view, "ana@x.test", "done", bucket.next);
    const g = layoutGraph(m, options({ mode: "all", ...view }));
    expect([n(g, "ben-failed"), n(g, "ana-done"), n(g, "ana-working")]).toEqual([39, 1, 0]);
  });
  it("counts a displaced folded orchestrator in the node of the most urgent agent it holds", () => {
    const m = buildLensModel(
      mapOf(
        [
          ...Array.from({ length: 45 }, (_, i) => session(`f${String(i).padStart(2, "0")}`, { state: "failed" })),
          session("zboss", { state: "waiting" }),
          session("zlane", { state: "failed" }),
        ],
        [],
        [{ from: "zboss", to: "zlane" }],
      ),
    );
    const g = layoutGraph(m, options({ ...foldView(NEW_VIEW, "s:zboss", false, "ana@x.test") }));
    const holding = kind(g, "bucket").filter((b) => b.sessions.some((s) => s.id === "zboss"));
    if (!cards(g).includes("zboss")) expect(holding.map((b) => b.state)).toEqual(["failed"]);
  });
  it("draws a folded orchestrator that holds an agent that needs you, over a state node click", () => {
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
    let view = foldView(NEW_VIEW, "s:aboss", false, "ana@x.test");
    const bucket = kind(layoutGraph(m, options({ ...view })), "bucket").find((b) => b.state === "waiting")!;
    view = bucketView(view, "ana@x.test", "waiting", bucket.next);
    const g = layoutGraph(m, options({ ...view }));
    expect(cards(g)).toContain("aboss");
  });
});

describe("no state node is dead", () => {
  /** `urgent` failed agents, a working orchestrator with one done lane, and `extra` working agents. */
  const shape = (urgent: number, extra: number, who = "ana") =>
    buildLensModel(
      mapOf(
        [
          ...Array.from({ length: urgent }, (_, i) => session(`f${i}`, { person: `${who}@x.test`, state: "failed" })),
          session("boss"),
          session("lane", { state: "done" }),
          ...Array.from({ length: extra }, (_, i) => session(`w${i}`)),
        ],
        [],
        [{ from: "boss", to: "lane" }],
      ),
    );
  const clickDone = (m: ReturnType<typeof shape>) => {
    let view = foldView(NEW_VIEW, "p:ana@x.test", true, "ana@x.test");
    const bucket = kind(layoutGraph(m, options({ mode: "all", ...view })), "bucket").find(
      (b) => b.state === "done" && b.person === "ana@x.test",
    )!;
    view = bucketView(view, "ana@x.test", "done", bucket.next);
    return layoutGraph(m, options({ mode: "all", ...view }));
  };
  it("draws a lane that needs its orchestrator, past 40 urgent agents", () => {
    expect(cards(clickDone(shape(40, 0)))).toContain("lane");
  });
  it("draws it with no urgent agent", () => {
    expect(cards(clickDone(shape(0, 40)))).toContain("lane");
  });
  it("draws it while another person holds 40 urgent agents", () => {
    const m = buildLensModel(
      mapOf(
        [
          ...Array.from({ length: 40 }, (_, i) => session(`b${i}`, { person: "ben@x.test", state: "failed" })),
          session("boss"),
          session("lane", { state: "done" }),
          ...Array.from({ length: 20 }, (_, i) => session(`w${i}`)),
        ],
        [],
        [{ from: "boss", to: "lane" }],
      ),
    );
    const g = clickDone(m);
    expect(cards(g)).toContain("lane");
    expect(cards(g)).toHaveLength(CARDS_MAX);
  });
  it("draws a new agent of its state on each state node click, in many fleets", () => {
    const states = ["waiting_on_person", "failed", "working", "waiting", "quiet", "done"];
    let seed = 7;
    const random = (n: number) => {
      seed = (seed * 48271) % 2147483647;
      return seed % n;
    };
    for (let fleetNo = 0; fleetNo < 150; fleetNo += 1) {
      const people = ["ana", "ben", "cy"].slice(0, 1 + random(3));
      const heads = Array.from({ length: 1 + random(6) }, (_, i) => `h${i}`);
      const lines: Array<{ from: string; to: string }> = [];
      const sessions = [
        ...heads.map((id) => session(id, { person: `${people[random(people.length)]}@x.test`, state: states[random(6)] })),
        ...Array.from({ length: 30 + random(60) }, (_, i) => {
          if (random(2)) lines.push({ from: heads[random(heads.length)], to: `a${i}` });
          return session(`a${i}`, { person: `${people[random(people.length)]}@x.test`, state: states[random(random(2) ? 2 : 6)] });
        }),
      ];
      const m = buildLensModel(mapOf(sessions, [], lines), new Map(), { email: "ana@x.test" });
      let view = NEW_VIEW;
      for (let click = 0; click < 6; click += 1) {
        const g = layoutGraph(m, options({ mode: "all", ...view }));
        const buckets = kind(g, "bucket");
        if (buckets.length === 0) break;
        const bucket = buckets[random(buckets.length)];
        view = bucketView(view, bucket.person, bucket.state, bucket.next);
        const after = layoutGraph(m, options({ mode: "all", ...view }));
        expect(cards(after).length).toBeLessThanOrEqual(CARDS_MAX);
        const fresh = bucket.sessions.filter((s) => cards(after).includes(s.id));
        expect(fresh.length, `fleet ${fleetNo} click ${click} ${bucket.state}`).toBeGreaterThan(0);
      }
    }
  });
});

describe("a calm, stable map (decision G4)", () => {
  const at = (g: Graph) => g.nodes.map((n) => [n.id, n.x, n.y, n.w, n.h]).sort();
  const base = () => splitFleet();
  it("keeps every node in place over new activity lines with no change of state", () => {
    for (const mode of ["active", "all"] as const) {
      const first = layoutGraph(buildLensModel(base()), options({ mode }));
      const next = base();
      next.sessions = next.sessions.map((s, i) => ({
        ...s,
        last_at: new Date(Date.parse("2026-11-26T11:00:00Z") + i * 7000).toISOString(),
        subagents: { total: i % 3, working: 0, failed: 0, done: 0, names: [] },
      }));
      next.runs = next.runs.map((r) => ({ ...r, title: `${r.title}, a new line`, age: (r.age ?? 0) + 30 }));
      expect(at(layoutGraph(buildLensModel(next), options({ mode })))).toEqual(at(first));
    }
  });
  it("puts me first, then the people by Needs you, Failed and active counts", () => {
    const m = buildLensModel(
      mapOf([
        session("a1", { person: "ana@x.test" }),
        session("a2", { person: "ana@x.test" }),
        session("b1", { person: "ben@x.test", state: "failed" }),
        session("c1", { person: "cy@x.test" }),
        session("z1", { person: "zed@x.test", state: "quiet" }),
      ]),
      new Map(),
      { email: "cy@x.test" },
    );
    const people = kind(layoutGraph(m, options({ me: "cy@x.test" })), "person").map((n) => n.person.split("@")[0]);
    expect(people).toEqual(["cy", "ben", "ana", "zed"]);
  });
  it("keeps the order when a newer run takes the place of the listed one", () => {
    const read = (age: number) =>
      buildLensModel(
        mapOf(
          [session("o1"), session("o2"), session("a"), session("b")],
          [run("r1", "o1", { age }), run("r2", "o2", { age: 500 })],
          [
            { from: "o1", to: "a" },
            { from: "o2", to: "b" },
          ],
        ),
      );
    const NOW = 10_000_000;
    const starts = rememberStarts(new Map(), read(900).sessions, NOW);
    const later = rememberStarts(starts, read(10).sessions, NOW + 30_000);
    expect(later).toBe(starts);
    const order = (m: ReturnType<typeof read>) =>
      kind(layoutGraph(m, options({ starts: later })), "orch").sort((a, b) => a.y - b.y).map((x) => x.session.id);
    expect(order(read(900))).toEqual(["o1", "o2"]);
    expect(order(read(10))).toEqual(["o1", "o2"]);
  });
  it("orders the orchestrators by urgency, then by their start", () => {
    const m = buildLensModel(
      mapOf(
        [session("young"), session("old"), session("hot"), session("y1"), session("o1"), session("h1", { state: "failed" })],
        [run("ry", "young", { age: 10 }), run("ro", "old", { age: 900 }), run("rh", "hot", { age: 1 })],
        [
          { from: "young", to: "y1" },
          { from: "old", to: "o1" },
          { from: "hot", to: "h1" },
        ],
      ),
    );
    const orchs = kind(layoutGraph(m, options()), "orch").sort((a, b) => a.y - b.y).map((n) => n.session.id);
    expect(orchs).toEqual(["hot", "old", "young"]);
  });
});

describe("relatedNodes", () => {
  it("lights the chain above an agent, and what it dispatched", () => {
    const lit = relatedNodes(graph, "s:lane-a");
    // The idle agent that lane-a dispatched sits in Ana's "idle or done" node.
    expect([...lit].sort()).toEqual([ROOT_ID, "p:ana@x.test", "s:boss", "s:lane-a", "r:ana@x.test"].sort());
  });
});
