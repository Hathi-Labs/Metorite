// The two-row toolbar and the saved views (WS-53 CRM-U8, crm_platform.md §20).
// The runner has no DOM, so each component is rendered to a string.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ViewChips from "@/components/ViewChips";
import { chipList } from "@/lib/savedViews";

import type { FieldDef } from "../lib/fields";
import { customFilters, setCustom, setRange } from "../lib/filterBar";
import {
  activeFilterChips,
  anyFilter,
  filterFields,
  filtersInForce,
  groupFields,
  rangeText,
  removeFilter,
  stageOptions,
} from "../lib/filterFields";
import {
  applyView,
  captureView,
  isBlankState,
  moduleOfScope,
  moduleScope,
  type SavedView,
} from "../lib/savedViews";
import { DEFAULT_VIEW } from "../lib/urlState";
import { defaultViewOf } from "../lib/useViewActions";
import ModuleList from "./ModuleList";

const noop = () => undefined;
const read = (rel: string) => readFileSync(new URL(rel, import.meta.url), "utf8");

const pick = (key: string, fill: number | null, type = "select"): FieldDef => ({
  key,
  label: key.charAt(0).toUpperCase() + key.slice(1),
  type,
  options: [
    { label: "West", value: "West" },
    { label: "East", value: "East" },
  ],
  position: 0,
  visible: true,
  source: "zoho",
  api_name: key,
  fill,
});

const view = (over: Partial<SavedView> = {}): SavedView => ({
  id: "v1",
  entity: "deals",
  name: "Mine",
  owner_email: "ann@x.test",
  shared: false,
  layout: "list",
  filters: { owner: "me" },
  sort: null,
  columns: null,
  pipeline_id: null,
  position: 0,
  mine: true,
  ...over,
});

// ── The "+ Filter" field list ───────────────────────────────────────────────

describe("the + Filter field list", () => {
  const custom = customFilters(
    [pick("region", 0.2), pick("tier", 0.9), pick("seats", 0.5, "number"), pick("signed", 0.4, "boolean")],
    true,
  );

  it("groups the fields in the order Dates, Picklists, Numbers, Other", () => {
    const fields = filterFields("deals", custom);
    expect(groupFields(fields).map((g) => g.group)).toEqual(["Dates", "Picklists", "Numbers", "Other"]);
    const dates = groupFields(fields)[0].items.map((f) => f.label);
    expect(dates).toEqual(["Created", "Updated", "Closing"]);
  });

  it("puts the fullest picklist first and shows its fill", () => {
    const picklists = groupFields(filterFields("deals", custom)).find((g) => g.group === "Picklists")!;
    expect(picklists.items.map((f) => f.label)).toEqual(["Tier", "Region"]);
    expect(picklists.items[0].fill).toBe(0.9);
  });

  it("offers Closing on deals only, Converted on leads only, and Source on both", () => {
    const labels = (entity: "deals" | "leads" | "contacts") =>
      filterFields(entity, []).map((f) => f.label);
    expect(labels("deals")).toContain("Closing");
    expect(labels("leads")).not.toContain("Closing");
    expect(labels("leads")).toContain("Converted");
    expect(labels("deals")).not.toContain("Converted");
    expect(labels("contacts")).toContain("Source");
  });

  it("hides Created in Mirror mode", () => {
    expect(filterFields("deals", [], true).map((f) => f.label)).not.toContain("Created");
  });

  it("finds a field by the words in its label and drops an empty group", () => {
    const groups = groupFields(filterFields("deals", custom), "ti");
    expect(groups.map((g) => g.group)).toEqual(["Picklists"]);
    expect(groups[0].items.map((f) => f.label)).toEqual(["Tier"]);
    expect(groupFields(filterFields("deals", custom), "zzz")).toEqual([]);
  });

  it("a module list offers + Filter too (CRM-U9, see crmU9.test.ts)", () => {
    const html = renderToStaticMarkup(
      createElement(ModuleList, moduleProps()),
    );
    expect(html).toContain(">Filter<");
  });
});

// ── The chips of the filters in force ───────────────────────────────────────

describe("the filter chips", () => {
  const fields = filterFields("leads", customFilters([pick("region", 0.5)], true));

  it("names each filter in force", () => {
    let v = setCustom(DEFAULT_VIEW, "region", "West");
    v = setRange(setRange(v, "close_from", "2026-10-01"), "close_to", "2026-12-31");
    v = { ...v, source: "email", includeConverted: true };
    expect(activeFilterChips(v, fields, 2026).map((c) => c.label)).toEqual([
      "Closing: 1 Oct–31 Dec",
      "Source: Email",
      "Converted",
      "Region: West",
    ]);
  });

  it("draws no chip for the owner, the stage or the search", () => {
    const v = { ...DEFAULT_VIEW, owner: "me", statusId: "s1", q: "acme" };
    expect(activeFilterChips(v, fields)).toEqual([]);
    expect(anyFilter(v)).toBe(true);
    expect(filtersInForce(v, [])).toBe(2);
  });

  it("removes only the filter of its chip", () => {
    let v = setRange(setRange(DEFAULT_VIEW, "close_from", "2026-10-01"), "close_to", "2026-12-31");
    v = { ...setCustom(v, "region", "West"), source: "email", includeConverted: true };
    v = removeFilter(v, "range:close");
    expect(v.ranges).toEqual({});
    expect(removeFilter(v, "source").source).toBeNull();
    expect(removeFilter(v, "converted").includeConverted).toBe(false);
    expect(removeFilter(v, "custom:region").custom).toEqual({});
    expect(removeFilter(v, "custom:region").source).toBe("email");
  });

  it("shows a range that the entity does not offer, so the list never hides why", () => {
    const v = setRange(DEFAULT_VIEW, "close_from", "2026-10-01");
    expect(activeFilterChips(v, fields, 2026).map((c) => c.id)).toEqual(["range:close"]);
  });

  it("writes a one-sided range and a range over a year end", () => {
    expect(rangeText("2026-10-01", undefined, 2026)).toBe("from 1 Oct");
    expect(rangeText(undefined, "2026-12-31", 2026)).toBe("to 31 Dec");
    expect(rangeText("2026-12-01", "2027-01-31", 2026)).toBe("1 Dec 2026–31 Jan 2027");
    expect(rangeText("2025-10-01", "2025-12-31", 2026)).toBe("1 Oct 2025–31 Dec 2025");
  });

  it("builds the Stage options from the stage chips, less Converted", () => {
    const options = stageOptions(
      [
        { id: "all", label: "All" },
        { id: "s1", label: "New" },
        { id: "converted", label: "Converted" },
      ],
      "Status",
    );
    expect(options).toEqual([
      { value: "", label: "Any status" },
      { value: "s1", label: "New" },
    ]);
  });
});

// ── The view chips ──────────────────────────────────────────────────────────

describe("the view chip strip", () => {
  const draw = (over: Record<string, unknown> = {}) =>
    renderToStaticMarkup(
      createElement(ViewChips as never, {
        allLabel: "All deals",
        views: [view({ id: "a", name: "Mine" }), view({ id: "b", name: "Team", shared: true, mine: false })],
        activeId: null,
        dirty: "blank",
        isAdmin: false,
        onSelect: noop,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
        onRename: async () => undefined,
        onShare: async () => undefined,
        onDelete: async () => undefined,
        deleteCopy: () => ({ title: "t", body: "b" }),
        ...over,
      }),
    );

  it("lists All first, then private views, then shared views, with a shared mark", () => {
    const html = draw();
    expect(html.indexOf("All deals")).toBeLessThan(html.indexOf("Mine"));
    expect(html.indexOf("Mine")).toBeLessThan(html.indexOf("Team"));
    expect(html).toContain("Team (shared)");
    expect(chipList("All deals", [], null)).toHaveLength(1);
  });

  it("marks the chip in effect and none else", () => {
    const html = draw({ activeId: "a" });
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
  });

  it("shows + Save view on the default view with something set, and nothing else", () => {
    expect(draw({ dirty: "unsaved" })).toContain("Save view");
    for (const dirty of ["blank", "clean"]) {
      const html = draw({ dirty });
      expect(html).not.toContain("Save view");
      expect(html).not.toContain("Update view");
    }
  });

  it("shows Update view and Reset on a changed view the member may edit", () => {
    const html = draw({ dirty: "changed", activeId: "a" });
    expect(html).toContain("Update view");
    expect(html).toContain("Reset");
    expect(html).toContain('aria-label="View options for Mine"');
  });

  it("hides Update view and the options button on a shared view of another member", () => {
    const html = draw({ dirty: "changed", activeId: "b" });
    expect(html).not.toContain("Update view");
    expect(html).not.toContain("View options");
    expect(html).toContain("Reset");
    // An admin may change it.
    expect(draw({ dirty: "changed", activeId: "b", isAdmin: true })).toContain("Update view");
  });

  it("keeps the chip menu to Rename, Share and Delete, and names the share box the same way", () => {
    const src = read("../../../components/ViewChips.tsx");
    for (const word of ['"Rename"', '"Delete"', "Share with everyone", "Make private"]) {
      expect(src).toContain(word);
    }
    expect(src).toContain('aria-label="Share with everyone"');
    expect(src).toContain("ConfirmDialog");
    expect(src).toContain("Modal");
  });
});

// ── The module scope key ────────────────────────────────────────────────────

describe("saved views of a module list", () => {
  it("keys the scope as module:<api_name> and reads it back", () => {
    expect(moduleScope("Products")).toBe("module:Products");
    expect(moduleOfScope("module:Products")).toBe("Products");
    expect(moduleOfScope("deals")).toBeNull();
    expect(moduleOfScope(null)).toBeNull();
  });

  it("opens a module view on the module tab with its search and sort", () => {
    const opened = applyView(
      view({
        id: "m1",
        entity: "module:Products",
        filters: { q: "pump" },
        sort: { key: "name", dir: "asc" },
      }),
    );
    expect(opened).toMatchObject({
      tab: "module",
      module: "Products",
      layout: "list",
      q: "pump",
      sort: "name",
      dir: "asc",
      viewId: "m1",
    });
  });

  it("captures a module screen as list state with no pipeline", () => {
    const screen = { ...DEFAULT_VIEW, tab: "module" as const, module: "Products", layout: "list" as const, q: "pump" };
    expect(captureView(screen, ["Colour"], "p1")).toEqual({
      layout: "list",
      filters: { q: "pump" },
      sort: null,
      columns: ["Colour"],
      pipeline_id: null,
    });
  });

  it("the page gives the module list its views, and the app bar no module tools", () => {
    const page = read("../page.tsx");
    expect(page).toContain("moduleScope(view.module)");
    expect(page).toContain("actions={viewActions}");
  });
});

// ── The dirty rule ──────────────────────────────────────────────────────────

describe("the dirty rule", () => {
  it("treats no filter, no sort and no columns as blank", () => {
    const blank = captureView(DEFAULT_VIEW, null, null);
    expect(isBlankState(blank)).toBe(true);
    expect(isBlankState(captureView({ ...DEFAULT_VIEW, sort: "name" }, null, null))).toBe(false);
    expect(isBlankState(captureView(DEFAULT_VIEW, [], null))).toBe(false);
    expect(isBlankState(captureView({ ...DEFAULT_VIEW, includeConverted: true }, null, null))).toBe(false);
  });

  it("opens All with no filter, sort or view, and keeps the tab", () => {
    const screen = { ...DEFAULT_VIEW, tab: "leads" as const, layout: "list" as const, q: "x", sort: "name", viewId: "v1", owner: "me" };
    expect(defaultViewOf(screen)).toMatchObject({
      tab: "leads",
      layout: "list",
      q: "",
      sort: null,
      viewId: null,
      owner: null,
    });
  });
});

// ── The app bar and the rail ────────────────────────────────────────────────

describe("the app bar keeps Refresh only", () => {
  const page = read("../page.tsx");
  const tools = page.slice(page.indexOf("const tools = ("), page.indexOf("return (", page.indexOf("const tools = (")));

  it("moves the layout switch, the picker, the board toggles, Columns and Export out", () => {
    for (const word of ["ModeSwitch", "Export", "Empty stages", "Next activity", "Columns", "{picker}"]) {
      expect(tools).not.toContain(word);
    }
    expect(tools).toContain('aria-label="Refresh"');
  });

  it("removes the stage pills and the Columns band", () => {
    expect(page).not.toContain("FilterPills");
    expect(read("./RecordList.tsx")).not.toContain("setChoosing");
  });

  it("keeps a rail of RailRow with no nested views", () => {
    const rail = read("./CrmRail.tsx");
    expect(rail).toContain("RailRow");
    expect(rail).not.toContain("viewRow");
  });
});

function moduleProps() {
  return {
    module: "Products",
    label: "Products",
    view: { ...DEFAULT_VIEW, tab: "module" as const, module: "Products", layout: "list" as const },
    onChange: noop,
    onOpen: noop,
    isAdmin: false,
    onColumnsPicked: noop,
    actions: {
      views: [],
      active: null,
      dirty: "blank",
      busy: false,
      columns: null,
      setColumns: noop,
      select: noop,
      save: async () => undefined,
      update: async () => undefined,
      reset: noop,
      rename: async () => undefined,
      toggleShare: async () => undefined,
      remove: async () => undefined,
      deleteCopy: () => ({ title: "", body: "" }),
    } as never,
  };
}

describe("a module list draws both rows", () => {
  it("has the view chips and the filter row, and no Export", () => {
    const html = renderToStaticMarkup(createElement(ModuleList, moduleProps()));
    expect(html).toContain("All products");
    expect(html).toContain('data-testid="crm-filter-bar"');
    expect(html).toContain('data-page-filter="products"');
    expect(html).not.toContain("Export");
  });
});
