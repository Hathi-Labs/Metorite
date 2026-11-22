import { describe, expect, it } from "vitest";

import {
  type AgentMap,
  agentMapOutline,
  layoutAgentMap,
  parseAgentMap,
  quietLine,
} from "./agentMap";

const NOW = Date.parse("2026-11-21T12:00:00Z");

const session = (over: Record<string, unknown> = {}) => ({
  id: "s1",
  person: "ana@example.com",
  name: "Fix login",
  client: "Claude Code",
  state: "working",
  last_at: "2026-11-21T11:50:00Z",
  run_count: 1,
  quiet_count: 0,
  quiet_last_at: null,
  ...over,
});
const run = (over: Record<string, unknown> = {}) => ({
  run_id: "r1",
  session_id: "s1",
  task_id: "t1",
  task_number: 7,
  title: "Fix the login",
  state: "working",
  quiet: false,
  age: 60,
  last_at: "2026-11-21T11:55:00Z",
  ...over,
});
const answer = (sessions: unknown[], runs: unknown[], lines: unknown[] = []) => ({
  people: [],
  sessions,
  runs,
  lines,
  count: runs.length,
  truncated: false,
  viewer: "admin",
});
const parse = (sessions: unknown[], runs: unknown[], lines: unknown[] = []): AgentMap =>
  parseAgentMap(answer(sessions, runs, lines)) as AgentMap;

function overlaps(a: { x: number; y: number; w: number; h: number }, b: typeof a): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}
function expectNoOverlap(map: AgentMap) {
  const { nodes } = layoutAgentMap(map, NOW);
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      expect(overlaps(nodes[i], nodes[j]), `${nodes[i].id} and ${nodes[j].id}`).toBe(false);
    }
  }
}

describe("parseAgentMap", () => {
  it("drops rows it cannot read, and unknown states", () => {
    const map = parse(
      [session(), { nope: 1 }, session({ id: "s2", state: "dancing" }), session({ id: "s1" })],
      [run(), { nope: 1 }, run({ run_id: "r2", session_id: "ghost" })],
    );
    expect(map.sessions.map((s) => s.id)).toEqual(["s1"]);
    expect(map.runs.map((r) => r.run_id)).toEqual(["r1"]);
  });
  it("refuses a body with no sessions or runs list", () => {
    expect(parseAgentMap(null)).toBeNull();
    expect(parseAgentMap({ sessions: [] })).toBeNull();
    expect(parseAgentMap({ agents: [] })).toBeNull();
  });
  it("keeps untrusted text as it is, and accepts unknown lines", () => {
    const map = parse([session({ name: "<b>x</b>" })], [run()], [{ from: "s1" }, { from: "a", to: "b" }, 5]);
    expect(map.sessions[0].name).toBe("<b>x</b>");
    expect(map.lines).toEqual([{ from: "a", to: "b" }]);
  });
});

describe("layoutAgentMap", () => {
  it("is empty for no people", () => {
    expect(layoutAgentMap(parse([], []), NOW)).toEqual({ nodes: [], edges: [] });
  });
  it("is deterministic", () => {
    const map = parse(
      [session({ id: "b", name: "B" }), session({ id: "a", name: "A", person: "zed@example.com" })],
      [run({ session_id: "a" }), run({ run_id: "r2", session_id: "b" })],
    );
    expect(layoutAgentMap(map, NOW)).toEqual(layoutAgentMap(map, NOW));
  });
  it("puts person, session and task in three columns from left to right", () => {
    const { nodes } = layoutAgentMap(parse([session()], [run()]), NOW);
    const x = (kind: string) => nodes.find((n) => n.kind === kind)!.x;
    expect(x("person")).toBeLessThan(x("session"));
    expect(x("session")).toBeLessThan(x("task"));
  });
  it("sorts waiting first, then working, then quiet, then by name and id", () => {
    const map = parse(
      [
        session({ id: "q", name: "A", state: "quiet" }),
        session({ id: "w2", name: "B", state: "working" }),
        session({ id: "w1", name: "B", state: "working" }),
        session({ id: "p", name: "Z", state: "waiting_on_person" }),
        session({ id: "a", name: "Y", state: "waiting" }),
      ],
      [],
    );
    const ids = layoutAgentMap(map, NOW)
      .nodes.filter((n) => n.kind === "session")
      .sort((a, b) => a.y - b.y)
      .map((n) => n.ref);
    expect(ids).toEqual(["p", "a", "w1", "w2", "q"]);
  });
  it("folds unlisted quiet runs into one line under the session", () => {
    const map = parse(
      [session({ quiet_count: 4, quiet_last_at: "2026-11-21T09:00:00Z" })],
      [run(), run({ run_id: "r2", quiet: true })],
    );
    const quiet = layoutAgentMap(map, NOW).nodes.filter((n) => n.kind === "quiet");
    expect(quiet).toHaveLength(1);
    expect(quiet[0].label).toBe("3 quiet runs, newest 3h ago");
    expectNoOverlap(map);
  });
  it("draws no quiet line when every quiet run is listed", () => {
    const map = parse([session({ quiet_count: 1 })], [run({ quiet: true })]);
    expect(layoutAgentMap(map, NOW).nodes.some((n) => n.kind === "quiet")).toBe(false);
  });
  it("draws a line only between two sessions of the map", () => {
    const map = parse([session(), session({ id: "s2", name: "Other" })], [], [
      { from: "s1", to: "s2" },
      { from: "s1", to: "gone" },
    ]);
    expect(layoutAgentMap(map, NOW).edges.filter((e) => e.kind === "line")).toHaveLength(1);
  });
  it("never overlaps two nodes", () => {
    const many = Array.from({ length: 30 }, (_, i) => session({ id: `s${i}`, name: `Session ${i}` }));
    const runs = Array.from({ length: 90 }, (_, i) =>
      run({ run_id: `r${i}`, session_id: `s${i % 30}`, title: "Very long title ".repeat(20) }),
    );
    expectNoOverlap(parse(many, runs));
    expectNoOverlap(
      parse(
        [session({ id: "a" }), session({ id: "b", person: "bo@example.com", quiet_count: 2 })],
        [run({ session_id: "a" }), run({ run_id: "r2", session_id: "a" }), run({ run_id: "r3", session_id: "b" })],
      ),
    );
    expectNoOverlap(parse([], []));
  });
});

describe("quietLine", () => {
  it("uses the singular for one run", () => {
    expect(quietLine(1, null, NOW)).toBe("1 quiet run");
  });
});

describe("agentMapOutline", () => {
  it("nests person, sessions and tasks", () => {
    const map = parse(
      [session({ quiet_count: 2, quiet_last_at: "2026-11-21T10:00:00Z" })],
      [run()],
    );
    const outline = agentMapOutline(map, NOW);
    expect(outline).toHaveLength(1);
    expect(outline[0].person).toBe("ana@example.com");
    expect(outline[0].sessions[0].tasks.map((t) => t.title)).toEqual(["Fix the login"]);
    expect(outline[0].sessions[0].quiet).toBe("2 quiet runs, newest 2h ago");
    expect(outline[0].sessions[0].word).toBe("Working");
  });
});
