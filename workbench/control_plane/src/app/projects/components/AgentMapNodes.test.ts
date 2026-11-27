/**
 * The node cards of the map (WS-MCP AMR-5). A server render draws no node of
 * `@xyflow/react`, so these tests render each card by itself.
 */
import { type ReactElement, createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { type GraphNode, layoutGraph } from "../lib/agentGraph";
import { buildLensModel } from "../lib/agentLenses";
import { splitFleet } from "../lib/agentLensesFixtures";
import { NEW_VIEW, foldView } from "../lib/agentScale";
import { AgentDetails } from "./AgentDetails";
import { GraphCard, NODE_MOVE, readingOrder } from "./AgentGraphLens";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const model = buildLensModel(splitFleet(), new Map(), { email: "ana@x.test" });
const graph = layoutGraph(model, { ...NEW_VIEW, mode: "active", person: "", find: "", pin: null, unit: 1, nameOf: (e) => e });
const byId = (id: string) => graph.nodes.find((n) => n.id === id)!;
type Data = Parameters<typeof GraphCard>[0]["data"];
const data = (node: GraphNode, over: Partial<Data> = {}) =>
  ({
    node, selected: false, dim: false, nowMs: NOW, nameOf: (e: string) => e, subsOpen: false, namesOnly: false,
    onPick: () => {}, onFold: () => {}, onHover: () => {}, onToggleSubs: () => {}, onRest: () => {},
    onBucket: () => {}, onPickSession: () => {}, onPickId: () => {}, ...over,
  }) as unknown as Data;
const draw = (id: string, over: Partial<Data> = {}) =>
  renderToStaticMarkup(createElement(GraphCard, { data: data(byId(id), over) }));
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");

describe("the node cards of the map", () => {
  it("says the totals of the scope on the root", () => {
    const out = text(draw("root"));
    for (const part of ["Everyone", "64 agents · 4 active", "0 subagents"]) expect(out).toContain(part);
  });
  it("opens the details from the root, as from any node", () => {
    expect(draw("root")).toMatch(/role="button"[^>]*aria-label="Everyone\. 64 agents · 4 active\./);
    const details = renderToStaticMarkup(
      createElement(AgentDetails, {
        model, selection: { kind: "root", person: "" }, onSelect: () => {}, onOpenTask: () => {}, nameOf: (e: string) => e, nowMs: NOW,
      }),
    );
    expect(details).toContain('data-agent-details="root"');
  });
  it("draws untrusted names as text on each card", () => {
    const evil = splitFleet();
    evil.sessions[0].name = "<img src=x onerror=1>";
    const g = layoutGraph(buildLensModel(evil), {
      ...NEW_VIEW, mode: "active", person: "", find: "", pin: null, unit: 1, nameOf: () => "<img src=y>",
    });
    const out = g.nodes
      .map((node) => renderToStaticMarkup(createElement(GraphCard, { data: data(node, { nameOf: () => "<img src=y>" }) })))
      .join("");
    expect(out).toContain("&lt;img src=x");
    expect(out).not.toMatch(/<img src=[xy]/);
  });
  it("draws a folded person as a card with counts and squares", () => {
    const out = draw("p:ben@x.test");
    expect(out).toContain('data-agent-graph-person="folded"');
    expect(out).toContain("data-agent-squares");
    expect(text(out)).toContain("17 agents · 0 active");
    expect(text(out)).toContain("17 idle or done");
    expect(out).toContain('aria-label="Unfold ben@x.test"');
  });
  it("draws an open person with its counts and a chevron that folds", () => {
    const out = draw("p:ana@x.test");
    expect(out).toContain('data-agent-graph-person="open"');
    expect(out).not.toContain("data-agent-squares");
    expect(text(out)).toContain("47 agents · 4 active");
    expect(out).toContain('aria-label="Fold ana@x.test"');
  });
  it("draws the idle or done node", () => {
    expect(text(draw("r:ana@x.test"))).toContain("43 idle or done Show");
  });
  it("writes the wait as a fact of the card (G1, G2)", () => {
    expect(text(draw("s:lane-2"))).toContain("Waiting · CI · 4 min");
  });
  it("writes the task, the age and the wait on an orchestrator card too (G1, G2)", () => {
    const waits = splitFleet();
    waits.sessions[0] = { ...waits.sessions[0], state: "waiting", host: "metorite-home" };
    waits.runs[0] = { ...waits.runs[0], state: "waiting", waiting_on: "ci", waiting_until: "2026-11-26T12:25:00Z" };
    const g = layoutGraph(buildLensModel(waits), { ...NEW_VIEW, mode: "active", person: "", find: "", pin: null, unit: 1, nameOf: (e) => e });
    const node = g.nodes.find((n) => n.id === "s:boss")!;
    const seen = text(renderToStaticMarkup(createElement(GraphCard, { data: data(node) })));
    for (const part of ["boss", "5m ago", "#7 Task of boss", "metorite-home", "Waiting · CI · 25 min"]) expect(seen).toContain(part);
  });
  it("shows the subagent count at the foot of an orchestrator card, and its task while folded", () => {
    const subs = splitFleet();
    subs.sessions[0] = { ...subs.sessions[0], subagents: { total: 7, working: 2, failed: 0, done: 5, names: [] } };
    const g = layoutGraph(buildLensModel(subs), {
      ...foldView(NEW_VIEW, "s:boss", false, "ana@x.test"), mode: "active", person: "", find: "", pin: null, unit: 1, nameOf: (e) => e,
    });
    const node = g.nodes.find((n) => n.id === "s:boss")!;
    const out = renderToStaticMarkup(createElement(GraphCard, { data: data(node) }));
    for (const part of ["7 · 2 working", "#7 Task of boss", "agents folded"]) expect(text(out)).toContain(part);
    expect(out).toContain('data-agent-sub-toggle="boss"');
  });
  it("draws the name, the task, the age and the machine, then the count at the foot (G2)", () => {
    const evil = splitFleet();
    evil.sessions[1] = { ...evil.sessions[1], host: "metorite-home", subagents: { total: 7, working: 2, failed: 0, done: 5, names: [] } };
    const g = layoutGraph(buildLensModel(evil), { ...NEW_VIEW, mode: "active", person: "", find: "", pin: null, unit: 1, nameOf: (e) => e });
    const node = g.nodes.find((n) => n.id === "s:lane-1")!;
    const out = renderToStaticMarkup(createElement(GraphCard, { data: data(node) }));
    const seen = text(out);
    const order = ["lane-1", "5m ago", "#7 Task of lane-1", "metorite-home", "7 · 2 working"].map((part) => seen.indexOf(part));
    expect(order.every((i) => i >= 0)).toBe(true);
    expect(order).toEqual([...order].sort((a, b) => a - b));
    expect(out.indexOf("data-agent-card-foot")).toBeLessThan(out.lastIndexOf("data-agent-sub-count"));
  });
  it("gives the tab stops column by column, from the scope to the agents", () => {
    expect(readingOrder(graph.nodes)[0].id).toBe("root");
    expect(readingOrder(graph.nodes).map((n) => n.x)).toEqual(graph.nodes.map((n) => n.x).sort((a, b) => a - b));
  });
  it("says a state node count once", () => {
    const sessions = model.sessions.filter((s) => s.state === "done");
    const bucket: GraphNode = { id: "k", kind: "bucket", person: "a", state: "done", sessions, next: 0, x: 0, y: 0, w: 1, h: 1 };
    const out = renderToStaticMarkup(createElement(GraphCard, { data: data(bucket) }));
    expect(text(out).match(/\+\d+/g)).toEqual([`+${sessions.length}`]);
  });
  it("moves a node in 200 ms, and not with reduced motion (G4)", () => {
    expect(NODE_MOVE).toContain("duration-200");
    expect(NODE_MOVE).toContain("motion-reduce:transition-none");
  });
  it("gives each node that folds one chevron", () => {
    for (const id of ["root", "p:ana@x.test", "p:ben@x.test", "s:boss"]) {
      expect(draw(id).split("data-agent-chevron").length - 1, id).toBe(1);
    }
  });
  it("sends the click of a state node with its next cards", () => {
    const bucket: GraphNode = {
      id: "k", kind: "bucket", person: "ana@x.test", state: "working", sessions: [], next: 12, x: 0, y: 0, w: 1, h: 1,
    };
    const onBucket = vi.fn();
    const card = GraphCard({ data: data(bucket, { onBucket }) }) as ReactElement<{ onOpen: () => void; word: string }>;
    expect(card.props.word).toBe("+0 working");
    card.props.onOpen();
    expect(onBucket).toHaveBeenCalledWith("ana@x.test", "working", 12);
  });
});
