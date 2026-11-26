import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildLensModel } from "../lib/agentLenses";
import { fleet, mapOf } from "../lib/agentLensesFixtures";
import { AgentLenses, type AgentLensesProps } from "./AgentLenses";
import { panToShow, startViewport } from "./AgentGraphLens";
import { AgentsPhone, type AgentsPhoneProps } from "./AgentsPhone";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const lenses = (over: Partial<AgentLensesProps> = {}) =>
  renderToStaticMarkup(
    createElement(AgentLenses, {
      read: { kind: "ready", map: fleet() },
      onOpenTask: () => {},
      emptyCopy: "Nothing here.",
      nowMs: NOW,
      initialLens: "board",
      phone: false,
      ...over,
    }),
  );
const phone = (over: Partial<AgentsPhoneProps> = {}) =>
  renderToStaticMarkup(
    createElement(AgentsPhone, {
      model: buildLensModel(fleet()),
      nowMs: NOW,
      nameOf: (e: string) => e,
      onOpenSession: () => {},
      onOpenTask: () => {},
      onOpenProject: () => {},
      questionsOn: false,
      ...over,
    }),
  );

describe("the toolbar and the lenses", () => {
  it("draws the four chips, the person filter, find and one switch of three lenses", () => {
    const out = lenses();
    for (const word of ["Needs you", "Working", "Waiting", "Idle", "Everyone", "Find a session or task"]) {
      expect(out).toContain(word);
    }
    for (const lens of ["Board", "Graph", "List"]) expect(out).toContain(`>${lens}<`);
    expect(out).toContain("data-agent-board");
  });
  it("draws each card with the edge of its state", () => {
    const out = lenses();
    for (const edge of ["live", "steady", "warning"]) expect(out).toContain(`data-agent-edge="${edge}"`);
    expect(out).toContain("1 idle session");
    expect(out).not.toContain("dashed");
  });
  it("draws the outline on the List lens", () => {
    const out = lenses({ initialLens: "list" });
    expect(out).toContain("data-agent-list-row");
    expect(out).toContain("dispatched 2");
  });
  it("draws the empty words for a map with no session", () => {
    expect(lenses({ read: { kind: "ready", map: mapOf([]) } })).toContain("Nothing here.");
  });
  it("draws untrusted names as text", () => {
    const map = fleet();
    map.sessions[0].name = "<img src=x onerror=1>";
    expect(lenses({ read: { kind: "ready", map } })).not.toContain("<img src=x");
  });
});

describe("the phone view", () => {
  it("never draws the graph or the toolbar", () => {
    const out = lenses({ phone: true, initialLens: "graph" });
    expect(out).toContain("data-agent-phone");
    expect(out).not.toContain("data-agent-graph");
    expect(out).not.toContain('role="toolbar"');
  });
  it("draws three counts, the idle line and the people on the first screen", () => {
    const out = phone();
    for (const state of ["you", "working", "waiting"]) expect(out).toContain(`data-agent-phone-tile="${state}"`);
    expect(out).toContain("2 idle sessions");
    expect(out).toContain("ben@x.test");
    expect(out).not.toContain("data-agent-session=");
  });
  it("draws the one question list when it is on, and none when it is off", () => {
    expect(phone({ questionsOn: true })).toContain("data-questions");
    expect(phone()).not.toContain("data-questions");
  });
  it("draws one level: the orchestrators of a person, then their sessions", () => {
    const person = phone({ initialPath: [{ kind: "person", person: "ana@x.test" }] });
    expect(person).toContain("Orchestrator boss");
    expect(person).toContain("data-agent-phone-back");
    const group = phone({
      initialPath: [
        { kind: "person", person: "ana@x.test" },
        { kind: "group", person: "ana@x.test", key: "ana@x.test|boss" },
      ],
    });
    expect(group).toContain("lane-a");
    expect(group).toContain("1 idle session");
  });
});

describe("the view of the graph", () => {
  it("starts a tall graph at its top, at a readable size", () => {
    const view = startViewport([{ x: 0, y: 0, w: 900, h: 3000 }], { width: 1000, height: 600 });
    expect(view.zoom).toBe(0.85);
    expect(view.y).toBeCloseTo(24 * 0.85);
  });
  it("never starts wider than the box", () => {
    expect(startViewport([{ x: 0, y: 0, w: 1952, h: 100 }], { width: 1000, height: 600 }).zoom).toBeCloseTo(0.5);
  });
  it("pans a selected node past the right edge into view, and leaves a visible one", () => {
    expect(panToShow({ x: 500, w: 300 }, { x: 0, zoom: 1 }, 600)).toBe(-224);
    expect(panToShow({ x: 100, w: 300 }, { x: 0, zoom: 1 }, 600)).toBeNull();
  });
});
