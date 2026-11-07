/**
 * The Today band as data (`package_host.md` §9.3, PKG-18a).
 *
 * The band is the top cards of the My Day feed, each with its `why` line.
 * My Day owns the ranking and the reasons (`my_day_feed.md` §7). This file
 * ranks nothing. It adds no request, no table and no model call.
 *
 * It is a pure function of the feed answer and the two sets that hide a
 * card. It writes nothing: no preference, no order and no storage. Fence:
 * `arrangement.test.ts`.
 */
import { type FeedAnswer, type FeedApp, type FeedGroupId, columnView } from "./feed";

/** The most items the band shows. */
export const BAND_LIMIT = 5;

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

/**
 * Up to `limit` items, in feed order: the cards of Now, then Needs you.
 * A card the member marked done or hid does not show. A card with no `why`
 * does not show, so no item lacks a reason. Fewer than `limit` is normal.
 */
export function todayBand(
  answer: FeedAnswer | undefined,
  marked: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  limit: number = BAND_LIMIT,
): BandItem[] {
  if (!answer) return [];
  // No held visit: the band follows the answer. `columnView` drops a hidden
  // or done card, as the column does.
  const view = columnView(answer, null, { shown: Number.MAX_SAFE_INTEGER, marked, hidden });
  const items: BandItem[] = [];
  const seen = new Set<string>();
  for (const id of BAND_GROUPS) {
    for (const g of view.groups) {
      if (g.id !== id) continue;
      for (const c of g.cards) {
        const reason = c.why?.trim();
        if (!reason || seen.has(c.signal_id)) continue;
        seen.add(c.signal_id);
        items.push({ signalId: c.signal_id, app: c.app, title: c.title, href: c.href, reason });
      }
    }
  }
  return items.slice(0, Math.max(0, limit));
}
