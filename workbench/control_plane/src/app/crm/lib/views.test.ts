import { describe, expect, it } from "vitest";

import { ENTITIES } from "./types";
import { NON_ENTITY_TABS, parseView, selectTab, DEFAULT_VIEW, viewHref } from "./urlState";
import { CRM_RAIL, CRM_SETTINGS_VIEW, CRM_VIEWS, viewLabel } from "./views";

describe("the CRM rail (CRM-U1, CRM-Q3)", () => {
  it("draws one row for each tab the URL grammar knows, and no other", () => {
    // A tab with no row cannot be reached by a click. A row with no tab
    // parses back to Deals, so the rail would light the wrong row.
    // `module` has no fixed row: its rows come from the profile (CRM-Q6b,
    // `railEntries`, fenced in `components/crmQ6b.test.ts`).
    const ids = CRM_RAIL.map((v) => v.id).sort();
    expect(ids).toEqual([...NON_ENTITY_TABS.filter((t) => t !== "module"), ...ENTITIES].sort());
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("names organizations Companies, and keeps the slug in the URL (D95.2)", () => {
    const row = CRM_RAIL.find((v) => v.id === "organizations");
    expect(row?.label).toBe("Companies");
    const href = viewHref(selectTab(DEFAULT_VIEW, "organizations"));
    expect(href).toBe("/crm?tab=organizations");
    expect(parseView(href.split("?")[1]).tab).toBe("organizations");
  });

  it("has no Pipeline row, and lists the views in the order of the spec", () => {
    // Spec crm_platform.md section 18.4. Activities has no view yet.
    expect(CRM_RAIL.map((v) => v.label)).toEqual([
      "Leads",
      "Contacts",
      "Companies",
      "Deals",
      "Reports",
      "CRM settings",
    ]);
    expect(CRM_RAIL.some((v) => /pipeline/i.test(v.label))).toBe(false);
  });

  it("keeps the settings row last, with the id the old links use", () => {
    expect(CRM_RAIL[CRM_RAIL.length - 1]).toBe(CRM_SETTINGS_VIEW);
    expect(CRM_VIEWS).not.toContain(CRM_SETTINGS_VIEW);
    expect(CRM_SETTINGS_VIEW.id).toBe("settings");
    expect(parseView("?tab=settings").tab).toBe("settings");
  });

  it("gives the phone bar the label of the view on screen", () => {
    expect(viewLabel("deals")).toBe("Deals");
    expect(viewLabel("organizations")).toBe("Companies");
    expect(viewLabel("settings")).toBe("CRM settings");
  });
});

describe("the old links (CRM-Q3)", () => {
  it("opens Deals as a board from ?tab=pipeline and ?tab=board", () => {
    for (const search of ["?tab=pipeline", "?tab=board"]) {
      expect(parseView(search)).toMatchObject({ tab: "deals", layout: "board" });
    }
  });

  it("lands on the Deals board with no tab", () => {
    expect(parseView("")).toMatchObject({ tab: "deals", layout: "board" });
  });

  it("opens the deals list when the link asks for it", () => {
    const view = parseView("?tab=deals&layout=list");
    expect(view).toMatchObject({ tab: "deals", layout: "list" });
    expect(viewHref(view)).toBe("/crm?layout=list");
  });

  it("keeps a lead board link and drops a board on a tab with none", () => {
    expect(parseView("?tab=leads&layout=board").layout).toBe("board");
    expect(parseView("?tab=leads").layout).toBe("list");
    expect(parseView("?tab=contacts&layout=board").layout).toBe("list");
  });

  it("keeps the record sheet open over the board from an old link", () => {
    const view = parseView("?tab=pipeline&deal=d1");
    expect(view.record?.id).toBe("d1");
    expect(view).toMatchObject({ tab: "deals", layout: "board" });
  });
});
