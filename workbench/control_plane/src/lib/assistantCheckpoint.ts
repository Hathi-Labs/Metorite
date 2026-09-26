/**
 * assistantCheckpoint — the one row shape the chat translator saves.
 *
 * `app/api/agent/chat/route.ts` saves the assistant turn every 3 s while it
 * streams, and once at the end. That route file may export only route names,
 * so the row shape lives here, where a test can reach it (WS-27bm S10).
 *
 * The row names its agent. Without `author_*` keys the gateway's `_attribute`
 * (`routes/chat.py`) stamps the ROOM's agent, and the COALESCE in its upsert
 * keeps that first stamp for ever. A Projects turn in a room whose agent is
 * the orchestrator then reloads as the orchestrator's, and loses its pills.
 * With `author_email` set to the agent that ran, the first stamp is correct.
 *
 * The reconnect path does not know which agent ran, so it sends no author.
 * The server then falls back to the room's agent, as before.
 *
 * Pure: no fetch, no Next.js.
 */

import { serializeReasoning } from "@/lib/chatStream";

export interface AssistantCheckpoint {
  threadId: string;
  content: string;
  toolEvents?: Array<Record<string, unknown>>;
  reasoningBlocks?: string[];
  progressLines?: string[];
  messageId?: string;
  todos?: Array<{ id: string; title: string; status: string }>;
  customEvents?: Array<{ name: string; value: unknown }>;
  segments?: Array<{ id: string; text: string }>;
  /** The agent that produced this turn. Absent on the reconnect path. */
  agentName?: string;
  /** The clock, for tests. Defaults to `Date.now()`. */
  now?: number;
}

/** True when the turn holds nothing worth a row. */
export function checkpointIsEmpty(c: AssistantCheckpoint): boolean {
  return (
    !c.content.trim() &&
    (c.toolEvents ?? []).length === 0 &&
    (c.reasoningBlocks ?? []).length === 0 &&
    (c.todos ?? []).length === 0 &&
    (c.customEvents ?? []).length === 0
  );
}

/** The one row `POST /chat/sessions/{id}/messages` receives for the turn. */
export function assistantCheckpointRow(c: AssistantCheckpoint): Record<string, unknown> {
  const now = c.now ?? Date.now();
  // Pack todos and segments (Phase 3b) into agent_state. There are no
  // dedicated columns for them.
  const agentState: Record<string, unknown> = {};
  if ((c.todos ?? []).length > 0) agentState.todos = c.todos;
  if ((c.segments ?? []).length > 0) agentState.segments = c.segments;
  const agentName = (c.agentName ?? "").trim();
  return {
    // The caller always supplies a message id (from the frontend, or a
    // per-stream fallback minted in translateAndPersistStream). A shared
    // `assistant-${threadId}` fallback once upserted every assistant turn of
    // the thread onto ONE row.
    id: c.messageId || `assistant-${c.threadId}-${now.toString(36)}`,
    role: "assistant",
    content: c.content,
    timestamp: now,
    tool_events: c.toolEvents ?? [],
    progress_lines: c.progressLines ?? [],
    reasoning: serializeReasoning(c.reasoningBlocks ?? []),
    agent_state: Object.keys(agentState).length > 0 ? agentState : null,
    custom_events: c.customEvents ?? [],
    ...(agentName ? { author_kind: "agent", author_email: agentName } : {}),
  };
}
