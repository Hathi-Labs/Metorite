/**
 * Fence for the chat's unmount memory save (H-236 follow-up,
 * `project-docs/specs/maf_coding_engine.md` §16.3).
 *
 * The save takes only the turns that the DEFAULT agent took part in, read
 * from each turn's own author stamp, never from the agent on screen at close.
 * So a named agent's turns post nothing, because the gateway extracts them
 * and skips a covered run. The default agent's turns still save, as before,
 * whichever way the member switched. The last two tests read the source, so
 * the component and the proxy cannot drift away from the rule.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import { agentAuthor } from "@/lib/projectsAgent";
import { DEFAULT_AGENT_NAMES, defaultAgentTurns, isDefaultAgent, saveConversationOnUnmount } from "./chatMemorySave";

let next = 0;
function user(content: string): ChatMessage {
  next += 1;
  return { id: `u${next}`, role: "user", content, timestamp: next };
}
function reply(agent: string, content: string): ChatMessage {
  next += 1;
  return { id: `a${next}`, role: "assistant", content, timestamp: next, ...agentAuthor(agent) };
}

function fakeFetch() {
  return vi.fn(async () => new Response("{}", { status: 202 })) as unknown as typeof fetch &
    ReturnType<typeof vi.fn>;
}

function posted(fetchImpl: ReturnType<typeof fakeFetch>): unknown {
  const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
  expect(url).toBe("/api/chat/memories");
  expect(init.method).toBe("POST");
  expect(init.keepalive).toBe(true);
  return JSON.parse(String(init.body)).messages;
}

function source(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
}

const MEMBER = "member@example.com";

describe("the chat's unmount memory save", () => {
  it.each(["projects-assistant", "email-assistant", "task-manager", "crm-assistant"])(
    "posts nothing for a conversation with the named agent %s",
    (agent) => {
      const fetchImpl = fakeFetch();
      const messages = [user("chart the burndown"), reply(agent, "Y earns 100")];
      expect(saveConversationOnUnmount({ memoryUserId: MEMBER, messages, fetchImpl })).toBe(false);
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it.each(["orchestrator", "metorite", "default", "", " Orchestrator "])(
    "saves a conversation with the default agent %j, as before",
    (agent) => {
      const fetchImpl = fakeFetch();
      const messages: ChatMessage[] = [
        user("what is due today"),
        reply(agent, "Two tasks."),
        { id: "s", role: "system", content: "a checkpoint", timestamp: 99 },
        user("   "),
      ];
      expect(saveConversationOnUnmount({ memoryUserId: MEMBER, messages, fetchImpl })).toBe(true);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      expect(posted(fetchImpl)).toEqual([
        { role: "user", content: "what is due today" },
        { role: "assistant", content: "Two tasks." },
      ]);
    },
  );

  it("default first, then a named agent: the default turns still save", () => {
    const fetchImpl = fakeFetch();
    const messages = [
      user("what is due today"), reply("orchestrator", "Two tasks."),
      user("chart the burndown"), reply("projects-assistant", "Y earns 100"),
    ];
    expect(saveConversationOnUnmount({ memoryUserId: MEMBER, messages, fetchImpl })).toBe(true);
    expect(posted(fetchImpl)).toEqual([
      { role: "user", content: "what is due today" },
      { role: "assistant", content: "Two tasks." },
    ]);
  });

  it("a covered named agent first, then the default: the covered turns stay out", () => {
    const fetchImpl = fakeFetch();
    const messages = [
      user("chart the burndown"), reply("projects-assistant", "Y earns 100"),
      user("thanks, now a haiku"), reply("orchestrator", "Bars rise in the dark."),
    ];
    expect(saveConversationOnUnmount({ memoryUserId: MEMBER, messages, fetchImpl })).toBe(true);
    expect(posted(fetchImpl)).toEqual([
      { role: "user", content: "thanks, now a haiku" },
      { role: "assistant", content: "Bars rise in the dark." },
    ]);
  });

  it("pairs every user message with the reply that answered it", () => {
    expect(defaultAgentTurns([
      user("one"), user("two"), reply("orchestrator", "both"),
      user("three"), reply("email-assistant", "mail"),
      user("left unanswered"),
    ])).toEqual([
      { role: "user", content: "one" },
      { role: "user", content: "two" },
      { role: "assistant", content: "both" },
    ]);
  });

  it("leaves out a turn that names no agent, and saves nothing without a member", () => {
    const fetchImpl = fakeFetch();
    const unnamed: ChatMessage = { id: "x", role: "assistant", content: "who said this", timestamp: 1 };
    expect(saveConversationOnUnmount({
      memoryUserId: MEMBER, messages: [user("hi"), unnamed], fetchImpl,
    })).toBe(false);
    expect(saveConversationOnUnmount({
      memoryUserId: "", messages: [user("hi"), reply("orchestrator", "hello")], fetchImpl,
    })).toBe(false);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("names the same default agent as the chat proxy", () => {
    expect([...DEFAULT_AGENT_NAMES].sort()).toEqual(["", "default", "metorite", "orchestrator"]);
    expect(isDefaultAgent(undefined)).toBe(true);
    expect(isDefaultAgent("projects-assistant")).toBe(false);
    const route = source("../app/api/agent/chat/route.ts");
    expect(route).toContain('isDefaultAgent(agentName) ? "orchestrator" : agentName');
    expect(route).not.toMatch(/new Set\(\["orchestrator"/);
  });

  it("AgentChat saves on unmount only through this rule, and passes no close-time agent", () => {
    const chat = source("../components/AgentChat.tsx");
    // No second, unguarded save of the conversation.
    expect(chat).not.toContain("/api/chat/memories");
    expect(chat).toContain("saveConversationOnUnmount({ memoryUserId, messages: messagesRef.current });");
    // The turn stamp that the rule reads is set when each turn starts.
    const hook = source("../hooks/useAgentChat.ts");
    expect(hook).toMatch(/streaming: true, toolEvents: \[\][^]*?\.\.\.agentAuthor\(agentNameRef\.current\)/);
  });
});
