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
 * The rule is PER TURN, never the agent on screen at close. One chat can
 * switch agents, so a conversation can hold default turns and covered turns
 * together. Each assistant turn names its agent: `useAgentChat` stamps
 * `agentAuthor(<the agent it was sent to>)` when the turn starts, and the
 * server keeps that stamp on the saved row. The save takes each assistant
 * turn of the default agent, with the user messages that it answered. A turn
 * that names no agent is left out, because nothing says who answered it.
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

type Turn = Pick<ChatMessage, "role" | "content" | "authorKind" | "authorEmail">;

/** True when the assistant turn *m* names the default agent as its author. */
function byDefaultAgent(m: Turn): boolean {
  return m.authorKind === "agent" && typeof m.authorEmail === "string" && isDefaultAgent(m.authorEmail);
}

/**
 * The messages of *messages* that the default agent took part in: each of
 * its assistant turns, and the user messages since the previous assistant
 * turn. Empty messages and system messages never count.
 */
export function defaultAgentTurns(
  messages: readonly Turn[],
): { role: "user" | "assistant"; content: string }[] {
  const out: { role: "user" | "assistant"; content: string }[] = [];
  let asked: Turn[] = [];
  for (const m of messages) {
    if (m.role === "user") {
      asked.push(m);
      continue;
    }
    if (m.role !== "assistant") continue;
    if (byDefaultAgent(m)) {
      for (const turn of [...asked, m]) {
        if (turn.content.trim()) {
          out.push({ role: turn.role as "user" | "assistant", content: turn.content });
        }
      }
    }
    asked = [];
  }
  return out;
}

type SaveOptions = {
  /** The signed-in member. With none, the chat saves nothing. */
  memoryUserId: string | null | undefined;
  messages: readonly Turn[];
  fetchImpl?: typeof fetch;
};

/**
 * Save the default agent's turns on unmount. A conversation with no such
 * turn posts nothing. Returns true when it sent the request. Never throws.
 */
export function saveConversationOnUnmount({
  memoryUserId,
  messages,
  fetchImpl = fetch,
}: SaveOptions): boolean {
  if (!memoryUserId) return false;
  const payload = defaultAgentTurns(messages);
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
