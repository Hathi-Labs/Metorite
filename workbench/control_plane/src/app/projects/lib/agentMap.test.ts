import { describe, expect, it } from "vitest";

import {
  type AgentMap,
  NODE_H,
  agentMapOutline,
  groupByMachine,
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

const BY_MACHINE = { groupByMachine: true } as const;

function overlaps(a: { x: number; y: number; w: number; h: number }, b: typeof a): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}
function expectNoOverlap(map: AgentMap, options = {}) {
  const { nodes } = layoutAgentMap(map, NOW, options);
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

/* ── SW-106: the machine and the model ───────────────────────────────────── */

const placed = (id: string, over: Record<string, unknown> = {}) =>
  session({ id, name: `Session ${id}`, ...over });

describe("parseAgentMap: the machine and the model", () => {
  it("reads the machine, its kind, its source and the model", () => {
    const map = parse(
      [session({ host: "metorite-home", host_kind: "server", host_source: "checked", model: "Sonnet 5.5" })],
      [],
    );
    expect(map.sessions[0]).toMatchObject({
      host: "metorite-home",
      host_kind: "server",
      host_source: "checked",
      model: "Sonnet 5.5",
    });
  });
  it("reads none as null, and drops each bad value", () => {
    const none = parse([session()], []).sessions[0];
    expect([none.host, none.host_kind, none.host_source, none.model]).toEqual([null, null, null, null]);
    const bad = parse(
      [session({ host: 7, host_kind: "laptop", host_source: "proved", model: { a: 1 } })],
      [],
    ).sessions[0];
    expect([bad.host, bad.host_kind, bad.host_source, bad.model]).toEqual([null, null, null, null]);
  });
  it("keeps the source only when there is a machine or a kind", () => {
    const s = parse([session({ host: null, host_kind: null, host_source: "checked", model: "X" })], []).sessions[0];
    expect(s.host_source).toBeNull();
  });
  it("keeps untrusted text as text, with its control characters out", () => {
    const s = parse([session({ host: "<img src=x>\u0007  box", model: "\nSonnet" })], []).sessions[0];
    expect(s.host).toBe("<img src=x> box");
    expect(s.model).toBe("Sonnet");
  });
  it("puts the place on the session node and the outline", () => {
    const map = parse([session({ host: "VJ laptop", host_kind: "computer", host_source: "reported", model: "Opus 5.5" })], []);
    const node = layoutAgentMap(map, NOW).nodes.find((n) => n.kind === "session")!;
    expect(node.place).toEqual({ host: "VJ laptop", kind: "computer", source: "reported", model: "Opus 5.5" });
    expect(agentMapOutline(map, NOW)[0].sessions[0].place.host).toBe("VJ laptop");
  });
  it("keeps the height of a session node fixed, with or without a place", () => {
    const withPlace = parse([session({ host: "x".repeat(300), model: "m".repeat(300), host_kind: "cloud" })], [run()]);
    const without = parse([session()], [run()]);
    for (const map of [withPlace, without]) {
      const node = layoutAgentMap(map, NOW).nodes.find((n) => n.kind === "session")!;
      expect(node.h).toBe(NODE_H.session);
    }
    expectNoOverlap(withPlace);
  });
});

describe("groupByMachine", () => {
  const sessions = parse(
    [
      placed("a", { host: "VJ laptop", host_kind: "computer", host_source: "reported" }),
      placed("b", { host: "metorite-home", host_kind: "server", host_source: "checked" }),
      placed("c", { host: "metorite-home", host_kind: "server", host_source: "checked" }),
      placed("d"),
      placed("e", { host: "Web sandbox", host_kind: "cloud", host_source: "reported" }),
      placed("f", { host: "box-2", host_kind: "server", host_source: "reported" }),
    ],
    [],
  ).sessions;
  it("groups by kind, then by name, with Not reported last", () => {
    const groups = groupByMachine(sessions);
    expect(groups.map((g) => g.place.host)).toEqual(["VJ laptop", "box-2", "metorite-home", "Web sandbox", null]);
    expect(groups[groups.length - 1].sessions.map((s) => s.id)).toEqual(["d"]);
    expect(groups.find((g) => g.place.host === "metorite-home")!.sessions.map((s) => s.id)).toEqual(["b", "c"]);
  });
  it("gives each group the one source of its sessions", () => {
    const mixed = parse(
      [
        placed("a", { host: "m", host_kind: "server", host_source: "checked" }),
        placed("b", { host: "M", host_kind: "server", host_source: "reported" }),
      ],
      [],
    ).sessions;
    expect(groupByMachine(mixed).map((g) => g.place.source).sort()).toEqual(["checked", "reported"]);
    expect(groupByMachine(sessions).find((g) => g.place.host === "metorite-home")!.place.source).toBe("checked");
  });
  it("keeps a reported machine apart from a checked machine of the same name", () => {
    const two = parse(
      [
        placed("a", { host: "metorite-home", host_kind: "server", host_source: "checked" }),
        placed("b", { host: "Metorite-Home", host_kind: "server", host_source: "reported" }),
        placed("c", { host: "metorite-home", host_kind: "server", host_source: "checked" }),
      ],
      [run({ session_id: "a" }), run({ run_id: "r2", session_id: "b" }), run({ run_id: "r3", session_id: "c" })],
    );
    const groups = groupByMachine(two.sessions);
    expect(groups).toHaveLength(2);
    const checked = groups.find((g) => g.place.source === "checked")!;
    expect(checked.sessions.map((s) => s.id)).toEqual(["a", "c"]);
    expect(groups.find((g) => g.place.source === "reported")!.sessions.map((s) => s.id)).toEqual(["b"]);
    expect(groupByMachine([...two.sessions].reverse())).toEqual(groups);
    const machines = layoutAgentMap(two, NOW, BY_MACHINE).nodes.filter((n) => n.kind === "machine");
    expect(machines).toHaveLength(2);
    expect(machines.map((n) => n.place?.source).sort()).toEqual(["checked", "reported"]);
    expectNoOverlap(two, BY_MACHINE);
    const ids = (m: AgentMap) => layoutAgentMap(m, NOW, BY_MACHINE).nodes.map((n) => [n.id, n.x, n.y]).sort();
    expect(ids({ ...two, sessions: [...two.sessions].reverse() })).toEqual(ids(two));
  });
  it("gives the same groups for the same input, in any input order", () => {
    expect(groupByMachine([...sessions].reverse())).toEqual(groupByMachine(sessions));
  });
});

describe("layoutAgentMap: group by machine", () => {
  const map = parse(
    [
      placed("a", { person: "ana@example.com", host: "VJ laptop", host_kind: "computer", host_source: "reported", quiet_count: 2 }),
      placed("b", { person: "bo@example.com", host: "metorite-home", host_kind: "server", host_source: "checked" }),
      placed("c", { person: "ana@example.com", host: "metorite-home", host_kind: "server", host_source: "checked" }),
      placed("d", { person: "bo@example.com" }),
    ],
    [
      run({ run_id: "r1", session_id: "a" }),
      run({ run_id: "r2", session_id: "a" }),
      run({ run_id: "r3", session_id: "b" }),
      run({ run_id: "r4", session_id: "c" }),
    ],
  );
  it("is deterministic: the same input gives the same positions", () => {
    expect(layoutAgentMap(map, NOW, BY_MACHINE)).toEqual(layoutAgentMap(map, NOW, BY_MACHINE));
    expect(layoutAgentMap({ ...map, sessions: [...map.sessions].reverse() }, NOW, BY_MACHINE).nodes.map((n) => [n.id, n.x, n.y]).sort()).toEqual(
      layoutAgentMap(map, NOW, BY_MACHINE).nodes.map((n) => [n.id, n.x, n.y]).sort(),
    );
  });
  it("never overlaps two nodes", () => {
    expectNoOverlap(map, BY_MACHINE);
    const many = Array.from({ length: 24 }, (_, i) =>
      placed(`s${i}`, {
        person: `p${i % 5}@example.com`,
        host: i % 4 === 0 ? null : `box-${i % 3}`,
        host_kind: i % 4 === 0 ? null : (["computer", "server", "cloud"] as const)[i % 3],
        quiet_count: i % 3,
      }),
    );
    const runs = Array.from({ length: 60 }, (_, i) => run({ run_id: `r${i}`, session_id: `s${i % 24}` }));
    expectNoOverlap(parse(many, runs), BY_MACHINE);
    expectNoOverlap(parse([], []), BY_MACHINE);
  });
  it("draws three columns, machine then session then task, with no person node", () => {
    const { nodes } = layoutAgentMap(map, NOW, BY_MACHINE);
    const x = (kind: string) => [...new Set(nodes.filter((n) => n.kind === kind).map((n) => n.x))];
    expect(nodes.some((n) => n.kind === "person")).toBe(false);
    expect(x("machine")).toHaveLength(1);
    expect(x("machine")[0]).toBeLessThan(x("session")[0]);
    expect(x("session")[0]).toBeLessThan(x("task")[0]);
  });
  it("puts the person on the client line of each session", () => {
    const sessions = layoutAgentMap(map, NOW, BY_MACHINE).nodes.filter((n) => n.kind === "session");
    expect(sessions.find((n) => n.ref === "b")!.detail).toBe("Claude Code \u00b7 bo@example.com");
    expect(sessions.find((n) => n.ref === "a")!.detail).toBe("Claude Code \u00b7 ana@example.com");
    // The plain layout keeps the client line as it was.
    const plain = layoutAgentMap(map, NOW).nodes.filter((n) => n.kind === "session");
    expect(plain.every((n) => n.detail === "Claude Code")).toBe(true);
  });
  it("orders the sessions of a group by state, name, person, then id", () => {
    const ordered = parse(
      [
        placed("z", { name: "Same", person: "bo@example.com", host: "m", host_kind: "server", host_source: "reported" }),
        placed("y", { name: "Same", person: "ana@example.com", host: "m", host_kind: "server", host_source: "reported" }),
        placed("x", { name: "Same", person: "ana@example.com", host: "m", host_kind: "server", host_source: "reported" }),
        placed("w", { name: "Other", state: "quiet", host: "m", host_kind: "server", host_source: "reported" }),
      ],
      [],
    );
    const ids = layoutAgentMap(ordered, NOW, BY_MACHINE)
      .nodes.filter((n) => n.kind === "session")
      .sort((a, b) => a.y - b.y)
      .map((n) => n.ref);
    expect(ids).toEqual(["x", "y", "z", "w"]);
  });
  it("draws one machine node for each group, with Not reported last", () => {
    const machines = layoutAgentMap(map, NOW, BY_MACHINE)
      .nodes.filter((n) => n.kind === "machine")
      .sort((a, b) => a.y - b.y);
    expect(machines.map((n) => n.label)).toEqual(["VJ laptop", "metorite-home", "Not reported"]);
    expect(machines[1].detail).toBe("2 sessions");
    expect(machines[1].place?.source).toBe("checked");
  });
  it("joins each session to its machine only, and draws no person line", () => {
    const { edges } = layoutAgentMap(map, NOW, BY_MACHINE);
    const into = (id: string) => edges.filter((e) => e.to === `s:${id}`).map((e) => e.from).sort();
    expect(into("b")).toEqual(["m:server|metorite-home|checked"]);
    expect(into("d")).toEqual(["m:none"]);
    expect(edges.some((e) => e.from.startsWith("p:") || e.to.startsWith("p:"))).toBe(false);
    expect(edges.filter((e) => e.from.startsWith("s:")).every((e) => e.to.startsWith("t:") || e.to.startsWith("q:"))).toBe(true);
  });
  it("leaves the default layout alone", () => {
    const plain = layoutAgentMap(map, NOW);
    expect(plain.nodes.some((n) => n.kind === "machine")).toBe(false);
    expect(layoutAgentMap(map, NOW, { groupByMachine: false })).toEqual(plain);
  });
});
