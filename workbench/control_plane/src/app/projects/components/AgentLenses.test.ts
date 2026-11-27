import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildLensModel } from "../lib/agentLenses";
import { BEN, fleet, mapOf, run, session } from "../lib/agentLensesFixtures";
import { AgentDetails, firstRun } from "./AgentDetails";
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
      viewerEmail: BEN.email,
      ...over,
    }),
  );
const phone = (over: Partial<AgentsPhoneProps> = {}) =>
  renderToStaticMarkup(
    createElement(AgentsPhone, {
      model: buildLensModel(fleet(), new Map(), BEN),
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
  it("draws the six chips, the person filter, find and one switch of three lenses", () => {
    const out = lenses();
    for (const word of ["Needs you", "Failed", "Working", "Waiting", "Idle", "Done", "Everyone", "Find a session, task or subagent"]) {
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
  it("draws four counts, the idle link and the people with their squares on the first screen", () => {
    const out = phone();
    for (const state of ["you", "failed", "working", "waiting"]) expect(out).toContain(`data-agent-phone-tile="${state}"`);
    expect(out).toContain("2 idle");
    expect(out).toContain('data-agent-square="you"');
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
    expect(group).toContain('data-agent-phone-section="waiting"');
    expect(group).toContain('aria-label="1 idle"');
  });
});

describe("the run that the details open on", () => {
  it("is the run that asks, not the newest one", () => {
    const map = mapOf(
      [session("s", { state: "waiting_on_person" })],
      [run("new", "s", { age: 1 }), run("ask", "s", { state: "awaiting", age: 900 })],
    );
    expect(firstRun(buildLensModel(map).sessions[0])?.run_id).toBe("ask");
  });
  it("is the newest run when none asks, and none with no run", () => {
    const map = mapOf([session("s")], [run("old", "s", { age: 900 }), run("new", "s", { age: 1 })]);
    expect(firstRun(buildLensModel(map).sessions[0])?.run_id).toBe("new");
    expect(firstRun({ runs: [] })).toBeNull();
  });
});

describe("a question of another member (EA3-50)", () => {
  it("reads Waiting for them on the card, and no card needs the viewer", () => {
    const out = lenses({ viewerEmail: "ana@x.test" });
    expect(out).toContain("Waiting for Ben");
    expect(out).not.toContain('data-agent-session="you"');
    expect(lenses()).toContain('data-agent-session="you"');
  });
  it("never reads Needs you on a run in the details", () => {
    const details = (email: string) =>
      renderToStaticMarkup(
        createElement(AgentDetails, {
          model: buildLensModel(fleet(), new Map(), { email }),
          selection: { kind: "session", id: "lane-b" },
          onSelect: () => {},
          onOpenTask: () => {},
          nameOf: (e: string) => e,
          nowMs: NOW,
        }),
      );
    expect(details("ana@x.test")).not.toContain("Needs you");
    expect(details(BEN.email)).toContain("Needs you");
  });
  // No DOM runs in this suite, so the pin is read from the source.
  it("pins the first run of the details, so a re-read does not swap the panel", () => {
    const source = readFileSync(new URL("./AgentDetails.tsx", import.meta.url), "utf-8");
    expect(source).toContain("useState<string | null>(() => firstRun(session)?.run_id ?? null)");
    // AMR-4: the rows come from the read of the session, else from the map.
    expect(source).toContain("runs.find((r) => r.run_id === picked) ?? firstRun({ runs })");
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
