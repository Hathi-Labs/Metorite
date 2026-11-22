import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AgentRunsView } from "../components/AgentsAtWork";
import { type AgentRunsRead, RUNS_EMPTY_COPY, parseAgentRuns, runTimeWord } from "./agentsAtWork";

const NOW = Date.parse("2026-11-11T12:00:00Z");
const run = (over: Record<string, unknown> = {}) => ({
  id: "r1",
  state: "completed",
  started_at: "2026-11-11T09:00:00Z",
  ended_at: "2026-11-11T09:03:20Z",
  run_seconds: 200,
  task: { id: "t1", title: "Fix the login" },
  error: null,
  trace: "SECRET TRACE",
  ...over,
});

describe("parseAgentRuns", () => {
  it("keeps the lean fields and drops the trace", () => {
    const out = parseAgentRuns({ key: "external:mcp:x", runs: [run()], count: 1, truncated: false });
    expect(out?.runs).toHaveLength(1);
    expect(JSON.stringify(out)).not.toContain("SECRET TRACE");
  });
  it("drops a row it cannot read, and refuses a body with no runs", () => {
    expect(parseAgentRuns({ runs: [run(), { nope: 1 }] })?.runs).toHaveLength(1);
    expect(parseAgentRuns({ agents: [] })).toBeNull();
  });
});

describe("runTimeWord", () => {
  it("writes the run time in words", () => {
    expect(runTimeWord(null)).toBe("Running");
    expect(runTimeWord(45)).toBe("45 s");
    expect(runTimeWord(200)).toBe("3 min 20 s");
    expect(runTimeWord(7500)).toBe("2 h 5 min");
  });
});

describe("AgentRunsView", () => {
  const html = (read: AgentRunsRead) => renderToStaticMarkup(createElement(AgentRunsView, { read, nowMs: NOW }));
  it("draws each run with its outcome, time and task", () => {
    const parsed = parseAgentRuns({ runs: [run(), run({ id: "r2", state: "failed", error: { what: "It failed.", next_step: "Retry." } })] })!;
    const out = html({ kind: "ready", runs: parsed });
    expect(out).toContain("Fix the login");
    expect(out).toContain("3 min 20 s");
    expect(out).toContain("It failed. Retry.");
  });
  it("says so when no run is visible", () => {
    expect(html({ kind: "ready", runs: { key: "k", runs: [], count: 0, truncated: false } })).toContain(RUNS_EMPTY_COPY);
  });
});
