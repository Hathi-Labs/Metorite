import { readFileSync } from "node:fs";

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { buildLensModel } from "../lib/agentLenses";
import { BEN, fleet, mapOf, run, session, splitFleet, workingFleet } from "../lib/agentLensesFixtures";
import { AgentDetails, firstRun } from "./AgentDetails";
import { AgentLenses, type AgentLensesProps, NO_AGENT_OF_YOURS } from "./AgentLenses";
import { Legend, panToShow, startViewport } from "./AgentGraphLens";
import { AgentsPhone, type AgentsPhoneProps } from "./AgentsPhone";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const lenses = (over: Partial<AgentLensesProps> = {}) =>
  renderToStaticMarkup(
    createElement(AgentLenses, {
      read: { kind: "ready", map: fleet() },
      onOpenTask: () => {},
      emptyCopy: "Nothing here.",
      nowMs: NOW,
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
/** The markup inside the toolbar: after its own tag, up to the tag that opens the map. */
const toolbar = (out: string) =>
  out.slice(out.indexOf(">", out.indexOf('role="toolbar"')) + 1, out.lastIndexOf("<", out.indexOf('data-agent-graph=""')));
/** The accessible names and the words of the controls of a piece of markup. */
const words = (html: string) => html.replace(/<[^>]*>/g, "|").split("|").map((w) => w.trim()).filter(Boolean);

describe("the toolbar (AMR-5)", () => {
  it("holds People, Active or All, and Find, and no other control", () => {
    const out = toolbar(lenses());
    expect(words(out)).toEqual(["Everyone", "Active", "All"]);
    expect(out).toContain('aria-label="Agents shown"');
    expect(out).toContain('aria-label="Find a session, task or subagent"');
    expect(out).toContain("People");
    // The People button, the two sides of the switch, and the find box.
    expect(out.split("<button").length - 1).toBe(3);
    expect(out.split("<input").length - 1).toBe(1);
  });
  it("starts on Active", () => {
    expect(toolbar(lenses())).toMatch(/aria-pressed="true"[^>]*title="Needs you, failed, working and waiting"|title="Needs you, failed, working and waiting"[^>]*aria-pressed="true"/);
  });
  it("draws no lens switch, no state chip, no breadcrumb and no Waits-on toggle", () => {
    const out = lenses();
    for (const gone of [">Board<", ">Graph<", ">List<", "data-agent-chip", "data-agent-crumbs", "Waits on<", "Fold all", "All agents", "data-agent-overview"]) {
      expect(out).not.toContain(gone);
    }
  });
  // The questions arrive after a read, so the server render holds no pager.
  it("shows Needs you N, with back and next, only when a question waits", () => {
    expect(lenses()).not.toContain("data-agent-question-pager");
    const source = readFileSync(new URL("./AgentLenses.tsx", import.meta.url), "utf-8");
    expect(source).toContain("{stops.length > 0 ? (");
    expect(source).toContain("{NEEDS_YOU} {stops.length}");
    for (const way of ["goQuestion(-1)", "goQuestion(1)"]) expect(source).toContain(way);
  });
  it("opens the legend from one button, and keeps it shut at the start", () => {
    const source = readFileSync(new URL("./AgentGraphLens.tsx", import.meta.url), "utf-8");
    expect(source).toContain("{legend ? <Legend /> : null}");
    expect(source).toContain("useState(false);\n  const [hover");
    expect(typeof Legend).toBe("function");
  });
});

describe("the map on the canvas", () => {
  it("draws the one map for each state of the switch", () => {
    for (const initialMode of ["active", "all"] as const) {
      const out = lenses({ initialMode });
      expect(out).toContain('data-agent-graph=""');
      expect(out).toContain('data-agent-lenses="map"');
    }
  });
  it("draws the empty words for a map with no session", () => {
    expect(lenses({ read: { kind: "ready", map: mapOf([]) } })).toContain("Nothing here.");
  });
  it("says that no agent is yours for Me with none", () => {
    const out = lenses({ viewerEmail: "nobody@x.test", initialPerson: "me" });
    expect(out).toContain(NO_AGENT_OF_YOURS);
    expect(out).not.toContain('data-agent-graph=""');
  });
  it("draws only my agents for Me, and nobody for Me with no address", () => {
    expect(lenses({ viewerEmail: "ben@x.test", initialPerson: "me" })).toContain('data-agent-graph=""');
    expect(lenses({ viewerEmail: "", initialPerson: "me" })).toContain(NO_AGENT_OF_YOURS);
  });
  it("opens a session of the details through the way to its card", () => {
    const source = readFileSync(new URL("./AgentLenses.tsx", import.meta.url), "utf-8");
    expect(source).toContain('onSelect={(next) => (next.kind === "session" ? goToSession(next.id) : setSelection(next))}');
  });
  it("draws untrusted names as text", () => {
    const map = fleet();
    map.sessions[0].name = "<img src=x onerror=1>";
    expect(lenses({ read: { kind: "ready", map }, phone: true })).not.toContain("<img src=x");
  });
  it("reads no session on the first draw", () => {
    const readSession = vi.fn();
    lenses({ readSession, read: { kind: "ready", map: splitFleet() } });
    expect(readSession).not.toHaveBeenCalled();
  });
  it("builds and draws 500 agents within two seconds on a loaded box", () => {
    const from = performance.now();
    lenses({ read: { kind: "ready", map: workingFleet(500) } });
    // A server render. The browser time (under 1 s) is in the AMR-5 captures.
    expect(performance.now() - from).toBeLessThan(2000);
  });
});

describe("the phone view", () => {
  it("never draws the map or the toolbar", () => {
    const out = lenses({ phone: true });
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
  it("reads Waiting for them, and no card needs the viewer", () => {
    const out = lenses({ viewerEmail: "ana@x.test", phone: true });
    expect(out).not.toContain('data-agent-square="you"');
    expect(lenses({ phone: true })).toContain('data-agent-square="you"');
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
    expect(details("ana@x.test")).toContain("Waiting for Ben");
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
