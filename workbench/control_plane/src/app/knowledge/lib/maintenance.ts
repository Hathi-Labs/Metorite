// ── Knowledge maintenance (WS-61 KB-AI-4, `knowledge_base.md` §22.3) ───────
//
// The client of `GET /knowledge/maintenance`
// (`gateway/routes/knowledge/maintenance.py`). The list holds the published
// pages that are overdue for review, have no owner, or have not been edited
// for a year. The gateway builds ONE row for each page, so a page with three
// reasons is one row with three chips. A manager (`knowledge:manage`) sees it.
// Every request goes through `call` in `api.ts`. The key sits under
// `KNOWLEDGE_CACHE`, so one `invalidate(KNOWLEDGE_CACHE)` after a verify also
// re-reads the list and the count.

import { call } from "./api";
import { KNOWLEDGE_HOME } from "./links";

/** The Maintenance view. */
export const KNOWLEDGE_MAINTENANCE = `${KNOWLEDGE_HOME}/maintenance`;

export const MAINTENANCE_KEY = "knowledge/maintenance";

/** The rows the view asks for: the gateway's maximum. */
export const MAINTENANCE_LIMIT = 500;

export type MaintenanceReason = "expired" | "unowned" | "stale";

/** One page of the list. `reasons` is in the gateway's order. */
export interface MaintenancePage {
  id: string;
  title: string;
  space_id: string;
  space_name: string;
  owner_email: string | null;
  review_at: string | null;
  updated_at: string | null;
  reasons: MaintenanceReason[];
}

export interface MaintenanceList {
  pages: MaintenancePage[];
  /** The whole list that the caller sees, not the cut at the limit. */
  counts: Record<MaintenanceReason, number>;
}

const RANK: Record<MaintenanceReason, number> = { expired: 0, unowned: 1, stale: 2 };

/** The first reason of a page decides its place: expired, then unowned, then stale. */
function rankOf(page: MaintenancePage): number {
  const ranks = page.reasons.map((r) => RANK[r] ?? 3);
  return ranks.length ? Math.min(...ranks) : 3;
}

/**
 * The rows in the order of the gateway: expired first (the oldest review
 * date first), then unowned, then stale, then the id. The gateway sorts
 * already. This keeps the order true when the cache hands back a list that
 * a local change edited.
 */
export function orderRows(pages: readonly MaintenancePage[]): MaintenancePage[] {
  return [...pages].sort((a, b) => {
    const byRank = rankOf(a) - rankOf(b);
    if (byRank) return byRank;
    if (rankOf(a) === 0) {
      const byDate = (a.review_at ?? "").localeCompare(b.review_at ?? "");
      if (byDate) return byDate;
    }
    return a.id.localeCompare(b.id);
  });
}

/** The read. A shape without a list is an error, not an empty list. */
export async function listMaintenance(): Promise<MaintenanceList> {
  const out = await call<MaintenanceList>(`/maintenance?limit=${MAINTENANCE_LIMIT}`);
  if (!out || !Array.isArray(out.pages)) throw new Error("The maintenance answer has no list.");
  const counts = Object.assign({ expired: 0, unowned: 0, stale: 0 }, out.counts);
  return { pages: orderRows(out.pages), counts };
}

/** A review date at or before now. A page with no date is not overdue. */
export function isOverdue(reviewAt: string | null | undefined, now: number = Date.now()): boolean {
  if (!reviewAt) return false;
  const at = Date.parse(reviewAt);
  return !Number.isNaN(at) && at <= now;
}

/** The chip of each reason. */
export const REASON_CHIPS: Record<MaintenanceReason, { label: string; tone: "warning" | "neutral"; icon: string }> = {
  expired: { label: "Overdue", tone: "warning", icon: "CalendarClock" },
  unowned: { label: "No owner", tone: "neutral", icon: "UserRound" },
  stale: { label: "Not edited for a year", tone: "neutral", icon: "Clock" },
};

/**
 * The words on a chip. The overdue chip carries the date: "Overdue since 27 Jul 2026".
 * The other reasons use their fixed label.
 */
export function chipLabel(
  reason: MaintenanceReason,
  reviewAt: string | null,
  day: (iso: string | null) => string,
): string {
  if (reason !== "expired") return REASON_CHIPS[reason].label;
  const since = day(reviewAt);
  return since ? `Overdue since ${since}` : REASON_CHIPS.expired.label;
}

/** The count a rail row shows: the expired pages, "99+" past 99, and none at 0. */
export function expiredCount(list: Pick<MaintenanceList, "counts"> | null | undefined): string | null {
  const n = list?.counts.expired ?? 0;
  if (n <= 0) return null;
  return n > 99 ? "99+" : String(n);
}
