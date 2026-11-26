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
    // The last orchestrator holds idle sessions only, so one tile is quiet.
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
