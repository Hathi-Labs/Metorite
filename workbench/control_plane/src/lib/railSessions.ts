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
  forgetSession,
  getSessions,
  upsertSession,
} from "@/lib/sessions";

/** The open chat, and the id it was restored as (null: opened on purpose). */
export interface RailPick {
  activeId: string;
  restoredId: string | null;
}

export const NO_PICK: RailPick = { activeId: "", restoredId: null };

/**
 * The note a rail shows after a send was refused and a new chat took its
 * place. A status, not an error: nothing failed that the member can fix.
 */
export const RECOVERED_NOTICE =
  "This browser held a chat from another sign-in, so a new chat is open. " +
  "Your message is in the box. Press Send to send it.";

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
