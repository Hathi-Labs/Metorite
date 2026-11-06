import { describe, expect, it } from "vitest";

import { activeNeedsTab, appRowCounts, appTabLabel, buildNeedsTabs, seeAllHref } from "./needsTabs";

const apps = (o: Record<string, number>) => new Map(Object.entries(o));

/*
 * Mutations, run by hand (2026-11-07): keep a zero tab (drop the `n > 0`
 * filter) and "drops a tab with no row" fails. Sort by count ascending and
 * "orders the apps" fails. Give All a count and "draws All with no count" fails. Return a one-tab bar and "no bar for one tab" fails.
 */
describe("buildNeedsTabs", () => {
  it("puts All first, then AI questions, then the apps by count, then Reminders", () => {
    const tabs = buildNeedsTabs({ apps: apps({ email: 2, projects: 5, calendar: 1 }), ai: 3, reminders: 4 });
    expect(tabs.map((t) => [t.id, t.label, t.count])).toEqual([
      ["all", "All", undefined],
      ["ai", "AI questions", 3],
      ["app:projects", "Projects", 5],
      ["app:email", "Email", 2],
      ["app:calendar", "Calendar", 1],
      ["reminders", "Reminders", 4],
    ]);
  });

  it("drops a tab with no row", () => {
    const tabs = buildNeedsTabs({ apps: apps({ email: 2, projects: 0 }), ai: 0, reminders: 0 });
    expect(tabs).toEqual([]);
    const two = buildNeedsTabs({ apps: apps({ email: 2, projects: 0, tasks: 1 }), ai: 0, reminders: 0 });
    expect(two.map((t) => t.id)).toEqual(["all", "app:email", "app:tasks"]);
  });

  it("draws All with no count, so it never disagrees with the page summary", () => {
    const tabs = buildNeedsTabs({ apps: apps({ email: 2, tasks: 7 }), ai: 1, reminders: 2 });
    expect(tabs[0].id).toBe("all");
    expect(tabs[0].count).toBeUndefined();
    expect(tabs.slice(1).every((t) => typeof t.count === "number")).toBe(true);
  });

  it("draws no bar for one tab", () => {
    expect(buildNeedsTabs({ apps: apps({ email: 4 }), ai: 0, reminders: 0 })).toEqual([]);
    expect(buildNeedsTabs({ apps: apps({}), ai: 0, reminders: 2 })).toEqual([]);
  });
});

describe("activeNeedsTab", () => {
  const tabs = buildNeedsTabs({ apps: apps({ email: 2, tasks: 1 }), ai: 0, reminders: 0 });
  it("keeps a tab that exists and falls back to All when it emptied", () => {
    expect(activeNeedsTab(tabs, "app:email")).toBe("app:email");
    expect(activeNeedsTab(tabs, "app:projects")).toBe("all");
    expect(activeNeedsTab([], "ai")).toBe("all");
  });
});

describe("seeAllHref", () => {
  it("links the Approvals tab to its list and no other tab", () => {
    expect(seeAllHref("app:approvals")).toBe("/approvals");
    expect(seeAllHref("all")).toBeNull();
    expect(seeAllHref("app:email")).toBeNull();
  });
});

describe("appTabLabel", () => {
  it("never shows a raw key", () => {
    expect(appTabLabel("email")).toBe("Email");
    expect(appTabLabel("knowledge")).toBe("Knowledge");
    expect(appTabLabel("purchase_orders")).toBe("Purchase Orders");
    expect(appTabLabel("vendor-desk")).toBe("Vendor Desk");
  });
  it("names an unknown app in the tab", () => {
    const tabs = buildNeedsTabs({ apps: apps({ email: 2, vendor_desk: 1 }), ai: 0, reminders: 0 });
    expect(tabs.map((t) => t.label)).toEqual(["All", "Email", "Vendor Desk"]);
  });
});

describe("appRowCounts", () => {
  it("counts each row of each app, with no cut", () => {
    const rows = (app: string, n: number) => Array.from({ length: n }, () => ({ app }));
    const counts = appRowCounts([{ cards: rows("tasks", 25) }, { cards: rows("email", 2) }, { cards: rows("tasks", 3) }]);
    expect(counts.get("tasks")).toBe(28);
    expect(counts.get("email")).toBe(2);
  });
});
