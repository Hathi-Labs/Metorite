/**
 * CRM-U10: the CRM's tabs in the phone's bottom bar.
 *
 * `lib/phoneNav.ts` decides the tabs, the page emits `cc-crm-nav`, and
 * `AppShell` draws them. This file holds the three pieces apart, because the
 * vitest setup has no DOM: the rule, the wire and the two source fences.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import {
  MORE_TAB,
  PHONE_TABS,
  moduleOfTab,
  moduleTabId,
  navDetail,
  phoneNav,
  readNavDetail,
} from "../lib/phoneNav";
import { railEntries } from "../lib/views";

const read = (p: string) => readFileSync(new URL(p, import.meta.url), "utf8");
const ids = (n: ReturnType<typeof phoneNav>) => n.tabs.map((t) => t.id);

const on = (tab: string, module?: string) => ({ tab, module: module ?? null });
const mod = (api_name: string, count: number, label = api_name) => ({
  api_name,
  label,
  singular_label: label,
  count,
});

describe("the tabs", () => {
  const all = railEntries(null);

  it("names Deals, Leads and Contacts, in that order", () => {
    expect(ids(phoneNav(all, on("deals"), null))).toEqual([
      "deals",
      "leads",
      "contacts",
    ]);
    expect(PHONE_TABS).toBe(3);
  });

  it("keeps the source's labels", () => {
    const entries = railEntries(null, {
      contacts: { label: "Existing Sites" },
    });
    const tab = phoneNav(entries, on("deals"), null).tabs.find(
      (t) => t.id === "contacts",
    );
    expect(tab?.label).toBe("Existing Sites");
  });

  it("gives a core list that the source turned off no tab, and takes the next one", () => {
    const states = { leads: { records: false, turned_off: true } };
    const entries = railEntries(null, null, null, states);
    expect(ids(phoneNav(entries, on("deals"), null, null, states))).toEqual([
      "deals",
      "contacts",
      "organizations",
    ]);
  });

  it("lights the tab of the view on screen", () => {
    expect(phoneNav(all, on("leads"), null).active).toBe("leads");
  });

  it("lights More for a view that has no tab", () => {
    for (const current of ["reports", "settings", "organizations"]) {
      expect(phoneNav(all, on(current), null).active).toBe(MORE_TAB);
    }
  });

  it("shows the open count on More only above zero", () => {
    expect(phoneNav(all, on("deals"), 4).badge).toBe(4);
    expect(phoneNav(all, on("deals"), 0).badge).toBeNull();
    expect(phoneNav(all, on("deals"), null).badge).toBeNull();
  });
});

describe("custom modules", () => {
  const modules = [
    mod("Sites", 2210),
    mod("Invoices", 1240),
    mod("Payments", 980),
    mod("Tiny", 3),
  ];
  // An org that lives in custom modules: no records in the built-in lists.
  const empty = {
    deals: { records: false, turned_off: false },
    leads: { records: false, turned_off: false },
    contacts: { records: false, turned_off: false },
    organizations: { records: false, turned_off: false },
  };

  it("fills the bar with the biggest modules when the built-in lists are empty", () => {
    const entries = railEntries(modules, null, null, empty);
    expect(ids(phoneNav(entries, on("deals"), null, modules, empty))).toEqual([
      "module:Sites",
      "module:Invoices",
      "module:Payments",
    ]);
  });

  it("puts a built-in list that holds records before any module", () => {
    const states = { ...empty, leads: { records: true, turned_off: false } };
    const entries = railEntries(modules, null, null, states);
    expect(ids(phoneNav(entries, on("deals"), null, modules, states))).toEqual([
      "leads",
      "module:Sites",
      "module:Invoices",
    ]);
  });

  it("keeps an empty built-in list for a slot no module fills", () => {
    const few = [mod("Sites", 10)];
    const entries = railEntries(few, null, null, empty);
    expect(ids(phoneNav(entries, on("deals"), null, few, empty))).toEqual([
      "module:Sites",
      "deals",
      "leads",
    ]);
  });

  it("lights the tab of the module on screen, and More for one with no tab", () => {
    const entries = railEntries(modules, null, null, empty);
    expect(
      phoneNav(entries, on("module", "Sites"), null, modules, empty).active,
    ).toBe("module:Sites");
    expect(
      phoneNav(entries, on("module", "Tiny"), null, modules, empty).active,
    ).toBe(MORE_TAB);
  });

  it("opens the module for a list that a module plays, under the list's label", () => {
    const overlays = { contacts: "Sites" };
    const labels = { contacts: { label: "Existing Sites" } };
    const entries = railEntries(modules, labels, overlays, null);
    const nav = phoneNav(entries, on("module", "Sites"), null, modules, null);
    const tab = nav.tabs.find((t) => t.id === "module:Sites");
    expect(tab?.label).toBe("Existing Sites");
    expect(nav.active).toBe("module:Sites");
    expect(nav.tabs.filter((t) => t.id === "module:Sites")).toHaveLength(1);
  });

  it("reads a module id back from a tab id", () => {
    expect(moduleOfTab(moduleTabId("Sites"))).toBe("Sites");
    expect(moduleOfTab("deals")).toBeNull();
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
    expect(page).toContain("moduleOfTab(");
    expect(page).toContain("railModules.onSelect(module)");
  });
});
