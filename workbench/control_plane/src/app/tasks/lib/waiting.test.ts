import { describe, expect, it } from "vitest";
import type { GtdItem } from "./types";
import {
  STALE_WAITING_DAYS,
  daysWaiting,
  groupByWaitingOn,
  isStaleWaiting,
  isWaitingOverdue,
  nudgeOutcome,
  waitingLine,
} from "./waiting";

// Minimal GtdItem factory — only the waiting fields matter to this math.
const item = (over: Partial<GtdItem>): GtdItem =>
  ({ id: "x", title: "t", disposition: "WAITING", isMine: false, ...over }) as GtdItem;

const DAY = 86_400_000;
const HOUR = 3_600_000;
// A fixed "now" so nothing here depends on the wall clock — the exact bug this
// module exists to end (the UI shipped a MOCK_NOW frozen at 2026-06-30).
const NOW = Date.UTC(2026, 7, 2, 9, 0, 0); // 2 Aug 2026, 09:00 UTC
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const ahead = (ms: number) => new Date(NOW + ms).toISOString();

describe("daysWaiting — the 'since-when' of who/what/since-when (§1 line 46)", () => {
  it("counts whole days since delegation", () => {
    expect(daysWaiting(item({ delegatedAt: ago(3 * DAY) }), NOW)).toBe(3);
    // Floors: 3d 23h still reads as 3d.
    expect(daysWaiting(item({ delegatedAt: ago(3 * DAY + 23 * HOUR) }), NOW)).toBe(3);
  });

  it("is 0 on the day it was delegated, not null", () => {
    expect(daysWaiting(item({ delegatedAt: ago(2 * HOUR) }), NOW)).toBe(0);
  });

  it("is null when there is no delegation date — the view must not invent one", () => {
    expect(daysWaiting(item({}), NOW)).toBeNull();
    expect(daysWaiting(item({ delegatedAt: "not-a-date" }), NOW)).toBeNull();
  });

  it("clamps a future delegation date to 0 rather than going negative", () => {
    expect(daysWaiting(item({ delegatedAt: ahead(2 * DAY) }), NOW)).toBe(0);
  });
});

describe("isWaitingOverdue — 'flags rows past expected_by' (§6 line 540)", () => {
  it("is true only once expectedBy is in the past", () => {
    expect(isWaitingOverdue(item({ expectedBy: ago(1) }), NOW)).toBe(true);
    expect(isWaitingOverdue(item({ expectedBy: ago(30 * DAY) }), NOW)).toBe(true);
    expect(isWaitingOverdue(item({ expectedBy: ahead(1) }), NOW)).toBe(false);
    // Exactly now is not yet past.
    expect(isWaitingOverdue(item({ expectedBy: new Date(NOW).toISOString() }), NOW))
      .toBe(false);
  });

  it("falls back to dueAt when nobody promised a date", () => {
    // No expectedBy ⇒ no promise was made, so the honest line is the item's
    // own deadline — read LIVE, so it can never be a stale copy.
    expect(isWaitingOverdue(item({ dueAt: ago(10 * DAY) }), NOW)).toBe(true);
    expect(isWaitingOverdue(item({ dueAt: ahead(DAY) }), NOW)).toBe(false);
    expect(isWaitingOverdue(item({ expectedBy: undefined, dueAt: ago(1) }), NOW))
      .toBe(true);
  });

  it("lets an explicit promise win over dueAt in BOTH directions", () => {
    // Promised for next week even though my own deadline has passed → they are
    // not late, and the badge must not say they are.
    expect(
      isWaitingOverdue(item({ expectedBy: ahead(7 * DAY), dueAt: ago(2 * DAY) }), NOW),
    ).toBe(false);
    // Promised for yesterday even though my deadline is still ahead → late.
    expect(
      isWaitingOverdue(item({ expectedBy: ago(DAY), dueAt: ahead(7 * DAY) }), NOW),
    ).toBe(true);
  });

  it("is false when there is no date at all — nothing to be late against", () => {
    expect(isWaitingOverdue(item({}), NOW)).toBe(false);
    expect(isWaitingOverdue(item({ expectedBy: undefined, dueAt: undefined }), NOW))
      .toBe(false);
  });

  it("does not read the wall clock when a nowMs is injected", () => {
    const stale = item({ expectedBy: ago(DAY) });
    // Same item, a "now" from before the promise date → not overdue.
    expect(isWaitingOverdue(stale, NOW - 5 * DAY)).toBe(false);
    expect(isWaitingOverdue(stale, NOW)).toBe(true);
  });
});

describe("waitingLine — which fact the row is judged on", () => {
  it("names the explicit promise when there is one", () => {
    const line = waitingLine(item({ expectedBy: ahead(DAY), dueAt: ago(DAY) }));
    expect(line).toEqual({ iso: ahead(DAY), kind: "promised" });
  });

  it("names the item's own deadline when nobody promised", () => {
    const line = waitingLine(item({ dueAt: ago(DAY) }));
    expect(line).toEqual({ iso: ago(DAY), kind: "due" });
  });

  it("is null when there is no line, and agrees with isWaitingOverdue", () => {
    expect(waitingLine(item({}))).toBeNull();
    expect(isWaitingOverdue(item({}), NOW)).toBe(false);
  });
});

describe("isStaleWaiting — same 5-day rule as the gateway's /tasks/insights", () => {
  it("pins the threshold the SQL states", () => {
    expect(STALE_WAITING_DAYS).toBe(5);
  });

  it("is false at 4d 23h and true at 5d 1h (the server's boundary)", () => {
    // ai.py inbox_insights: w.delegated_at < now() - interval '5 days'
    expect(isStaleWaiting(item({ delegatedAt: ago(4 * DAY + 23 * HOUR) }), NOW))
      .toBe(false);
    expect(isStaleWaiting(item({ delegatedAt: ago(5 * DAY + HOUR) }), NOW))
      .toBe(true);
  });

  it("is false at exactly five days — strictly greater, like the SQL", () => {
    expect(isStaleWaiting(item({ delegatedAt: ago(5 * DAY) }), NOW)).toBe(false);
  });

  it("is false without a delegation date", () => {
    expect(isStaleWaiting(item({}), NOW)).toBe(false);
  });

  it("is independent of expectedBy — overdue and stale are different clocks", () => {
    // Promised for tomorrow, but handed over three weeks ago and silent since.
    const quietButNotLate = item({
      delegatedAt: ago(21 * DAY),
      expectedBy: ahead(DAY),
    });
    expect(isStaleWaiting(quietButNotLate, NOW)).toBe(true);
    expect(isWaitingOverdue(quietButNotLate, NOW)).toBe(false);
    // Late already, but only asked for yesterday.
    const lateButFresh = item({ delegatedAt: ago(DAY), expectedBy: ago(HOUR) });
    expect(isStaleWaiting(lateButFresh, NOW)).toBe(false);
    expect(isWaitingOverdue(lateButFresh, NOW)).toBe(true);
  });
});

describe("groupByWaitingOn — the view's who/what/since-when rows", () => {
  const sai = { name: "Sai Kumar", email: "sai@fracktal.in" };
  const priya = { name: "Priya", email: "priya@fracktal.in" };

  it("groups by person and renders who, what and since-when together", () => {
    const rows = [
      item({ id: "a", title: "Vendor quote", waitingOn: sai, delegatedAt: ago(3 * DAY) }),
      item({ id: "b", title: "Datasheet", waitingOn: priya, delegatedAt: ago(DAY) }),
      item({ id: "c", title: "PO signature", waitingOn: sai, delegatedAt: ago(9 * DAY) }),
    ];
    const groups = groupByWaitingOn(rows, NOW);
    expect(groups.map((g) => g.label).sort()).toEqual(["Priya", "Sai Kumar"]);
    const saiGroup = groups.find((g) => g.label === "Sai Kumar")!;
    // WHO (group label) · WHAT (title) · SINCE-WHEN (daysWaiting) — all three.
    expect(saiGroup.items.map((i) => i.title)).toEqual(["PO signature", "Vendor quote"]);
    expect(saiGroup.items.map((i) => daysWaiting(i, NOW))).toEqual([9, 3]);
  });

  it("keys on email, so two people with the same display name stay apart", () => {
    const groups = groupByWaitingOn(
      [
        item({ id: "a", waitingOn: { name: "Priya", email: "priya@fracktal.in" } }),
        item({ id: "b", waitingOn: { name: "Priya", email: "priya.n@fracktal.in" } }),
      ],
      NOW,
    );
    expect(groups).toHaveLength(2);
  });

  it("buckets a record with no person rather than dropping the row", () => {
    const groups = groupByWaitingOn([item({ id: "a", title: "Orphan" })], NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Unassigned");
    expect(groups[0].items).toHaveLength(1);
  });

  it("puts the person you most need to chase first (overdue count)", () => {
    const groups = groupByWaitingOn(
      [
        item({ id: "a", waitingOn: priya }),
        item({ id: "b", waitingOn: priya }),
        item({ id: "c", waitingOn: sai, expectedBy: ago(2 * DAY) }),
      ],
      NOW,
    );
    expect(groups.map((g) => g.label)).toEqual(["Sai Kumar", "Priya"]);
    expect(groups[0].overdueCount).toBe(1);
    expect(groups[1].overdueCount).toBe(0);
  });

  it("returns nothing for an empty list", () => {
    expect(groupByWaitingOn([], NOW)).toEqual([]);
  });
});

describe("nudgeOutcome", () => {
  it("names the person when one was told", () => {
    expect(nudgeOutcome({ notified: ["priya@fracktal.in"], skipped: [] })).toEqual({
      ok: true,
      message: "Nudged priya@fracktal.in.",
    });
  });

  it("counts them when several were told", () => {
    const got = nudgeOutcome({ notified: ["a@x.in", "b@x.in"], skipped: [] });
    expect(got.ok).toBe(true);
    expect(got.message).toBe("Nudged 2 people.");
  });

  it("🔴 does NOT claim success when the server told nobody", () => {
    // The defect this function exists to prevent. `notified` empty on a 200
    // means the person cannot open the task, and a row that said "Nudged"
    // would leave somebody waiting on a colleague who was never told.
    const got = nudgeOutcome({ notified: [], skipped: ["priya@fracktal.in"] });
    expect(got.ok).toBe(false);
    expect(got.message).toContain("cannot open this task");
    expect(got.message).not.toMatch(/^Nudged/);
  });

  it("says so plainly when nothing happened at all", () => {
    const got = nudgeOutcome({ notified: [], skipped: [] });
    expect(got.ok).toBe(false);
    expect(got.message).toBe("Nobody was nudged.");
  });

  it("is never ok with an empty notified list, whatever else is set", () => {
    for (const skipped of [[], ["a@x.in"], ["a@x.in", "b@x.in"]]) {
      expect(nudgeOutcome({ notified: [], skipped }).ok).toBe(false);
    }
  });
});
