/**
 * cardTabs — the rules of the tabs on ONE approval card.
 *
 * Owner, 2026-11-03 (D173): "Instead of having, say, a stacked cards
 * animation, we just have one card. When there are multiple cards that need
 * to be approved, it shows up as one single card with tabs." The cards that
 * wait together are tabs inside one card frame. No stacked edges, no swipe,
 * no swap animation. `components/ui/CardTabs.tsx` draws the row.
 *
 * Pure, so the node-env vitest holds it. Fence: `src/lib/cardTabs.test.ts`.
 */

import type { PendingConfirmation } from "./confirmationQueue";

/** What a card is, for its tab. */
export type CardKind = "send" | "reply" | "task" | "reminder" | "other";

/** The icon of each kind: Lucide names, drawn by `<Icon name>`. */
export const KIND_ICON: Record<CardKind, string> = {
  send: "Mail",
  reply: "Reply",
  task: "CheckSquare",
  reminder: "Bell",
  other: "ShieldCheck",
};

/**
 * The kind of a card, in ONE place. The card value carries no kind field
 * (`cardFromEvent` keeps title, detail, context, rows and reply), so the
 * kind comes from the reply form, else from the server's stable title
 * phrases ("Send this new email?", "Create this task?", "Set this
 * reminder?", "Watch this task?"). A title it does not know is "other".
 */
export function cardKind(card: Pick<PendingConfirmation, "title" | "reply">): CardKind {
  if (card.reply !== undefined) return "reply";
  const t = card.title.trim();
  if (/^reply\b/i.test(t)) return "reply";
  if (/\breminder\b/i.test(t)) return "reminder";
  if (/^send\b/i.test(t) && /\b(email|mail|draft|reply|forward|message)\b/i.test(t)) return "send";
  if (/\btasks?\?$/i.test(t) && /^(create|watch|update|archive|delete|complete)\b/i.test(t)) return "task";
  return "other";
}

/** The count label that leads the tab row. */
export function tabsCount(total: number): string {
  return `${total} to approve`;
}

/** The accessible name of the tab row. */
export function tabsLabel(total: number, noun = "approvals"): string {
  return `${total} ${noun} to approve. Use the arrow keys to see each one.`;
}

/**
 * The tab an arrow key asks for, or null when the key is not one of ours.
 * The ends do not wrap.
 */
export function tabKeyTarget(key: string, index: number, total: number): number | null {
  if (total < 1) return null;
  const to = key === "ArrowRight" ? index + 1 : key === "ArrowLeft" ? index - 1 : key === "Home" ? 0 : key === "End" ? total - 1 : null;
  return to === null ? null : Math.min(Math.max(to, 0), total - 1);
}

/**
 * The key of the card to open after the queue changed, or null when the
 * member's card stays. A card that comes back first (Undo of a Reject, which
 * `restore` puts at the front) opens, so the member sees what they tried to
 * answer. A new card never arrives at the front any other way: the stream
 * appends. When the shown card was answered, `pickShown` takes the next tab
 * to the right, or else the one to the left.
 */
export function restoredFirst(prevKeys: readonly string[], keys: readonly string[]): string | null {
  if (prevKeys.length === 0 || keys.length === 0) return null;
  const first = keys[0];
  return prevKeys.includes(first) ? null : first;
}
