import { describe, expect, it } from "vitest";

import {
  DEFAULT_STATES,
  type LensFilters,
  buildLensModel,
  countStates,
  foldWord,
  lensChip,
  lensEdge,
  lensIcon,
  lensWord,
  sessionLine,
  splitSessions,
} from "./agentLenses";
import { BEN, fleet, mapOf, run, session } from "./agentLensesFixtures";
import { agentStateChip } from "@/lib/statusAccent";

const NOW = Date.parse("2026-11-26T12:00:00Z");

const all = (over: Partial<LensFilters> = {}): LensFilters => ({
  states: new Set(DEFAULT_STATES),
  person: "",
  find: "",
  ...over,
});
const name = (email: string) => email;

describe("the words of a state", () => {
  it("come from the one agent vocabulary", () => {
    expect((["you", "working", "waiting", "idle"] as const).map(lensWord)).toEqual([
      "Needs you",
      "Working",
      "Waiting",
      "Idle",
    ]);
    expect(lensChip("you")).toBe(agentStateChip("awaiting"));
    expect(lensIcon("waiting")).toBe("Hourglass");
  });
  it("give each state its card edge", () => {
    expect((["working", "waiting", "you", "idle"] as const).map(lensEdge)).toEqual([
      "live",
      "steady",
      "warning",
      "breathing",
    ]);
  });
});

describe("buildLensModel", () => {
  const model = buildLensModel(fleet(), new Map(), BEN);
  const ana = model.people.find((p) => p.person === "ana@x.test")!;
  const ben = model.people.find((p) => p.person === "ben@x.test")!;

  it("puts a chain of one person under its orchestrator, and the orchestrator not among them", () => {
    const group = ana.groups.find((g) => g.orch?.id === "boss")!;
    expect(group.sessions.map((s) => s.id).sort()).toEqual(["lane-a", "sub"]);
    expect(ana.groups.find((g) => !g.orch)!.sessions.map((s) => s.id)).toEqual(["solo"]);
  });
  it("keeps a lane of another person under that person, with its dispatcher", () => {
    expect(ben.groups).toHaveLength(1);
    expect(ben.groups[0].orch).toBeNull();
    expect(ben.groups[0].sessions[0].dispatchers).toEqual(["boss"]);
  });
  it("sorts the person who waits on somebody first", () => {
    expect(model.people.map((p) => p.person)).toEqual(["ben@x.test", "ana@x.test"]);
  });
  it("reads the wait from the structured field only", () => {
    expect(model.byId.get("lane-a")!.wait).toEqual({ on: "ci", until: "2026-11-26T12:25:00Z" });
    const bare = buildLensModel(mapOf([session("w", { state: "waiting" })], [run("r", "w", { state: "waiting" })]));
    expect(bare.byId.get("w")!.wait).toBeNull();
    const wrong = buildLensModel(
      mapOf([session("w", { state: "waiting" })], [run("r", "w", { state: "waiting", waiting_on: "lunch" })]),
    );
    expect(wrong.byId.get("w")!.wait).toBeNull();
  });
  it("makes a quiet orchestrator with a live lane wait (SW-109)", () => {
    const m = buildLensModel(
      mapOf([session("boss", { state: "quiet" }), session("lane")], [], [{ from: "boss", to: "lane" }]),
    );
    expect(m.byId.get("boss")!.state).toBe("waiting");
  });
  it("does not loop on lines that make a loop", () => {
    const m = buildLensModel(
      mapOf([session("a"), session("b")], [], [{ from: "a", to: "b" }, { from: "b", to: "a" }]),
    );
    expect(m.people[0].groups.flatMap((g) => g.sessions)).toHaveLength(1);
  });
  it("counts the sessions of each state", () => {
    expect(countStates(model.sessions)).toEqual({ you: 1, failed: 0, working: 1, waiting: 1, idle: 2, done: 0 });
  });
});

describe("who needs you (EA3-50)", () => {
  const labels = new Map([["ben@x.test", "Ben Rao"]]);
  it("is the member of the session only, in any letter case", () => {
    const own = buildLensModel(fleet(), new Map(), { email: "BEN@x.test", labels });
    expect(own.byId.get("lane-b")).toMatchObject({ state: "you", askedOf: null });
  });
  it("reads Waiting for the name to everyone else, and counts no need", () => {
    const other = buildLensModel(fleet(), new Map(), { email: "ana@x.test", labels });
    const lane = other.byId.get("lane-b")!;
    expect(lane).toMatchObject({ state: "waiting", wait: { on: "member", until: null } });
    expect(sessionLine(lane, NOW)).toBe("Waiting for Ben");
    expect(countStates(other.sessions).you).toBe(0);
  });
  it("is nobody with no viewer", () => {
    expect(countStates(buildLensModel(fleet()).sessions).you).toBe(0);
  });
});

describe("the line of a card", () => {
  it("is the question, else the wait, else the task", () => {
    const asked = buildLensModel(fleet(), new Map([["r3", "May PR 12 merge?"]]), BEN);
    expect(sessionLine(asked.byId.get("lane-b")!, NOW)).toBe("May PR 12 merge?");
    expect(sessionLine(asked.byId.get("lane-a")!, NOW)).toBe("Waiting on CI · about 25 min");
    expect(sessionLine(asked.byId.get("boss")!, NOW)).toBe("#7 Task of boss");
  });
  it("says the quiet runs of a session with no listed run", () => {
    const m = buildLensModel(mapOf([session("s", { state: "quiet", quiet_count: 3 })]));
    expect(sessionLine(m.byId.get("s")!, NOW)).toBe("3 quiet runs");
  });
});

describe("splitSessions", () => {
  const sessions = buildLensModel(fleet(), new Map(), BEN).sessions;
  it("folds the states whose chip is off, and says idle", () => {
    const split = splitSessions(sessions, all(), false, name);
    expect(split.folded.map((s) => s.id).sort()).toEqual(["solo", "sub"]);
    expect(foldWord(split.folded)).toBe("2 idle sessions");
  });
  it("opens a fold", () => {
    expect(splitSessions(sessions, all(), true, name).folded).toEqual([]);
  });
  it("shows each match of a find, a folded one too", () => {
    const split = splitSessions(sessions, all({ find: "solo" }), false, name);
    expect(split.shown.map((s) => s.id)).toEqual(["solo"]);
    expect(split.folded).toEqual([]);
  });
  it("finds by the task number and by the name of the person", () => {
    expect(splitSessions(sessions, all({ find: "#7" }), false, name).shown).toHaveLength(3);
    expect(splitSessions(sessions, all({ find: "benny" }), false, () => "Benny").shown).toHaveLength(5);
  });
});
