/**
 * pinnedDraft — the one email draft that sits above the composer.
 *
 * Owner report, 2026-10-22. The member revised a reply over chat, and each
 * revision drew a new draft card at the foot of the thread. The cards
 * scrolled away under the chat, and the old drafts stayed open beside the
 * new one. Now:
 *
 * - The NEWEST draft of the session pins above the composer. Each revision
 *   replaces it in place, so the member always edits one card.
 * - In the thread, the pinned draft draws as one line that names the pin,
 *   and an older draft draws as one line that says a newer one replaced it.
 * - The member closes the pin with its X. The pin comes back only for a new
 *   draft (a new tool call id). Closed, the newest draft draws in full in
 *   the thread again.
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
