/**
 * Fence for the chat's unmount memory save (H-236 follow-up,
 * `project-docs/specs/maf_coding_engine.md` §16.3).
 *
 * A named agent's chat posts nothing to `/api/chat/memories` when its panel
 * closes, because the gateway extracts its turns and skips a covered run. The
 * default agent's chat still saves, as before. The last two tests read the
 * source, so the component and the proxy cannot drift away from the rule.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/lib/chatStore";
import { DEFAULT_AGENT_NAMES, isDefaultAgent, saveConversationOnUnmount } from "./chatMemorySave";

const TURN: ChatMessage[] = [
  { id: "1", role: "user", content: "chart the burndown", timestamp: 1 },
  { id: "2", role: "assistant", content: "Y earns 100", timestamp: 2 },
  { id: "3", role: "system", content: "a checkpoint", timestamp: 3 },
  { id: "4", role: "user", content: "   ", timestamp: 4 },
];

function fakeFetch() {
  return vi.fn(async () => new Response("{}", { status: 202 })) as unknown as typeof fetch &
    ReturnType<typeof vi.fn>;
}

function source(rel: string): string {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");
}

describe("the chat's unmount memory save", () => {
  it.each(["projects-assistant", "email-assistant", "task-manager", "crm-assistant"])(
    "posts nothing for the named agent %s",
    (agentName) => {
      const fetchImpl = fakeFetch();
      const sent = saveConversationOnUnmount({
        agentName, memoryUserId: "member@example.com", messages: TURN, fetchImpl,
      });
      expect(sent).toBe(false);
      expect(fetchImpl).not.toHaveBeenCalled();
    },
  );

  it.each(["orchestrator", "metorite", "default", "", " Orchestrator "])(
    "saves the conversation for the default agent %j, as before",
    (agentName) => {
      const fetchImpl = fakeFetch();
      const sent = saveConversationOnUnmount({
        agentName, memoryUserId: "member@example.com", messages: TURN, fetchImpl,
      });
      expect(sent).toBe(true);
      expect(fetchImpl).toHaveBeenCalledTimes(1);
      const [url, init] = fetchImpl.mock.calls[0] as [string, RequestInit];
      expect(url).toBe("/api/chat/memories");
      expect(init.method).toBe("POST");
      expect(init.keepalive).toBe(true);
      expect(JSON.parse(String(init.body))).toEqual({
        messages: [
          { role: "user", content: "chart the burndown" },
          { role: "assistant", content: "Y earns 100" },
        ],
      });
    },
  );

  it("saves nothing with no member or no message", () => {
    const fetchImpl = fakeFetch();
    expect(saveConversationOnUnmount({
      agentName: "orchestrator", memoryUserId: "", messages: TURN, fetchImpl,
    })).toBe(false);
    expect(saveConversationOnUnmount({
      agentName: "orchestrator", memoryUserId: "member@example.com", messages: TURN.slice(2), fetchImpl,
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

  it("AgentChat saves on unmount only through this rule, with the agent it shows", () => {
    const chat = source("../components/AgentChat.tsx");
    // No second, unguarded save of the conversation.
    expect(chat).not.toContain("/api/chat/memories");
    expect(chat).toMatch(/saveConversationOnUnmount\(\{\s*agentName: agentNameRef\.current,/);
    expect(chat).toMatch(/agentNameRef\.current = currentAgentName;/);
  });
});
