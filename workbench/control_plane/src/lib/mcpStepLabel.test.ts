// CSY-10 (`chat_connected_systems.md` §3.8): the tool row of an MCP tool keeps
// the server's label: "Shaurrya App: search operations".
//
// R7 fences named here:
//   * the row reads "<label>: <tool in words>", running and done;
//   * the words come from the tool NAME. An argument or a result with a
//     sentinel never reaches the label;
//   * the label is one short line: no control character, capped at 24;
//   * no server on the event, or a name that does not start with the server,
//     keeps the old words.

import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describeToolStep, mcpStepLabel, SERVER_LABEL_CAP } from "@/lib/toolSteps";
import { applyStreamEvent } from "@/lib/chatStream";

const SENTINEL = "SENTINEL-9f3a";

describe("an MCP tool row keeps its server label", () => {
  it("reads <label>: <tool in words> while it runs and when it is done", () => {
    const base = { name: "shaurrya-app_search_operations", mcp: { server: "shaurrya-app", label: "Shaurrya App" } };
    expect(describeToolStep({ ...base, status: "running" }).label).toBe("Shaurrya App: search operations");
    expect(describeToolStep({ ...base, status: "done" }).label).toBe("Shaurrya App: search operations");
  });

  it("matches the exact wire name <server>_<tool>, and guesses nothing", () => {
    const stamp = { server: "ops", label: "Ops" };
    expect(mcpStepLabel({ name: "ops_list-items", mcp: stamp })).toBe("Ops: list items");
    expect(mcpStepLabel({ name: "mcp__ops__list", mcp: stamp })).toBeNull();
    expect(mcpStepLabel({ name: "mcp_ops_list", mcp: stamp })).toBeNull();
    expect(mcpStepLabel({ name: "OPS_list", mcp: stamp })).toBeNull();
    expect(mcpStepLabel({ name: "ops_", mcp: stamp })).toBeNull();
    // A dashed server keeps its dash: the wire name does not change it.
    expect(mcpStepLabel({ name: "mcp-ops_read", mcp: { server: "mcp-ops", label: "A" } })).toBe("A: read");
    expect(mcpStepLabel({ name: "mcp_ops_read", mcp: { server: "mcp-ops", label: "A" } })).toBeNull();
  });

  it("never holds an argument or a result", () => {
    const step = describeToolStep({
      name: "shaurrya_search_operations",
      mcp: { server: "shaurrya", label: "Shaurrya App" },
      status: "done",
      args: { query: SENTINEL, path: `/x/${SENTINEL}` },
      result: `done ${SENTINEL}`,
    });
    expect(step.label).toBe("Shaurrya App: search operations");
    expect(JSON.stringify(step)).not.toContain(SENTINEL);
  });

  it("bounds the label and strips control characters", () => {
    const label = mcpStepLabel({
      name: "ops_read",
      mcp: { server: "ops", label: "A\u0000B\u001b<b>C</b>‮​D" + "x".repeat(60) },
    });
    expect(label).not.toBeNull();
    // eslint-disable-next-line no-control-regex
    expect(label).not.toMatch(/[\u0000-\u001f<>]/);
    expect(label).not.toMatch(/[​-‏‪-‮⁠-⁤﻿]/);
    expect((label as string).split(": ")[0].length).toBeLessThanOrEqual(SERVER_LABEL_CAP);
    expect(label).toMatch(/: read$/);
  });

  it("falls back to the old words when it cannot name the server", () => {
    expect(mcpStepLabel({ name: "search_tasks" })).toBeNull();
    expect(mcpStepLabel({ name: "search_tasks", mcp: { server: "other", label: "Other" } })).toBeNull();
    expect(mcpStepLabel({ name: "ops_read", mcp: { server: "ops", label: "  " } })).toBeNull();
    expect(describeToolStep({ name: "search_tasks", status: "running" }).label).toBe("Searching tasks");
  });
});

describe("the stream puts the server on the tool row", () => {
  const msg = { id: "m1", role: "assistant", content: "", toolEvents: [] } as never;
  const fold = { foldedAnswerIdx: -1 } as never;

  it("keeps the mcp stamp of a tool_start, and the row reads the label", () => {
    const m = applyStreamEvent(msg, {
      type: "tool_start", id: "t1", name: "ops_read", args: {},
      mcp: { server: "ops", label: "Ops" },
    }, fold);
    const tool = m.toolEvents![0];
    expect(tool.mcp).toEqual({ server: "ops", label: "Ops" });
    expect(describeToolStep(tool).label).toBe("Ops: read");
  });

  it("drops a stamp that is not two strings", () => {
    const m = applyStreamEvent(msg, {
      type: "tool_start", id: "t2", name: "ops_read", args: {}, mcp: { server: 1, label: {} },
    }, fold);
    expect(m.toolEvents![0].mcp).toBeUndefined();
  });
});

describe("the chat route passes the stamp on", () => {
  // The BFF copies `mcp` only when both parts are strings (no fetch, no guess).
  const route = readFileSync(
    join(process.cwd(), "src/app/api/agent/chat/route.ts"), "utf8",
  );

  it("copies mcp from the tool start, and only when server and label are strings", () => {
    expect(route).toMatch(/typeof mcp\.server === "string" && typeof mcp\.label === "string"/);
    expect(route).toMatch(/\{ mcp: \{ server: mcp\.server, label: mcp\.label \} \}/);
    // It builds the copy from the two strings, never from args or a result.
    expect(route).not.toMatch(/mcp:\s*\{[^}]*(args|result|delta)/);
  });
});
