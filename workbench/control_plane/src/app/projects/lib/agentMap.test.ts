import { describe, expect, it } from "vitest";

import {
  type AgentMap,
  DEPTH_STEP,
  MAX_DISPATCH_DEPTH,
  NODE_H,
  agentMapOutline,
  dispatchGraph,
  groupByMachine,
  layoutAgentMap,
  parseAgentMap,
  quietLine,
  sessionStateChip,
  sessionStateWord,
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

/* ── SW-109: who dispatched whom ─────────────────────────────────────────── */

const ANA = "ana@example.com";
const BO = "bo@example.com";
const lane = (id: string, person = ANA, over: Record<string, unknown> = {}) =>
  session({ id, name: `Lane ${id}`, person, ...over });
const link = (from: string, to: string) => ({ from, to });
const nodeOf = (map: AgentMap, id: string, options = {}) =>
  layoutAgentMap(map, NOW, options).nodes.find((n) => n.id === `s:${id}`)!;
const lineEdges = (map: AgentMap, options = {}) =>
  layoutAgentMap(map, NOW, options)
    .edges.filter((e) => e.kind === "line")
    .map((e) => e.id)
    .sort();
const positions = (map: AgentMap, options = {}) =>
  layoutAgentMap(map, NOW, options)
    .nodes.map((n) => [n.id, n.x, n.y])
    .sort();
/** A chain a0 > a1 > ... with the people spread over `people`. */
const chain = (n: number, people = 2) => {
  const sessions = Array.from({ length: n }, (_, i) => lane(`a${i}`, `p${i % people}@example.com`));
  const lines = Array.from({ length: n - 1 }, (_, i) => link(`a${i}`, `a${i + 1}`));
  return { sessions, lines };
};

describe("dispatchGraph", () => {
  it("gives each session the longest chain of dispatchers above it", () => {
    const map = parse(
      [lane("a"), lane("b"), lane("c"), lane("d"), lane("e")],
      [],
      [link("a", "b"), link("b", "c"), link("a", "c"), link("a", "d")],
    );
    const { depth } = dispatchGraph(map);
    expect(depth.get("a")).toBe(0);
    expect(depth.get("b")).toBe(1);
    expect(depth.get("c")).toBe(2);
    expect(depth.get("d")).toBe(1);
    expect(depth.has("e")).toBe(false);
  });
  it("drops an unknown id, a line to itself and a copy of a line", () => {
    const map = parse([lane("a"), lane("b")], [], [
      link("a", "ghost"),
      link("ghost", "b"),
      link("a", "a"),
      link("a", "b"),
      link("a", "b"),
    ]);
    expect(dispatchGraph(map).lines).toEqual([link("a", "b")]);
    expect(lineEdges(map)).toEqual(["s:a~s:b"]);
  });
  it("drops the line that closes a loop, chosen by sorted id", () => {
    const two = dispatchGraph(parse([lane("a"), lane("b")], [], [link("b", "a"), link("a", "b")]));
    expect(two.lines).toEqual([link("a", "b")]);
    const three = dispatchGraph(
      parse([lane("a"), lane("b"), lane("c")], [], [link("c", "a"), link("b", "c"), link("a", "b")]),
    );
    expect(three.lines).toEqual([link("a", "b"), link("b", "c")]);
    expect(three.depth.get("c")).toBe(2);
    expect(dispatchGraph(parse([lane("a")], [], [link("a", "a")])).lines).toEqual([]);
  });
  it("stops the depth at the cap", () => {
    const { sessions, lines } = chain(9);
    const { depth } = dispatchGraph(parse(sessions, [], lines));
    expect(Math.max(...depth.values())).toBe(MAX_DISPATCH_DEPTH);
    expect(depth.get("a8")).toBe(MAX_DISPATCH_DEPTH);
    expect(depth.get("a3")).toBe(3);
  });
  it("gives the same result for the lines in any order", () => {
    const { sessions, lines } = chain(6);
    const loop = [...lines, link("a5", "a1"), link("a2", "a0")];
    const forward = dispatchGraph(parse(sessions, [], loop));
    const reversed = dispatchGraph(parse([...sessions].reverse(), [], [...loop].reverse()));
    expect(reversed.lines).toEqual(forward.lines);
    expect([...reversed.depth].sort()).toEqual([...forward.depth].sort());
  });
});

describe("layoutAgentMap: dispatch lines", () => {
  const crossing = parse(
    [
      lane("orch", ANA, { name: "Orchestrator" }),
      lane("l1", BO),
      lane("l2", ANA),
      lane("solo", BO),
    ],
    [run({ run_id: "r1", session_id: "l1" }), run({ run_id: "r2", session_id: "orch" })],
    [link("orch", "l1"), link("orch", "l2")],
  );
  it("puts the orchestrator to the left of a lane of another person, in the block of that person", () => {
    const orch = nodeOf(crossing, "orch");
    const l1 = nodeOf(crossing, "l1");
    expect(orch.x + orch.w).toBeLessThan(l1.x);
    expect(l1.x).toBe(orch.x + DEPTH_STEP);
    // The lane stays in the block of Bo: it sits with Bo's other session, far from Ana's.
    const bo = layoutAgentMap(crossing, NOW).nodes.find((n) => n.id === `p:${BO}`)!;
    const solo = nodeOf(crossing, "solo");
    expect(l1.y).toBeGreaterThanOrEqual(bo.y);
    expect(Math.abs(l1.y - solo.y)).toBeLessThan(2 * (NODE_H.session + 24));
    expect(lineEdges(crossing)).toEqual(["s:orch~s:l1", "s:orch~s:l2"]);
  });
  it("keeps a lane with no dispatcher a root with a person edge, and gives a dispatched lane none", () => {
    const { edges } = layoutAgentMap(crossing, NOW);
    const into = (id: string) => edges.filter((e) => e.to === `s:${id}` && e.kind === "tree").map((e) => e.from);
    expect(into("orch")).toEqual([`p:${ANA}`]);
    expect(into("solo")).toEqual([`p:${BO}`]);
    expect(into("l1")).toEqual([]);
    expect(into("l2")).toEqual([]);
    expect(nodeOf(crossing, "solo").x).toBe(nodeOf(crossing, "orch").x);
  });
  it("puts the tasks of each session one step right of that session, and keeps person left of all", () => {
    const { nodes } = layoutAgentMap(crossing, NOW);
    for (const task of nodes.filter((n) => n.kind === "task")) {
      const owner = nodes.find((n) => n.id === `s:${task.sessionId}`)!;
      expect(task.x).toBe(owner.x + DEPTH_STEP);
      expect(task.x).toBeGreaterThanOrEqual(owner.x + owner.w);
      expect(task.y).toBeGreaterThanOrEqual(owner.y - NODE_H.session);
    }
    // The orchestrator is at depth 0, so its task sits in the column of its lanes, and clear of them.
    expect(nodes.find((n) => n.id === "t:r2")!.x).toBe(nodeOf(crossing, "l1").x);
    expectNoOverlap(crossing);
    expect(Math.max(...nodes.filter((n) => n.kind === "person").map((n) => n.x + n.w))).toBeLessThan(
      Math.min(...nodes.filter((n) => n.kind === "session").map((n) => n.x)),
    );
  });
  it("keeps the old columns when there are no lines", () => {
    const plain = parse([session()], [run()]);
    const { nodes } = layoutAgentMap(plain, NOW);
    expect(nodes.find((n) => n.kind === "session")!.x).toBe(300);
    expect(nodes.find((n) => n.kind === "task")!.x).toBe(640);
  });
  it("puts each session of a chain one column right of its dispatcher, up to the cap", () => {
    const { sessions, lines } = chain(8);
    const map = parse(sessions, [], lines);
    const xs = sessions.map((s) => nodeOf(map, s.id).x);
    expect(xs.slice(0, 5)).toEqual([0, 1, 2, 3, 4].map((d) => 300 + DEPTH_STEP * d));
    expect(xs.slice(5)).toEqual([4, 4, 4].map((d) => 300 + DEPTH_STEP * d));
    expectNoOverlap(map);
  });
  it("gives a finite layout with no overlap for a loop in the data", () => {
    const sessions = [lane("a"), lane("b", BO), lane("c")];
    const loops = [
      [link("a", "b"), link("b", "a")],
      [link("a", "a")],
      [link("a", "b"), link("b", "c"), link("c", "a")],
    ];
    for (const lines of loops) {
      const map = parse(sessions, [run({ session_id: "a" })], lines);
      expectNoOverlap(map);
      expect(layoutAgentMap(map, NOW)).toEqual(layoutAgentMap(map, NOW));
      expect(lineEdges(map).length).toBeLessThan(lines.length + (lines.length === 1 ? 1 : 0));
      const roots = layoutAgentMap(map, NOW).edges.filter((e) => e.from.startsWith("p:"));
      expect(roots.length).toBeGreaterThanOrEqual(1);
    }
  });
  it("gives the same positions for the same input in any input order", () => {
    const { sessions, lines } = chain(7, 3);
    const runs = sessions.map((s, i) => run({ run_id: `r${i}`, session_id: s.id }));
    const loop = [...lines, link("a6", "a2")];
    const forward = parse(sessions, runs, loop);
    const reversed = parse([...sessions].reverse(), [...runs].reverse(), [...loop].reverse());
    expect(positions(reversed)).toEqual(positions(forward));
    expect(lineEdges(reversed)).toEqual(lineEdges(forward));
  });
  it("never overlaps two nodes in a generated set of 50 sessions with chains", () => {
    const sessions = Array.from({ length: 50 }, (_, i) =>
      lane(`s${String(i).padStart(2, "0")}`, `p${i % 6}@example.com`, {
        state: (["working", "quiet", "waiting", "waiting_on_person"] as const)[i % 4],
        quiet_count: i % 3,
      }),
    );
    const lines = [
      ...Array.from({ length: 49 }, (_, i) => link(`s${String(i).padStart(2, "0")}`, `s${String(i + 1).padStart(2, "0")}`)),
      ...Array.from({ length: 10 }, (_, i) => link("s00", `s${String(i * 5 + 2).padStart(2, "0")}`)),
      link("s49", "s03"),
    ];
    const runs = Array.from({ length: 120 }, (_, i) =>
      run({ run_id: `r${i}`, session_id: `s${String(i % 50).padStart(2, "0")}` }),
    );
    const map = parse(sessions, runs, lines);
    expectNoOverlap(map);
    expect(Math.max(...layoutAgentMap(map, NOW).nodes.map((n) => n.x))).toBeLessThanOrEqual(
      300 + DEPTH_STEP * (MAX_DISPATCH_DEPTH + 1),
    );
  });
  it("leaves the grouped layout as it was: no depth columns, lines still dashed edges", () => {
    const { sessions, lines } = chain(4);
    const withLines = parse(sessions, [run({ session_id: "a0" })], lines);
    const without = parse(sessions, [run({ session_id: "a0" })], []);
    expect(positions(withLines, BY_MACHINE)).toEqual(positions(without, BY_MACHINE));
    expect(new Set(layoutAgentMap(withLines, NOW, BY_MACHINE).nodes.filter((n) => n.kind === "session").map((n) => n.x))).toEqual(
      new Set([280]),
    );
    expect(lineEdges(withLines, BY_MACHINE)).toHaveLength(3);
    expectNoOverlap(withLines, BY_MACHINE);
  });
});

describe("a quiet orchestrator", () => {
  const map = parse(
    [
      lane("orch", ANA, { state: "quiet", last_at: "2026-11-21T09:00:00Z" }),
      lane("l1", ANA, { state: "working" }),
      lane("l2", ANA, { state: "quiet" }),
    ],
    [],
    [link("orch", "l1"), link("orch", "l2")],
  );
  it("reads Waiting with its last event time, and a quiet lane still reads Quiet", () => {
    expect(sessionStateWord("quiet", true)).toBe("Waiting");
    expect(sessionStateWord("quiet", false)).toBe("Quiet");
    expect(sessionStateWord("working", true)).toBe("Working");
    expect(sessionStateChip("quiet", true)).not.toBe(sessionStateChip("quiet", false));
    const orch = nodeOf(map, "orch");
    expect(orch.quietWait).toBe(true);
    expect(orch.detail).toBe("Claude Code \u00b7 3h ago");
    const l2 = nodeOf(map, "l2");
    expect(l2.quietWait).toBe(false);
    expect(l2.detail).toBe("Claude Code");
  });
  it("reads Quiet when every lane that it dispatched is quiet", () => {
    const quiet = parse(
      [lane("orch", ANA, { state: "quiet" }), lane("l1", ANA, { state: "quiet" }), lane("l2", ANA, { state: "quiet" })],
      [],
      [link("orch", "l1"), link("orch", "l2")],
    );
    expect(dispatchGraph(quiet).waiting.size).toBe(0);
    expect(nodeOf(quiet, "orch").quietWait).toBe(false);
    expect(agentMapOutline(quiet, NOW)[0].sessions.every((s) => s.word === "Quiet")).toBe(true);
    // One lane that works is enough.
    const one = parse(
      [lane("orch", ANA, { state: "quiet" }), lane("l1", ANA, { state: "quiet" }), lane("l2", ANA, { state: "waiting" })],
      [],
      [link("orch", "l1"), link("orch", "l2")],
    );
    expect([...dispatchGraph(one).waiting]).toEqual(["orch"]);
  });
  it("sorts a quiet orchestrator that reads Waiting before a working session", () => {
    const sorted = parse(
      [
        lane("w", ANA, { name: "A working", state: "working" }),
        lane("orch", ANA, { name: "Z orchestrator", state: "quiet" }),
        lane("l1", BO, { state: "working" }),
      ],
      [],
      [link("orch", "l1")],
    );
    const ids = layoutAgentMap(sorted, NOW)
      .nodes.filter((n) => n.kind === "session" && n.id.startsWith("s:") && ["s:w", "s:orch"].includes(n.id))
      .sort((a, b) => a.y - b.y)
      .map((n) => n.ref);
    expect(ids).toEqual(["orch", "w"]);
    const outline = agentMapOutline(sorted, NOW).find((p) => p.person === ANA)!;
    expect(outline.sessions.map((s) => s.id)).toEqual(["orch", "w"]);
  });
  it("reads the same in the outline, and never says lost or dead", () => {
    const outline = agentMapOutline(map, NOW)[0].sessions;
    const orch = outline.find((s) => s.id === "orch")!;
    const l1 = outline.find((s) => s.id === "l1")!;
    expect(orch.word).toBe("Waiting");
    expect(orch.lastEvent).toBe("3h ago");
    expect(l1.word).toBe("Working");
    expect(l1.lastEvent).toBeNull();
    expect(JSON.stringify(outline).toLowerCase()).not.toMatch(/lost|dead/);
  });
  it("reads Quiet when its only line was dropped as a loop of the data", () => {
    const solo = parse([lane("a", ANA, { state: "quiet" })], [], [link("a", "a")]);
    expect(nodeOf(solo, "a").quietWait).toBe(false);
  });
});

describe("agentMapOutline: dispatched by", () => {
  it("names the dispatcher under a lane that sits under its own person", () => {
    const map = parse(
      [lane("orch", ANA, { name: "<b>Boss</b>" }), lane("l1", BO)],
      [],
      [link("orch", "l1"), link("ghost", "l1")],
    );
    const outline = agentMapOutline(map, NOW);
    const bo = outline.find((p) => p.person === BO)!;
    expect(bo.sessions.map((s) => s.id)).toEqual(["l1"]);
    expect(bo.sessions[0].dispatchedBy).toEqual(["<b>Boss</b>"]);
    expect(outline.find((p) => p.person === ANA)!.sessions[0].dispatchedBy).toEqual([]);
  });
});
