// The field studio, the studio UI (WS-53 CRM-C4c, spec section 20.7).
//
// The runner has no DOM, so a component renders to a string, a drag runs
// through the pure moves of `lib/fieldStudio.ts`, and each studio call runs
// against a stubbed fetch. A `Modal` portals out of a static render, so the
// field form is tested through `FieldFormBody`, the form inside the dialog.
//
// `tests/unit/test_crm_field_type_change_parity.py` holds done-when 11 (the
// type matrix on both sides).
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it } from "vitest";

import { isKnownIcon } from "@/lib/icons";
import { CrmError } from "../lib/api";
import type { FieldDef } from "../lib/fields";
import { FIELD_TYPES, LOOKUP_TARGETS, type FieldLayout } from "../lib/fieldLayout";
import {
  TARGET_LABELS,
  TYPE_CHANGES,
  TYPE_LABELS,
  checkDraft,
  createBody,
  createField,
  createSection,
  deleteSection,
  draftOf,
  editableParts,
  errorPart,
  fieldsCardShape,
  layoutBody,
  moveField,
  moveOption,
  moveSection,
  newDraft,
  optionsBody,
  patchBody,
  patchField,
  putFieldLayout,
  renameSection,
  restoreField,
  retireField,
  retype,
  sectionDeleteBody,
  studioLayout,
  typeChoices,
  type FieldDraft,
  type OptionDraft,
} from "../lib/fieldStudio";
import { visibleSections } from "../lib/settingsSections";
import { FieldStudioView, RETIRED_LABEL, type StudioHandlers } from "./FieldStudio";
import { FieldFormBody, TARGET_OPTIONS } from "./FieldStudioFieldDialog";
import FieldStudioOptions from "./FieldStudioOptions";
import FieldsCard, { FIELD_ENTITIES, FIELDS_MIRROR_NOTE, FieldsCardView } from "./FieldsCard";

const noop = () => undefined;
const SOURCE_OF = (name: string) =>
  readFileSync(fileURLToPath(new URL(name, import.meta.url)), "utf8");

const SEC_A = "0b9c2f9e-4a8e-4b8f-9c2d-1f2e3d4c5b6a";
const SEC_B = "1c8d3e0f-5b9f-4c90-8d3e-2a3b4c5d6e7f";

const def = (key: string, over: Partial<FieldDef> = {}): FieldDef => ({
  key,
  label: key,
  type: "text",
  options: null,
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

const TIER_OPTIONS = [
  { label: "Gold", value: "gold" },
  { label: "Silver", value: "silver" },
  { label: "Bronze", value: "bronze", retired: true },
];

/** The deals layout of the visual check: two sections, system rows and a retired field. */
const LAYOUT: FieldLayout = {
  entity: "deals",
  sections: [
    { id: SEC_B, label: "Commercial", position: 1 },
    { id: SEC_A, label: "Qualification", position: 0 },
  ],
  fields: [
    def("name", { label: "Deal name", is_system: true, required: true, position: 0 }),
    def("amount", { label: "Amount", type: "currency", is_system: true, position: 1 }),
    def("tier", {
      label: "Tier",
      type: "select",
      options: TIER_OPTIONS,
      section_id: SEC_A,
      position: 2,
    }),
    def("budget", { label: "Budget", type: "number", section_id: SEC_A, position: 3 }),
    def("po_number", { label: "PO number", section_id: SEC_B, required: true, position: 4 }),
    def("old_code", { label: "Old code", retired: true, position: 5 }),
  ],
  studio: true,
  studio_admin: true,
};

// ── Stub of the gateway ────────────────────────────────────────────────────

type Sent = { url: string; method: string; body: unknown };
const original = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = original;
});

function stub(status: number, answer: unknown = {}): Sent[] {
  const sent: Sent[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    sent.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => answer,
      text: async () => JSON.stringify(answer),
    } as Response;
  }) as typeof globalThis.fetch;
  return sent;
}

// ── 1. Create ──────────────────────────────────────────────────────────────

describe("create (done-when 1)", () => {
  it("the create menu offers each of the 15 types, each with its studio label", () => {
    expect(typeChoices(null)).toEqual([...FIELD_TYPES]);
    expect(Object.keys(TYPE_LABELS).sort()).toEqual([...FIELD_TYPES].sort());
    expect(TYPE_LABELS.textarea).toBe("Long text");
    expect(TYPE_LABELS.select).toBe("Picklist");
  });

  it("a create sends the type word of each type", () => {
    for (const type of FIELD_TYPES) {
      let draft = retype({ ...newDraft(), label: `My ${type}` }, type);
      if (type === "lookup") draft = { ...draft, lookup_target: "contacts" };
      const body = createBody(draft);
      expect(body.type, type).toBe(type);
      expect(body.label).toBe(`My ${type}`);
      expect(checkDraft(draft, null), type).toEqual({});
    }
  });

  it("a lookup create sends one of the four entity slugs, and no default", () => {
    expect(TARGET_OPTIONS.map((o) => o.value)).toEqual([...LOOKUP_TARGETS]);
    expect(TARGET_OPTIONS.some((o) => o.value.startsWith("module:"))).toBe(false);
    expect(TARGET_LABELS.organizations).toBe("Companies");
    const draft: FieldDraft = { ...retype(newDraft(), "lookup"), label: "Partner", default_value: "x" };
    expect(checkDraft(draft, null).target).toBeTruthy();
    const body = createBody({ ...draft, lookup_target: "organizations" });
    expect(body.lookup_target).toBe("organizations");
    expect(body).not.toHaveProperty("default_value");
  });

  it("a picklist create sends its options as labels, and the gateway makes each value", () => {
    const draft: FieldDraft = {
      ...retype(newDraft(SEC_A), "select"),
      label: "Tier",
      options: [
        { value: null, label: "Gold", retired: false },
        { value: null, label: " Silver ", retired: false },
      ],
    };
    expect(createBody(draft)).toEqual({
      label: "Tier",
      type: "select",
      required: false,
      visible: true,
      section_id: SEC_A,
      options: [{ label: "Gold" }, { label: "Silver" }],
    });
  });

  it("POSTs to the studio route of the entity through call", async () => {
    const sent = stub(201, def("tier"));
    await createField("deals", { label: "Tier", type: "text" });
    expect(sent).toEqual([
      { url: "/api/crm/fields/deals", method: "POST", body: { label: "Tier", type: "text" } },
    ]);
  });

  it("the form refuses an empty label and a long one before the save", () => {
    expect(checkDraft(newDraft(), null).label).toBeTruthy();
    expect(checkDraft({ ...newDraft(), label: "x".repeat(81) }, null).label).toBeTruthy();
    expect(checkDraft({ ...newDraft(), label: "x".repeat(80) }, null)).toEqual({});
  });
});

// ── 2. Rename ──────────────────────────────────────────────────────────────

describe("rename (done-when 2)", () => {
  it("sends the label only, and the key stays in the path", async () => {
    const tier = LAYOUT.fields[2];
    const body = patchBody(tier, { ...draftOf(tier), label: "Customer tier" });
    expect(body).toEqual({ label: "Customer tier" });
    const sent = stub(200, tier);
    await patchField("deals", tier.key, body);
    expect(sent[0]).toEqual({
      url: "/api/crm/fields/deals/tier",
      method: "PATCH",
      body: { label: "Customer tier" },
    });
  });

  it("an unchanged form sends nothing", () => {
    for (const f of LAYOUT.fields) expect(patchBody(f, draftOf(f)), f.key).toEqual({});
  });
});

// ── 3. Reorder ─────────────────────────────────────────────────────────────

describe("reorder by drag (done-when 3)", () => {
  const shown = studioLayout(LAYOUT);

  it("draws the fields in no section first, then each section in order, then the retired", () => {
    expect(shown.groups.map((g) => g.section?.label ?? null)).toEqual([
      null,
      "Qualification",
      "Commercial",
    ]);
    expect(shown.groups.map((g) => g.fields.map((f) => f.key))).toEqual([
      ["name", "amount"],
      ["tier", "budget"],
      ["po_number"],
    ]);
    expect(shown.retired.map((f) => f.key)).toEqual(["old_code"]);
  });

  it("a drag inside a section moves the field to the place of the drop", () => {
    const next = moveField(shown, "tier", { section: SEC_A, index: 1 });
    expect(next.groups[1].fields.map((f) => f.key)).toEqual(["budget", "tier"]);
  });

  it("a drag into another section moves the field there, and the body says so", () => {
    const next = moveField(shown, "budget", { section: SEC_B, index: 0 });
    expect(layoutBody(next)).toEqual({
      sections: [SEC_A, SEC_B],
      fields: [
        { key: "name", section_id: null },
        { key: "amount", section_id: null },
        { key: "tier", section_id: SEC_A },
        { key: "budget", section_id: SEC_B },
        { key: "po_number", section_id: SEC_B },
      ],
    });
  });

  it("a system field moves into a section too, and a drop on its own place changes nothing", () => {
    const next = moveField(shown, "amount", { section: SEC_B, index: 1 });
    expect(next.groups[2].fields.map((f) => f.key)).toEqual(["po_number", "amount"]);
    expect(moveField(shown, "tier", { section: SEC_A, index: 0 })).toBe(shown);
  });

  it("the body names each section once and each live field once, and leaves out a retired one", () => {
    const body = layoutBody(shown);
    expect(body.sections).toEqual([SEC_A, SEC_B]);
    const keys = body.fields.map((f) => f.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys).not.toContain("old_code");
    expect(keys.sort()).toEqual(["amount", "budget", "name", "po_number", "tier"]);
  });

  it("a drop PUTs the whole order in one body", async () => {
    const sent = stub(200, LAYOUT);
    const body = layoutBody(moveField(shown, "budget", { section: null, index: 0 }));
    await putFieldLayout("deals", body);
    expect(sent).toEqual([{ url: "/api/crm/field-layout/deals", method: "PUT", body }]);
    expect(body.fields[0]).toEqual({ key: "budget", section_id: null });
  });

  it("each row and each section header carries an HTML5 drag handle", () => {
    const html = view();
    // Five live rows and two section headers.
    expect(html.match(/draggable="true"/g)?.length).toBe(7);
    const source = SOURCE_OF("./FieldStudio.tsx");
    expect(source).toContain("onDragStart");
    expect(source).toContain("putFieldLayout(entity, layoutBody(next))");
  });
});

// ── 4. Sections ────────────────────────────────────────────────────────────

describe("sections (done-when 4)", () => {
  it("creates, renames and deletes a section through call", async () => {
    let sent = stub(201, { id: SEC_A, label: "Billing", position: 2 });
    await createSection("deals", " Billing ");
    expect(sent[0]).toEqual({
      url: "/api/crm/field-sections/deals",
      method: "POST",
      body: { label: "Billing" },
    });
    sent = stub(200, { id: SEC_A, label: "Money", position: 0 });
    await renameSection("deals", SEC_A, "Money");
    expect(sent[0]).toEqual({
      url: `/api/crm/field-sections/deals/${SEC_A}`,
      method: "PATCH",
      body: { label: "Money" },
    });
    sent = stub(200, { deleted: SEC_A, moved: 2 });
    await deleteSection("deals", SEC_A);
    expect(sent[0]).toEqual({
      url: `/api/crm/field-sections/deals/${SEC_A}`,
      method: "DELETE",
      body: undefined,
    });
  });

  it("reorders a section by drag, and the fields in no section stay first", () => {
    const shown = studioLayout(LAYOUT);
    const next = moveSection(shown, SEC_B, 0);
    expect(next.groups.map((g) => g.section?.id ?? null)).toEqual([null, SEC_B, SEC_A]);
    expect(layoutBody(next).sections).toEqual([SEC_B, SEC_A]);
    expect(moveSection(shown, SEC_A, 0)).toBe(shown);
  });

  it("before a delete, says that the fields move to no section", () => {
    expect(sectionDeleteBody(2)).toContain("Its 2 fields move to no section");
    expect(sectionDeleteBody(1)).toContain("Its 1 field moves to no section");
    expect(sectionDeleteBody(0)).toContain("No field and no value is lost");
    const source = SOURCE_OF("./FieldStudio.tsx");
    expect(source).toMatch(/<ConfirmDialog[\s\S]*?sectionDeleteBody\(/);
    expect(source).toMatch(/onDeleteSection: \(section, count\) => setConfirm\(/);
  });

  it("each section header offers add, rename and delete", () => {
    const html = view();
    for (const label of ["Qualification", "Commercial"]) {
      expect(html).toContain(`aria-label="Add a field to ${label}"`);
      expect(html).toContain(`aria-label="Rename ${label}"`);
      expect(html).toContain(`aria-label="Delete ${label}"`);
    }
  });
});

// ── 5. Picklist options ────────────────────────────────────────────────────

describe("picklist options (done-when 5)", () => {
  const stored = draftOf(LAYOUT.fields[2]).options;

  it("reads the stored options with their values and the retired flag", () => {
    expect(stored).toEqual([
      { value: "gold", label: "Gold", retired: false },
      { value: "silver", label: "Silver", retired: false },
      { value: "bronze", label: "Bronze", retired: true },
    ]);
  });

  it("a rename keeps the value, and a retire keeps the option with retired: true", () => {
    const edited: OptionDraft[] = stored.map((o) =>
      o.value === "gold" ? { ...o, label: "Gold plus" } : o.value === "silver" ? { ...o, retired: true } : o,
    );
    expect(optionsBody(edited)).toEqual([
      { label: "Gold plus", value: "gold" },
      { label: "Silver", value: "silver", retired: true },
      { label: "Bronze", value: "bronze", retired: true },
    ]);
  });

  it("an add sends a label only, and a reorder moves the option", () => {
    const added = [...stored, { value: null, label: "Platinum", retired: false }];
    expect(optionsBody(added).at(-1)).toEqual({ label: "Platinum" });
    expect(moveOption(added, 3, 0).map((o) => o.label)).toEqual([
      "Platinum",
      "Gold",
      "Silver",
      "Bronze",
    ]);
  });

  it("the PATCH holds every saved option, so the gateway never sees one removed", () => {
    const tier = LAYOUT.fields[2];
    const draft = { ...draftOf(tier), options: moveOption(stored, 0, 1) };
    const body = patchBody(tier, draft);
    expect(Object.keys(body)).toEqual(["options"]);
    expect((body.options as Array<{ value?: string }>).map((o) => o.value)).toEqual([
      "silver",
      "gold",
      "bronze",
    ]);
  });

  it("the editor offers Retire for a saved option and Remove for a new one", () => {
    const html = renderToStaticMarkup(
      createElement(FieldStudioOptions, {
        options: [...stored, { value: null, label: "Platinum", retired: false }],
        onChange: noop,
      }),
    );
    expect(html).toContain('aria-label="Retire Gold"');
    expect(html).toContain('aria-label="Restore Bronze"');
    expect(html).toContain('aria-label="Remove Platinum"');
    expect(html).not.toContain('aria-label="Remove Gold"');
    expect(html.match(/draggable="true"/g)?.length).toBe(4);
  });

  it("the 422 option_removed shows beside the options", () => {
    const err = new CrmError("An option cannot be removed.", 422, "option_removed");
    expect(errorPart(err)).toEqual({ part: "options", message: "An option cannot be removed." });
  });
});

// ── 6. Retire and restore ──────────────────────────────────────────────────

describe("retire and restore (done-when 6)", () => {
  it("a retire DELETEs the field and a restore POSTs to /restore", async () => {
    let sent = stub(200, def("tier"));
    await retireField("deals", "tier");
    expect(sent[0]).toMatchObject({ url: "/api/crm/fields/deals/tier", method: "DELETE" });
    sent = stub(200, def("tier"));
    await restoreField("deals", "old_code");
    expect(sent[0]).toMatchObject({ url: "/api/crm/fields/deals/old_code/restore", method: "POST" });
  });

  it("a retired field shows under its own heading with a Restore control", () => {
    const html = view();
    expect(html).toContain(RETIRED_LABEL);
    expect(html).toContain('aria-label="Restore Old code"');
    expect(html).not.toContain('aria-label="Edit Old code"');
  });

  it("a retire asks first, in a ConfirmDialog", () => {
    const source = SOURCE_OF("./FieldStudio.tsx");
    expect(source).toMatch(/onRetire=\{\(def\) => setConfirm\(\{ kind: "retire", def \}\)\}/);
    expect(source).toMatch(/retireField\(entity, def\.key\)/);
  });
});

// ── 7. Required ────────────────────────────────────────────────────────────

describe("required (done-when 7)", () => {
  it("a toggle sends required only", () => {
    const tier = LAYOUT.fields[2];
    expect(patchBody(tier, { ...draftOf(tier), required: true })).toEqual({ required: true });
    const po = LAYOUT.fields[4];
    expect(patchBody(po, { ...draftOf(po), required: false })).toEqual({ required: false });
  });

  it("the form draws the Required box for a custom field", () => {
    expect(form(LAYOUT.fields[2])).toContain("Required");
  });
});

// ── 8. Default ─────────────────────────────────────────────────────────────

describe("the default value (done-when 8)", () => {
  const budget = LAYOUT.fields[3];

  it("is edited with FieldInput, with the error of the form", () => {
    const source = SOURCE_OF("./FieldStudioFieldDialog.tsx");
    expect(source).toMatch(/<FieldInput[\s\S]*?error=\{problems\.default\}/);
    const html = form(budget);
    expect(html).toContain("Default value");
    expect(html).toContain('inputMode="text"');
  });

  it("refuses a bad value before the save, and passes a good one", () => {
    expect(checkDraft({ ...draftOf(budget), default_value: "twelve" }, budget).default).toBeTruthy();
    expect(checkDraft({ ...draftOf(budget), default_value: 1.5 }, budget).default).toBeTruthy();
    expect(checkDraft({ ...draftOf(budget), default_value: 12 }, budget)).toEqual({});
    expect(patchBody(budget, { ...draftOf(budget), default_value: 12 })).toEqual({ default_value: 12 });
  });

  it("a cleared default sends null", () => {
    const withDefault = { ...budget, default_value: 5 };
    expect(patchBody(withDefault, { ...draftOf(withDefault), default_value: null })).toEqual({
      default_value: null,
    });
  });

  it("a 422 that names the field shows beside the default editor", () => {
    const err = new CrmError("Budget: give a whole number.", 422, "field_value", "budget");
    expect(errorPart(err).part).toBe("default");
    expect(errorPart(new CrmError("x", 422, undefined, "budget")).part).toBe("default");
  });

  it("a lookup offers no default", () => {
    const partner = def("partner", { type: "lookup", lookup_target: "contacts" });
    expect(editableParts(partner).default).toBe(false);
    expect(form(partner)).toContain("A lookup has no default value.");
  });

  it("a picklist that becomes a multi-select keeps its default as a list of one", () => {
    const draft = { ...draftOf(LAYOUT.fields[2]), default_value: "gold" };
    expect(retype(draft, "multiselect").default_value).toEqual(["gold"]);
  });
});

// ── 9. System fields ───────────────────────────────────────────────────────

describe("system fields (done-when 9)", () => {
  const name = LAYOUT.fields[0];
  const amount = LAYOUT.fields[1];

  it("offer a label, a hide and an order only", () => {
    expect(editableParts(amount)).toEqual({
      label: true,
      visible: true,
      type: false,
      target: false,
      options: false,
      section: false,
      required: false,
      help: false,
      default: false,
      retire: false,
    });
  });

  it("a system field that the gateway needs (required on the layout) offers no hide", () => {
    expect(editableParts(name).visible).toBe(false);
    const html = form(name);
    expect(html).toContain("stays visible");
    expect(html).not.toContain(">Hidden<");
  });

  it("the form has no type menu, no retire, no required and no default", () => {
    const html = form(amount);
    expect(html).toContain("Hidden");
    for (const absent of ["Type", "Retire", "Required", "Default value", "Help text", "Section"]) {
      expect(html, absent).not.toContain(`>${absent}<`);
    }
  });

  it("a PATCH of a system field sends a label and visible only", () => {
    const body = patchBody(amount, {
      ...draftOf(amount),
      label: "Value",
      visible: false,
      required: true,
      help_text: "x",
      default_value: 5,
    });
    expect(body).toEqual({ label: "Value", visible: false });
  });

  it("the list marks a system field and gives it no retire", () => {
    const html = view();
    expect(html.match(/>System</g)?.length).toBe(2);
    expect(html).not.toContain('aria-label="Retire ');
  });
});

// ── 10. The type menu ──────────────────────────────────────────────────────

describe("the type menu of a field that exists (done-when 10)", () => {
  it("offers the type and the matrix changes only", () => {
    expect(typeChoices(def("n", { type: "number" }))).toEqual([
      "number",
      "decimal",
      "currency",
      "percent",
    ]);
    expect(typeChoices(def("u", { type: "url" }))).toEqual(["url", "textarea"]);
    expect(typeChoices(def("d", { type: "date" }))).toEqual(["date"]);
    for (const type of FIELD_TYPES) {
      const offered = typeChoices(def("f", { type }));
      expect(offered[0]).toBe(type);
      expect(offered.slice(1)).toEqual([...(TYPE_CHANGES[type] ?? [])]);
    }
  });

  it("the form refuses a type outside the menu", () => {
    const date = def("d", { type: "date" });
    expect(checkDraft({ ...draftOf(date), type: "text" }, date).type).toBeTruthy();
  });

  it("a type with no change draws its menu disabled", () => {
    expect(form(def("d", { label: "Due", type: "date" }))).toContain("cannot change");
  });

  it("a refused type change shows beside the type menu", () => {
    expect(errorPart(new CrmError("no", 422, "field_type_change")).part).toBe("type");
    expect(errorPart(new CrmError("no", 422, "lookup_target_change")).part).toBe("target");
    expect(errorPart(new CrmError("no", 422, "label")).part).toBe("label");
    expect(errorPart(new CrmError("no", 409, "mirror_mode")).part).toBe("form");
    expect(errorPart(new Error("down")).part).toBe("form");
  });
});

// ── 12. Who sees it ────────────────────────────────────────────────────────

describe("who sees the studio (done-when 12)", () => {
  const layout = (studio: boolean, admin: boolean): FieldLayout => ({
    ...LAYOUT,
    studio,
    studio_admin: admin,
  });

  it("an admin of a Native org with the flag on sees the studio", () => {
    expect(fieldsCardShape(false, { layout: layout(true, true), loading: false })).toBe("studio");
  });

  it("an admin with the flag off sees the read-only card", () => {
    expect(fieldsCardShape(false, { layout: layout(false, false), loading: false })).toBe("read-only");
    expect(fieldsCardShape(false, { layout: layout(true, false), loading: false })).toBe("read-only");
  });

  it("an admin of a Mirror org sees the read-only card, which sends them to Zoho", () => {
    expect(fieldsCardShape(true, { layout: layout(true, true), loading: false })).toBe("read-only");
    expect(FIELDS_MIRROR_NOTE).toContain("Edit fields in Zoho");
    const html = renderToStaticMarkup(createElement(FieldsCard, { readOnly: true }));
    expect(html).toContain("Edit fields in Zoho");
  });

  it("a failed read draws the card of today, and a first read draws a skeleton", () => {
    expect(fieldsCardShape(false, { layout: null, loading: false })).toBe("read-only");
    expect(fieldsCardShape(false, { layout: null, loading: true })).toBe("loading");
  });

  it("a member never sees the fields section", () => {
    for (const mode of ["native", "mirror"] as const) {
      expect(visibleSections({ isAdmin: false, mode }).map((s) => s.id)).not.toContain("fields");
      expect(visibleSections({ isAdmin: true, mode }).map((s) => s.id)).toContain("fields");
    }
  });

  it("the card draws the studio only for that shape", () => {
    const source = SOURCE_OF("./FieldsCard.tsx");
    expect(source).toMatch(/if \(shape === "studio"\) return <FieldStudio entities=\{FIELD_ENTITIES\} \/>;/);
    expect(source).not.toContain("CRM-S6");
    expect(FIELD_ENTITIES.map((e) => e.slug)).toEqual(["leads", "contacts", "organizations", "deals"]);
  });

  it("the read-only card keeps its list", () => {
    const html = renderToStaticMarkup(
      createElement(FieldsCardView, {
        byEntity: { leads: [], contacts: [], organizations: [], deals: LAYOUT.fields },
        readOnly: false,
      }),
    );
    expect(html).toContain("Field editing comes later.");
    expect(html).not.toContain("<input");
  });
});

// ── The seams ──────────────────────────────────────────────────────────────

describe("the studio keeps to the shared seams", () => {
  it("calls the gateway only through call of api.ts", () => {
    const lib = SOURCE_OF("../lib/fieldStudio.ts");
    expect(lib).toMatch(/import \{ call, CrmError \} from "\.\/api"/);
    expect(lib).not.toMatch(/\bfetch\(/);
    for (const file of [
      "./FieldStudio.tsx",
      "./FieldStudioFieldDialog.tsx",
      "./FieldStudioOptions.tsx",
      "./FieldStudioSectionDialog.tsx",
    ]) {
      const source = SOURCE_OF(file);
      expect(source, file).not.toMatch(/\bfetch\(/);
      expect(source, file).not.toMatch(/router\.(push|replace)/);
      expect(source, file).not.toMatch(/rounded-\[/);
      expect(source, file).not.toMatch(/fixed inset-0/);
    }
  });

  it("each dialog is a Modal, and each confirm is a ConfirmDialog", () => {
    expect(SOURCE_OF("./FieldStudioFieldDialog.tsx")).toContain('import Modal from "@/components/ui/Modal"');
    expect(SOURCE_OF("./FieldStudioSectionDialog.tsx")).toContain('import Modal from "@/components/ui/Modal"');
    expect(SOURCE_OF("./FieldStudio.tsx")).toContain(
      'import ConfirmDialog from "@/components/ui/ConfirmDialog"',
    );
  });

  it("uses only icons that exist", () => {
    for (const file of ["./FieldStudio.tsx", "./FieldStudioFieldDialog.tsx", "./FieldStudioOptions.tsx", "./FieldStudioSectionDialog.tsx"]) {
      for (const m of SOURCE_OF(file).matchAll(/icon(?:=|: )"([A-Za-z0-9]+)"|name="([A-Za-z0-9]+)"/g)) {
        const name = m[1] ?? m[2];
        expect(isKnownIcon(name), `${file}: ${name}`).toBe(true);
      }
    }
  });
});

// ── Helpers ────────────────────────────────────────────────────────────────

const HANDLERS: StudioHandlers = {
  onEntity: noop,
  onNewField: noop,
  onEdit: noop,
  onRestore: noop,
  onNewSection: noop,
  onRenameSection: noop,
  onDeleteSection: noop,
  onMove: noop,
  onRetry: noop,
};

function view(layout: FieldLayout = LAYOUT): string {
  return renderToStaticMarkup(
    createElement(FieldStudioView, {
      entities: FIELD_ENTITIES,
      entity: "deals",
      layout: studioLayout(layout),
      loading: false,
      readError: null,
      busy: false,
      notice: null,
      handlers: HANDLERS,
    }),
  );
}

function form(field: FieldDef): string {
  return renderToStaticMarkup(
    createElement(FieldFormBody, {
      form: { mode: "edit", def: field },
      sections: LAYOUT.sections,
      onClose: noop,
      onSave: async () => undefined,
      onRetire: noop,
    }),
  );
}
