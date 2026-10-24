/**
 * pinnedDraft — which email draft in a chat is the live one.
 *
 * Owner report, 2026-10-22. The member revised a reply over chat, and each
 * revision drew a new draft card. The old drafts stayed open beside the new
 * one. Owner report, 2026-10-23: a draft pinned above the composer covered
 * the chat on a phone. Now:
 *
 * - The NEWEST draft of the session draws in full in the thread, and it
 *   scrolls with the thread. Nothing pins above the composer.
 * - An older draft draws as one line that says a newer one replaced it.
 * - `draftPin` still names a `pinned` draft for a caller that wants one.
 *   The chat passes `pinnedId: null` in its scope.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/pinnedDraft.test.ts`.
 */

import { createContext } from "react";

/** The tools whose result is an editable draft (`EmailToolCards.tsx`). */
export const DRAFT_TOOLS: ReadonlySet<string> = new Set(["draft_reply", "draft_email"]);

interface DraftEventLike {
  id: string;
  name: string;
  status?: string;
  result?: string | null;
}

interface MessageLike<E extends DraftEventLike> {
  toolEvents?: E[];
}

/** The session's drafts, oldest first: done, with a body, and an act. A
 *  result that did not act (`acted` says no) is never a draft. */
export function draftsOf<E extends DraftEventLike>(
  messages: readonly MessageLike<E>[],
  acted: (e: E) => boolean = () => true,
): E[] {
  const out: E[] = [];
  const seen = new Set<string>();
  for (const m of messages) {
    for (const e of m.toolEvents ?? []) {
      if (seen.has(e.id) || !DRAFT_TOOLS.has(e.name)) continue;
      if (e.status !== "done" || !(e.result ?? "").trim() || !acted(e)) continue;
      seen.add(e.id);
      out.push(e);
    }
  }
  return out;
}

export interface DraftPin<E extends DraftEventLike> {
  /** The draft above the composer, or null when there is none or the member
   *  closed it. */
  pinned: E | null;
  /** The ids of the drafts a newer draft replaced. */
  superseded: ReadonlySet<string>;
}

/** Which draft pins, and which drafts a newer one replaced. `closedId` is
 *  the draft the member closed. */
export function draftPin<E extends DraftEventLike>(
  drafts: readonly E[],
  closedId: string | null,
): DraftPin<E> {
  const newest = drafts.length ? drafts[drafts.length - 1] : null;
  return {
    pinned: newest && newest.id !== closedId ? newest : null,
    superseded: new Set(drafts.slice(0, -1).map((d) => d.id)),
  };
}

/** What a draft card in the thread reads, so it draws the pin's one line
 *  and not a second editable copy. Empty outside a chat with a pin. */
export interface DraftPinScope {
  pinnedId: string | null;
  superseded: ReadonlySet<string>;
}

export const DraftPinContext = createContext<DraftPinScope>({
  pinnedId: null,
  superseded: new Set(),
});
