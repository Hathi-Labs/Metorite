/**
 * The chat's save to memory when its panel closes (H-236 follow-up).
 *
 * `AgentChat` posts the conversation to `/api/chat/memories` on unmount, and
 * the route files it in the member's Mem0 store. That save exists for the
 * DEFAULT agent only. The orchestrator can run on the direct LiteLLM path
 * (`mode: "litellm"`), and nothing on that path extracts memory.
 *
 * A NAMED agent never needs it. `AgentChat` sends every named agent in
 * `copilot` mode, so each turn runs through the gateway's `/agent/run/stream`.
 * The gateway extracts each turn at the run's end
 * (`gateway/routes/agent.py::_extract_run_memory`), and it skips a covered
 * run (`maf_coding_engine.md` §16.3). An unmount save for a named agent went
 * around that skip, so a covered Projects conversation reached memory.
 *
 * Spec: `project-docs/specs/maf_coding_engine.md` §16.3.
 * Fence: `src/lib/chatMemorySave.test.ts`.
 */
import type { ChatMessage } from "@/lib/chatStore";

/**
 * The names that mean the default agent. The chat proxy
 * (`app/api/agent/chat/route.ts`) maps each of them to `orchestrator`.
 */
export const DEFAULT_AGENT_NAMES: ReadonlySet<string> = new Set([
  "orchestrator",
  "default",
  "metorite",
  "",
]);

/** True when *name* is the default agent, in any of its aliases. */
export function isDefaultAgent(name: string | null | undefined): boolean {
  return DEFAULT_AGENT_NAMES.has(String(name ?? "").toLowerCase().trim());
}

type SaveOptions = {
  /** The agent that the chat shows when its panel closes. */
  agentName: string | null | undefined;
  /** The signed-in member. With none, the chat saves nothing. */
  memoryUserId: string | null | undefined;
  messages: readonly ChatMessage[];
  fetchImpl?: typeof fetch;
};

/**
 * Save the conversation on unmount, for the default agent only. Returns true
 * when it sent the request. It never throws.
 */
export function saveConversationOnUnmount({
  agentName,
  memoryUserId,
  messages,
  fetchImpl = fetch,
}: SaveOptions): boolean {
  if (!memoryUserId || !isDefaultAgent(agentName)) return false;
  const payload = messages
    .filter((m) => (m.role === "user" || m.role === "assistant") && m.content.trim())
    .map((m) => ({ role: m.role as "user" | "assistant", content: m.content }));
  if (payload.length === 0) return false;
  try {
    fetchImpl("/api/chat/memories", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // No userId: the scope is the signed-in member, resolved server-side.
      // Sending one here would be a claim the route no longer accepts.
      body: JSON.stringify({ messages: payload }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    return false;
  }
  return true;
}
