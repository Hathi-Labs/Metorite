/**
 * AMR-5b (`agent_coordination.md` §3.12.8, G8): the orchestrator outside the
 * project. The summary read of a project sends it as a context row. It is no
 * session: it counts in no total, and it draws only with a lane that it dispatched.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentDetails, selectedSessionId, selectionNodeId, selectionTitle } from "../components/AgentDetails";
import { GraphCard } from "../components/AgentGraphLens";
import { type Graph, type GraphNode, type GraphOptions, GRAPH_X, ROOT_ID, drawnContextIds, layoutGraph } from "./agentGraph";
import { buildLensModel, personSessions } from "./agentLenses";
import { contextRow, mapOf, outsideFleet, run, session } from "./agentLensesFixtures";
import { OUTSIDE_PROJECT, parseAgentMap } from "./agentMap";
import { CARDS_MAX, NEW_VIEW, foldView, planMap } from "./agentScale";
import { wantedSessions } from "./agentSessionReads";

const NOW = Date.parse("2026-11-26T12:00:00Z");
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
const model = buildLensModel(outsideFleet(), new Map(), { email: "ana@x.test" });
const graph = layoutGraph(model, options());
const node = (id: string, g: Graph = graph) => g.nodes.find((n) => n.id === id);
const pairs = (g: Graph = graph) => g.edges.map((e) => `${e.from}>${e.to}:${e.kind}`).sort();
const contextsOf = (g: Graph) => g.nodes.filter((n) => n.kind === "context").map((n) => n.id).sort();
const cards = (g: Graph) => g.nodes.filter((n) => n.kind === "session" || n.kind === "orch" || n.kind === "context");
type Data = Parameters<typeof GraphCard>[0]["data"];
const draw = (n: GraphNode) =>
  renderToStaticMarkup(
    createElement(GraphCard, {
      data: {
        node: n, selected: false, dim: false, nowMs: NOW, nameOf: (e: string) => e, subsOpen: false, namesOnly: false,
        onPick: () => {}, onFold: () => {}, onHover: () => {}, onToggleSubs: () => {}, onRest: () => {},
        onBucket: () => {}, onPickSession: () => {}, onPickId: () => {},
      } as unknown as Data,
    }),
  );
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("the context row of the read", () => {
  it("goes to its own list, never to the sessions", () => {
    const map = outsideFleet();
    expect(map.sessions.map((s) => s.id)).not.toContain("ctx-ana");
    expect(map.contexts).toEqual([
      { id: "ctx-ana", name: "ctx-ana", person: "ana@x.test", project: "Platform" },
      { id: "ctx-zed", name: "ctx-zed", person: "zed@x.test", project: null },
    ]);
    expect(map.people).toEqual([
      { person: "ana@x.test", sessions: 4 },
      { person: "ben@x.test", sessions: 1 },
    ]);
  });
  it("takes no run, and never stands for a session of the same id", () => {
    const map = parseAgentMap({
      sessions: [session("a"), contextRow("a", "x@x.test"), contextRow("c", "x@x.test")],
      runs: [run("r1", "a"), run("r2", "c")],
      lines: [],
    })!;
    expect(map.sessions.map((s) => s.id)).toEqual(["a"]);
    expect(map.contexts!.map((c) => c.id)).toEqual(["c"]);
    expect(map.runs.map((r) => r.run_id)).toEqual(["r1"]);
  });
});

describe("the model", () => {
  it("adds no session, no person and no count", () => {
    const plain = buildLensModel({ ...outsideFleet(), contexts: [] }, new Map(), { email: "ana@x.test" });
    expect(model.sessions.map((s) => s.id)).toEqual(plain.sessions.map((s) => s.id));
    expect(model.people.map((p) => [p.person, personSessions(p).length])).toEqual(
      plain.people.map((p) => [p.person, personSessions(p).length]),
    );
    // The phone view reads the groups: the lanes still read "Started directly".
    expect(model.people.map((p) => p.groups.map((g) => g.key))).toEqual(plain.people.map((p) => p.groups.map((g) => g.key)));
    expect(model.byId.has("ctx-ana")).toBe(false);
  });
  it("gives each context its lanes, and each lane its contexts", () => {
    expect(model.contexts.map((c) => [c.id, c.lanes])).toEqual([
      ["ctx-ana", ["ben-1", "own-1"]],
      ["ctx-zed", ["own-2"]],
    ]);
    expect(model.byId.get("ben-1")!.contexts).toEqual(["ctx-ana"]);
    expect(model.byId.get("ben-1")!.dispatchers).toEqual([]);
  });
});

describe("the map draws the context node (G8)", () => {
  it("draws it in the orchestrator column, under its person, with its lanes", () => {
    const ctx = node("c:ctx-ana")!;
    expect(ctx.x).toBe(GRAPH_X.orch);
    expect(ctx.kind === "context" && ctx.person).toBe("ana@x.test");
    expect(pairs()).toEqual(
      expect.arrayContaining(["p:ana@x.test>c:ctx-ana:tree", "c:ctx-ana>s:own-1:tree", "c:ctx-ana>s:ben-1:dispatch"]),
    );
    // Ben's lane stays under Ben.
    expect(pairs()).toContain("p:ben@x.test>s:ben-1:tree");
    expect(pairs()).not.toContain("p:ana@x.test>s:own-1:tree");
  });
  it("draws a context of a person not on the map after the people, from the scope", () => {
    const ctx = node("c:ctx-zed")!;
    expect(ctx.x).toBe(GRAPH_X.orch);
    expect(ctx.kind === "context" && ctx.person).toBeNull();
    const people = graph.nodes.filter((n) => n.kind === "person");
    expect(ctx.y).toBeGreaterThan(Math.max(...people.map((p) => p.y)));
    expect(pairs()).toEqual(expect.arrayContaining([`${ROOT_ID}>c:ctx-zed:tree`, "c:ctx-zed>s:own-2:dispatch"]));
    expect(node("p:zed@x.test")).toBeUndefined();
  });
  it("draws it after the people when its person is outside the People choice", () => {
    const g = layoutGraph(model, options({ person: "ben@x.test" }));
    const ctx = node("c:ctx-ana", g)!;
    expect(ctx.kind === "context" && ctx.person).toBeNull();
    expect(pairs(g)).toEqual(expect.arrayContaining([`${ROOT_ID}>c:ctx-ana:tree`, "c:ctx-ana>s:ben-1:dispatch"]));
    // Zed's context dispatched only a lane of Ana, so it does not draw.
    expect(contextsOf(g)).toEqual(["c:ctx-ana"]);
  });
  it("draws only while a lane that it dispatched draws", () => {
    const g = layoutGraph(model, options({ ...foldView(NEW_VIEW, "p:ana@x.test", false, "ana@x.test") }));
    // Ana folds: own-2 draws no card, so ctx-zed does not draw. Ben's lane keeps ctx-ana.
    expect(contextsOf(g)).toEqual(["c:ctx-ana"]);
    const folded = layoutGraph(model, options({ ...foldView(NEW_VIEW, ROOT_ID, false, null) }));
    expect(contextsOf(folded)).toEqual([]);
  });
  it("adds nothing to the totals of the scope and the people", () => {
    const root = node(ROOT_ID)!;
    expect(root.kind === "root" && root.sessions.length).toBe(5);
    const ana = node("p:ana@x.test")!;
    expect(ana.kind === "person" && ana.sessions.length).toBe(4);
  });
  it("keeps every node apart, and every line on a drawn node", () => {
    const ids = new Set(graph.nodes.map((n) => n.id));
    for (const e of graph.edges) expect([ids.has(e.from), ids.has(e.to)], e.id).toEqual([true, true]);
    for (const a of graph.nodes) {
      for (const b of graph.nodes) {
        if (a.id >= b.id) continue;
        const hit = a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
        expect(hit, `${a.id} and ${b.id}`).toBe(false);
      }
    }
  });
});

describe("the bound counts a context card", () => {
  /** 60 working lanes of one person, each two dispatched by one of 30 contexts of a person not on the map. */
  const wide = () =>
    mapOf(
      [
        ...Array.from({ length: 60 }, (_, i) => session(`w${i}`)),
        ...Array.from({ length: 30 }, (_, i) => contextRow(`c${i}`, "zed@x.test")),
      ],
      Array.from({ length: 60 }, (_, i) => run(`r${i}`, `w${i}`)),
      Array.from({ length: 60 }, (_, i) => ({ from: `c${Math.floor(i / 2)}`, to: `w${i}` })),
    );
  it("draws at most CARDS_MAX cards, and a drawn lane brings its context", () => {
    const m = buildLensModel(wide());
    const g = layoutGraph(m, options());
    expect(cards(g).length).toBeLessThanOrEqual(CARDS_MAX);
    const drawn = new Set(g.nodes.filter((n) => n.kind === "session").map((n) => n.id.slice(2)));
    for (const id of drawn) expect(node(`c:${m.byId.get(id)!.contexts[0]}`, g), id).toBeDefined();
    // A state node stands for the rest, so a click reaches them.
    expect(g.nodes.some((n) => n.kind === "bucket")).toBe(true);
  });
  it("counts the context card in the plan", () => {
    const m = buildLensModel(wide());
    const [plan] = planMap(m, options());
    const lanes = [...plan.shown].filter((id) => id.startsWith("w"));
    const contexts = [...plan.shown].filter((id) => id.startsWith("c:"));
    expect(lanes.length + contexts.length).toBeLessThanOrEqual(CARDS_MAX);
    expect(contexts.length).toBe(new Set(lanes.map((id) => m.byId.get(id)!.contexts[0])).size);
  });
});

describe("the context card and its details", () => {
  it("reads the name, the mark and the project, with no chevron", () => {
    const out = draw(node("c:ctx-ana")!);
    for (const part of ["ctx-ana", OUTSIDE_PROJECT, "Platform"]) expect(text(out)).toContain(part);
    expect(out).not.toContain("data-agent-chevron");
    expect(out).toContain('data-agent-graph-context="ctx-ana"');
    expect(text(draw(node("c:ctx-zed")!))).not.toContain("Platform");
  });
  it("draws untrusted names as text", () => {
    const evil = outsideFleet();
    evil.contexts = [{ id: "ctx-ana", name: "<img src=x>", person: "ana@x.test", project: "<img src=y>" }];
    const g = layoutGraph(buildLensModel(evil), options());
    const out = draw(g.nodes.find((n) => n.kind === "context")!);
    expect(out).toContain("&lt;img src=x");
    expect(out).not.toMatch(/<img src=[xy]/);
  });
  it("opens the details with the same three facts, and never reads session=", () => {
    const selection = { kind: "context", id: "ctx-ana" } as const;
    expect(selectionNodeId(selection)).toBe("c:ctx-ana");
    expect(selectionTitle(model, selection, (e) => e)).toBe("ctx-ana");
    expect(selectedSessionId(selection)).toBeNull();
    expect(wantedSessions(null, selectedSessionId(selection))).toEqual([]);
    expect(selectedSessionId({ kind: "session", id: "own-1" })).toBe("own-1");
    const out = renderToStaticMarkup(
      createElement(AgentDetails, {
        model, selection, onSelect: () => {}, onOpenTask: () => {}, nameOf: (e: string) => e, nowMs: NOW,
      }),
    );
    expect(out).toContain('data-agent-details="context"');
    for (const part of ["ctx-ana", OUTSIDE_PROJECT, "Platform"]) expect(text(out)).toContain(part);
  });
  it("closes the details of a context that left the map", () => {
    expect(selectionTitle(model, { kind: "context", id: "gone" }, (e) => e)).toBeNull();
  });
  it("knows which context cards draw, by the one rule of the layout", () => {
    const ids = (over: Partial<GraphOptions>) => [...drawnContextIds(model, options(over))].sort();
    const layout = (over: Partial<GraphOptions>) => contextsOf(layoutGraph(model, options(over))).map((id) => id.slice(2));
    const cases: Array<Partial<GraphOptions>> = [
      {},
      { ...foldView(NEW_VIEW, "p:ana@x.test", false, "ana@x.test") },
      { ...foldView(NEW_VIEW, ROOT_ID, false, null) },
      { person: "ben@x.test" },
      { person: "nobody@x.test" },
      { find: "ben-1" },
    ];
    for (const over of cases) expect(ids(over)).toEqual(layout(over));
    expect(ids({})).toEqual(["ctx-ana", "ctx-zed"]);
    // A fold of Ana or the People choice of Ben takes ctx-zed off the map, so its details close.
    expect(ids({ ...foldView(NEW_VIEW, "p:ana@x.test", false, "ana@x.test") })).toEqual(["ctx-ana"]);
    expect(ids({ person: "ben@x.test" })).toEqual(["ctx-ana"]);
    expect(ids({ ...foldView(NEW_VIEW, ROOT_ID, false, null) })).toEqual([]);
  });
});
