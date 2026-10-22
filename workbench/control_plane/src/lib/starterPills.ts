/**
 * The starter pills of the empty chat (WS-62 CF-4, `chat_first_surface.md`
 * §4.6). With the chat-first flag on, the empty chat of the one assistant
 * shows three pills from the member's My Day feed: the headline of each of
 * the top three cards, in the feed's own order. A tap sends the headline as
 * the member's message. No model writes a starter pill.
 *
 * This file is the one rule, and it is pure, so `starterPills.test.ts` can
 * drive every branch. `AgentChat.tsx` reads the feed through the feed's own
 * cache key, the one My Day reads (`useFeed`), and hands the cards here. With
 * the flag off, no cards, or no headline that survives the trim, the fixed
 * list shows, as before.
 *
 * Fences: `starterPills.test.ts`, and the source block "the starter pills
 * come from the feed" in `lib/shell/assistant.test.ts`.
 */

/** How many pills the empty chat shows. */
export const STARTER_PILL_COUNT = 3;

/** About how long a pill may be. A longer headline is cut at a word. */
export const STARTER_PILL_CHARS = 60;

/** The part of a feed card the pills read: its headline (`FeedCard.title`). */
export interface StarterCard {
  title: string;
}

export interface StarterPillsInput {
  /** The chat-first flag, for the one assistant's empty chat. */
  chatFirst: boolean;
  /**
   * The feed cards the member holds, in the feed's order. Absent while the
   * feed loads, or after it failed with nothing cached.
   */
  cards: readonly StarterCard[] | null | undefined;
  /** The agent's fixed list, shown when the feed gives no pill. */
  fallback: readonly string[];
}

/**
 * One headline as a pill: whitespace folded, and cut at a word to about
 * `max` characters, with an ellipsis. A cut at a word that would leave less
 * than half the room cuts at `max` instead, so one long word still gives a
 * pill. Trailing punctuation goes before the ellipsis.
 */
export function trimHeadline(text: string, max = STARTER_PILL_CHARS): string {
  const folded = text.replace(/\s+/g, " ").trim();
  if (folded.length <= max) return folded;
  const cut = folded.slice(0, max);
  const space = cut.lastIndexOf(" ");
  const head = (space >= Math.floor(max / 2) ? cut.slice(0, space) : cut).replace(/[\s,;:.!?]+$/, "");
  return head ? `${head}…` : "";
}

/**
 * The pills the empty chat shows.
 *
 *   - The flag off: the fixed list, whatever the feed holds.
 *   - No cards yet, or none at all: the fixed list.
 *   - Else the first `STARTER_PILL_COUNT` headlines, in the feed's order,
 *     each trimmed (`trimHeadline`), with a repeat dropped (case folded).
 *     Fewer than three cards give fewer pills. No headline left gives the
 *     fixed list.
 *
 * The fallback comes back as a copy, never as the same array.
 */
export function starterPills({ chatFirst, cards, fallback }: StarterPillsInput): string[] {
  if (!chatFirst || !cards || cards.length === 0) return [...fallback];
  const out: string[] = [];
  const seen = new Set<string>();
  for (const card of cards) {
    if (out.length >= STARTER_PILL_COUNT) break;
    if (typeof card?.title !== "string") continue;
    const pill = trimHeadline(card.title);
    if (!pill) continue;
    const key = pill.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(pill);
  }
  return out.length > 0 ? out : [...fallback];
}
