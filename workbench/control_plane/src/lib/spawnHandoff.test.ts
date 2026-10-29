// WS-67 OA-9b hardening, item 1 — `spawn_subagents` and `delegate_to_agent`
// are hand-offs for the chat UI.
//
// R7 fence `spawn-handoff-rows`. Each child of `spawn_subagents` streams
// `SUB_AGENT_*` events into the parent stream, as `call_agent` does. The live
// reducer and the saved turn (`takeHandoff`, called by `route.ts`) keep each
// child step under the row of the tool that started it. The text of a child is
// item JSON for the parent model, so the spawn row keeps no text. A later
// specialist or `call_agent` row gets its own steps, and none labelled `worker`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import {
  applyStreamEvent,
  applySubAgentEvent,
  isHandoffTool,
  isSpawnTool,
  takeHandoff,
  type SubAgentAccumulator,
} from "@/lib/chatStream";

const CHILD_JSON = '{"items":[{"value":"fact","kind":"quoted","source":"page"}]}';

function turn(): ChatMessage {
  return { id: "a1", role: "assistant", content: "", timestamp: 0, toolEvents: [] };
}

const fold = { foldedAnswerIdx: -1 } as never;

function start(m: ChatMessage, id: string, name: string): ChatMessage {
  return applyStreamEvent(m, { type: "tool_start", id, name, args: {} }, fold);
}

function end(m: ChatMessage, id: string): ChatMessage {
  return applyStreamEvent(m, { type: "tool_end", id, success: true, result: "ok" }, fold);
}

function childStep(m: ChatMessage, agent: string, id: string, name: string): ChatMessage {
  let out = applySubAgentEvent(m, { type: "sub_agent_tool_start", agentName: agent, id, name, args: { q: id } });
  out = applySubAgentEvent(out, { type: "sub_agent_tool_end", id, success: true, result: "r" });
  return out;
}

describe("the hand-off tools", () => {
  it("names the four hand-offs", () => {
    for (const n of ["call_agent", "call_agents_parallel", "delegate_to_agent", "spawn_subagents"]) {
      expect(isHandoffTool(n), n).toBe(true);
    }
    for (const n of ["web_search", "fetch_page", "email_assistant", "", null, undefined]) {
      expect(isHandoffTool(n as never), String(n)).toBe(false);
    }
    expect(isSpawnTool("spawn_subagents")).toBe(true);
    expect(isSpawnTool("delegate_to_agent")).toBe(false);
  });
});

describe("the live stream keeps each child step on its spawn row", () => {
  it("attaches the steps of two children to the spawn row", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    m = childStep(m, "worker", "c1", "web_search");
    m = childStep(m, "worker", "c2", "fetch_page");
    const row = m.toolEvents![0];
    expect(row.subAgentActive).toBe(true);
    expect(row.subAgentTools?.map((t) => t.id)).toEqual(["c1", "c2"]);
    expect(row.subAgentTools?.every((t) => t.status === "done")).toBe(true);
  });

  it("shows no raw child JSON as text on the spawn row", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    m = applySubAgentEvent(m, { type: "sub_agent_delta", agentName: "worker", delta: CHILD_JSON });
    expect(m.toolEvents![0].subAgentText ?? "").toBe("");
    expect(m.content).toBe("");
  });

  it("still shows the text of a call_agent child", () => {
    let m = start(turn(), "c1", "call_agent");
    m = applySubAgentEvent(m, { type: "sub_agent_delta", agentName: "projects-assistant", delta: "Hi" });
    expect(m.toolEvents![0].subAgentText).toBe("Hi");
  });

  it("attaches the steps of delegate_to_agent to its row", () => {
    let m = start(turn(), "d1", "delegate_to_agent");
    m = childStep(m, "projects-assistant", "s1", "list_tasks");
    expect(m.toolEvents![0].subAgentTools?.map((t) => t.id)).toEqual(["s1"]);
    expect(m.toolEvents![0].subAgentName).toBe("projects-assistant");
  });

  it("keeps a spawn child off a later call_agent row", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    m = childStep(m, "worker", "c1", "web_search");
    m = end(m, "sp1");
    m = start(m, "ca1", "call_agent");
    m = childStep(m, "email-assistant", "e1", "draft_reply");
    const [spawn, call] = m.toolEvents!;
    expect(spawn.subAgentActive).toBe(false);
    expect(spawn.subAgentTools?.map((t) => t.id)).toEqual(["c1"]);
    expect(call.subAgentTools?.map((t) => t.id)).toEqual(["e1"]);
    expect(call.subAgentName).toBe("email-assistant");
  });

  it("keeps the spawn row active when a worker fails with no step, and shows that child as failed", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    // A worker is a batch run: it sends no step before it fails.
    m = applySubAgentEvent(m, { type: "sub_agent_error", agentName: "worker", runId: "r1", error: "provider error" });
    const row = m.toolEvents![0];
    expect(row.subAgentActive).toBe(true);
    expect(row.status).toBe("running");
    expect(row.subAgentTools).toEqual([{ id: "err-r1", name: "worker", status: "error", result: "provider error" }]);
    // The steps of the other children still land on the row.
    m = childStep(m, "worker", "c2", "fetch_page");
    expect(m.toolEvents![0].subAgentTools!.map((t) => t.id)).toEqual(["err-r1", "c2"]);
  });

  it("never marks the step of a sibling when an error arrives between its start and its result", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    m = applySubAgentEvent(m, { type: "sub_agent_tool_start", agentName: "worker", id: "c1", name: "web_search" });
    m = applySubAgentEvent(m, { type: "sub_agent_error", agentName: "worker", runId: "r2", error: "boom" });
    m = applySubAgentEvent(m, { type: "sub_agent_tool_end", id: "c1", success: true, result: "3 results" });
    const steps = m.toolEvents![0].subAgentTools!;
    expect(steps.find((s) => s.id === "c1")).toMatchObject({ status: "done", result: "3 results" });
    expect(steps.find((s) => s.id === "err-r2")).toMatchObject({ status: "error", result: "boom" });
  });

  it("shows one failed step for one error that arrives twice", () => {
    let m = start(turn(), "sp1", "spawn_subagents");
    const err = { type: "sub_agent_error", agentName: "worker", runId: "r1", error: "x" };
    m = applySubAgentEvent(applySubAgentEvent(m, err), err);
    expect(m.toolEvents![0].subAgentTools).toHaveLength(1);
  });

  it("route.ts forwards the run id of a sub-agent error", () => {
    const route = readFileSync(join(__dirname, "..", "..", "src/app/api/agent/chat/route.ts"), "utf-8");
    expect(route).toMatch(/type: "sub_agent_error", agentName: [^}]*runId: String\(ev\.runId/);
  });

  it("still closes a call_agent row on a sub-agent error", () => {
    let m = start(turn(), "c1", "call_agent");
    m = applySubAgentEvent(m, { type: "sub_agent_error", agentName: "x", error: "boom" });
    expect(m.toolEvents![0].subAgentActive).toBe(false);
    expect(m.toolEvents![0].status).toBe("error");
  });

  it("drops a child step when no hand-off row is active", () => {
    const m = childStep(start(turn(), "t1", "web_search"), "worker", "c1", "web_search");
    expect(m.toolEvents![0].subAgentTools).toBeUndefined();
  });
});

type Acc = SubAgentAccumulator<{ id: string; name: string; status: string }>;

function acc(name: string, text: string, ids: string[]): Acc {
  return { name, text, tools: ids.map((id) => ({ id, name: "web_search", status: "done" })) };
}

describe("the saved turn (takeHandoff) keeps the same rows", () => {
  it("gives the spawn row its steps, its name and no text", () => {
    const { fields, rest } = takeHandoff("spawn_subagents", acc("worker", CHILD_JSON, ["c1", "c2"]));
    expect(fields).toMatchObject({ subAgentName: "worker", subAgentText: "" });
    expect((fields.subAgentTools as unknown[]).length).toBe(2);
    expect(rest).toEqual({ name: "", text: "", tools: [] });
  });

  it("gives delegate_to_agent its steps and its text", () => {
    const { fields, rest } = takeHandoff("delegate_to_agent", acc("projects-assistant", "Done.", ["s1"]));
    expect(fields).toMatchObject({ subAgentName: "projects-assistant", subAgentText: "Done." });
    expect(rest.tools).toEqual([]);
  });

  it("gives a later specialist row its own steps after a spawn row", () => {
    // spawn ends: the children's steps stream, then the row takes them.
    let a: Acc = acc("worker", CHILD_JSON, ["c1"]);
    const spawn = takeHandoff("spawn_subagents", a);
    a = spawn.rest;
    // the email specialist runs next and streams its own step.
    a = { name: "email-assistant", text: "", tools: [{ id: "e1", name: "draft_reply", status: "done" }] };
    const email = takeHandoff("email_assistant", a);
    expect(email.fields).toMatchObject({ subAgentName: "email-assistant" });
    expect((email.fields.subAgentTools as Array<{ id: string }>).map((t) => t.id)).toEqual(["e1"]);
  });

  it("does not label a later call_agent row worker", () => {
    const afterSpawn = takeHandoff("spawn_subagents", acc("worker", "", ["c1"])).rest;
    const withNext: Acc = {
      name: afterSpawn.name || "projects-assistant", text: "", tools: [{ id: "p1", name: "list_tasks", status: "done" }],
    };
    const call = takeHandoff("call_agent", withNext);
    expect(call.fields.subAgentName).toBe("projects-assistant");
  });

  it("leaves the accumulator alone for a tool that is no hand-off", () => {
    const a = acc("worker", "", ["c1"]);
    const { fields, rest } = takeHandoff("web_search", a);
    expect(fields).toEqual({});
    expect(rest).toBe(a);
  });

  it("route.ts folds each tool end through takeHandoff", () => {
    const route = readFileSync(join(__dirname, "..", "..", "src/app/api/agent/chat/route.ts"), "utf-8");
    expect(route).toContain("takeHandoff(name, subAgent)");
    expect(route).not.toContain('includes("call_agent")');
  });
});
