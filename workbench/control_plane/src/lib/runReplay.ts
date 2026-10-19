/**
 * runReplay — which message a re-attach replays into, and which saved rows
 * it draws first (`chat_run_continuity.md` §4.10, review of #1171).
 *
 * Pure. `useAgentChat.attach` calls it with the rows the server holds, when
 * no message of the run is known.
 *
 * Fence (R7): `runStatus.test.ts` ("a run started elsewhere").
 */

import type { ChatMessage } from "@/lib/chatStore";

/** The reply row of the run in flight: the last assistant row after the
 *  member's last turn, or null. */
export function runRowOf(messages: readonly ChatMessage[]): ChatMessage | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.role === "user") return null;
    if (m.role === "assistant") return m;
  }
  return null;
}

/**
 * The replay target among the server's rows, and the rows to draw first.
 *
 * - The target is the run's own saved reply row. A reply that this chat
 *   already holds as FINISHED belongs to an earlier run, and is never a
 *   target: a run started elsewhere must not replay into it.
 * - `missing` is every saved row that this chat lacks and that is newer than
 *   what shows. That is the prompt of a run started elsewhere.
 */
export function planReplay(
  saved: readonly ChatMessage[],
  shown: readonly ChatMessage[],
): { target: ChatMessage | null; missing: ChatMessage[] } {
  const have = new Set(shown.map((m) => m.id));
  const finished = new Set(shown.filter((m) => !m.streaming).map((m) => m.id));
  const row = runRowOf(saved);
  const target = row && !finished.has(row.id) ? { ...row, streaming: true } : null;
  const tail = shown[shown.length - 1]?.timestamp ?? 0;
  const missing = saved.filter((m) => !have.has(m.id) && m.id !== target?.id && m.timestamp > tail);
  return { target, missing };
}
