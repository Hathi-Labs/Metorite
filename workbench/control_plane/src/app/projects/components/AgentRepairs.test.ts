/**
 * The repairs of the AMR-4 review (task 4023, `agent_coordination.md`
 * §3.12.9). Each test fails without its repair.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AGENT_STATES, agentStateChip, agentStateVisual } from "@/lib/statusAccent";

import {
  buildLensModel,
  lensChip,
  lensIcon,
  lensRun,
  lensWord,
  matchesFind,
} from "../lib/agentLenses";
import { mapOf, run, session } from "../lib/agentLensesFixtures";
import { parseAgentMap, runStateChip } from "../lib/agentMap";
import { type ReadCache, putRead, readsOf } from "../lib/agentSessionReads";
import { outcomeChip, outcomeIcon } from "../lib/agentsAtWork";
import { pageView, remWindow, windowRange } from "../lib/windowedList";
import { AgentDetails } from "./AgentDetails";
import { SessionCard } from "./AgentSessionCard";
import { AgentsPhone } from "./AgentsPhone";

/** The applied root font size of the test. Density changes it. */
const root = vi.hoisted(() => ({ px: 16 }));
vi.mock("@/lib/rootRem", () => ({ rootRemPx: () => root.px }));
afterEach(() => {
  root.px = 16;
});

const NOW = Date.parse("2026-11-26T12:00:00Z");
const nameOf = (email: string) => email;
const count = (text: string, needle: string) => text.split(needle).length - 1;

describe("3 · the windowed list at each density", () => {
  const rows = Array.from({ length: 500 }, () => 2.75);
  for (const scale of [0.92, 1, 1.08]) {
    it(`covers the view on a deep scroll at ${scale}`, () => {
      const px = 16 * scale;
      const viewport = 800;
      for (const row of [120, 300, 470]) {
        const scrollTop = row * 2.75 * px;
        const range = remWindow(rows, scrollTop, viewport, px);
        // The rows that the browser draws start at the padding and reach past the view.
        expect(range.before * px).toBeLessThanOrEqual(scrollTop);
        const bottom = (range.before + (range.end - range.start) * 2.75) * px;
        expect(bottom).toBeGreaterThanOrEqual(Math.min(scrollTop + viewport, 500 * 2.75 * px) - 0.001);
        expect(range.before + range.after + (range.end - range.start) * 2.75).toBeCloseTo(500 * 2.75, 6);
      }
    });
  }
  it("is wrong with 16 px for a rem, which is what the repair removes", () => {
    const scrollTop = 400 * 2.75 * 16 * 0.92;
    const at16 = windowRange(rows.map((h) => h * 16), scrollTop, 800);
    // The drawn rows end above the view: a blank list.
    expect((at16.before / 16 + (at16.end - at16.start) * 2.75) * 16 * 0.92).toBeLessThan(scrollTop);
  });
  it("reads a list that the page scrolls from the two tops", () => {
    expect(pageView(500, 100, 700)).toEqual({ top: 0, height: 700 });
    expect(pageView(-900, 100, 700)).toEqual({ top: 1000, height: 700 });
  });
});

describe("5 · the phone cards and the task rows are windowed", () => {
  const model = buildLensModel(mapOf(Array.from({ length: 150 }, (_, i) => session(`s${i}`))));
  const phone = (shown: number) =>
    renderToStaticMarkup(
      createElement(AgentsPhone, {
        model, nowMs: NOW, nameOf, onOpenSession: () => {}, onOpenTask: () => {}, onOpenProject: () => {},
        questionsOn: false, initialPath: [{ kind: "state", state: "working" }], initialPages: { ":working": shown },
      }),
    );
  it("draws only the cards on screen after four Show more taps", () => {
    const out = phone(90);
    const cards = count(out, "data-agent-session=");
    expect(cards).toBeGreaterThan(5);
    expect(cards).toBeLessThan(50);
    expect(out).toContain("data-agent-phone-window");
    expect(out).toMatch(/padding-bottom:[0-9.]+rem/);
    expect(out).toContain("Show 20 more of 60");
  });
  it("draws each card of a list of 50", () => {
    expect(count(phone(50), "data-agent-session=")).toBe(50);
  });
  it("draws only the task rows on screen for a session of 300 runs", () => {
    const one = mapOf([session("a")], [run("r0", "a")]);
    const runs = mapOf(
      [session("a")],
      Array.from({ length: 300 }, (_, i) => run(`r${i}`, "a", { task_number: i, age: i })),
    ).runs;
    const out = renderToStaticMarkup(
      createElement(AgentDetails, {
        model: buildLensModel(one), selection: { kind: "session", id: "a" }, onSelect: () => {},
        onOpenTask: () => {}, nameOf, nowMs: NOW, read: { kind: "ready", runs, children: [] },
      }),
    );
    const drawn = count(out, "data-agent-task-row");
    expect(drawn).toBeGreaterThan(5);
    expect(drawn).toBeLessThan(50);
    expect(out).toContain("data-agent-task-window");
  });
});

describe("6 · the subagent count leaves the line to the task", () => {
  const [s] = buildLensModel(
    mapOf(
      [session("a", { subagents: { total: 7, working: 2, failed: 1, done: 4, names: [] } })],
      [run("r", "a", { title: "A task title that needs its room" })],
    ),
  ).sessions;
  /** The words that a reader sees: the markup with no tag. */
  const seen = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  for (const toggle of [false, true]) {
    it(`shows a short count and says the full one (${toggle ? "graph" : "card"})`, () => {
      const out = renderToStaticMarkup(
        createElement(SessionCard, { session: s, nowMs: NOW, onOpen: () => {}, onToggleSubs: toggle ? () => {} : undefined }),
      );
      expect(seen(out)).toContain("7 · 2 working");
      expect(seen(out)).not.toContain("subagents");
      expect(seen(out)).not.toContain("failed");
      expect(out).toContain("data-agent-sub-failed");
      // The accessible name and the tip hold the full words.
      expect(out).toContain("7 subagents · 2 working · 1 failed.");
      expect(out).toContain('title="7 subagents · 2 working · 1 failed"');
      expect(out).toContain("max-w-[55%]");
    });
  }
});

describe("7 · the old chips keep their look", () => {
  it("draws a completed run gray outside the Agents lenses", () => {
    expect(agentStateVisual("completed")).toBeNull();
    expect(Object.keys(AGENT_STATES)).not.toContain("completed");
    expect(outcomeChip("completed")).toBe(agentStateChip(null));
    expect(outcomeChip("completed")).toBe(outcomeChip("released"));
    expect(outcomeIcon("completed")).toBeNull();
    expect(runStateChip({ state: "completed", quiet: false })).toBe(agentStateChip(null));
  });
  it("draws Done green with its check in the Agents lenses, from the one vocabulary", () => {
    expect(lensWord("done")).toBe("Done");
    expect(lensIcon("done")).toBe("CircleCheck");
    expect(lensChip("done")).not.toBe(agentStateChip(null));
    expect(lensRun({ state: "completed", quiet: false })).toEqual({ word: "Done", chip: lensChip("done") });
  });
});

describe("8 · the session reads belong to one project", () => {
  const ready = { kind: "ready" as const, runs: [], children: [] };
  it("gives no read of another scope, and starts a new cache for it", () => {
    let cache: ReadCache = putRead({ scope: "p1", reads: new Map() }, "p1", "node", ready);
    expect(readsOf(cache, "p1").get("node")).toBe(ready);
    expect(readsOf(cache, "p2").size).toBe(0);
    expect(readsOf(cache, "").size).toBe(0);
    // A failed read in the new scope never keeps the rows of the old one.
    cache = putRead(cache, "p2", "node", { kind: "error" });
    expect(readsOf(cache, "p2").get("node")).toEqual({ kind: "error" });
    expect(readsOf(cache, "p1").size).toBe(0);
  });
});

describe("4 · find reaches each task of a session", () => {
  const map = parseAgentMap({
    sessions: [
      session("a", {
        tasks: [
          { number: 7, title: "Task of a" },
          { number: 4512, title: "Rotate the signing key" },
          { number: null, title: "<b>No number</b>" },
          { number: 9, title: "" },
        ],
      }),
    ],
    runs: [run("r", "a")],
  })!;
  it("finds a task of the session that is not the lead one, by title and by number", () => {
    const [s] = buildLensModel(map).sessions;
    expect(s.tasks).toHaveLength(3);
    expect(matchesFind(s, "signing KEY", "Ana")).toBe(true);
    expect(matchesFind(s, "#4512", "Ana")).toBe(true);
    expect(matchesFind(s, "no number", "Ana")).toBe(true);
    expect(matchesFind(s, "#999", "Ana")).toBe(false);
  });
  it("reads no task words from a map that sends none", () => {
    expect(buildLensModel(mapOf([session("a")])).sessions[0].tasks).toEqual([]);
  });
});
