// D99, navigation_shell.md §7.1.1 rule 5 — email cards draw in the one
// assistant.
//
// R7 fence `one-assistant-email-cards`: the steps of a delegated
// email-assistant run arrive as `subAgentTools` on the hand-off's row. The
// live reducer and the saved turn keep each step's arguments, a running
// specialist tool (`email_assistant`) takes the steps of its agent, and
// `withEmailSteps` gives the email card file each step as a tool event, with
// its own `account_id`. A step of another agent never becomes an email card.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import type { ChatMessage, ToolEvent } from "@/lib/chatStore";
import { applySubAgentEvent, applyStreamEvent, isSpecialistTool } from "@/lib/chatStream";
import { EMAIL_AGENT, delegatedSteps, stepAsToolEvent, withEmailSteps } from "@/lib/delegatedSteps";

const DRAFT_RESULT =
  "Draft from Work · vj@hathilabs.test (mailbox acc-work) (saved to Drafts):\n\nHi Dr Sagar\nDraft: draft_id=d-9 | subject: Re: Quote";

function turn(): ChatMessage {
  return { id: "a1", role: "assistant", content: "", timestamp: 0, toolEvents: [] };
}

describe("the orchestrator's specialist tool is a hand-off", () => {
  it("knows the tool by the agent's name", () => {
    expect(isSpecialistTool("email_assistant", "email-assistant")).toBe(true);
    expect(isSpecialistTool("Email_Assistant", "email-assistant")).toBe(true);
    expect(isSpecialistTool("projects_assistant", "email-assistant")).toBe(false);
    expect(isSpecialistTool("email_assistant", "")).toBe(false);
  });
});

describe("the live reducer keeps a delegated step on the specialist row", () => {
  it("attaches the step, with its arguments and result", () => {
    let m = applyStreamEvent(turn(), { type: "tool_start", id: "t1", name: "email_assistant", args: { task: "reply" } }, { foldedAnswerIdx: -1 } as never);
    m = applySubAgentEvent(m, {
      type: "sub_agent_tool_start", agentName: EMAIL_AGENT, id: "s1", name: "draft_reply",
      args: { email_id: "e-1", account_id: "acc-work" },
    });
    m = applySubAgentEvent(m, { type: "sub_agent_tool_end", id: "s1", result: DRAFT_RESULT, success: true });
    const row = m.toolEvents![0];
    expect(row.subAgentName).toBe(EMAIL_AGENT);
    expect(row.subAgentTools).toEqual([
      { id: "s1", name: "draft_reply", status: "done", result: DRAFT_RESULT, args: { email_id: "e-1", account_id: "acc-work" } },
    ]);
  });

  it("drops a step when no hand-off of its agent runs", () => {
    let m = applyStreamEvent(turn(), { type: "tool_start", id: "t1", name: "projects_assistant", args: {} }, { foldedAnswerIdx: -1 } as never);
    m = applySubAgentEvent(m, { type: "sub_agent_tool_start", agentName: EMAIL_AGENT, id: "s1", name: "draft_reply" });
    expect(m.toolEvents![0].subAgentTools).toBeUndefined();
  });
});

describe("withEmailSteps gives the email cards each delegated step", () => {
  const handOff: ToolEvent = {
    id: "t1", name: "email_assistant", args: { task: "reply" }, status: "done",
    subAgentName: EMAIL_AGENT,
    subAgentTools: [
      { id: "s1", name: "read_thread", status: "done", result: "Thread: Quote", args: { email_id: "e-1" } },
      { id: "s2", name: "draft_reply", status: "done", result: DRAFT_RESULT, args: { email_id: "e-1", account_id: "acc-work" } },
    ],
  };
  const other: ToolEvent = {
    id: "t2", name: "projects_assistant", args: {}, status: "done", subAgentName: "projects-assistant",
    subAgentTools: [{ id: "p1", name: "comment", status: "done", result: "ok" }],
  };

  it("adds the email steps after the turn's own events, with their own account", () => {
    const out = withEmailSteps([handOff, other]);
    expect(out.map((e) => e.id)).toEqual(["t1", "t2", "s1", "s2"]);
    expect(out[3]).toEqual({ id: "s2", name: "draft_reply", args: { email_id: "e-1", account_id: "acc-work" }, result: DRAFT_RESULT, status: "done" });
  });

  it("reads a row saved before steps had arguments", () => {
    expect(stepAsToolEvent({ id: "x", name: "read_email", status: "done" }).args).toEqual({});
  });

  it("never draws a step twice, and never a step of another agent", () => {
    expect(delegatedSteps([handOff, handOff], EMAIL_AGENT).map((e) => e.id)).toEqual(["s1", "s2"]);
    expect(delegatedSteps([other], EMAIL_AGENT)).toEqual([]);
  });
});

describe("the saved turn keeps the same shape", () => {
  const src = (p: string) => readFileSync(join(__dirname, "..", "..", p), "utf-8");

  it("route.ts folds a specialist tool's steps and their arguments", () => {
    const route = src("src/app/api/agent/chat/route.ts");
    expect(route).toContain('|| isSpecialistTool(name, subAgent.name)');
    expect(route).toMatch(/status: "running", args: stArgs/);
    expect(route).toMatch(/type: "sub_agent_tool_start".*args: stArgs/);
  });

  it("the bubble and the chat read the delegated steps", () => {
    expect(src("src/components/MessageBubble.tsx")).toContain("toolEvents={emailCardEvents}");
    expect(src("src/components/AgentChat.tsx")).toContain("withEmailSteps(m.toolEvents)");
  });
});
