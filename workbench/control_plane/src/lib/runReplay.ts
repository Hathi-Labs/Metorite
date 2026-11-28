/**
 * runReplay — which message a re-attach replays into, and which saved rows
 * it draws first (`chat_run_continuity.md` §4.10, review of #1171).
 *
 * Pure. `useAgentChat.attach` calls it with the rows the server holds, when
 * no message of the run is known.
 *
 * Fences (R7): `runStatus.test.ts` ("a run started elsewhere") and
 * `chatOrder.test.ts` (the replay places its rows by the server's order).
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
 * - `missing` is every saved row that this chat lacks and that comes after
 *   the last saved row that it shows, the target aside. That is the prompt
 *   of a run started elsewhere. "After" is the server's order, never a
 *   timestamp: a phone's clock and this tab's clock disagree, and the
 *   prompt of this run is older than a reply that this tab stamped
 *   (task 4146). With no saved row shown, every row it lacks is missing.
 *   `chatOrder.placeReplayRows` places them.
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
  let waterline = -1;
  saved.forEach((m, i) => { if (have.has(m.id) && m.id !== target?.id) waterline = i; });
  const missing = saved.filter((m, i) => i > waterline && !have.has(m.id) && m.id !== target?.id);
  return { target, missing };
}

/**
 * The id that a re-attach replays into (RESUME-3a, C2). The target's own id,
 * else the reply row that the server named for a continuation (`resumeId`),
 * else a new placeholder id. A replay into the server's own row id makes the
 * fold at the run's end write that same row, so one bubble shows.
 */
export function replayIdOf(
  target: Pick<ChatMessage, "id"> | null | undefined,
  resumeId: string | null | undefined,
  mint: () => string,
): string {
  return target?.id ?? (resumeId || mint());
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
