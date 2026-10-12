// Filters and saved views in the CRM UI (WS-53 CRM-Q8, spec section 18.5).
// The runner has no DOM, so each component is rendered to a string.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { FieldDef } from "../lib/fields";
import { extraQuery, listQuery } from "../lib/filters";
import {
  ME,
  activeCount,
  dateFilters,
  optionsOf,
  ownerOptions,
  picklistDefs,
  pruneFilters,
  setCustom,
  setRange,
} from "../lib/filterBar";
import {
  activeSavedView,
  applyView,
  canEditView,
  captureView,
  clearFilters,
  deleteCopy,
  railViews,
  resolveBareLink,
  stateOf,
  viewDiverges,
  type SavedView,
} from "../lib/savedViews";
import { DEFAULT_VIEW, parseView, serializeView, selectTab, type CrmView } from "../lib/urlState";
import CrmRail, { type RailViewActions } from "./CrmRail";
import FilterBar from "./FilterBar";
import RecordList from "./RecordList";

const noop = () => undefined;

const saved = (over: Partial<SavedView> = {}): SavedView => ({
  id: "v1",
  entity: "deals",
  name: "Mine",
  owner_email: "ann@x.test",
  shared: false,
  layout: "board",
  filters: { owner: "me", custom: { Region: ["North"] } },
  sort: { key: "amount", dir: "desc" },
  columns: ["Region"],
  pipeline_id: null,
  position: 0,
  mine: true,
  ...over,
});

const region: FieldDef = {
  key: "Region",
  label: "Region",
  type: "select",
  options: [
    { label: "North", value: "North" },
    { label: "South", value: "South" },
  ],
  position: 1,
  visible: true,
  source: "zoho",
  api_name: "Region",
};

// ── The URL grammar ────────────────────────────────────────────────────────

describe("the URL carries the filters and the view", () => {
  it("round trips source, ranges, custom picklists and ?view=", () => {
    const view: CrmView = {
      ...DEFAULT_VIEW,
      tab: "deals",
      layout: "list",
      source: "email",
      ranges: { created_from: "2026-01-01", close_to: "2026-03-01" },
      custom: { Region: ["North", "East"] },
      viewId: "abc",
    };
    const qs = serializeView(view);
    expect(qs).toContain("view=abc");
    expect(qs).toContain("source=email");
    expect(qs).toContain("created_from=2026-01-01");
    expect(qs).toContain("cf.Region=North%2CEast");
    expect(parseView(qs)).toMatchObject({
      source: "email",
      ranges: { created_from: "2026-01-01", close_to: "2026-03-01" },
      custom: { Region: ["North", "East"] },
      viewId: "abc",
    });
  });

  it("drops a hand-edited date, so a bad link cannot become a 422", () => {
    expect(parseView("created_from=tomorrow&close_to=2026-02-30x").ranges).toEqual({});
  });

  it("clears the field filters and the view when the tab changes", () => {
    const next = selectTab(
      { ...DEFAULT_VIEW, source: "email", custom: { Region: ["N"] }, viewId: "abc", owner: "me" },
      "leads",
    );
    expect(next).toMatchObject({ source: null, custom: {}, ranges: {}, viewId: null, owner: "me" });
  });
});

// ── The request ────────────────────────────────────────────────────────────

describe("the list request carries the new filters", () => {
  const view: CrmView = {
    ...DEFAULT_VIEW,
    owner: ME,
    source: "agent",
    ranges: { updated_to: "2026-02-01", close_from: "2026-05-01" },
    custom: { Region: ["North", "East"] },
  };

  it("sends owner=me, the dates and custom.<key>.in", () => {
    expect(listQuery("deals", view)).toMatchObject({
      owner: "me",
      source: "agent",
      updated_to: "2026-02-01",
      close_from: "2026-05-01",
      "custom.Region.in": "North,East",
    });
  });

  it("never sends a close date for an entity without one", () => {
    expect(extraQuery("leads", view)).not.toHaveProperty("close_from");
    expect(extraQuery("leads", view)).toHaveProperty("updated_to");
  });
});

// ── The bar's choices ──────────────────────────────────────────────────────

describe("the filter bar choices", () => {
  it("offers Anyone, Me, the owners on screen and a chosen one that left", () => {
    const options = ownerOptions([{ owner_email: "b@x.test" }, { owner_email: "B@x.test" }], "gone@x.test");
    expect(options.map((o) => o.value)).toEqual(["", ME, "B@x.test", "gone@x.test"]);
  });

  it("offers close dates on deals only", () => {
    expect(dateFilters("deals").map((d) => d.label)).toEqual(["Created", "Updated", "Closing"]);
    expect(dateFilters("contacts").map((d) => d.label)).toEqual(["Created", "Updated"]);
  });

  it("lists only picklists that carry options", () => {
    const text: FieldDef = { ...region, key: "T", type: "text", options: null };
    const empty: FieldDef = { ...region, key: "E", options: [] };
    expect(picklistDefs([region, text, empty]).map((d) => d.key)).toEqual(["Region"]);
    expect(optionsOf(region)).toEqual([
      { value: "North", label: "North" },
      { value: "South", label: "South" },
    ]);
  });

  it("sets and clears a picklist, a range and the count", () => {
    let view = setCustom(DEFAULT_VIEW, "Region", "South");
    view = setRange(view, "created_from", "2026-01-01");
    expect(view.custom).toEqual({ Region: ["South"] });
    expect(activeCount(view)).toBe(2);
    view = setRange(setCustom(view, "Region", ""), "created_from", "");
    expect(activeCount(view)).toBe(0);
  });

  it("renders the bar with its controls", () => {
    const html = renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "deals",
        view: DEFAULT_VIEW,
        onChange: noop,
        rows: [{ owner_email: "a@x.test" }],
        fieldDefs: [region],
        activeView: null,
        dirty: false,
        canEdit: false,
        isAdmin: false,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
      }),
    );
    for (const word of ["Owner", "Source", "Region", "Created from", "Closing to", "Save view"]) {
      expect(html).toContain(word);
    }
    expect(html).not.toContain("Share with everyone");
  });

  it("offers Update view and Reset when the screen left the saved view", () => {
    const html = renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "deals",
        view: DEFAULT_VIEW,
        onChange: noop,
        rows: [],
        fieldDefs: null,
        activeView: saved(),
        dirty: true,
        canEdit: true,
        isAdmin: true,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
      }),
    );
    expect(html).toContain("has unsaved changes");
    expect(html).toContain("Update view");
    expect(html).toContain("Reset");
    expect(html).toContain("Save as new");
  });
});

// ── Saving and applying a view ─────────────────────────────────────────────

describe("saving and applying a view", () => {
  it("captures the screen and opens the same screen again", () => {
    const screen: CrmView = {
      ...DEFAULT_VIEW,
      tab: "deals",
      layout: "list",
      owner: ME,
      q: " acme ",
      source: "email",
      ranges: { created_from: "2026-01-01" },
      custom: { Region: ["North"] },
      sort: "amount",
      dir: "asc",
    };
    const state = captureView(screen, ["Region"], "p1");
    expect(state).toMatchObject({
      layout: "list",
      sort: { key: "amount", dir: "asc" },
      columns: ["Region"],
      pipeline_id: "p1",
      filters: { q: "acme", owner: "me", source: "email", created_from: "2026-01-01" },
    });
    const opened = applyView(saved({ ...state, id: "v9", entity: "deals", pipeline_id: "p1" }));
    expect(opened).toMatchObject({
      tab: "deals",
      layout: "list",
      owner: "me",
      q: "acme",
      sort: "amount",
      dir: "asc",
      pipelineId: "p1",
      viewId: "v9",
      custom: { Region: ["North"] },
    });
    // Opening a view and typing the same thing give one request.
    expect(listQuery("deals", opened)).toEqual(listQuery("deals", { ...screen, pipelineId: "p1" }));
  });

  it("knows when the screen has moved away from the view", () => {
    const view = saved();
    const same = captureView(applyView(view), ["Region"], null);
    expect(viewDiverges(view, same)).toBe(false);
    expect(viewDiverges(view, { ...same, columns: null })).toBe(true);
    expect(viewDiverges(view, captureView({ ...applyView(view), source: "email" }, ["Region"], null))).toBe(true);
    expect(stateOf(view).columns).toEqual(["Region"]);
  });

  it("clears filters and keeps the layout", () => {
    const cleared = clearFilters({ ...applyView(saved()), source: "x", q: "y" });
    expect(cleared).toMatchObject({ owner: null, source: null, q: "", custom: {}, layout: "board" });
  });

  it("opens a link that carries only the view id", () => {
    const views = [saved({ id: "v7", entity: "leads", layout: "list" })];
    expect(resolveBareLink("view=v7", views)).toMatchObject({ tab: "leads", viewId: "v7" });
    expect(resolveBareLink("view=v7&tab=deals", views)).toBeNull();
    expect(resolveBareLink("view=nope", views)).toBeNull();
    expect(resolveBareLink("", views)).toBeNull();
  });

  it("finds the view in effect and orders the rail", () => {
    const views = [saved({ id: "b", shared: true, position: 0 }), saved({ id: "a", position: 5 })];
    expect(railViews(views).map((v) => v.id)).toEqual(["a", "b"]);
    expect(activeSavedView(views, "b")?.id).toBe("b");
    expect(activeSavedView(views, "zzz")).toBeNull();
  });

  it("lets the owner or an admin change a view", () => {
    expect(canEditView(saved({ mine: false }), false)).toBe(false);
    expect(canEditView(saved({ mine: false }), true)).toBe(true);
    expect(canEditView(saved({ mine: true }), false)).toBe(true);
  });

  it("warns that a shared view goes away for everyone", () => {
    expect(deleteCopy(saved({ shared: true })).body).toContain("shared");
    expect(deleteCopy(saved()).title).toContain("Mine");
  });
});

// ── The rail ───────────────────────────────────────────────────────────────

describe("saved views nest under their entity in the rail", () => {
  const actions = (over: Partial<RailViewActions> = {}): RailViewActions => ({
    views: {
      deals: [saved({ id: "d1", name: "Big deals" }), saved({ id: "d2", name: "Team board", shared: true, mine: false })],
      leads: [],
      contacts: [],
      organizations: [],
    },
    activeViewId: null,
    isAdmin: false,
    onSelectView: noop,
    onRenameView: noop,
    onShareView: noop,
    onDeleteView: noop,
    ...over,
  });
  const draw = (a: RailViewActions | undefined, tab: CrmView["tab"] = "deals") =>
    renderToStaticMarkup(createElement(CrmRail, { tab, onSelect: noop, saved: a }));

  it("draws each view under Deals and none under the others", () => {
    const html = draw(actions());
    expect(html).toContain("Big deals");
    expect(html).toContain("Team board");
    expect(html.indexOf("Deals")).toBeLessThan(html.indexOf("Big deals"));
    expect(html.indexOf("Big deals")).toBeLessThan(html.indexOf("Reports"));
  });

  it("draws no view row when there are no saved views", () => {
    expect(draw(undefined)).not.toContain("Big deals");
  });

  it("offers rename and delete on the caller's own view only", () => {
    const html = draw(actions());
    expect(html).toContain('aria-label="Delete Big deals"');
    expect(html).not.toContain('aria-label="Delete Team board"');
  });

  it("offers an admin delete and share on a view of another member", () => {
    const html = draw(actions({ isAdmin: true }));
    expect(html).toContain('aria-label="Delete Team board"');
    expect(html).toContain("Make Team board private");
    expect(html).toContain("Share Big deals");
  });

  it("marks the view in effect and not its entity row", () => {
    const html = draw(actions({ activeViewId: "d1" }));
    expect(html.match(/aria-current="page"/g)?.length).toBe(1);
    expect(html).toMatch(/aria-current="page"[^>]*>(?:(?!<button)[\s\S])*Big deals/);
  });
});

// ── The columns ────────────────────────────────────────────────────────────

describe("the column chooser follows the saved view", () => {
  it("shows the view's columns instead of the browser's", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: [{ id: "1", name: "A", custom: { Region: "North" } }],
        total: 1,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        fieldDefs: [region],
        columnKeys: ["Region"],
        onColumnsChange: noop,
      }),
    );
    expect(html).toContain("<th");
    expect(html).toContain("North");
  });

  it("leaves the extra column out when the view chose none", () => {
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "deals",
        rows: [{ id: "1", name: "A", custom: { Region: "North" } }],
        total: 1,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        fieldDefs: [region],
        columnKeys: [],
        onColumnsChange: noop,
      }),
    );
    expect(html).not.toContain("North");
  });
});

// ── Filters the gateway would refuse (review) ──────────────────────────────

describe("a stale filter is dropped before the request", () => {
  const stale: CrmView = {
    ...DEFAULT_VIEW,
    custom: { Region: ["North"], Gone: ["x"] },
    ranges: { created_from: "2026-01-01", updated_to: "2026-02-01" },
  };

  it("drops a custom key that the definitions no longer list", () => {
    const { view, removed } = pruneFilters(stale, [region], false);
    expect(view.custom).toEqual({ Region: ["North"] });
    expect(removed).toEqual(["Gone"]);
    expect(listQuery("deals", view)).not.toHaveProperty("custom.Gone.in");
  });

  it("drops every custom key when the definitions failed to load", () => {
    const { view, removed } = pruneFilters(stale, null, false);
    expect(view.custom).toEqual({});
    expect(removed).toEqual(["Region", "Gone"]);
  });

  it("drops the created range in Mirror mode and keeps the updated range", () => {
    const { view, removed } = pruneFilters(stale, [region], true);
    expect(view.ranges).toEqual({ updated_to: "2026-02-01" });
    expect(removed).toContain("Created");
  });

  it("returns the same view when nothing is stale", () => {
    const clean: CrmView = { ...DEFAULT_VIEW, custom: { Region: ["North"] } };
    const out = pruneFilters(clean, [region], false);
    expect(out.view).toBe(clean);
    expect(out.removed).toEqual([]);
  });

  it("hides the Created range in Mirror mode", () => {
    expect(dateFilters("deals", true).map((d) => d.label)).toEqual(["Updated", "Closing"]);
  });

  it("tells the member that a filter was removed", () => {
    const html = renderToStaticMarkup(
      createElement(FilterBar, {
        entity: "deals",
        view: DEFAULT_VIEW,
        onChange: noop,
        rows: [],
        fieldDefs: [region],
        mirror: true,
        removed: ["Gone"],
        activeView: null,
        dirty: false,
        canEdit: false,
        isAdmin: false,
        saving: false,
        onSave: async () => undefined,
        onUpdate: async () => undefined,
        onReset: noop,
      }),
    );
    expect(html).toContain("A filter was removed: Gone");
    expect(html).not.toContain("Created from");
  });
});
