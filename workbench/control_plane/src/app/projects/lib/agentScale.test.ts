import { describe, expect, it } from "vitest";

import { AGENT_DONE_KEY, AGENT_MARKS, agentStateVisual } from "@/lib/statusAccent";

import {
  LENS_STATES,
  buildLensModel,
  lensMark,
  lensOfChild,
  lensWord,
  matchesFind,
  subRows,
  subWords,
} from "./agentLenses";
import { BEN, fleet, mapOf, run, session } from "./agentLensesFixtures";
import { parseAgentMap } from "./agentMap";
import {
  questionStops,
  stepStop,
} from "./agentScale";
import { keepReady, sessionRead, wantedSessions } from "./agentSessionReads";
import { agentMapQuery } from "./api";
import { windowRange } from "./windowedList";

describe("the states of Failed and Done", () => {
  it("takes the words, the icons and the marks from the one vocabulary", () => {
    expect(lensWord("failed")).toBe("Failed");
    expect(lensWord("done")).toBe("Done");
    expect(agentStateVisual(AGENT_DONE_KEY)).toMatchObject({ hue: "green", icon: "CircleCheck" });
    for (const state of LENS_STATES) expect(Object.values(AGENT_MARKS)).toContain(lensMark(state));
  });
  it("reads a failed and a done session of the summary", () => {
    const map = mapOf([session("a", { state: "failed" }), session("b", { state: "done" })]);
    expect(buildLensModel(map).sessions.map((s) => s.state)).toEqual(["failed", "done"]);
  });
  it("keeps a quiet orchestrator quiet when its lanes are all ended", () => {
    const map = mapOf(
      [session("boss", { state: "quiet" }), session("a", { state: "done" }), session("b", { state: "failed" })],
      [],
      [
        { from: "boss", to: "a" },
        { from: "boss", to: "b" },
      ],
    );
    expect(buildLensModel(map).byId.get("boss")?.state).toBe("idle");
  });
});

describe("the subagents are a count", () => {
  const summary = parseAgentMap({
    sessions: [session("a", { subagents: { total: 7, working: 2, failed: 1, done: 4, names: ["verifier", ""] } })],
    runs: [run("r", "a")],
  })!;
  it("reads the counts and the names of the summary, with no child row", () => {
    const [s] = buildLensModel(summary).sessions;
    expect(s.subs).toEqual({ total: 7, working: 2, failed: 1, done: 4 });
    expect(s.subNames).toEqual(["verifier"]);
    expect(s.children).toEqual([]);
    expect(subWords(s.subs)).toBe("7 subagents · 2 working · 1 failed");
    expect(subWords({ total: 1, working: 0, failed: 0, done: 1 })).toBe("1 subagent");
    expect(subWords({ total: 0, working: 0, failed: 0, done: 0 })).toBe("");
  });
  it("lets the find box reach a subagent", () => {
    const [s] = buildLensModel(summary).sessions;
    expect(matchesFind(s, "VERIF", "Ana")).toBe(true);
    expect(matchesFind(s, "nobody", "Ana")).toBe(false);
  });
  it("counts the listed children of an older map", () => {
    const map = parseAgentMap({
      sessions: [session("a")],
      runs: [run("r", "a")],
      children: [{ child_id: "c", run_id: "r", session_id: "a", name: "x", state: "working" }],
    })!;
    expect(buildLensModel(map).sessions[0].subs).toEqual({ total: 1, working: 1, failed: 0, done: 0 });
  });
  it("sorts the rows of a session read by attention", () => {
    expect(["completed", "failed", "working", "stopped"].map(lensOfChild)).toEqual(["done", "failed", "working", "idle"]);
    const child = (child_id: string, state: string, age: number) => ({
      child_id, run_id: "r", session_id: "a", name: child_id, state, model: null, age, last_at: null,
    });
    const rows = subRows([child("d", "completed", 1), child("w2", "working", 9), child("f", "failed", 5), child("w1", "working", 2)]);
    expect(rows.map((r) => r.id)).toEqual(["f", "w1", "w2", "d"]);
  });
});

describe("the questions, one at a time", () => {
  it("walks the sessions that need the member, oldest first, once each", () => {
    const map = mapOf(
      [session("a", { state: "waiting_on_person" }), session("b", { state: "waiting_on_person" }), session("c")],
      [run("ra", "a", { state: "awaiting" }), run("rb", "b", { state: "awaiting" }), run("rb2", "b", { state: "awaiting" }), run("rc", "c")],
    );
    const model = buildLensModel(map, new Map(), { email: "ana@x.test" });
    expect(questionStops(model, ["rb", "gone", "rc", "ra", "rb2"])).toEqual(["b", "a"]);
    expect(questionStops(buildLensModel(map, new Map(), BEN), ["rb"])).toEqual([]);
    expect(stepStop(0, -1, 5)).toBe(4);
    expect(stepStop(4, 1, 5)).toBe(0);
    expect(stepStop(0, 1, 0)).toBe(0);
  });
});

describe("the reads at scale", () => {
  it("asks for the summary, and for one session by its node", () => {
    expect(agentMapQuery({ summary: true, projectId: "p", showQuiet: true })).toBe("shape=map&project_id=p&detail=summary");
    expect(agentMapQuery({ session: "abc", summary: true })).toBe("shape=map&session=abc");
    expect(agentMapQuery({ showQuiet: true })).toBe("shape=map&show_quiet=true");
  });
  it("reads no session until one opens, and each open one once", () => {
    expect(wantedSessions(null, undefined)).toEqual([]);
    expect(wantedSessions("a", "a")).toEqual(["a"]);
    expect(wantedSessions("b", "a")).toEqual(["a", "b"]);
  });
  it("takes only the rows of that session, and keeps them over a failed re-read", () => {
    const map = fleet();
    const read = sessionRead(map, "boss");
    expect(read.kind === "ready" && read.runs.map((r) => r.run_id)).toEqual(["r1"]);
    expect(sessionRead(null, "boss")).toEqual({ kind: "error" });
    expect(keepReady(read, { kind: "error" })).toBe(read);
    expect(keepReady(undefined, { kind: "error" })).toEqual({ kind: "error" });
  });
});

describe("the windowed list", () => {
  const heights = Array.from({ length: 500 }, () => 40);
  it("draws the rows in view and a few more", () => {
    expect(windowRange(heights, 0, 400, 2)).toEqual({ start: 0, end: 12, before: 0, after: 488 * 40 });
    const mid = windowRange(heights, 4000, 400, 2);
    expect([mid.start, mid.end, mid.before]).toEqual([98, 112, 98 * 40]);
  });
  it("holds the whole height whatever the scroll", () => {
    const end = windowRange(heights, 1e9, 400);
    expect(end.before + end.after + (end.end - end.start) * 40).toBe(500 * 40);
  });
});
