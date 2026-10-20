/**
 * CRM-U10: the CRM's tabs in the phone's bottom bar.
 *
 * `lib/phoneNav.ts` decides the tabs, the page emits `cc-crm-nav`, and
 * `AppShell` draws them. This file holds the three pieces apart, because the
 * vitest setup has no DOM: the rule, the wire and the two source fences.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { MORE_TAB, PHONE_TABS, navDetail, phoneNav, readNavDetail } from "../lib/phoneNav";
import { railEntries } from "../lib/views";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
const ids = (n: ReturnType<typeof phoneNav>) => n.tabs.map((t) => t.id);

describe("the tabs", () => {
  const all = railEntries(null);

  it("names Deals, Leads and Contacts, in that order", () => {
    expect(ids(phoneNav(all, "deals", null))).toEqual(["deals", "leads", "contacts"]);
    expect(PHONE_TABS).toBe(3);
  });

  it("keeps the source's labels", () => {
    const entries = railEntries(null, { contacts: { label: "Existing Sites" } });
    const tab = phoneNav(entries, "deals", null).tabs.find((t) => t.id === "contacts");
    expect(tab?.label).toBe("Existing Sites");
  });

  it("gives a core list that the source turned off no tab, and takes the next one", () => {
    const states = { leads: { records: false, turned_off: true } };
    expect(ids(phoneNav(railEntries(null, null, null, states), "deals", null))).toEqual([
      "deals",
      "contacts",
      "organizations",
    ]);
  });

  it("lights the tab of the view on screen", () => {
    expect(phoneNav(all, "leads", null).active).toBe("leads");
  });

  it("lights More for a view that has no tab", () => {
    for (const current of ["reports", "settings", "module", "organizations"]) {
      expect(phoneNav(all, current, null).active).toBe(MORE_TAB);
    }
  });

  it("shows the open count on More only above zero", () => {
    expect(phoneNav(all, "deals", 4).badge).toBe(4);
    expect(phoneNav(all, "deals", 0).badge).toBeNull();
    expect(phoneNav(all, "deals", null).badge).toBeNull();
  });
});

describe("the wire", () => {
  it("round-trips a tab id and ignores every other app's detail", () => {
    expect(readNavDetail(navDetail("leads"))).toBe("leads");
    expect(readNavDetail(navDetail(MORE_TAB))).toBe(MORE_TAB);
    expect(readNavDetail("projects-tree")).toBeNull();
    expect(readNavDetail(undefined)).toBeNull();
  });
});

describe("the source fences", () => {
  const shell = read("../../../components/AppShell.tsx");
  const page = read("../page.tsx");

  it("AppShell draws the CRM tabs, and only from what the page emits", () => {
    expect(shell).toContain('startsWith("/crm")');
    expect(shell).toContain("cc-crm-nav");
    expect(shell).toContain("navDetail(");
  });

  it("the page emits the tabs and clears them on unmount", () => {
    expect(page).toContain('"cc-crm-nav"');
    expect(page).toContain("phoneNav(");
    expect(page).toMatch(/cc-crm-nav[\s\S]{0,200}detail: null/);
  });

  it("the page answers a tap through the one chooseView", () => {
    expect(page).toContain("readNavDetail(");
    expect(page).toContain("MORE_TAB");
  });
});
