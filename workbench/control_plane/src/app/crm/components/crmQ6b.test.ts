// The views of the generic Zoho modules (WS-53 CRM-Q6b, `crm_platform.md`
// section 18.7). The runner has no DOM, so each component is rendered to a
// string and read as text.
//
// The fence of "Done when" item 2: the rail rows follow the profile. The
// gateway returns the mirrored modules in the profile order, and the rail
// draws one row for each, under the known views and before Reports.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  MODULE_COLUMNS,
  MODULE_NOT_FOUND,
  linkOf,
  listRow,
  lookupKeys,
  moduleFieldRows,
  moduleListQuery,
  moduleRailRows,
  moduleRelated,
  moduleSheetState,
  moduleTabs,
  sheetTitle,
  shownModuleTab,
  type CrmModule,
  type ModuleRecordPage,
} from "../lib/modules";
import {
  DEFAULT_VIEW,
  closeRecord,
  openModuleRecord,
  openRecord,
  parseView,
  selectModule,
  selectTab,
  serializeView,
} from "../lib/urlState";
import { CRM_VIEWS, railEntries } from "../lib/views";
import CrmRail from "./CrmRail";
import { ModuleRecordBody } from "./ModuleRecordSheet";
import RecordList from "./RecordList";

const noop = () => undefined;

const mod = (api_name: string, label: string, count = 0): CrmModule => ({
  api_name,
  label,
  singular_label: null,
  count,
});

/** `GET /crm/modules` for an org with Products, Quotes and one custom module. */
const PROFILE = [mod("Products", "Products", 2), mod("Quotes", "Quotes", 1), mod("Install_Base", "Install Base")];

const labels = (entries: ReturnType<typeof railEntries>) =>
  entries.map((e) => (e.kind === "view" ? e.view.label : e.row.label));

// ── The rail rows follow the profile ──────────────────────────────────────

describe("the rail rows of the generic modules", () => {
  it("draws one row for each module, in the profile order, under the known views", () => {
    expect(labels(railEntries(PROFILE))).toEqual([
      "Leads",
      "Contacts",
      "Companies",
      "Deals",
      "Products",
      "Quotes",
      "Install Base",
      "Reports",
    ]);
  });

  it("keeps the order that the gateway gives, never its own", () => {
    const reversed = [...PROFILE].reverse();
    expect(labels(railEntries(reversed)).slice(4, 7)).toEqual(["Install Base", "Quotes", "Products"]);
  });

  it("shows no group when the org has no module, or when the read failed", () => {
    const known = CRM_VIEWS.map((v) => v.label);
    expect(labels(railEntries([]))).toEqual(known);
    expect(labels(railEntries(null))).toEqual(known);
    expect(moduleRailRows(undefined)).toEqual([]);
  });

  it("drops a name that is not a module API name", () => {
    expect(moduleRailRows([mod("Products", "Products"), mod("x';--", "Bad")]).map((r) => r.module)).toEqual([
      "Products",
    ]);
  });

  it("draws the rows in that order in the rail, with the count and the selected module", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "module",
        onSelect: noop,
        modules: { modules: PROFILE, active: "Quotes", onSelect: noop },
      }),
    );
    const at = (text: string) => html.indexOf(`>${text}<`);
    expect(at("Deals")).toBeGreaterThan(-1);
    expect(at("Deals")).toBeLessThan(at("Products"));
    expect(at("Products")).toBeLessThan(at("Quotes"));
    expect(at("Quotes")).toBeLessThan(at("Install Base"));
    expect(at("Install Base")).toBeLessThan(at("Reports"));
    // The selected module is the one row with aria-current.
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
    const current = html.slice(html.indexOf('aria-current="page"'));
    expect(current.indexOf(">Quotes<")).toBeGreaterThan(-1);
    expect(current.indexOf(">Quotes<")).toBeLessThan(current.indexOf(">Install Base<"));
  });

  it("draws no module row in the rail of an org with none", () => {
    const html = renderToStaticMarkup(
      createElement(CrmRail, {
        tab: "deals",
        onSelect: noop,
        modules: { modules: [], active: null, onSelect: noop },
      }),
    );
    expect(html).not.toContain(">Products<");
    expect(html.indexOf(">Deals<")).toBeLessThan(html.indexOf(">Reports<"));
  });
});

// ── The URL grammar ───────────────────────────────────────────────────────

describe("the URL of a module list and a generic record", () => {
  it("reads and writes ?tab=module&module=", () => {
    const view = parseView("tab=module&module=Products");
    expect(view.tab).toBe("module");
    expect(view.module).toBe("Products");
    expect(serializeView(view)).toBe("tab=module&module=Products");
  });

  it("opens Deals for a module tab with no valid name", () => {
    expect(parseView("tab=module").tab).toBe("deals");
    expect(parseView("tab=module&module=x%27%3B--").tab).toBe("deals");
  });

  it("reads ?mrec=<Module>:<id>, and a known record wins a URL that names both", () => {
    expect(parseView("mrec=Quotes:abc").moduleRecord).toEqual({ module: "Quotes", id: "abc" });
    expect(parseView("mrec=nocolon").moduleRecord).toBeNull();
    const both = parseView("deal=d1&mrec=Quotes:abc");
    expect(both.record?.id).toBe("d1");
    expect(both.moduleRecord).toBeNull();
  });

  it("keeps the record tab of a generic record in ?rtab=", () => {
    const view = parseView("tab=module&module=Quotes&mrec=Quotes%3Aq1&rtab=related");
    expect(view.recordTab).toBe("related");
    expect(serializeView(view)).toBe("tab=module&module=Quotes&mrec=Quotes%3Aq1&rtab=related");
  });

  it("never stacks the two sheets", () => {
    const generic = openModuleRecord(DEFAULT_VIEW, "Quotes", "q1");
    expect(serializeView(generic)).toBe("mrec=Quotes%3Aq1");
    const known = openRecord(generic, "deal", "d1");
    expect(known.moduleRecord).toBeNull();
    expect(openModuleRecord(known, "Quotes", "q1").record).toBeNull();
    expect(closeRecord(generic).moduleRecord).toBeNull();
  });

  it("moves between modules and back to a known view with no filter left over", () => {
    const products = selectModule({ ...DEFAULT_VIEW, sort: "amount", q: "x" }, "Products");
    expect(products).toMatchObject({ tab: "module", module: "Products", sort: null, q: "" });
    const quotes = selectModule({ ...products, sort: "name" }, "Quotes");
    expect(quotes).toMatchObject({ module: "Quotes", sort: null });
    expect(selectTab(quotes, "deals").module).toBeNull();
  });
});

describe("the URL grammar imports no CRM module that imports it back", () => {
  it("keeps urlState.ts free of a value import from modules.ts or filters.ts", () => {
    // filters.ts imports urlState.ts, and modules.ts imports filters.ts. A value
    // import back into urlState.ts closes a cycle, and the browser then reads
    // `LIST_PAGE_SIZE` before it is set (found by the CRM-Q6b visual rig).
    const src = readFileSync(new URL("../lib/urlState.ts", import.meta.url), "utf8");
    const values = src
      .split(/\r?\n/)
      .filter((line) => /^import (?!type )/.test(line))
      .join(" ");
    expect(values).not.toMatch(/from "\.\/(modules|filters)"/);
  });
});

// ── The list ──────────────────────────────────────────────────────────────

describe("the module list", () => {
  it("sends the page, the search and only an allowed sort", () => {
    expect(moduleListQuery({ q: " widget ", sort: "name", dir: "asc" }, 2)).toEqual({
      page: 2,
      page_size: 50,
      q: "widget",
      sort: "name",
      dir: "asc",
    });
    expect(moduleListQuery({ q: "", sort: "amount", dir: "asc" })).toEqual({ page: 1, page_size: 50 });
  });

  it("draws the rows in the shared list, with the fields as columns", () => {
    const row = listRow({
      id: "r1",
      module: "Products",
      name: "Widget",
      owner_email: "priya@fracktal.in",
      fields: { Unit_Price: 1200 },
      ext_source: "zoho",
      ext_id: "p1",
      ext_url: null,
      ext_created_at: null,
      ext_modified_at: "2026-10-01T10:00:00+00:00",
    });
    const html = renderToStaticMarkup(
      createElement(RecordList, {
        entity: "module:Products",
        columns: MODULE_COLUMNS,
        sortKeys: ["name"],
        rows: [row],
        total: 1,
        loading: false,
        sort: null,
        direction: "desc",
        onSort: noop,
        onOpen: noop,
        fieldDefs: [
          { key: "Unit_Price", label: "Unit price", type: "currency", options: null, position: 1, visible: true, source: "zoho", api_name: "Unit_Price" },
        ],
        columnKeys: ["Unit_Price"],
      }),
    );
    expect(html).toContain("Widget");
    expect(html).toContain(">Modified<");
    expect(html).toContain(">Unit price<");
    expect(html).toContain("1,200");
  });
});

// ── The record page and the lookup links ──────────────────────────────────

const PAGE: ModuleRecordPage = {
  module: { api_name: "Quotes", label: "Quotes", singular_label: "Quote", count: 1 },
  record: {
    id: "q-local",
    module: "Quotes",
    name: "Quote 1",
    owner_email: "priya@fracktal.in",
    fields: {
      Subject: "Quote 1",
      Deal_Name: { id: "d1", name: "Bosch" },
      Product_Name: { id: "p1", name: "Widget" },
      Vendor_Name: { id: "v1", name: "Acme Vendor" },
    },
    ext_source: "zoho",
    ext_id: "q1",
    ext_url: "https://crm.zoho.in/crm/org1/tab/Quotes/q1",
    ext_created_at: null,
    ext_modified_at: "2026-10-01T10:00:00+00:00",
  },
  field_defs: [
    { key: "Subject", label: "Subject", type: "text", options: null, position: 1, visible: true, source: "zoho", api_name: "Subject", lookup_module: null },
    { key: "Deal_Name", label: "Deal", type: "lookup", options: null, position: 2, visible: true, source: "zoho", api_name: "Deal_Name", lookup_module: "Deals" },
    { key: "Product_Name", label: "Product", type: "lookup", options: null, position: 3, visible: true, source: "zoho", api_name: "Product_Name", lookup_module: "Products" },
    { key: "Vendor_Name", label: "Vendor", type: "lookup", options: null, position: 4, visible: true, source: "zoho", api_name: "Vendor_Name", lookup_module: "Vendors" },
  ],
  links: {
    Deal_Name: { kind: "record", entity: "deals", id: "deal-local", name: "Bosch" },
    Product_Name: { kind: "module", module: "Products", id: "p-local", name: "Widget" },
  },
};

describe("the lookup links", () => {
  it("link a known record, a generic record, and leave any other lookup as text", () => {
    const rows = moduleFieldRows(PAGE);
    const by = Object.fromEntries(rows.map((r) => [r.key, r]));
    expect(by.Deal_Name.link).toEqual({ kind: "record", entity: "deals", id: "deal-local", name: "Bosch" });
    expect(by.Product_Name.link).toEqual({ kind: "module", module: "Products", id: "p-local", name: "Widget" });
    expect(by.Vendor_Name.link).toBeNull();
    expect(by.Vendor_Name.value).toEqual({ kind: "text", text: "Acme Vendor" });
  });

  it("refuses a link of an unknown kind, entity or module", () => {
    const bad = {
      a: { kind: "record", entity: "users", id: "x", name: null },
      b: { kind: "module", module: "x';--", id: "x", name: null },
      c: { kind: "module", module: "Products", id: "", name: null },
    } as unknown as ModuleRecordPage["links"];
    expect(linkOf(bad, "a")).toBeNull();
    expect(linkOf(bad, "b")).toBeNull();
    expect(linkOf(bad, "c")).toBeNull();
  });

  it("shows Related only when a lookup holds a value", () => {
    // CRM-Q23c adds the Timeline tab to each module record.
    expect(moduleTabs(PAGE)).toEqual(["overview", "timeline", "fields", "related"]);
    const noLookup = {
      ...PAGE,
      record: { ...PAGE.record, fields: { Subject: "Quote 1" } },
    };
    expect(moduleTabs(noLookup)).toEqual(["overview", "timeline", "fields"]);
    expect(shownModuleTab(noLookup, "related")).toBe("overview");
    expect(shownModuleTab(PAGE, "timeline")).toBe("timeline");
  });

  it("counts only a visible lookup for the Related tab (review note 1)", () => {
    // The only lookup that holds a value is hidden, so Related would be empty.
    const hidden = {
      ...PAGE,
      record: { ...PAGE.record, fields: { Subject: "Quote 1", Deal_Name: { id: "d1", name: "Bosch" } } },
      field_defs: PAGE.field_defs.map((d) => (d.key === "Deal_Name" ? { ...d, visible: false } : d)),
    };
    expect(lookupKeys(hidden)).toEqual([]);
    expect(moduleTabs(hidden)).toEqual(["overview", "timeline", "fields"]);
    expect(moduleRelated(hidden)).toEqual([]);
  });
});

describe("the state of the generic record sheet (review notes 2 and 3)", () => {
  const LOWER = "0b0e8c1e-5c7a-4f6e-9a35-c3d0c0de0001";
  const page = { ...PAGE, record: { ...PAGE.record, id: LOWER } };

  it("reads ?mrec= with an uppercase id as the lowercase id the gateway returns", () => {
    expect(parseView(`mrec=Quotes:${LOWER.toUpperCase()}`).moduleRecord).toEqual({
      module: "Quotes",
      id: LOWER,
    });
  });

  it("matches the record to the asked id with no regard to case", () => {
    expect(moduleSheetState(page, null, LOWER.toUpperCase())).toEqual({ kind: "page", page });
    expect(moduleSheetState(page, null, "another-id")).toEqual({ kind: "loading" });
  });

  it("says Not found only for a 404, and offers a retry for any other error", () => {
    expect(moduleSheetState(MODULE_NOT_FOUND, null, LOWER)).toEqual({ kind: "missing" });
    expect(moduleSheetState(undefined, "Gateway error 500", LOWER)).toEqual({ kind: "failed" });
    expect(moduleSheetState(undefined, null, LOWER)).toEqual({ kind: "loading" });
    expect(sheetTitle({ kind: "missing" })).toBe("Not found");
    expect(sheetTitle({ kind: "failed" })).toBe("This record did not load. Try again.");
  });

  it("draws Not found and Retry in the sheet from those states", () => {
    const src = readFileSync(new URL("./ModuleRecordSheet.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/moduleSheetState\(/);
    expect(src).toMatch(/refresh/);
    expect(src).not.toMatch(/res\.error \? "Not found"/);
  });

  it("draws the record page read only, with the header and the links as buttons", () => {
    const html = renderToStaticMarkup(
      createElement(ModuleRecordBody, { page: PAGE, tab: "related", onTab: noop, onOpenLink: noop }),
    );
    expect(html).toContain("priya@fracktal.in");
    expect(html).toContain(">Modified<");
    expect(html).toContain(">Overview<");
    expect(html).toContain(">Related<");
    // CRM-Q23c: the Timeline tab joins, read only (`crmQ23c.test.ts`).
    expect(html).toContain(">Timeline<");
    expect(html).toMatch(/<button[^>]*>Bosch<\/button>/);
    expect(html).toMatch(/<button[^>]*>Widget<\/button>/);
    expect(html).not.toMatch(/<button[^>]*>Acme Vendor<\/button>/);
    expect(html).toContain("Acme Vendor");
    // No control writes: a generic record has no native writer.
    expect(html).not.toMatch(/<input|<textarea|<select/);
  });
});
