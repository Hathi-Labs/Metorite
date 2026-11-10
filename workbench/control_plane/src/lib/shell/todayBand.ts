/**
 * The Do first band as data (`package_host.md` §9.3, PKG-18a).
 *
 * The band is the top cards of the My Day feed, each with its `why` line.
 * My Day owns the ranking and the reasons (`my_day_feed.md` §7). This file
 * ranks nothing. It adds no request, no table and no model call.
 *
 * It is a pure function of the feed answer, the two sets that hide a card,
 * and the hold. It writes nothing: no preference, no order and no storage.
 * Fence: `arrangement.test.ts`.
 *
 * **The band is stable within a visit** (PKG-Q15). The band sits above the
 * column, and the column holds its order during a visit (`my_day_feed.md`
 * §7 rule 4). So a poll must not add a band row. A new row moves the held
 * column down under the pointer. The rule (`bandStep`):
 *
 *   - Only a FRESH answer that gives an item starts the hold. The hold is
 *     the ids of those items, in their order. An answer that gives no item
 *     starts no hold.
 *   - A cached answer from an earlier visit is not fresh. The band draws
 *     its items, and holds none. The first fresh answer then starts the
 *     hold. So the band changes once at most before it settles.
 *   - A later answer adds no item and reorders none. A held item takes the
 *     new content of its card.
 *   - A card that the member marks done, snoozes or hides leaves at once.
 *     That is a member action. A card that left the answer goes at once, as
 *     it does in the column (`visitCards`).
 *   - A hold with no item left is empty. The next fresh answer that gives
 *     an item starts a new hold. A hold with an item never gains one.
 *   - The member takes in the column's new cards (the "N new" pill). The
 *     column counts that act in a visit number, and a new number starts
 *     a new hold from the current answer.
 *   - A reload mounts the band again, so the hold starts again.
 *
 * Fence: `todayBand.test.ts`, "the hold".
 */
import { type FeedAnswer, type FeedApp, type FeedCard, type FeedGroupId, cardHidden, columnView } from "./feed";
import { isReminderKind } from "./reminderKinds";

/** The most items the band shows. */
export const BAND_LIMIT = 5;

/** The heading of the band (PKG-Q18). The calendar rail says "Today", so the band does not. */
export const BAND_HEADING = "Do first";

/**
 * The wrap of a title and a reason, as the column's card title has it
 * (`FeedCard.tsx`): two lines on a phone, one truncated line from `sm`.
 */
export const BAND_WRAP = "min-w-0 max-sm:line-clamp-2 max-sm:break-words sm:truncate";

/** The groups the band reads, in order, as `feedNeedsCount` counts them. */
const BAND_GROUPS: readonly FeedGroupId[] = ["now", "needs_you"];

export interface BandItem {
  signalId: string;
  app: FeedApp;
  title: string;
  /** The door to the entity. Empty when the card has none. */
  href: string;
  /** The `why` line of the card. Never empty. */
  reason: string;
}

/** The items of one visit, by signal id, in the order of the answer that started it. */
export interface BandHold {
  readonly ids: readonly string[];
  /** The visit number of the column when the hold started. */
  readonly visit: number;
}

/** What `bandStep` reads, apart from the feed. */
export interface BandRead {
  /** False while a newer answer is on its way over a cached one (`refreshing`). */
  fresh: boolean;
  /** The visit number of the column. It goes up when the member takes in new cards. */
  visit: number;
  limit?: number;
}

/** Whether a card may be an item: not hidden, not closed, not a reminder, and with a reason. */
function eligible(c: FeedCard, marked: ReadonlySet<string>, hidden: ReadonlySet<string>): boolean {
  // The reminder cards draw in the Reminders band (RX-3), so the column and
  // the band both leave them out, and no reminder shows twice.
  if (isReminderKind(c.kind) || hidden.has(c.signal_id) || cardHidden(c, marked)) return false;
  return Boolean(c.why?.trim());
}

const itemOf = (c: FeedCard): BandItem => ({
  signalId: c.signal_id,
  app: c.app,
  title: c.title,
  href: c.href,
  reason: (c.why ?? "").trim(),
});

/**
 * Up to `limit` items, in feed order: the cards of Now, then Needs you.
 * A card the member marked done or hid does not show. A reminder card does
 * not show, because the Reminders band draws it. A card with no `why` does
 * not show, so no item lacks a reason. Fewer than `limit` is normal.
 *
 * With a `held` hold, the items are the held ids that still show, in the
 * held order. No other card enters.
 */
export function todayBand(
  answer: FeedAnswer | undefined,
  marked: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  limit: number = BAND_LIMIT,
  held: BandHold | null = null,
): BandItem[] {
  if (!answer) return [];
  const items: BandItem[] = [];
  if (held) {
    const byId = new Map(answer.cards.map((c) => [c.signal_id, c] as const));
    for (const id of held.ids) {
      const c = byId.get(id);
      if (c && eligible(c, marked, hidden)) items.push(itemOf(c));
    }
    return items.slice(0, Math.max(0, limit));
  }
  // No hold yet: the order of a new visit. `columnView` puts each signal id
  // in one group once (`visitCards`), so no item shows twice.
  const view = columnView(answer, null, { shown: Number.MAX_SAFE_INTEGER, marked, hidden });
  for (const id of BAND_GROUPS) {
    for (const g of view.groups) {
      if (g.id !== id) continue;
      for (const c of g.cards) if (eligible(c, marked, hidden)) items.push(itemOf(c));
    }
  }
  return items.slice(0, Math.max(0, limit));
}

/**
 * One render of the band: the hold to keep, and the items to draw. The
 * component keeps the hold in state, so each later poll reads the same ids.
 * The rule is in the header of this file.
 */
export function bandStep(
  hold: BandHold | null,
  answer: FeedAnswer | undefined,
  marked: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  read: BandRead,
): { hold: BandHold | null; items: BandItem[] } {
  const limit = read.limit ?? BAND_LIMIT;
  // A hold of this visit with an item left keeps its ids. A hold of an
  // earlier visit, or a hold with no item left, is no hold.
  if (hold && hold.visit === read.visit) {
    const items = todayBand(answer, marked, hidden, limit, hold);
    if (items.length > 0) return { hold, items };
  }
  const items = todayBand(answer, marked, hidden, limit);
  if (items.length === 0 || !read.fresh) return { hold: null, items };
  return { hold: { ids: items.map((i) => i.signalId), visit: read.visit }, items };
}
