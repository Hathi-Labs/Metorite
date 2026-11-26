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
