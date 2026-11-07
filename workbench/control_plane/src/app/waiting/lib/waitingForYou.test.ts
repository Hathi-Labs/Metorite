import { describe, expect, it } from "vitest";

import type { AgentActivity } from "@/app/projects/lib/agentRuns";

import {
  answersInPlace,
  askedAt,
  rowAgent,
  rowTitle,
  taskHref,
  waitingRuns,
  type WaitingRun,
} from "./waitingForYou";

function ask(createdAt: string, extra: Partial<AgentActivity> = {}): AgentActivity {
  return {
    id: `a-${createdAt}`,
    seq: 1,
    type: "elicitation",
    content: { body: "Which database?" },
    created_at: createdAt,
    ...extra,
  };
}

function run(id: string, createdAt: string | null, over: Partial<WaitingRun> = {}): WaitingRun {
  return {
    id,
    task_id: `t-${id}`,
    member_email: "m@x.test",
    client_via: "mcp:claude.ai:claude-code",
    status: "awaiting",
    open_elicitation: createdAt ? ask(createdAt) : null,
    ...over,
  };
}

describe("waitingRuns", () => {
  it("sorts the oldest question first", () => {
    const out = waitingRuns([
      run("new", "2026-11-05T10:00:00Z"),
      run("old", "2026-11-01T10:00:00Z"),
      run("mid", "2026-11-03T10:00:00Z"),
    ]);
    expect(out.map((r) => r.id)).toEqual(["old", "mid", "new"]);
  });

  it("drops a run with no open question and a run that is not awaiting", () => {
    const out = waitingRuns([
      run("none", null),
      run("done", "2026-11-01T10:00:00Z", { status: "completed" }),
      run("ok", "2026-11-02T10:00:00Z"),
    ]);
    expect(out.map((r) => r.id)).toEqual(["ok"]);
  });

  it("keeps the order of the wire for equal times", () => {
    const out = waitingRuns([run("a", "2026-11-01T10:00:00Z"), run("b", "2026-11-01T10:00:00Z")]);
    expect(out.map((r) => r.id)).toEqual(["a", "b"]);
  });
});

describe("the times and titles", () => {
  it("reads the time of the open question, else the last activity", () => {
    expect(askedAt(run("q", "2026-11-01T10:00:00Z"))).toBe("2026-11-01T10:00:00Z");
    expect(askedAt(run("n", null, { last_activity_at: "2026-11-02T09:00:00Z" }))).toBe(
      "2026-11-02T09:00:00Z",
    );
    expect(askedAt(run("x", null))).toBeNull();
  });

  it("builds the row title from the task facts", () => {
    const r = run("x", "2026-11-01T10:00:00Z", {
      task: { number: 12, title: " Fix the extruder ", project_name: "Plant" },
    });
    expect(rowTitle(r)).toBe("#12 Fix the extruder");
    expect(rowTitle(run("y", "2026-11-01T10:00:00Z"))).toBe("Task");
  });

  it("names the agent and links the task", () => {
    const r = run("x", "2026-11-01T10:00:00Z", { client_name: "Claude Code", client_host: "claude.ai" });
    expect(rowAgent(r)).toBe("Claude Code · claude.ai");
    expect(taskHref(r)).toBe("/projects?task=t-x");
  });
});

describe("answersInPlace", () => {
  it("is true for a question and false for an archive request", () => {
    expect(answersInPlace(run("q", "2026-11-01T10:00:00Z"))).toBe(true);
    const archive = run("r", "2026-11-01T10:00:00Z", {
      open_elicitation: ask("2026-11-01T10:00:00Z", {
        content_metadata: { request: { kind: "archive_task", expires_at: "2026-12-01T00:00:00Z" } },
      } as Partial<AgentActivity>),
    });
    expect(answersInPlace(archive)).toBe(false);
  });
});
