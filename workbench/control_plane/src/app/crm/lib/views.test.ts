import { describe, expect, it } from "vitest";

import { ENTITIES } from "./types";
import { NON_ENTITY_TABS, parseView, selectTab, DEFAULT_VIEW, viewHref } from "./urlState";
import { CRM_RAIL, CRM_SETTINGS_VIEW, CRM_VIEWS, viewLabel } from "./views";

describe("the CRM rail (CRM-U1)", () => {
  it("draws one row for each tab the URL grammar knows, and no other", () => {
    // A tab with no row cannot be reached by a click. A row with no tab
    // parses back to the board, so the rail would light the wrong row.
    const ids = CRM_RAIL.map((v) => v.id).sort();
    expect(ids).toEqual([...NON_ENTITY_TABS, ...ENTITIES].sort());
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("names organizations Companies, and keeps the slug in the URL (D95.2)", () => {
    const row = CRM_RAIL.find((v) => v.id === "organizations");
    expect(row?.label).toBe("Companies");
    const href = viewHref(selectTab(DEFAULT_VIEW, "organizations"));
    expect(href).toBe("/crm?tab=organizations");
    expect(parseView(href.split("?")[1]).tab).toBe("organizations");
  });

  it("opens on the pipeline, and keeps the settings row last", () => {
    expect(CRM_VIEWS[0].id).toBe("board");
    expect(CRM_RAIL[CRM_RAIL.length - 1]).toBe(CRM_SETTINGS_VIEW);
    expect(CRM_SETTINGS_VIEW.id).toBe("settings");
  });

  it("gives the phone bar the label of the view on screen", () => {
    expect(viewLabel("board")).toBe("Pipeline");
    expect(viewLabel("organizations")).toBe("Companies");
    expect(viewLabel("settings")).toBe("Pipeline settings");
  });
});
