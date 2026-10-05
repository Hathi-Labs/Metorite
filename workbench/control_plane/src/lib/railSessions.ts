/**
 * Which chat a rail opens, and how it gets out of a chat it may not use.
 *
 * Production bug, 2026-10-05. A rail restored the "last" session of its agent
 * from a list that the whole browser shared. A second member who signed in on
 * that browser got the first member's chat, from another org, and every send
 * failed with "You are not a participant of this conversation." The list is
 * now keyed per member and org (`lib/sessions.ts`). This module is the second
 * line: when the server refuses a RESTORED id, the rail drops it and starts a
 * new chat, with no error card.
 *
 * ⚠️ Only a restored id recovers. A member who opens a chat on purpose, from
 * the history list or with the New chat button, sees a refusal as an error,
 * because that answer is about the chat they chose.
 *
 * Pure, apart from the session store, so the decisions test without a DOM.
 * Fence: `railSessions.test.ts`.
 */
import type { SessionRefusedHandler } from "@/lib/chatTurnFailure";
import {
  createSession,
  fetchAndMergeSessionsFromDb,
  forgetSession,
  getQueue,
  getSessions,
  upsertSession,
  type ChatSession,
} from "@/lib/sessions";

/** The open chat, and the id it was restored as (null: opened on purpose). */
export interface RailPick {
  activeId: string;
  restoredId: string | null;
}

export const NO_PICK: RailPick = { activeId: "", restoredId: null };

/**
 * The note a surface shows after a refused chat gave way to a new one, on
 * load or on send (PR #652 fix round 1: a chat that disappears is explained).
 * A status, not an error: nothing failed that the member can fix.
 */
export const RECOVERED_NOTICE =
  "The chat this browser remembered is not open to you, so a new chat is open.";
/** The second sentence, when the member's words went into the composer. */
export const RECOVERED_TEXT_NOTICE = " Your message is in the box. Press Send to send it.";

export function recoveredNotice(carried: string | undefined): string {
  return carried ? RECOVERED_NOTICE + RECOVERED_TEXT_NOTICE : RECOVERED_NOTICE;
}

/**
 * The words to put in the composer of the new chat: the refused message, then
 * every queued message of the refused chat. Unsent work is never dropped.
 * Undefined when there is nothing.
 */
export function carriedText(refusedId: string, pendingText?: string): string | undefined {
  const parts = [pendingText ?? "", ...getQueue(refusedId)]
    .map((t) => t.trim())
    .filter(Boolean);
  return parts.length > 0 ? parts.join("\n\n") : undefined;
}

/** How long a first visit waits for the server's list before it chooses. */
export const MERGE_WAIT_MS = 3_000;

/**
 * Like `restoreOrStart`, but when this browser holds no session of `agent`
 * it first waits (at most `waitMs`) for the server's list. A member's first
 * visit after the list moved to its per-member key would otherwise start an
 * empty chat row in every rail, while the server holds their chats.
 */
export async function restoreOrStartAfterMerge(
  agent: string,
  merge: () => Promise<ChatSession[]> = fetchAndMergeSessionsFromDb,
  waitMs: number = MERGE_WAIT_MS,
): Promise<RailPick> {
  if (!getSessions().some((s) => s.agentName === agent)) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      merge().catch(() => []),
      new Promise((resolve) => { timer = setTimeout(resolve, waitMs); }),
    ]);
    if (timer) clearTimeout(timer);
  }
  return restoreOrStart(agent);
}

/** Open the newest session of `agent` for the bound member, or start one. */
export function restoreOrStart(agent: string): RailPick {
  const existing = getSessions().find((s) => s.agentName === agent);
  if (existing) return { activeId: existing.id, restoredId: existing.id };
  return startFresh(agent);
}

/** A new session, opened on purpose. */
export function startFresh(agent: string): RailPick {
  const s = createSession(agent);
  upsertSession(s);
  return { activeId: s.id, restoredId: null };
}

/** Open `id` on purpose. A refusal of it is then an error the member sees. */
export function openDeliberately(id: string): RailPick {
  return { activeId: id, restoredId: null };
}

/**
 * The server refused `refusedId`. Returns the pick to move to, or null when
 * this is not a refusal to recover from, and the error must show.
 */
export function recoverRefused(
  pick: RailPick,
  refusedId: string,
  agent: string,
): RailPick | null {
  if (!refusedId) return null;
  if (refusedId !== pick.activeId || refusedId !== pick.restoredId) return null;
  forgetSession(refusedId);
  return startFresh(agent);
}

/** True when the open chat came from storage, so a refusal may recover. */
export function isRestored(pick: RailPick): boolean {
  return Boolean(pick.activeId) && pick.activeId === pick.restoredId;
}

/**
 * The handler a surface gives `AgentChat` as `onSessionRefused`: one for a
 * restored chat, and none for a chat opened on purpose, so its refusal shows.
 */
export function refusalHandler(
  pick: RailPick,
  recover: (refusedId: string, pendingText?: string) => boolean,
): SessionRefusedHandler | undefined {
  if (!isRestored(pick)) return undefined;
  const id = pick.activeId;
  return (text: string) => recover(id, text);
}
