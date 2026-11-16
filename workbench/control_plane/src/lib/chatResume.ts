/**
 * chatResume — the small pure rules of a chat that takes a resumed run
 * (RESUME-3a, `project-docs/specs/chat_run_continuity.md` §4.12.4 item 5).
 *
 * A late Approve of a parked card starts a NEW run on the thread (case B of
 * §4.12.4 item 2): its own run id, its own reply row at the end of the chat,
 * and no member turn. The run state (`lib/runStatus.ts`, the `resumed` event)
 * and the answer POST (`lib/respondInput.ts`, `resumedOf`) hold the rest.
 *
 * Fence (R7): `chatResume.test.ts`.
 */

import type { ChatMessage } from "@/lib/chatStore";

/**
 * The message that the "Approved" line of a card draws under: the last
 * message before the reply of the run that goes on now. With no such reply
 * it is the last message. Null for an empty chat, and the line then draws at
 * the foot.
 */
export function receiptAnchorBeforeRun(
  messages: readonly Pick<ChatMessage, "id" | "role" | "content">[],
  runMessageId: string | null,
): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.id === runMessageId) continue;
    // A notice line is no turn, so a line never draws under it.
    if (m.role === "system" && m.content.startsWith("__ERROR__")) continue;
    return m.id;
  }
  return null;
}

/**
 * The cards to ask the server about when it stops listing a parked thread
 * (RESUME-3a step 7). Each blocking card that still waits on screen, and each
 * card that a remount drew again as waiting. The caller hands the set to
 * `cardsToSettle`, so a failed read settles nothing.
 */
export function waitingCardIds(
  cards: readonly { requestId?: string; redraft?: unknown }[],
  questions: readonly (string | null | undefined)[],
  redrawnWaiting: ReadonlySet<string>,
): Set<string> {
  const out = new Set(redrawnWaiting);
  for (const c of cards) if (c.requestId && !c.redraft) out.add(c.requestId);
  for (const q of questions) if (q) out.add(q);
  return out;
}
