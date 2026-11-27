import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { type SubRow, buildLensModel } from "../lib/agentLenses";
import { BEN, bigFleet, session, mapOf, run } from "../lib/agentLensesFixtures";
import { overviewTiles } from "../lib/agentScale";
import { AgentLenses, type AgentLensesProps } from "./AgentLenses";
import { AgentOverview } from "./AgentOverview";
import { AgentsPhone } from "./AgentsPhone";
import { AgentSubagents, subLines } from "./AgentSubagents";

const NOW = Date.parse("2026-11-26T12:00:00Z");
const map = bigFleet(500);
const lenses = (over: Partial<AgentLensesProps> = {}) =>
  renderToStaticMarkup(
    createElement(AgentLenses, {
      read: { kind: "ready", map },
      onOpenTask: () => {},
      emptyCopy: "Nothing here.",
      nowMs: NOW,
      phone: false,
      viewerEmail: BEN.email,
      ...over,
    }),
  );
const count = (text: string, needle: string) => text.split(needle).length - 1;

describe("a fleet of 500 sessions", () => {
  it("draws the overview on the Graph and the Board: tiles, no session card", () => {
    for (const initialLens of ["graph", "board"] as const) {
      const out = lenses({ initialLens });
      expect(out).toContain("data-agent-overview");
      expect(count(out, "data-agent-tile=")).toBe(14);
      expect(out).toContain("1 quiet orchestrator");
      expect(out).not.toContain("data-agent-session=");
      expect(out).toContain("500 sessions");
      expect(out).toContain("All agents");
    }
  });
  it("opens the quiet row for a find", () => {
    const model = buildLensModel(map, new Map(), BEN);
    const people = overviewTiles(model, { person: "", find: "" }, (e) => e);
    const draw = (finding: boolean) =>
      renderToStaticMarkup(
        createElement(AgentOverview, {
          people, nowMs: NOW, nameOf: (e: string) => e, onOpenTile: () => {}, onPickSession: () => {}, onOpenPerson: () => {}, finding,
        }),
      );
    expect(count(draw(false), "data-agent-tile=")).toBe(14);
    expect(count(draw(true), "data-agent-tile=")).toBe(15);
  });
  it("draws one orchestrator bounded on the Board, with its buckets", () => {
    const out = lenses({ initialLens: "board", initialGroup: "ben@x.test|o1" });
    // The orchestrator and 12 sessions.
    expect(count(out, "data-agent-session=")).toBe(13);
    expect(out).toContain("data-agent-bucket");
    expect(out).toContain("4 subagents");
    expect(out).toContain("data-agent-crumb");
  });
  it("draws only the rows on screen in the List", () => {
    const out = lenses({ initialLens: "list" });
    expect(count(out, "data-agent-list-row")).toBeLessThan(50);
    expect(count(out, "data-agent-list-row")).toBeGreaterThan(5);
  });
  it("reads no session on the first draw", () => {
    const readSession = vi.fn();
    lenses({ readSession });
    expect(readSession).not.toHaveBeenCalled();
  });
  it("builds and draws the first overview within two seconds on a loaded box", () => {
    const from = performance.now();
    lenses();
    // A server render of the markup. The browser time is in the AMR-4 captures.
    expect(performance.now() - from).toBeLessThan(2000);
  });
});

describe("a fleet of 40 sessions", () => {
  it("keeps the board of AMR-2, with the subagents as a count", () => {
    const out = lenses({ read: { kind: "ready", map: bigFleet(40) }, initialLens: "board" });
    expect(out).not.toContain("data-agent-overview");
    expect(out).toContain("data-agent-session=");
    expect(out).toContain("4 subagents");
  });
});

describe("the phone at scale", () => {
  const model = buildLensModel(map, new Map(), BEN);
  const phone = (initialPath: Parameters<typeof AgentsPhone>[0]["initialPath"]) =>
    renderToStaticMarkup(
      createElement(AgentsPhone, {
        model, nowMs: NOW, nameOf: (e: string) => e, onOpenSession: () => {}, onOpenTask: () => {},
        onOpenProject: () => {}, questionsOn: false, initialPath,
      }),
    );
  it("draws people as rows with squares on the first screen, and no card", () => {
    const out = phone([]);
    expect(count(out, "data-agent-phone-row")).toBe(3);
    expect(out).not.toContain("data-agent-session=");
  });
  it("draws ten cards of a state, then Show more", () => {
    const out = phone([{ kind: "state", state: "working" }]);
    expect(count(out, "data-agent-session=")).toBe(10);
    expect(out).toContain("data-agent-phone-more");
  });
  it("draws the sections of an orchestrator, with idle and done as rows", () => {
    const out = phone([{ kind: "group", person: "ben@x.test", key: "ben@x.test|o1" }]);
    for (const state of ["you", "failed", "working", "waiting"]) expect(out).toContain(`data-agent-phone-section="${state}"`);
    expect(out).not.toContain('data-agent-phone-section="done"');
    expect(out).toMatch(/aria-label="\d+ done"/);
  });
});

describe("every subagent of one session", () => {
  const rows: SubRow[] = Array.from({ length: 200 }, (_, i) => ({
    id: `c${i}`, name: `<img src=x ${i}>`, state: i < 2 ? "failed" : i < 5 ? "working" : "done", model: null, age: i,
  }));
  const draw = (list: SubRow[] | null, failed = false) =>
    renderToStaticMarkup(
      createElement(AgentSubagents, { counts: { total: 200, working: 3, failed: 2, done: 195 }, rows: list, failed, nowMs: NOW }),
    );
  it("groups by state and folds the done ones", () => {
    const lines = subLines(rows, false);
    expect(lines.filter((l) => l.kind === "row")).toHaveLength(5);
    expect(lines.at(-1)).toMatchObject({ kind: "head", state: "done", folded: true });
    const out = draw(rows);
    expect(out).toContain("195 done · Show");
    expect(out).not.toContain("<img src=x");
  });
  it("draws only the rows on screen when the list is long", () => {
    expect(subLines(rows, true).length).toBeGreaterThan(200);
    const many: SubRow[] = rows.map((r) => ({ ...r, state: "working" }));
    const out = draw(many);
    expect(out).toContain("data-agent-sub-window");
    expect(count(out, "data-agent-detail-child")).toBeLessThan(40);
  });
  it("says that it loads, and says when the read failed", () => {
    expect(draw(null)).toContain('role="status"');
    expect(draw(null, true)).toContain("The subagents did not load.");
  });
  it("draws one session of the summary with its count in the details", () => {
    const one = mapOf([session("a", { subagents: { total: 3, working: 1, failed: 0, done: 2, names: [] } })], [run("r", "a")]);
    expect(buildLensModel(one).sessions[0].subs.total).toBe(3);
  });
});
