/**
 * The chat's ONE retry rule: which turn to re-send, and what leaves the thread.
 *
 * Two controls use it. The answer bubble's "Retry" regenerates an assistant
 * turn, and the error card's "Retry" (owner report, 2026-10-08) re-sends the
 * turn that failed. Both re-send the member's last message before the turn,
 * in the same thread, and both drop the old pair, so the thread never shows
 * the same question twice.
 *
 * `AgentChat`'s `handleRetryMessage` is the only caller. Fence:
 * `runErrors.test.ts`.
 */
import type { ChatMessage } from "@/hooks/useAgentChat";

/** A system turn that holds a failed turn's error card. */
export function isErrorTurn(m: Pick<ChatMessage, "role" | "content">): boolean {
  return m.role === "system" && m.content.startsWith("__ERROR__");
}

/** Whether a turn offers Retry: an answer, or a failed turn's error card. */
export function canRetry(m: Pick<ChatMessage, "role" | "content">): boolean {
  return m.role === "assistant" || isErrorTurn(m);
}

/**
 * What a Retry on turn `id` does, or null when there is nothing to re-send.
 * `resend` is the member's last message before the turn. `drop` is the turn
 * and that message, because `submitText` appends both again.
 */
export function retryPlan(
  messages: ReadonlyArray<Pick<ChatMessage, "id" | "role" | "content">>,
  id: string,
): { resend: string; drop: string[] } | null {
  const idx = messages.findIndex((m) => m.id === id);
  if (idx < 0 || !canRetry(messages[idx])) return null;
  for (let i = idx - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user") return { resend: m.content, drop: [messages[idx].id, m.id] };
  }
  return null;
}
