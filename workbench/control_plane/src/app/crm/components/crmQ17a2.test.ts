// The screens of the fill rank (WS-53 CRM-Q17a-2, `crm_platform.md`
// section 18.9.6). One block for each done-when item. The runner has no DOM,
// so each component draws to a string.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import { COLUMNS, shownColumns } from "../lib/columns";
import type { FieldLayout } from "../lib/fieldLayout";
import {
  MAX_COLUMNS,
  OWNER_NAME_KEY,
  ZOHO_SYSTEM_KEYS,
  defaultColumns,
  effectiveColumns,
  fieldRows,
  fillRankOn,
  filledFirst,
  foldFields,
  foldLabel,
  readColumns,
  summaryFacts,
  undefinedRows,
  writeColumns,
  type FieldDef,
} from "../lib/fields";
import type { ModuleRecordPage } from "../lib/modules";
import { captureView, stateOf, type SavedView } from "../lib/savedViews";
import { DEFAULT_VIEW } from "../lib/urlState";
import { fieldLayoutKey } from "../lib/useCrmMode";
import {
  labelledViews,
  railEntries,
  sourceSingular,
  viewLabel,
  type SourceLabels,
} from "../lib/views";
import CrmRail from "./CrmRail";
import FieldsPanel, { LayoutFields, overviewEntries, type OverviewEntry } from "./FieldsPanel";
import { ModuleRecordBody } from "./ModuleRecordSheet";
import RecordList from "./RecordList";

const noop = () => undefined;
const CRM = fileURLToPath(new URL("..", import.meta.url));
const source = (path: string) => readFileSync(join(CRM, path), "utf8");

const def = (key: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label: `My ${key}`,
  type: "text",
  options: null,
  position: 0,
  visible: true,
  source: "zoho",
  api_name: key,
  ...over,
});

const layout = (over: Partial<FieldLayout> = {}): FieldLayout => ({
  entity: "contacts",
  sections: [],
  fields: [],
  studio: false,
  studio_admin: false,
  empty_columns: null,
  ...over,
});

afterEach(() => clearAll());

const listProps = {
  rows: [] as Record<string, unknown>[],
  total: 0,
  loading: false,
  sort: null,
  direction: "asc" as const,
  onSort: noop,
  onOpen: noop,
};

/** Draw the list with this layout in the read cache. */
function drawList(
  entity: "contacts" | "deals",
  value: FieldLayout | null,
  over: Record<string, unknown> = {},
): string {
  clearAll();
  if (value) put(fieldLayoutKey(entity), value);
  const html = renderToStaticMarkup(createElement(RecordList, { ...listProps, entity, ...over }));
  clearAll();
  return html;
}

const headers = (html: string) => [...html.matchAll(/<th\b[^>]*>(?:<button[^>]*>)?([^<]*)/g)].map((m) => m[1]);

// ── Item 1: the list hides each key of `empty_columns` ─────────────────────

describe("the list hides each standard column that no record fills (item 1)", () => {
  it("leaves out each key of the set, and never the name", () => {
    const cols = shownColumns(COLUMNS.contacts, ["title", "email", "phone", "first_name"]);
    expect(cols.map((c) => c.key)).toEqual(["first_name", "owner_email", "last_activity_at"]);
  });

  it("hides nothing with no set, or with the empty set of stale numbers", () => {
    expect(shownColumns(COLUMNS.contacts, null)).toEqual(COLUMNS.contacts);
    expect(shownColumns(COLUMNS.contacts, [])).toEqual(COLUMNS.contacts);
  });

  it("draws the list without the empty columns that the layout names", () => {
    const html = drawList("contacts", layout({ empty_columns: ["title", "email", "phone"] }));
    expect(headers(html)).toEqual(["Name", "Owner", "Last touched"]);
    const before = drawList("contacts", layout());
    expect(headers(before)).toEqual(["Name", "Title", "Email", "Phone", "Owner", "Last touched"]);
  });

  it("hides a joined column by its list key", () => {
    const html = drawList(
      "deals",
      layout({ entity: "deals", empty_columns: ["company_id", "organization_name"] }),
    );
    expect(headers(html)).not.toContain("Company");
    expect(headers(html)).toContain("Stage");
  });
});

// ── Item 2: with no saved choice, the fields with the highest fill ─────────

const FILLED: FieldDef[] = [
  def("low", { fill: 0.1, position: 0 }),
  def("none", { fill: 0, position: 1 }),
  def("top", { fill: 0.95, position: 2 }),
  def("Layout", { fill: 1, position: 3 }),
  def("mid", { fill: 0.5, distinct: 3, position: 4 }),
  def("mid2", { fill: 0.5, distinct: 40, position: 5 }),
  def("hidden", { fill: 0.99, visible: false, position: 6 }),
  def("unknown", { fill: null, position: 7 }),
  def("sys", { fill: 1, is_system: true, position: 8 }),
  def("a6", { fill: 0.2, position: 9 }),
  def("a7", { fill: 0.3, position: 10 }),
];

describe("the default columns are the fields with the highest fill (item 2)", () => {
  it("takes the fullest fields, up to the column limit", () => {
    expect(defaultColumns(FILLED)).toEqual(["top", "mid2", "mid", "a7", "a6"]);
    expect(defaultColumns(FILLED)).toHaveLength(MAX_COLUMNS);
  });

  it("never takes an empty field, a hidden one, a system row or a Zoho system key", () => {
    const picked = defaultColumns(FILLED);
    for (const key of ["none", "hidden", "unknown", "sys", "Layout"]) {
      expect(picked).not.toContain(key);
    }
  });

  it("gives no default with no fill numbers, which is the list of before", () => {
    expect(defaultColumns([def("a"), def("b")])).toEqual([]);
    expect(fillRankOn([def("a")])).toBe(false);
    expect(fillRankOn(FILLED)).toBe(true);
  });

  it("lets a saved choice win, and keeps a saved empty list empty", () => {
    expect(effectiveColumns(null, FILLED)).toEqual(defaultColumns(FILLED));
    expect(effectiveColumns([], FILLED)).toEqual([]);
    expect(effectiveColumns(["low"], FILLED)).toEqual(["low"]);
  });

  it("tells nothing saved from saved empty in this browser", () => {
    const data: Record<string, string> = {};
    const store = { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => void (data[k] = v) };
    expect(readColumns("contacts", store)).toBeNull();
    writeColumns("contacts", [], store);
    expect(readColumns("contacts", store)).toEqual([]);
  });

  it("keeps the difference in a saved view", () => {
    expect(captureView(DEFAULT_VIEW, null, null).columns).toBeNull();
    expect(captureView(DEFAULT_VIEW, [], null).columns).toEqual([]);
    const saved = { columns: [] } as unknown as SavedView;
    expect(stateOf(saved).columns).toEqual([]);
    expect(stateOf({ columns: null } as unknown as SavedView).columns).toBeNull();
  });

  it("draws the default columns, and a view's saved choice in their place", () => {
    const defs = [def("top", { label: "Top", fill: 0.9 }), def("low", { label: "Low", fill: 0.2 })];
    const html = drawList("contacts", null, { fieldDefs: defs, columnKeys: null });
    expect(headers(html)).toEqual(expect.arrayContaining(["Top", "Low"]));
    const empty = drawList("contacts", null, { fieldDefs: defs, columnKeys: [] });
    expect(headers(empty)).not.toContain("Top");
    const one = drawList("contacts", null, { fieldDefs: defs, columnKeys: ["low"] });
    expect(headers(one)).toContain("Low");
    expect(headers(one)).not.toContain("Top");
  });

  it("is wired in the page: a view with nothing saved hands null to the list", () => {
    const page = source("page.tsx");
    expect(page).toContain("draftOfView ?? activeSaved?.columns ?? null");
    expect(page).not.toContain("activeSaved?.columns ?? []");
  });
});

// ── Item 3: a cell does not wrap ────────────────────────────────────────────

describe("a list cell does not wrap (item 3)", () => {
  const LONG = "A very long site name that goes on and on past the width of one column";

  it("cuts a standard cell with an ellipsis and keeps the full text in its title", () => {
    const html = drawList("contacts", null, {
      rows: [{ id: "c1", first_name: "Asha", title: LONG, custom: {} }],
      total: 1,
    });
    expect(html).toContain(`title="${LONG}"`);
    expect(html).toMatch(/<td class="whitespace-nowrap[^"]*"><span class="block max-w-xs truncate" title="A very long/);
  });

  it("does the same for a custom cell, and keeps its chips on one line", () => {
    const defs = [def("notes", { label: "Notes", fill: 0.9 }), def("tags", { type: "multiselect", label: "Tags", fill: 0.8 })];
    const html = drawList("contacts", null, {
      rows: [{ id: "c1", first_name: "Asha", custom: { notes: LONG, tags: ["a", "b"] } }],
      total: 1,
      fieldDefs: defs,
      columnKeys: null,
    });
    expect(html).toContain(`title="${LONG}"`);
    expect(html).toContain('title="a, b"');
    expect(html).toContain("flex gap-1 overflow-hidden");
    expect(html).not.toMatch(/<td[^>]*>[^<]*<span class="block max-w-xs truncate"[^>]*><span class="flex flex-wrap/);
  });

  it("gives an empty cell no title", () => {
    const html = drawList("contacts", null, {
      rows: [{ id: "c1", first_name: "Asha", custom: {} }],
      total: 1,
    });
    expect(html).not.toContain('title="—"');
  });
});

// ── Item 4: an owner who is not a member ────────────────────────────────────

const ZOHO_OWNED = {
  id: "d1",
  name: "Bosch order",
  owner_email: null,
  ext_source: "zoho",
  custom: { [OWNER_NAME_KEY]: "Ravi Kumar", Region: "North" },
};

const panelProps = {
  statuses: [],
  dealContacts: [],
  saving: false,
  onPatch: noop,
  onConvert: noop,
  onSetPrimary: noop,
  onDetach: noop,
  onOpenContact: noop,
};

describe("an owner who is not a member shows by the Zoho name (item 4)", () => {
  it("names the owner in the summary header, with Zoho only", () => {
    const facts = summaryFacts("deals", ZOHO_OWNED, null);
    expect(facts.find((f) => f.key === "owner_email")?.value).toEqual({
      kind: "text",
      text: "Ravi Kumar (Zoho only)",
    });
  });

  it("lets a member's email win over a stale name", () => {
    const facts = summaryFacts("deals", { ...ZOHO_OWNED, owner_email: "asha@x.in" }, null);
    expect(facts.find((f) => f.key === "owner_email")?.value).toEqual({ kind: "text", text: "asha@x.in" });
  });

  it("names the owner on Overview and in the list", () => {
    const overview = renderToStaticMarkup(
      createElement(FieldsPanel, { ...panelProps, part: "overview", entity: "deals", record: ZOHO_OWNED }),
    );
    expect(overview).toContain("Ravi Kumar (Zoho only)");
    const list = drawList("deals", null, { rows: [ZOHO_OWNED], total: 1 });
    expect(list).toContain("Ravi Kumar (Zoho only)");
  });

  it("names the owner in the Fields tab of the layout", () => {
    const html = renderToStaticMarkup(
      createElement(LayoutFields, {
        layout: layout({
          entity: "deals",
          fields: [def("owner_email", { label: "Owner", type: "email", is_system: true })],
        }),
        record: ZOHO_OWNED,
        statuses: [],
        companyName: null,
        saving: false,
        refusal: null,
        locked: true,
        onPatch: noop,
      }),
    );
    expect(html).toContain("Ravi Kumar (Zoho only)");
  });

  it("takes the key `_owner_name` out of the Fields tab", () => {
    expect(fieldRows(ZOHO_OWNED, []).map((r) => r.key)).toEqual(["Region"]);
    expect(undefinedRows(ZOHO_OWNED, []).map((r) => r.key)).toEqual(["Region"]);
    const html = renderToStaticMarkup(
      createElement(FieldsPanel, { ...panelProps, part: "fields", entity: "deals", record: ZOHO_OWNED }),
    );
    expect(html).not.toContain("Owner Name");
    expect(html).toContain("North");
  });
});

// ── Item 5: Overview by fill, and the fold of the Fields tab ───────────────

const CONTACT = {
  id: "c1",
  first_name: "Asha",
  last_name: "Rao",
  title: null,
  email: null,
  phone: null,
  mobile: "98450 12345",
  owner_email: "asha@x.in",
  ext_source: "zoho",
  custom: { Site_Code: "S-7", Region: null, Notes: "" },
};

const SYSTEM = (key: string, fill: number) => def(key, { is_system: true, fill });

/** The key of each Overview row: `custom:<key>` for a custom field. */
const entryKeys = (entries: OverviewEntry[]) =>
  entries.map((e) => (e.kind === "fixed" ? e.field.key : `custom:${e.row.key}`));

describe("Overview shows the filled fields first, by fill (item 5)", () => {
  const ranked = layout({
    fields: [
      SYSTEM("first_name", 1),
      SYSTEM("last_name", 0.6),
      SYSTEM("title", 0),
      SYSTEM("email", 0),
      SYSTEM("phone", 0),
      SYSTEM("mobile", 0.9),
      SYSTEM("owner_email", 0.7),
      def("Site_Code", { label: "Site code", fill: 0.8 }),
      def("Region", { label: "Region", fill: 0.95 }),
      def("Notes", { label: "Notes", fill: 0.05 }),
    ],
  });

  it("orders the filled fields by fill, the custom ones too, and the empty ones after", () => {
    expect(entryKeys(overviewEntries("contacts", CONTACT, ranked))).toEqual([
      "first_name",
      "mobile",
      "custom:Site_Code",
      "owner_email",
      "last_name",
      "title",
      "email",
      "phone",
      "description",
    ]);
  });

  it("shows the fullest custom fields that the record fills, at most eight", () => {
    const many = Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`F${i}`, `v${i}`]));
    const defs = Array.from({ length: 12 }, (_, i) => def(`F${i}`, { fill: i / 12 }));
    const keys = entryKeys(
      overviewEntries("contacts", { ...CONTACT, custom: many }, layout({ fields: defs })),
    ).filter((k) => k.startsWith("custom:"));
    expect(keys).toEqual(["F11", "F10", "F9", "F8", "F7", "F6", "F5", "F4"].map((k) => `custom:${k}`));
  });

  it("draws a custom row on Overview, read only", () => {
    const html = renderToStaticMarkup(
      createElement(FieldsPanel, {
        ...panelProps,
        part: "overview",
        entity: "contacts",
        record: CONTACT,
        layout: ranked,
      }),
    );
    expect(html).toContain("Site code");
    expect(html).toContain("S-7");
    expect(html.indexOf("S-7")).toBeLessThan(html.indexOf(">Title<"));
  });

  it("keeps the order of before with no fill numbers", () => {
    const keys = entryKeys(overviewEntries("contacts", CONTACT, layout()));
    expect(keys).toEqual([
      "first_name",
      "last_name",
      "title",
      "email",
      "phone",
      "mobile",
      "owner_email",
      "description",
    ]);
    expect(entryKeys(overviewEntries("contacts", CONTACT, null))).toEqual(keys);
  });

  it("puts a filled row first, then the higher fill, in a pure helper", () => {
    const rows = [
      { key: "a", filled: false, fill: 1 },
      { key: "b", filled: true, fill: 0.2 },
      { key: "c", filled: true, fill: 0.8 },
      { key: "d", filled: true, fill: 0.2 },
    ];
    const out = filledFirst(
      rows,
      (r) => r.filled,
      (r) => def(r.key, { fill: r.fill }),
    );
    expect(out.map((r) => r.key)).toEqual(["c", "b", "d", "a"]);
  });
});

describe("the Fields tab has a filter box and folds the empty fields (item 5)", () => {
  const defs = [
    def("Site_Code", { label: "Site code", fill: 0.9 }),
    def("Region", { label: "Region", fill: 0.1 }),
    def("Notes", { label: "Notes", fill: 0.05 }),
  ];
  const drawFields = (fieldDefs: FieldDef[]) =>
    renderToStaticMarkup(
      createElement(FieldsPanel, {
        ...panelProps,
        part: "fields",
        entity: "contacts",
        record: CONTACT,
        fieldDefs,
      }),
    );

  it("folds the empty fields behind Show n empty fields, and draws a filter box", () => {
    const html = drawFields(defs);
    expect(html).toContain('aria-label="Filter fields"');
    expect(html).toContain("Show 2 empty fields");
    expect(html).toContain("Site code");
    expect(html).not.toContain(">Region<");
    expect(html).not.toContain(">Notes<");
  });

  it("draws as before with no fill numbers", () => {
    const html = drawFields(defs.map((d) => ({ ...d, fill: null })));
    expect(html).not.toContain("Filter fields");
    expect(html).not.toContain("empty field");
    expect(html).toContain(">Region<");
  });

  it("filters by label or key, and counts only the empty rows that match", () => {
    const rows = fieldRows(CONTACT, defs);
    const isEmpty = (r: { value: { kind: string } }) => r.value.kind === "empty";
    expect(foldFields(rows, "", false, isEmpty)).toMatchObject({ folded: 2, empty: 2 });
    expect(foldFields(rows, "reg", false, isEmpty)).toMatchObject({ shown: [], folded: 1 });
    expect(foldFields(rows, "SITE", false, isEmpty).shown.map((r) => r.key)).toEqual(["Site_Code"]);
    expect(foldFields(rows, "", true, isEmpty).shown).toHaveLength(3);
    expect(foldLabel(1, false)).toBe("Show 1 empty field");
    expect(foldLabel(2, true)).toBe("Hide empty fields");
  });

  it("keeps the sections of the layout's Fields tab, with the filter box and the fold", () => {
    const SECTION = { id: "11111111-1111-4111-8111-111111111111", label: "Site facts", position: 0 };
    const html = renderToStaticMarkup(
      createElement(LayoutFields, {
        tools: true,
        layout: layout({
          sections: [SECTION],
          fields: defs.map((d) => ({ ...d, section_id: SECTION.id })),
        }),
        record: CONTACT,
        statuses: [],
        companyName: null,
        saving: false,
        refusal: null,
        locked: true,
        onPatch: noop,
      }),
    );
    expect(html).toContain("Site facts");
    expect(html).toContain('aria-label="Filter fields"');
    expect(html).toContain("Show 2 empty fields");
    expect(html).not.toContain(">Region<");
  });

  it("is on in the page only while the layout carries fill numbers", () => {
    expect(source("components/FieldsPanel.tsx")).toContain("tools={fillRankOn(layout.fields)}");
  });
});

// ── Item 5 and 6 on the record page of a generic module ─────────────────────

const MODULE_PAGE: ModuleRecordPage = {
  module: { api_name: "Operator_Associates", label: "Operator Associates", singular_label: "Operator Associate", count: 1 },
  record: {
    id: "m1",
    module: "Operator_Associates",
    name: "OA 1",
    owner_email: null,
    fields: {
      Layout: { id: "l1", name: "Standard" },
      Record_Image: "img.png",
      Tag: [{ name: "x" }],
      Low: "yes",
      Empty: null,
      High: "x",
    },
    ext_source: "zoho",
    ext_id: "z1",
    ext_url: null,
    ext_created_at: null,
    ext_modified_at: null,
  },
  field_defs: [
    { ...def("Layout", { label: "Layout", fill: 1, position: 0 }), lookup_module: null },
    { ...def("Low", { label: "Low", fill: 0.1, position: 1 }), lookup_module: null },
    { ...def("Empty", { label: "Empty", fill: 0.5, position: 2 }), lookup_module: null },
    { ...def("High", { label: "High", fill: 0.9, position: 3 }), lookup_module: null },
  ],
  links: {},
};

describe("the record page of a generic module (items 5 and 6)", () => {
  const draw = (tab: string, page = MODULE_PAGE) =>
    renderToStaticMarkup(createElement(ModuleRecordBody, { page, tab, onTab: noop, onOpenLink: noop }));

  it("draws the filled fields first on Overview, by fill", () => {
    const html = draw("overview");
    expect(html.indexOf(">High<")).toBeGreaterThan(-1);
    expect(html.indexOf(">High<")).toBeLessThan(html.indexOf(">Low<"));
    expect(html.indexOf(">Low<")).toBeLessThan(html.indexOf(">Empty<"));
  });

  it("folds the empty fields of the Fields tab", () => {
    const html = draw("fields");
    expect(html).toContain('aria-label="Filter fields"');
    expect(html).toContain("Show 1 empty field");
  });

  it("leaves out the Zoho system keys", () => {
    for (const tab of ["overview", "fields"]) {
      const html = draw(tab);
      expect(html).not.toContain(">Layout<");
      expect(html).not.toContain("Record Image");
      expect(html).not.toContain(">Tag<");
    }
  });
});

// ── Item 6: one named constant holds the Zoho system keys ──────────────────

describe("the Zoho system keys leave the record page (item 6)", () => {
  it("names the three keys in one constant", () => {
    expect([...ZOHO_SYSTEM_KEYS]).toEqual(["Layout", "Record_Image", "Tag"]);
  });

  it("drops them from the Fields tab, with and without a definition", () => {
    const record = { custom: { Layout: { name: "Standard" }, Record_Image: "a.png", Tag: [], Site: "S" } };
    expect(fieldRows(record, [def("Layout")]).map((r) => r.key)).toEqual(["Site"]);
    expect(undefinedRows(record, []).map((r) => r.key)).toEqual(["Site"]);
  });

  it("drops a definition of one from the layout's Fields tab", () => {
    const html = renderToStaticMarkup(
      createElement(LayoutFields, {
        layout: layout({ fields: [def("Layout", { label: "Layout" }), def("Site", { label: "Site" })] }),
        record: { custom: { Layout: { name: "Standard" }, Site: "S" } },
        statuses: [],
        companyName: null,
        saving: false,
        refusal: null,
        locked: true,
        onPatch: noop,
      }),
    );
    expect(html).toContain(">Site<");
    expect(html).not.toContain(">Layout<");
  });

  it("holds the list in no other file of the CRM", () => {
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) files.push(path);
      }
    };
    walk(CRM);
    const holders = files.filter((f) => readFileSync(f, "utf8").includes('"Record_Image"'));
    expect(holders.map((f) => f.replace(/\\/g, "/").split("/app/crm/")[1])).toEqual(["lib/fields.ts"]);
  });
});

// ── Item 7: the source's labels in Mirror mode ──────────────────────────────

const LABELS: SourceLabels = {
  contacts: { label: "Existing Sites", singular_label: "Existing Site" },
  organizations: { label: "Accounts", singular_label: null },
};

describe("a core entity shows the source's label in Mirror mode (item 7)", () => {
  it("names the list and the rail by the source's plural", () => {
    expect(viewLabel("contacts", LABELS)).toBe("Existing Sites");
    expect(viewLabel("organizations", LABELS)).toBe("Accounts");
    expect(viewLabel("deals", LABELS)).toBe("Deals");
    expect(viewLabel("reports", LABELS)).toBe("Reports");
    const labels = labelledViews(LABELS).map((v) => v.label);
    expect(labels).toEqual(["Leads", "Existing Sites", "Accounts", "Deals", "Reports"]);
    const rail = railEntries([], LABELS).flatMap((e) => (e.kind === "view" ? [e.view.label] : []));
    expect(rail).toEqual(labels);
  });

  it("names one record by the source's singular, else its plural", () => {
    expect(sourceSingular("contacts", LABELS)).toBe("Existing Site");
    expect(sourceSingular("organizations", LABELS)).toBe("Accounts");
    expect(sourceSingular("deals", LABELS)).toBeNull();
  });

  it("keeps the CRM's names with no labels, as a Native CRM has", () => {
    expect(viewLabel("organizations", null)).toBe("Companies");
    expect(viewLabel("contacts")).toBe("Contacts");
    expect(sourceSingular("contacts", null)).toBeNull();
  });

  it("draws the rail with the source's names", () => {
    const html = renderToStaticMarkup(createElement(CrmRail, { tab: "contacts", onSelect: noop, labels: LABELS }));
    expect(html).toContain("Existing Sites");
    expect(html).not.toContain(">Contacts<");
    const native = renderToStaticMarkup(createElement(CrmRail, { tab: "contacts", onSelect: noop }));
    expect(native).toContain("Companies");
  });

  it("reads the labels in Mirror mode only, and hands them to the rail, the heading and the record", () => {
    const page = source("page.tsx");
    expect(page).toContain('useSourceLabels(ctx.mode === "mirror")');
    // The rail (twice), the record and, since CRM-Q17b, the filter bar.
    expect(page.match(/labels=\{labels\}/g)?.length).toBe(4);
    expect(page).toContain("viewLabel(view.tab, labels)");
    expect(page).not.toMatch(/viewLabel\(view\.tab\)/);
    expect(source("components/RecordSheet.tsx")).toContain(
      "noun={sourceSingular(entity, labels) ?? ENTITY_NOUN[entity]}",
    );
  });
});
