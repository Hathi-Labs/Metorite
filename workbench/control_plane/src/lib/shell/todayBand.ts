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
 * column down under the pointer. The rule:
 *
 *   - The first answer that holds a card starts the hold. The hold is the
 *     ids of the items that answer gives, in their order.
 *   - A later answer adds no item and reorders none. A held item takes the
 *     new content of its card.
 *   - A card that the member marks done, snoozes or hides leaves at once.
 *     That is a member action. A card that left the answer goes at once, as
 *     it does in the column (`visitCards`).
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

/** The items of one visit, by signal id, in the order of the first answer. */
export interface BandHold {
  readonly ids: readonly string[];
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
 * One render of the band: the hold to keep, and the items to draw. With no
 * hold, the first answer that holds a card starts one. The component keeps
 * the hold it returns in state, so each later poll reads the same ids.
 */
export function bandStep(
  hold: BandHold | null,
  answer: FeedAnswer | undefined,
  marked: ReadonlySet<string>,
  hidden: ReadonlySet<string>,
  limit: number = BAND_LIMIT,
): { hold: BandHold | null; items: BandItem[] } {
  if (hold) return { hold, items: todayBand(answer, marked, hidden, limit, hold) };
  if (!answer || answer.cards.length === 0) return { hold: null, items: [] };
  const items = todayBand(answer, marked, hidden, limit);
  return { hold: { ids: items.map((i) => i.signalId) }, items };
}
