/** The fleet that the tests of the Agents lenses share. Not a test file, so an import runs no test. */
import { type AgentMap, parseAgentMap } from "./agentMap";

export const session = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  person: "ana@x.test",
  name: id,
  client: "Claude Code",
  state: "working",
  last_at: "2026-11-26T11:55:00Z",
  run_count: 1,
  quiet_count: 0,
  ...over,
});
export const run = (id: string, sessionId: string, over: Record<string, unknown> = {}) => ({
  run_id: id,
  session_id: sessionId,
  task_id: `t-${id}`,
  task_number: 7,
  title: `Task of ${sessionId}`,
  state: "working",
  quiet: false,
  age: 60,
  ...over,
});
export const mapOf = (sessions: unknown[], runs: unknown[] = [], lines: unknown[] = []): AgentMap =>
  parseAgentMap({ sessions, runs, lines, count: runs.length, truncated: false, viewer: "admin" }) as AgentMap;

/** The member who looks: the one whose session asks in `fleet()`. */
export const BEN = { email: "ben@x.test" };

/** boss dispatched lane-a and lane-b (of another person). lane-a dispatched sub. solo holds nothing. */
export const fleet = () =>
  mapOf(
    [
      session("boss"),
      session("lane-a", { state: "waiting" }),
      session("lane-b", { person: "ben@x.test", state: "waiting_on_person" }),
      session("sub", { state: "quiet" }),
      session("solo", { state: "quiet" }),
    ],
    [
      run("r1", "boss"),
      run("r2", "lane-a", { state: "waiting", waiting_on: "ci", waiting_until: "2026-11-26T12:25:00Z" }),
      run("r3", "lane-b", { state: "awaiting" }),
    ],
    [
      { from: "boss", to: "lane-a" },
      { from: "boss", to: "lane-b" },
      { from: "lane-a", to: "sub" },
    ],
  );

/** A large fleet in the shape of the summary read: 3 people, 15 orchestrators, `size` sessions. */
export function bigFleet(size = 500, subsEach = 4): AgentMap {
  const states = ["waiting_on_person", "failed", "working", "working", "waiting", "quiet", "quiet", "done"];
  const people = ["ana@x.test", "ben@x.test", "cy@x.test"];
  const orchs = Array.from({ length: 15 }, (_, i) => ({ id: `o${i}`, person: people[i % 3] }));
  const sessions = orchs.map((o) =>
    session(o.id, {
      person: o.person,
      state: o.id === "o14" ? "quiet" : "working",
      subagents: { total: 0, working: 0, failed: 0, done: 0, names: [] },
    }),
  );
  const runs = orchs.map((o) => run(`r-${o.id}`, o.id));
  const lines: Array<{ from: string; to: string }> = [];
  for (let i = 0; i < size - orchs.length; i += 1) {
    const orch = orchs[i % orchs.length];
    // The last orchestrator holds idle sessions only.
    const state = orch.id === "o14" ? "quiet" : states[(i * 7) % states.length];
    const id = `s${i}`;
    sessions.push(
      session(id, {
        person: orch.person,
        state,
        host: i % 2 ? "metorite-home" : "Vijay PC",
        subagents: { total: subsEach, working: 1, failed: 1, done: subsEach - 2, names: [`builder ${i}`] },
      }),
    );
    const runState: Record<string, string> = { waiting_on_person: "awaiting", failed: "failed", done: "completed", quiet: "stale" };
    runs.push(run(`r-${id}`, id, { state: runState[state] ?? state, quiet: state === "quiet" }));
    lines.push({ from: orch.id, to: id });
  }
  if (size <= 15) lines.length = 0;
  return mapOf(sessions, runs, lines);
}

/**
 * The fleet of AMR-5, 64 sessions of two people. Ana holds 4 active agents and
 * 43 idle or done ones. Ben holds 17 idle or done ones, so nothing of Ben is
 * active. Ana's orchestrator `boss` dispatched three of the active ones.
 */
export function splitFleet(): AgentMap {
  const quiet = (id: string, i: number, over: Record<string, unknown> = {}) =>
    session(id, { state: i % 2 ? "done" : "quiet", ...over });
  return mapOf(
    [
      session("boss"),
      session("lane-1"),
      session("lane-2", { state: "waiting" }),
      session("lane-3", { state: "failed" }),
      ...Array.from({ length: 43 }, (_, i) => quiet(`a${i}`, i)),
      ...Array.from({ length: 17 }, (_, i) => quiet(`b${i}`, i, { person: "ben@x.test" })),
    ],
    [
      run("rb", "boss"),
      run("r1", "lane-1"),
      run("r2", "lane-2", { state: "waiting", waiting_on: "ci", waiting_until: "2026-11-26T12:04:00Z" }),
    ],
    [1, 2, 3].map((n) => ({ from: "boss", to: `lane-${n}` })),
  );
}

/** `size` working sessions of three people, ten to an orchestrator. */
export function workingFleet(size = 500): AgentMap {
  const people = ["ana@x.test", "ben@x.test", "cy@x.test"];
  const lines: Array<{ from: string; to: string }> = [];
  const sessions = Array.from({ length: size }, (_, i) => {
    const head = i - (i % 10);
    if (i !== head) lines.push({ from: `w${head}`, to: `w${i}` });
    return session(`w${i}`, { person: people[(head / 10) % 3] });
  });
  return mapOf(sessions, sessions.map((s) => run(`r-${s.id}`, s.id)), lines);
}
