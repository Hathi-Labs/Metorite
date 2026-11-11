/**
 * WS-MCP SW-7b — a display label for each agent session
 * (`agent_coordination.md` §3.7 item 2 and SW-7b).
 *
 * Mutations caught, by test:
 * - `withAgentLabel` puts the label in place of the client name, or drops it;
 * - `agentLabelText` keeps a control or a format character, keeps a run of
 *   white space, or stops capping at 40 characters;
 * - the run pill, the held-by line or the card line loses the label;
 * - two sessions with two labels read as one name on the board;
 * - `TaskBoard` stops passing the row's label, or `AgentRunPanel` stops
 *   merging it into the row (the source reads at the end).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { agentCardLook } from "./agentCardLook";
import {
  AGENT_LABEL_MAX,
  agentLabelText,
  heldByLine,
  runIdentity,
  withAgentLabel,
} from "./agentRuns";

const VIA = "mcp:claude.ai:claude-code";

describe("agentLabelText", () => {
  it("reads no label as null", () => {
    expect(agentLabelText(undefined)).toBeNull();
    expect(agentLabelText(null)).toBeNull();
    expect(agentLabelText("")).toBeNull();
    expect(agentLabelText("   ")).toBeNull();
    expect(agentLabelText(42 as unknown as string)).toBeNull();
  });

  it("keeps a plain label as it is", () => {
    expect(agentLabelText("backend-worker-3")).toBe("backend-worker-3");
  });

  it("keeps one space for each run of white space, and trims the ends", () => {
    expect(agentLabelText("  backend\t\tworker\n3  ")).toBe("backend worker 3");
  });

  it("drops each control and format character", () => {
    expect(agentLabelText("worker\u0007-1")).toBe("worker-1");
    expect(agentLabelText("worker​-2")).toBe("worker-2");
    expect(agentLabelText("‮worker-3")).toBe("worker-3");
    expect(agentLabelText("\u0000\u0001")).toBeNull();
  });

  it("caps the label at 40 characters", () => {
    expect(AGENT_LABEL_MAX).toBe(40);
    expect(agentLabelText("x".repeat(60))).toBe("x".repeat(40));
    // A character outside the basic plane counts as one.
    expect(Array.from(agentLabelText("\u{1F916}".repeat(45)) ?? "")).toHaveLength(40);
  });
});

describe("withAgentLabel", () => {
  it("puts the label beside the client name, never in its place", () => {
    expect(withAgentLabel("Claude Code", "backend-worker-3")).toBe(
      "Claude Code (backend-worker-3)",
    );
    expect(withAgentLabel("Claude Code", "Cursor")).toBe("Claude Code (Cursor)");
  });

  it("gives the client name alone when the session has no label", () => {
    expect(withAgentLabel("Claude Code", null)).toBe("Claude Code");
    expect(withAgentLabel("Claude Code", undefined)).toBe("Claude Code");
    expect(withAgentLabel("Claude Code", " ​ ")).toBe("Claude Code");
  });
});

describe("the run panel, the card and the held-by line show the label", () => {
  it("the run pill reads the client, the label and the host", () => {
    expect(runIdentity({ client_via: VIA, agent_label: "backend-worker-3" })).toBe(
      "Claude Code (backend-worker-3) · claude.ai",
    );
    expect(runIdentity({ client_via: VIA })).toBe("Claude Code · claude.ai");
    expect(runIdentity({ client_via: "mcp:box:ci-0a1b2c3d", agent_label: "lane-4" })).toBe(
      "Box ci (lane-4)",
    );
  });

  it("the held-by line names the session", () => {
    const run = {
      member_email: "priya@example.test",
      member_name: "Priya Rao",
      client_via: VIA,
      client_name: "Claude Code",
      agent_label: "worker-2",
    };
    expect(heldByLine(run)).toBe("Held by Priya Rao via Claude Code (worker-2)");
    expect(heldByLine({ ...run, agent_label: null })).toBe("Held by Priya Rao via Claude Code");
  });

  it("the card line names the session", () => {
    expect(
      agentCardLook("working", { clientVia: VIA, agentLabel: "backend-worker-3", note: "tests" }),
    ).toEqual({ edge: "live", line: "Claude Code (backend-worker-3) · tests" });
    expect(agentCardLook("working", { clientVia: VIA, agentLabel: "backend-worker-3" }).line).toBe(
      "Claude Code (backend-worker-3) working",
    );
    expect(agentCardLook("working", { clientVia: VIA }).line).toBe("Claude Code working");
  });

  it("two sessions with two labels show two names on the board", () => {
    const one = agentCardLook("working", { clientVia: VIA, agentLabel: "worker-1" }).line;
    const two = agentCardLook("working", { clientVia: VIA, agentLabel: "worker-2" }).line;
    expect(one).toBe("Claude Code (worker-1) working");
    expect(two).toBe("Claude Code (worker-2) working");
    expect(one).not.toBe(two);
  });
});

describe("the surfaces pass the label", () => {
  const read = (path: string) =>
    readFileSync(fileURLToPath(new URL(path, import.meta.url)), "utf8");

  it("TaskBoard passes the row's label to the card look", () => {
    expect(read("../components/TaskBoard.tsx")).toContain("agentLabel: task.agent_label");
  });

  it("AgentRunPanel merges the run's label into the task row", () => {
    const src = read("../components/AgentRunPanel.tsx");
    expect(src).toContain("agent_label: run?.agent_label ?? null");
    expect(src).toContain("(task.agent_label ?? null) === next.agent_label");
  });
});
