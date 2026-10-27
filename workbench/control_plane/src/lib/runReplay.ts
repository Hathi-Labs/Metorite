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
 * - The target is the run's own saved reply row: the last reply after the
 *   member's last turn. A row in `reject` belongs to an earlier run, and is
 *   never a target, so a run started elsewhere never replays into it.
 *   `rejectFor` builds that set.
 * - `missing` is every saved row that this chat lacks and that is newer than
 *   what shows. That is the prompt of a run started elsewhere.
 *
 * A saved row carries no `streaming` flag, so "finished" alone cannot tell
 * the current run's reply from an old one. The run's identity does.
 */
export function planReplay(
  saved: readonly ChatMessage[],
  shown: readonly ChatMessage[],
  reject: ReadonlySet<string> = new Set(),
): { target: ChatMessage | null; missing: ChatMessage[] } {
  const have = new Set(shown.map((m) => m.id));
  const row = runRowOf(saved);
  const target = row && !reject.has(row.id) ? { ...row, streaming: true } : null;
  const tail = shown[shown.length - 1]?.timestamp ?? 0;
  const missing = saved.filter((m) => !have.has(m.id) && m.id !== target?.id && m.timestamp > tail);
  return { target, missing };
}

/**
 * The replies a re-attach must never take: the reply of the run that ended
 * in this chat (`doneId`) and, when the server named a run with ANOTHER
 * identity (`stale`), every reply this chat holds. They are all an old
 * run's.
 */
export function rejectFor(
  shown: readonly ChatMessage[],
  doneId: string | null,
  stale: boolean,
): Set<string> {
  const out = new Set<string>();
  if (doneId) out.add(doneId);
  if (stale) for (const m of shown) if (m.role === "assistant") out.add(m.id);
  return out;
}
