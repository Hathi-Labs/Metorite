// The field studio, the record UI (WS-53 CRM-C4b, spec section 20.7).
//
// The runner has no DOM, so each component is rendered to a string, and a
// hook reads the layout that a test puts in the one read cache. A click
// cannot run here, so each decision of a click lives in `lib/recordForm.ts`
// and `lib/filterBar.ts`, and this file tests it there. A source check holds
// the one wire that a pure test cannot see: the create modal of `page.tsx`.
//
// Done-when 10 (the visual review) is not a test. The "Built as" note of
// the spec records it.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAll, put } from "@/lib/dataCache";
import { LOCK_TIP } from "../lib/connect";
import { checkValue, type FieldLayout } from "../lib/fieldLayout";
import {
  columnGroups,
  fieldText,
  formatField,
  layoutGroups,
  pickable,
  type FieldDef,
} from "../lib/fields";
import {
  NO_HINT,
  booleanFilterOptions,
  customFilters,
  dateFilterValue,
  numberFilterValue,
  optionsOf,
  picklistDefs,
  setCustom,
} from "../lib/filterBar";
import { listQuery, queryString } from "../lib/filters";
import {
  createCustom,
  createGroups,
  createSubmission,
  createValue,
  fieldPatch,
  missingRequired,
  placeRefusal,
  referenceValue,
  saveEdit,
  storedValue,
} from "../lib/recordForm";
import { useCrmStore } from "../lib/store";
import type { EntitySlug } from "../lib/types";
import { DEFAULT_VIEW, type CrmView } from "../lib/urlState";
import { fieldLayoutKey } from "../lib/useCrmMode";
import FieldInput, { pickLookup, toggleOption } from "./FieldInput";
import FieldsPanel from "./FieldsPanel";
import FilterBar from "./FilterBar";
import { QuickCreateForm } from "./QuickCreateModal";
import RecordList, { ColumnChoices } from "./RecordList";

const noop = () => undefined;
const CRM = fileURLToPath(new URL("..", import.meta.url));
const source = (path: string) => readFileSync(`${CRM}${path}`, "utf8");

// ── The fixture: one layout of each kind of field ──────────────────────────

const OPTIONS = [
  { label: "Gold", value: "gold" },
  { label: "Silver", value: "silver" },
  { label: "Bronze", value: "bronze", retired: true },
];

const def = (key: string, type: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label: `My ${key}`,
  type,
  options: type === "select" || type === "multiselect" ? OPTIONS : null,
  position: 0,
  visible: true,
  source: "native",
  api_name: null,
  section_id: null,
  required: false,
  help_text: null,
  default_value: null,
  lookup_target: null,
  is_system: false,
  retired: false,
  ...over,
});

const SALES = { id: "11111111-1111-4111-8111-111111111111", label: "Sales facts", position: 1 };
const MORE = { id: "22222222-2222-4222-8222-222222222222", label: "More detail", position: 0 };

/** Section order: no section, then More detail (0), then Sales facts (1). */
const FIELDS: FieldDef[] = [
  def("name", "text", { label: "Deal name", is_system: true, required: true, position: 0 }),
  def("status_id", "select", { label: "Stage", is_system: true, options: null, position: 1 }),
  def("pipeline_id", "select", { label: "Pipeline", is_system: true, options: null, position: 2 }),
  def("code", "text", { label: "Deal code", default_value: "007", position: 3 }),
  def("tier", "select", { label: "Tier", section_id: SALES.id, position: 4 }),
  def("colours", "multiselect", { label: "Colours", section_id: SALES.id, position: 5 }),
  def("seats", "number", { label: "Seats", section_id: SALES.id, position: 6, required: true }),
  def("rate", "decimal", { label: "Rate", section_id: SALES.id, position: 7 }),
  def("budget", "currency", { label: "Budget", section_id: SALES.id, position: 8 }),
  def("signed", "boolean", { label: "Signed", section_id: MORE.id, position: 9, required: true }),
  def("starts", "date", { label: "Starts", section_id: MORE.id, position: 10 }),
  def("contact", "lookup", {
    label: "Champion",
    section_id: MORE.id,
    position: 11,
    lookup_target: "contacts",
  }),
  def("old", "text", { label: "Old code", retired: true, position: 12 }),
  def("secret", "text", { label: "Secret note", visible: false, position: 13 }),
  def("lost", "text", { label: "Lost in space", section_id: "gone", position: 14 }),
];

const layout = (over: Partial<FieldLayout> = {}): FieldLayout => ({
  entity: "deals",
  sections: [SALES, MORE],
  fields: FIELDS,
  studio: true,
  studio_admin: false,
  ...over,
});

const byKey = (key: string) => FIELDS.find((f) => f.key === key)!;

const CONTACT_ID = "33333333-3333-4333-8333-333333333333";

const record = (over: Record<string, unknown> = {}) => ({
  id: "d1",
  name: "Bosch order",
  status_id: "s1",
  status_name: "Proposal",
  pipeline_id: "p1",
  custom: {
    tier: "gold",
    colours: ["silver", "bronze"],
    contact: { id: CONTACT_ID, name: "Asha Rao", missing: true },
  },
  ...over,
});

beforeEach(() => clearAll());
afterEach(() => {
  clearAll();
  vi.unstubAllGlobals();
});

/** Draw a component with this layout in the read cache, or with none. */
function withLayout(entity: EntitySlug, value: FieldLayout | null, draw: () => string): string {
  clearAll();
  if (value) put(fieldLayoutKey(entity), value);
  const html = draw();
  clearAll();
  return html;
}

const createProps = {
  entity: "deals" as const,
  saving: false,
  onClose: noop,
  onCreate: noop,
};

const drawCreate = (value: FieldLayout | null, over: Record<string, unknown> = {}) =>
  withLayout("deals", value, () =>
    renderToStaticMarkup(createElement(QuickCreateForm, { ...createProps, ...over })),
  );

// ── Done-when 1: the create form ────────────────────────────────────────────

describe("the create form shows the custom fields of the layout (done-when 1)", () => {
  it("draws each visible custom field through FieldInput, in section order", () => {
    const html = drawCreate(layout());
    const order = ["Deal code", "Lost in space", "More detail", "Signed", "Starts", "Champion",
      "Sales facts", "Tier", "Colours", "Seats", "Rate", "Budget"];
    const at = order.map((word) => html.indexOf(word));
    expect(at.every((i) => i >= 0), `${order.join(", ")} all show`).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    // No retired field, no hidden field, and no system field of the layout.
    for (const word of ["Old code", "Secret note", "Pipeline", "Stage"]) {
      expect(html).not.toContain(word);
    }
    // The fixed fields of today stay first.
    expect(html.indexOf("Expected close")).toBeLessThan(html.indexOf("Deal code"));
  });

  it("groups the fields by section, and the fields of no section come first", () => {
    const groups = createGroups(layout());
    expect(groups.map((g) => g.section?.label ?? null)).toEqual([null, "More detail", "Sales facts"]);
    expect(groups[0].fields.map((f) => f.key)).toEqual(["code", "lost"]);
  });

  it("fills an input with its default", () => {
    expect(drawCreate(layout())).toContain('value="007"');
    expect(createValue(byKey("code"), {})).toBe("007");
    expect(createValue(byKey("code"), { code: "" })).toBe("");
  });

  it("blocks the save while a required field has no value", () => {
    const defs = createGroups(layout()).flatMap((g) => g.fields);
    expect(missingRequired(defs, (d) => createValue(d, {}))).toEqual(["Seats"]);
    expect(missingRequired(defs, (d) => createValue(d, { seats: 4 }))).toEqual([]);
    // The Create button is disabled.
    expect(drawCreate(layout())).toMatch(/<button[^>]*disabled=""[^>]*>[^<]*(<[^>]+>)*Create/);
  });

  it("never blocks the save with a required boolean, which sends false (audit fix 3)", () => {
    const signed = byKey("signed");
    expect(signed.required).toBe(true);
    expect(missingRequired([signed], (d) => createValue(d, {}))).toEqual([]);
    expect(createValue(signed, {})).toBe(false);
    expect(checkValue(signed, createValue(signed, {}))).toBeNull();
    expect(createCustom([signed], {})).toEqual({ signed: false });
    expect(createSubmission([signed], {}, false)).toEqual({ custom: { signed: false }, errors: {} });
  });

  it("sends each value, and a cleared default as null", () => {
    const defs = [byKey("code"), byKey("tier"), byKey("starts")];
    expect(createCustom(defs, {})).toEqual({ code: "007" });
    expect(createCustom(defs, { code: "", tier: "gold" })).toEqual({ code: null, tier: "gold" });
  });

  it("refuses a bad value before the save, and sends nothing", () => {
    const out = createSubmission([byKey("seats")], { seats: "four" }, false);
    expect(out.custom).toBeNull();
    expect(out.errors.seats).toMatch(/whole number/);
  });

  it("sends no custom value from a Mirror form, which reads only until CRM-C5", () => {
    expect(createSubmission([byKey("code")], { code: "x" }, true)).toEqual({ custom: null, errors: {} });
  });
});

// ── Done-when 2 and the missing-lookup duty: an edit sends its key only ─────

describe("an edit sends only the key that changed (done-when 2)", () => {
  it("sends custom as a partial map of one key", () => {
    expect(fieldPatch(byKey("tier"), "gold", "silver")).toEqual({ custom: { tier: "silver" } });
    expect(fieldPatch(byKey("seats"), undefined, 4)).toEqual({ custom: { seats: 4 } });
  });

  it("sends a cleared value as a JSON null", () => {
    expect(fieldPatch(byKey("code"), "007", "")).toEqual({ custom: { code: null } });
    expect(fieldPatch(byKey("colours"), ["silver"], null)).toEqual({ custom: { colours: null } });
  });

  it("sends nothing when the value did not change", () => {
    expect(fieldPatch(byKey("code"), "007", "007")).toBeNull();
    expect(fieldPatch(byKey("code"), undefined, "")).toBeNull();
    expect(fieldPatch(byKey("signed"), undefined, false)).toBeNull();
  });

  it("sends a system field as its own column", () => {
    expect(fieldPatch(byKey("name"), "A", "B")).toEqual({ name: "B" });
  });

  it("never sends a missing lookup when another key changes (audit fix 4)", async () => {
    const held = record();
    const sent: Array<Record<string, unknown>> = [];
    const onPatch = async (body: Record<string, unknown>) => {
      sent.push(body);
      return true;
    };
    const tier = byKey("tier");
    const outcome = await saveEdit(tier, storedValue(tier, held), "silver", onPatch);
    expect(outcome).toEqual({ done: true });
    expect(sent).toEqual([{ custom: { tier: "silver" } }]);
    expect(JSON.stringify(sent)).not.toContain("contact");
    expect(JSON.stringify(sent)).not.toContain(CONTACT_ID);
  });

  it("reads an untouched lookup as unchanged, also with its missing flag", () => {
    const contact = byKey("contact");
    const stored = storedValue(contact, record());
    expect(fieldPatch(contact, stored, { id: CONTACT_ID, name: "Asha Rao" })).toBeNull();
  });
});

// ── Done-when 3: the lookup ─────────────────────────────────────────────────

describe("the lookup input (done-when 3)", () => {
  it("searches the lookup_target entity only, through ?q=", () => {
    const text = source("components/FieldInput.tsx");
    expect(text).toMatch(/api\s*\.listRecords\(target, \{ q, page_size: LOOKUP_PAGE \}\)/);
    expect(text).toMatch(/isLookupTarget\(def\.lookup_target\) \? def\.lookup_target : null/);
  });

  it("writes {id, name}, and the record shows the live name", () => {
    const hits = [{ id: CONTACT_ID, name: "Asha Rao" }];
    const value = pickLookup(CONTACT_ID, hits, null);
    expect(value).toEqual({ id: CONTACT_ID, name: "Asha Rao" });
    expect(fieldPatch(byKey("contact"), undefined, value)).toEqual({ custom: { contact: value } });
    // A record read gives the live name of the target.
    expect(fieldText(formatField(byKey("contact"), { id: CONTACT_ID, name: "Asha R." }))).toBe("Asha R.");
  });
});

// ── Done-when 4: the multi-select ───────────────────────────────────────────

describe("the multi-select (done-when 4)", () => {
  it("writes a JSON array of option values", () => {
    const next = toggleOption(["gold"], "silver", true);
    expect(next).toEqual(["gold", "silver"]);
    expect(fieldPatch(byKey("colours"), ["gold"], next)).toEqual({ custom: { colours: ["gold", "silver"] } });
  });

  it("shows a retired option on a record that holds it, and no picker offers it", () => {
    const colours = byKey("colours");
    expect(fieldText(formatField(colours, ["silver", "bronze"]))).toBe("Silver, Bronze");
    const empty = renderToStaticMarkup(createElement(FieldInput, { def: colours, value: [], onChange: noop }));
    expect(empty).not.toContain("Bronze");
    const held = renderToStaticMarkup(
      createElement(FieldInput, { def: colours, value: ["bronze"], onChange: noop }),
    );
    expect(held).toContain("Bronze");
    // The filter offers the active options only.
    expect(optionsOf(colours).map((o) => o.value)).toEqual(["gold", "silver"]);
  });
});

// ── Done-when 5: the Fields tab by layout ───────────────────────────────────

const panelProps = {
  part: "fields" as const,
  entity: "deals" as const,
  statuses: [],
  dealContacts: [],
  saving: false,
  onPatch: noop,
  onConvert: noop,
  onSetPrimary: noop,
  onDetach: noop,
  onOpenContact: noop,
  fieldDefs: FIELDS.filter((f) => !f.is_system),
};

const drawFields = (value: FieldLayout | null, row = record()) =>
  renderToStaticMarkup(createElement(FieldsPanel, { ...panelProps, record: row, layout: value }));

describe("the Fields tab draws the layout (done-when 5)", () => {
  it("draws each section in order, with the system and the custom fields", () => {
    const html = drawFields(layout());
    const order = ["Deal name", "Stage", "Deal code", "Lost in space", "More detail", "Signed",
      "Champion", "Sales facts", "Tier", "Colours", "Budget"];
    const at = order.map((word) => html.indexOf(word));
    expect(at.every((i) => i >= 0), `${order.join(", ")} all show`).toBe(true);
    expect([...at].sort((a, b) => a - b)).toEqual(at);
    expect(html).not.toContain("Old code");
    expect(html).not.toContain("Secret note");
    // The stage reads its name. A reference with no name the record carries is left out.
    expect(html).toContain("Proposal");
    expect(html).not.toContain("Pipeline");
  });

  it("shows a visible field with no value as a dash, with an edit", () => {
    const html = drawFields(layout());
    const budget = html.slice(html.indexOf("Budget"));
    expect(budget).toMatch(/^Budget<\/dt><dd[^>]*>.*?—.*?aria-label="Edit budget"/);
    expect(html).toContain('aria-label="Edit seats"');
    expect(html).toContain('aria-label="Edit deal code"');
  });

  it("reads each value by its type, and a missing lookup says so", () => {
    const html = drawFields(layout());
    expect(html).toContain("Gold");
    expect(html).toContain("Asha Rao (missing)");
  });

  it("shows each field of a Mirror record locked, with no edit", () => {
    const html = drawFields(layout(), record({ ext_source: "zoho" }));
    expect(html).toContain(LOCK_TIP);
    expect(html).not.toContain('aria-label="Edit');
    expect(html).toContain("Gold");
  });

  it("keeps an edit open when the gateway refuses it (done-when 8)", async () => {
    const tier = byKey("tier");
    const refused = await saveEdit(tier, "gold", "silver", async () => false);
    expect(refused).toEqual({ done: false, problem: null });
    const bad = await saveEdit(byKey("seats"), 2, "two", async () => true);
    expect(bad.done).toBe(false);
    // A required field that an edit clears never reaches the gateway.
    const onPatch = vi.fn(async () => true);
    const cleared = await saveEdit(byKey("seats"), 2, null, onPatch);
    expect(cleared).toEqual({ done: false, problem: "Give a value. This field is required." });
    expect(onPatch).not.toHaveBeenCalled();
  });

  it("names only the stage and the company of the system references", () => {
    const status = byKey("status_id");
    expect(referenceValue(status, record(), [], null)).toEqual({ kind: "text", text: "Proposal" });
    expect(referenceValue(status, record({ status_id: null }), [], null)).toEqual({ kind: "empty" });
    expect(referenceValue(byKey("pipeline_id"), record(), [], null)).toBeNull();
    const company = def("company_id", "lookup", { is_system: true });
    expect(referenceValue(company, { company_id: "c1" }, [], "Bosch")).toEqual({ kind: "text", text: "Bosch" });
  });
});

// ── Done-when 6: no retired field and no system row in the pickers ──────────

describe("the Columns control and the filter bar offer no retired field (done-when 6)", () => {
  const retiredOfEachType = ["select", "boolean", "number", "decimal", "currency", "date"].map((type) =>
    def(`r_${type}`, type, { retired: true }),
  );
  const systemOfEachType = ["select", "boolean", "number", "date"].map((type) =>
    def(`s_${type}`, type, { is_system: true }),
  );

  it("leaves each retired field and each system row out of every new filter type", () => {
    const keys = customFilters([...FIELDS, ...retiredOfEachType, ...systemOfEachType], true).map(
      (f) => f.def.key,
    );
    for (const gone of [...retiredOfEachType, ...systemOfEachType, byKey("old"), byKey("name")]) {
      expect(keys).not.toContain(gone.key);
    }
    expect(keys).toEqual(["tier", "colours", "seats", "rate", "budget", "signed", "starts"]);
  });

  it("offers the picklists only without the studio, as today", () => {
    expect(customFilters(FIELDS, false).map((f) => f.def.key)).toEqual(picklistDefs(FIELDS).map((d) => d.key));
  });

  it("gives the Columns control the set of pickable, by section", () => {
    const grouped = columnGroups(layout()).flatMap((g) => g.fields.map((f) => f.key));
    expect([...grouped].sort()).toEqual(pickable(FIELDS).map((f) => f.key).sort());
    expect(columnGroups(layout()).map((g) => g.section?.label ?? null)).toEqual([
      null,
      "More detail",
      "Sales facts",
    ]);
  });
});

// ── Done-when 7: the filter bar and its query ───────────────────────────────

const barProps = {
  entity: "deals" as const,
  onChange: noop,
  fieldDefs: FIELDS.filter((f) => !f.is_system),
  activeView: null,
  dirty: false,
  canEdit: false,
  isAdmin: false,
  saving: false,
  onSave: async () => undefined,
  onUpdate: async () => undefined,
  onReset: noop,
};

const drawBar = (value: FieldLayout | null, view: CrmView = DEFAULT_VIEW) =>
  withLayout("deals", value, () =>
    renderToStaticMarkup(createElement(FilterBar, { ...barProps, view })),
  );

const query = (view: CrmView) => queryString(listQuery("deals", { ...view, tab: "deals" }));

describe("the filter bar offers the custom filters of Scope item 5 (done-when 7)", () => {
  it("sends each filter as custom.<key>.in with one value (audit fix 2)", () => {
    const rows: Array<[string, string, string]> = [
      ["tier", "gold", "custom.tier.in=gold"],
      ["colours", "silver", "custom.colours.in=silver"],
      ["signed", "true", "custom.signed.in=true"],
      ["signed", "false", "custom.signed.in=false"],
      ["seats", numberFilterValue(byKey("seats"), "12")!, "custom.seats.in=12"],
      ["rate", numberFilterValue(byKey("rate"), "1.50")!, "custom.rate.in=1.5"],
      ["budget", numberFilterValue(byKey("budget"), "1,20,000")!, "custom.budget.in=120000"],
      ["starts", dateFilterValue("2026-10-14")!, "custom.starts.in=2026-10-14"],
    ];
    for (const [key, value, expected] of rows) {
      expect(query(setCustom(DEFAULT_VIEW, key, value))).toContain(expected);
    }
  });

  it("gives a number filter the text of String(Number(text))", () => {
    expect(numberFilterValue(byKey("rate"), "-2.50")).toBe("-2.5");
    expect(numberFilterValue(byKey("seats"), "")).toBe("");
    expect(numberFilterValue(byKey("seats"), "1,5")).toBeNull();
    expect(numberFilterValue(byKey("seats"), "ten")).toBeNull();
  });

  it("takes a real day only", () => {
    expect(dateFilterValue("")).toBe("");
    expect(dateFilterValue("2026-02-30")).toBeNull();
  });

  it("says in a hint that No does not find a record with no value", () => {
    const no = booleanFilterOptions(byKey("signed")).find((o) => o.value === "false");
    expect(no?.description).toBe(NO_HINT);
  });

  it("draws a control for each new type", () => {
    const html = drawBar(layout());
    for (const word of ["Tier", "Colours", "Signed", 'aria-label="Seats equals"', 'aria-label="Rate equals"',
      'aria-label="Budget equals"', 'aria-label="Starts on"']) {
      expect(html).toContain(word);
    }
    expect(html).not.toContain("Old code");
    expect(html).not.toContain("Deal code");
  });

  it("shows a number filter that the URL holds", () => {
    expect(drawBar(layout(), setCustom(DEFAULT_VIEW, "seats", "12"))).toMatch(/value="12"/);
  });
});

// ── Done-when 8: a refusal keeps the form open ──────────────────────────────

describe("a refused save keeps the form open (done-when 8)", () => {
  it("keeps the field of a refusal in the store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ detail: { code: "field_required", message: "Seats is required.", field: "seats" } }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const id = await useCrmStore.getState().createRecord("deals", { name: "x" });
    expect(id).toBeNull();
    expect(useCrmStore.getState().refusal).toEqual({ message: "Seats is required.", field: "seats" });
    const ok = await useCrmStore.getState().patchRecord("deals", "d1", { custom: { seats: null } });
    expect(ok).toBe(false);
    expect(useCrmStore.getState().refusal).toEqual({ message: "Seats is required.", field: "seats" });
  });

  it("places the message beside its field, or at the top", () => {
    const refusal = { message: "Seats is required.", field: "seats" };
    expect(placeRefusal(refusal, ["name", "seats"]).field).toBe("seats");
    expect(placeRefusal(refusal, ["name"]).top).toBe("Seats is required.");
    expect(placeRefusal({ message: "No.", field: null }, ["name"]).top).toBe("No.");
  });

  it("closes the create modal only when the save returns an id", () => {
    const page = source("page.tsx");
    const hunk = page.slice(page.indexOf("<QuickCreateModal"), page.indexOf("{converting && ("));
    expect(hunk).toContain("refusal={store.refusal}");
    expect(hunk).toMatch(/const id = await store\.createRecord\(entity, body\);[\s\S]*if \(!id\) return;[\s\S]*setCreating\(null\);/);
  });

  it("draws a refusal of the store only after this form sent a save", () => {
    const refusal = { message: "Budget is too big.", field: "budget" };
    // A refusal of another write (an edit on the sheet) does not show here.
    expect(drawCreate(layout(), { refusal })).not.toContain("Budget is too big.");
  });
});

// ── Done-when 9: with `studio` false, each surface draws as today ───────────

describe("with studio false, each surface draws as today (done-when 9)", () => {
  const off = layout({ studio: false });

  it("draws the create form of today", () => {
    expect(drawCreate(off)).toBe(drawCreate(null));
    expect(drawCreate(off)).not.toContain("Deal code");
  });

  it("draws the Fields tab of today", () => {
    expect(drawFields(off)).toBe(drawFields(null));
    expect(drawFields(off)).not.toContain("Sales facts");
  });

  it("draws the Columns control of today", () => {
    const listProps = {
      entity: "deals" as const,
      rows: [{ id: "1", name: "Deal one", custom: { tier: "gold" } }],
      total: 1,
      loading: false,
      sort: null,
      direction: "asc" as const,
      onSort: noop,
      onOpen: noop,
      fieldDefs: FIELDS.filter((f) => !f.is_system),
      columnKeys: ["tier"],
    };
    const list = (value: FieldLayout | null) =>
      withLayout("deals", value, () => renderToStaticMarkup(createElement(RecordList, listProps)));
    expect(list(off)).toBe(list(null));
    // The custom column shows in both.
    expect(list(layout())).toContain("Gold");
    // Without the studio, the chooser draws no heading.
    const flat = renderToStaticMarkup(
      createElement(ColumnChoices, {
        groups: [{ section: null, fields: pickable(FIELDS) }],
        chosen: [],
        onToggle: noop,
      }),
    );
    expect(flat).not.toContain("Sales facts");
    const grouped = renderToStaticMarkup(
      createElement(ColumnChoices, { groups: columnGroups(layout()), chosen: [], onToggle: noop }),
    );
    expect(grouped.indexOf("More detail")).toBeLessThan(grouped.indexOf("Sales facts"));
  });

  it("draws the filter bar of today", () => {
    expect(drawBar(off)).toBe(drawBar(null));
    expect(drawBar(off)).not.toContain("Seats equals");
  });
});

// ── The layout groups ───────────────────────────────────────────────────────

describe("layoutGroups", () => {
  it("puts a field of an unknown section in no section, and leaves out an empty section", () => {
    const groups = layoutGroups(layout({ sections: [SALES, MORE, { id: "e", label: "Empty", position: 9 }] }));
    expect(groups.map((g) => g.section?.label ?? null)).toEqual([null, "More detail", "Sales facts"]);
    expect(groups[0].fields.map((f) => f.key)).toContain("lost");
  });
});
