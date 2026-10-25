/**
 * The pure half of the Maintenance view (WS-61 KB-AI-4,
 * `knowledge_base.md` §22.3), and of the care of a page.
 *
 * The gateway builds one row for each page, so a page with three reasons is
 * one row. The client keeps that order and shows the expired count.
 */
import { describe, expect, it, vi } from "vitest";

import { dayLabel } from "./labels";
import { PAGE_MENU, pageMenuItems, type PageMenuState } from "./pageMenu";
import { intervalOptions, intervalValue } from "./care";
import {
  expiredCount,
  isOverdue,
  listMaintenance,
  orderRows,
  reasonsText,
  type MaintenancePage,
} from "./maintenance";

const row = (id: string, reasons: MaintenancePage["reasons"], review_at: string | null = null): MaintenancePage => ({
  id,
  title: id,
  space_id: "s",
  space_name: "Company",
  owner_email: null,
  review_at,
  updated_at: null,
  reasons,
});

describe("the row order", () => {
  it("puts expired first (the oldest review first), then unowned, then stale, then the id", () => {
    const out = orderRows([
      row("d", ["stale"]),
      row("c", ["unowned"]),
      row("b", ["expired"], "2026-05-01T00:00:00Z"),
      row("a", ["stale", "expired"], "2026-03-01T00:00:00Z"),
      row("a2", ["unowned", "stale"]),
    ]);
    expect(out.map((p) => p.id)).toEqual(["a", "b", "a2", "c", "d"]);
  });
});

describe("the reasons text", () => {
  it("names each reason once, in order", () => {
    const text = reasonsText(row("p", ["expired", "unowned", "stale"], "2026-03-03T00:00:00Z"), dayLabel);
    expect(text).toBe("Review overdue since 3 Mar 2026. No owner. Not edited for a year.");
  });

  it("drops the date when the page has none", () => {
    expect(reasonsText(row("p", ["expired"]), dayLabel)).toBe("Review overdue.");
  });
});

describe("the rail count", () => {
  it("is the expired pages, 99+ past 99, and none at 0", () => {
    expect(expiredCount(null)).toBeNull();
    expect(expiredCount({ counts: { expired: 0, unowned: 4, stale: 1 } })).toBeNull();
    expect(expiredCount({ counts: { expired: 3, unowned: 0, stale: 0 } })).toBe("3");
    expect(expiredCount({ counts: { expired: 150, unowned: 0, stale: 0 } })).toBe("99+");
  });
});

describe("overdue", () => {
  it("is a review date at or before now, and never a missing date", () => {
    const now = Date.parse("2026-10-25T00:00:00Z");
    expect(isOverdue("2026-10-24T00:00:00Z", now)).toBe(true);
    expect(isOverdue("2026-10-25T00:00:00Z", now)).toBe(true);
    expect(isOverdue("2026-10-26T00:00:00Z", now)).toBe(false);
    expect(isOverdue(null, now)).toBe(false);
    expect(isOverdue("not a date", now)).toBe(false);
  });
});

describe("the read", () => {
  it("fills a missing count and orders the rows", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({ pages: [row("z", ["stale"]), row("y", ["expired"], "2026-01-01T00:00:00Z")], counts: { expired: 1 } }),
      })),
    );
    const out = await listMaintenance();
    expect(out.pages.map((p) => p.id)).toEqual(["y", "z"]);
    expect(out.counts).toEqual({ expired: 1, unowned: 0, stale: 0 });
    vi.unstubAllGlobals();
  });
});

describe("the care entries of the page menu", () => {
  const state = (over: Partial<PageMenuState> = {}): PageMenuState => ({
    canWrite: false,
    canManage: false,
    archived: false,
    editing: false,
    fullWidth: false,
    starred: false,
    published: true,
    ...over,
  });
  const ids = (s: PageMenuState) => pageMenuItems(s).flatMap((r) => (r.kind === "entry" ? [r.entry.id] : []));

  it("shows Set owner and Review every to a writer of a published page", () => {
    expect(ids(state({ canWrite: true }))).toEqual(expect.arrayContaining(["set-owner", "review-every"]));
    expect(PAGE_MENU.filter((e) => e.id === "set-owner" || e.id === "review-every")).toHaveLength(2);
  });

  it("hides both from a reader, from a draft and from an archived page", () => {
    for (const s of [state(), state({ canWrite: true, published: false }), state({ canWrite: true, archived: true })]) {
      expect(ids(s)).not.toContain("set-owner");
      expect(ids(s)).not.toContain("review-every");
    }
  });
});

describe("the review interval", () => {
  it("offers the default and the presets, and keeps an odd current value", () => {
    expect(intervalOptions(null).map((o) => o.value)).toEqual(["default", "30", "90", "180", "365"]);
    expect(intervalOptions(45).map((o) => o.value)).toEqual(["default", "30", "45", "90", "180", "365"]);
  });

  it("sends null for the default and the days otherwise", () => {
    expect(intervalValue("default")).toBeNull();
    expect(intervalValue("90")).toBe(90);
    expect(intervalValue("0")).toBeNull();
  });
});
