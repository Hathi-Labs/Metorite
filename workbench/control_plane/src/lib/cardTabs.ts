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

import { unfenced } from "./fencedText";
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
  if (/\btasks?\b/i.test(t) && /^(create|watch|update|archive|delete|complete)\b/i.test(t)) return "task";
  return "other";
}

/**
 * The compact rule of the tab row, given the widths. A row that overflows
 * goes compact: each tab that is not open shows its icon and dot only. It
 * remembers the full width it overflowed at, and grows back only when the
 * room is that wide plus a margin, so a row on the edge does not flicker.
 */
export function nextCompact(
  state: { compact: boolean; full: number },
  scrollWidth: number,
  clientWidth: number,
  margin = 8,
): { compact: boolean; full: number } {
  if (!state.compact) return { compact: scrollWidth > clientWidth, full: scrollWidth };
  if (clientWidth >= state.full + margin) return { compact: false, full: state.full };
  return state;
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

// ── The tab's words ─────────────────────────────────────────────────────────

/** The most characters of a tab label. The full text goes in the tooltip. */
export const TAB_LABEL_MAX = 24;

/** `s` cut to `n` characters, with an ellipsis when it was longer. */
export function cutLabel(s: string, n: number): string {
  const t = s.trim();
  return t.length <= n ? t : t.slice(0, Math.max(1, n - 1)).trimEnd() + "…";
}

/** A person's short name from "Name <addr>", "addr" or a plain name: the
 *  display name when the row has one, else the local part of the address. */
export function personName(raw: string): string {
  const first = unfenced(raw).split(/ · |[,;]/)[0] ?? "";
  const v = first.replace(/^(to|cc|bcc)\s*:\s*/i, "").trim();
  const m = /^"?([^"<]+?)"?\s*<[^>]+>$/.exec(v);
  if (m) return m[1].trim();
  const at = v.indexOf("@");
  if (at <= 0) return v;
  // An address with no display name: its local part, split on . _ and -,
  // and the first word with a capital ("sharat.satya" is "Sharat").
  const word = v.slice(0, at).split(/[._-]+/).find(Boolean) ?? v.slice(0, at);
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/** The object a card names: the first part of its detail, without «marks». */
function objectOf(detail: string | undefined): string {
  const first = (detail ?? "").split(" · ")[0] ?? "";
  return unfenced(first).replace(/[«»]/g, "").trim();
}

/** The first recipient a send card names: the first "to" row, else the first
 *  row, else the "To:" line of its text. */
function sendTo(card: Pick<PendingConfirmation, "rows" | "context">): string {
  const rows = card.rows ?? [];
  const row = rows.find((r) => r.kind === "to") ?? rows[0];
  if (row) return personName(row.label);
  const line = /^To:\s*(.+)$/m.exec(card.context ?? "");
  return line ? personName(line[1]) : "";
}

type TabCard = Pick<PendingConfirmation, "title" | "detail" | "context" | "rows" | "reply">;

/**
 * A tab's short label: a kind word and a short object, at most
 * `TAB_LABEL_MAX` characters. "Email to Kim", "Reply to Sharat",
 * "Task: Fix the extruder", "Reminder: Call Rajendra". Any other card shows
 * its title without the "?", cut. The tab's tooltip holds the full text.
 */
export function tabLabel(card: TabCard): string {
  const kind = cardKind(card);
  const generic = cutLabel(card.title.trim().replace(/\?$/, ""), TAB_LABEL_MAX);
  const lead = (word: string, object: string) =>
    object ? `${word} ${cutLabel(object, TAB_LABEL_MAX - word.length - 1)}` : "";
  switch (kind) {
    case "send":
      return lead("Email to", sendTo(card)) || "Email";
    case "reply": {
      const f = card.reply;
      const to = f ? f.recipients[f.replyAll ? "reply_all" : "reply"].to[0] : undefined;
      return lead("Reply to", to ? personName(to) : "") || "Reply";
    }
    case "task":
      return lead("Task:", objectOf(card.detail)) || generic;
    case "reminder":
      return lead("Reminder:", objectOf(card.detail)) || generic;
    default:
      return generic;
  }
}
