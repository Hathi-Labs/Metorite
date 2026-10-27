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
  guardClose,
  mergeOptions,
  readFieldOptions,
  revertedOrder,
  sectionFieldCount,
  stepField,
  stepSection,
  HIDDEN_REQUIRED,
  NO_ACTIVE_OPTION,
  OPTIONS_CONFLICT,
  PENDING_OPTION,
  type FieldDraft,
  type OptionDraft,
} from "../lib/fieldStudio";
import { visibleSections } from "../lib/settingsSections";
import { FieldStudioView, RETIRED_LABEL, type StudioHandlers } from "./FieldStudio";
import { FieldFormBody, TARGET_OPTIONS } from "./FieldStudioFieldDialog";
import FieldStudioOptions from "./FieldStudioOptions";
import FieldsCard, {
  FIELD_ENTITIES,
  FIELDS_MIRROR_NOTE,
  FIELDS_READ_FAILED,
  FieldsCardError,
  FieldsCardView,
} from "./FieldsCard";

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
    expect(source).toContain("putFieldLayout(moved, layoutBody(next))");
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
        pending: "",
        onPending: noop,
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

  it("a failed read draws an error with Retry, and a first read draws a skeleton (review P2)", () => {
    expect(fieldsCardShape(false, { layout: null, loading: false, error: "Gateway error 502" })).toBe(
      "error",
    );
    expect(fieldsCardShape(false, { layout: null, loading: true })).toBe("loading");
    // Mirror mode reads no layout, so it never shows the error.
    expect(fieldsCardShape(true, { layout: null, loading: false, error: "x" })).toBe("read-only");
    const html = renderToStaticMarkup(
      createElement(FieldsCardError, { error: "Gateway error 502", onRetry: noop }),
    );
    expect(html).toContain(FIELDS_READ_FAILED);
    expect(html).toContain(">Retry<");
    expect(html).not.toContain("Only a studio admin can edit fields.");
    expect(SOURCE_OF("./FieldsCard.tsx")).toMatch(
      /if \(shape === "error"\) return <FieldsCardError error=\{probe\.error\} onRetry=\{probe\.refresh\} \/>;/,
    );
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
    expect(html).toContain("Only a studio admin can edit fields.");
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

// ── Review round 1 ─────────────────────────────────────────────────────────

describe("P1: a required field that no member can fill", () => {
  const text = (over: Partial<FieldDraft>): FieldDraft => ({ ...newDraft(), label: "Code", ...over });

  it("refuses a required field that is hidden with no default", () => {
    expect(checkDraft(text({ required: true, visible: false }), null).required).toBe(HIDDEN_REQUIRED);
    expect(checkDraft(text({ required: true, visible: false, default_value: "web" }), null)).toEqual({});
    expect(checkDraft(text({ required: false, visible: false }), null)).toEqual({});
    const lookup = { ...retype(text({}), "lookup"), lookup_target: "contacts" as const };
    expect(checkDraft({ ...lookup, required: true, visible: false }, null).required).toBe(
      HIDDEN_REQUIRED,
    );
  });

  it("refuses a hide of a required field with no default", () => {
    const po = LAYOUT.fields[4];
    expect(checkDraft({ ...draftOf(po), visible: false }, po).required).toBe(HIDDEN_REQUIRED);
  });

  it("refuses a required picklist with no active option", () => {
    const select = retype(text({ required: true }), "select");
    expect(checkDraft(select, null).options).toBe(NO_ACTIVE_OPTION);
    const tier = LAYOUT.fields[2];
    const allRetired = draftOf(tier).options.map((o) => ({ ...o, retired: true }));
    expect(checkDraft({ ...draftOf(tier), required: true, options: allRetired }, tier).options).toBe(
      NO_ACTIVE_OPTION,
    );
    expect(checkDraft({ ...draftOf(tier), required: true }, tier)).toEqual({});
    expect(checkDraft({ ...draftOf(tier), options: allRetired }, tier)).toEqual({});
  });

  it("refuses a typed new option that the admin did not add", () => {
    const tier = LAYOUT.fields[2];
    expect(checkDraft({ ...draftOf(tier), new_option: "Platinum" }, tier).options).toBe(
      PENDING_OPTION,
    );
    expect(checkDraft({ ...draftOf(tier), new_option: "   " }, tier)).toEqual({});
    expect(SOURCE_OF("./FieldStudioFieldDialog.tsx")).toContain(
      "onPending={(text) => set({ new_option: text })}",
    );
  });

  it("the gateway's 422 required_unfillable shows beside Required", () => {
    expect(errorPart(new CrmError("PO is required and hidden.", 422, "required_unfillable"))).toEqual({
      part: "required",
      message: "PO is required and hidden.",
    });
    expect(SOURCE_OF("./FieldStudioFieldDialog.tsx")).toContain(
      '<Problem id={errId("required")} text={problems.required} />',
    );
  });
});

describe("P2: a failed drop after a tab switch keeps the new tab", () => {
  it("reverts only while the admin is on the entity of the drop", () => {
    expect(revertedOrder("deals", "leads", LAYOUT)).toBeUndefined();
    expect(revertedOrder("deals", "deals", LAYOUT)).toEqual(studioLayout(LAYOUT));
    expect(revertedOrder("deals", "deals", null)).toBeNull();
  });

  it("the tabs are off while a write runs", () => {
    const html = renderToStaticMarkup(
      createElement(FieldStudioView, {
        entities: FIELD_ENTITIES,
        entity: "deals",
        layout: studioLayout(LAYOUT),
        loading: false,
        readError: null,
        busy: true,
        notice: null,
        handlers: HANDLERS,
      }),
    );
    expect(html).toMatch(/<fieldset disabled=""[^>]*>[\s\S]*role="tablist"/);
    expect(view()).not.toMatch(/<fieldset disabled/);
    const source = SOURCE_OF("./FieldStudio.tsx");
    expect(source).toMatch(/onEntity: \(next\) => \{\s+if \(busy\) return;/);
    expect(source).toContain("revertedOrder(moved, live.current.entity, live.current.served)");
  });
});

describe("P2: a close during a save keeps the refusal", () => {
  it("guardClose ignores a close while the save runs", () => {
    let closed = 0;
    const close = () => {
      closed += 1;
    };
    guardClose(true, close)();
    expect(closed).toBe(0);
    guardClose(false, close)();
    expect(closed).toBe(1);
  });

  it("both dialogs guard the Modal, and a late refusal goes to the studio notice", () => {
    for (const file of ["./FieldStudioFieldDialog.tsx", "./FieldStudioSectionDialog.tsx"]) {
      expect(SOURCE_OF(file), file).toContain("onClose={guardClose(busy, onClose)}");
    }
    const dialog = SOURCE_OF("./FieldStudioFieldDialog.tsx");
    expect(dialog).toMatch(/if \(mounted\.current\) setProblems\(\{ \[part\]: message \}\);\s+else onLateError\?\.\(message\);/);
    expect(SOURCE_OF("./FieldStudio.tsx")).toContain("onLateError={setNotice}");
  });
});

describe("P2: Move up and Move down, for a keyboard and a phone", () => {
  const shown = studioLayout(LAYOUT);
  const order = (l: ReturnType<typeof studioLayout>) => l.groups.map((g) => g.fields.map((f) => f.key));

  it("moves a field one place, and across a section edge", () => {
    expect(order(stepField(shown, "budget", -1))[1]).toEqual(["budget", "tier"]);
    expect(order(stepField(shown, "tier", -1))).toEqual([
      ["name", "amount", "tier"],
      ["budget"],
      ["po_number"],
    ]);
    expect(order(stepField(shown, "budget", 1))).toEqual([
      ["name", "amount"],
      ["tier"],
      ["budget", "po_number"],
    ]);
    expect(stepField(shown, "name", -1)).toBe(shown);
    expect(stepField(shown, "po_number", 1)).toBe(shown);
  });

  it("moves a section one place", () => {
    expect(layoutBody(stepSection(shown, SEC_B, -1)).sections).toEqual([SEC_B, SEC_A]);
    expect(stepSection(shown, SEC_A, -1)).toBe(shown);
    expect(stepSection(shown, SEC_B, 1)).toBe(shown);
  });

  it("each field and section draws the two buttons, off at an end", () => {
    const html = view();
    for (const label of ["Tier", "Budget", "Qualification", "Commercial"]) {
      expect(html).toContain(`aria-label="Move ${label} up"`);
      expect(html).toContain(`aria-label="Move ${label} down"`);
    }
    expect(html).toMatch(/aria-label="Move Deal name up" disabled=""/);
    expect(html).toMatch(/aria-label="Move PO number down" disabled=""/);
    expect(html).toMatch(/aria-label="Move Qualification up" disabled=""/);
    expect(html).not.toMatch(/aria-label="Move Tier up" disabled=""/);
    // A move sends the same full order as a drag.
    expect(SOURCE_OF("./FieldStudio.tsx")).toMatch(/onMove=\{handlers\.onMove\}/);
  });

  it("each option draws the two buttons too", () => {
    const html = renderToStaticMarkup(
      createElement(FieldStudioOptions, {
        options: draftOf(LAYOUT.fields[2]).options,
        onChange: noop,
        pending: "",
        onPending: noop,
      }),
    );
    expect(html).toMatch(/aria-label="Move Gold up" disabled=""/);
    expect(html).toMatch(/aria-label="Move Bronze down" disabled=""/);
    expect(html).toContain('aria-label="Move Silver up"');
  });
});

describe("P2: a PATCH of options keeps the change of another admin", () => {
  const opened: OptionDraft[] = [
    { value: "gold", label: "Gold", retired: false },
    { value: "silver", label: "Silver", retired: false },
  ];

  it("applies this dialog's rename over another admin's rename of another option", () => {
    const mine = [{ ...opened[0], label: "Gold plus" }, opened[1]];
    const current = [opened[0], { ...opened[1], label: "Silver 2" }];
    expect(mergeOptions(opened, mine, current)).toEqual({
      options: [
        { value: "gold", label: "Gold plus", retired: false },
        { value: "silver", label: "Silver 2", retired: false },
      ],
    });
  });

  it("refuses when another admin changed an option that this dialog changed", () => {
    const mine = [{ ...opened[0], retired: true }, opened[1]];
    const current = [{ ...opened[0], label: "Golden" }, opened[1]];
    expect(mergeOptions(opened, mine, current)).toEqual({ conflict: OPTIONS_CONFLICT });
  });

  it("keeps an option that another admin added, and adds this dialog's options", () => {
    const fresh = { value: null, label: "Bronze", retired: false };
    const theirs = { value: "platinum", label: "Platinum", retired: false };
    expect(mergeOptions(opened, [...opened, fresh], [...opened, theirs])).toEqual({
      options: [...opened, theirs, fresh],
    });
    // With a new order from this dialog, the other admin's option goes last.
    const swapped = [opened[1], opened[0]];
    expect(mergeOptions(opened, swapped, [...opened, theirs])).toEqual({
      options: [opened[1], opened[0], theirs],
    });
  });

  it("the save reads the options again and sends the merge", async () => {
    const sent = stub(200, LAYOUT);
    expect(await readFieldOptions("deals", "tier")).toEqual(draftOf(LAYOUT.fields[2]).options);
    expect(sent[0]).toMatchObject({ url: "/api/crm/fields/deals?layout=1", method: "GET" });
    expect(SOURCE_OF("./FieldStudioFieldDialog.tsx")).toContain(
      "mergeOptions(draftOf(def).options, draft.options, await readOptions(def.key))",
    );
  });
});

describe("review notes", () => {
  it("a system field gets its own type only (verifier advisory)", () => {
    expect(typeChoices(LAYOUT.fields[1])).toEqual(["currency"]);
    expect(typeChoices(def("x", { type: "number", is_system: true }))).toEqual(["number"]);
  });

  it("a section delete counts its retired fields too", () => {
    const withRetired: FieldLayout = {
      ...LAYOUT,
      fields: [...LAYOUT.fields, def("old_tier", { retired: true, section_id: SEC_A })],
    };
    expect(sectionFieldCount(studioLayout(withRetired), SEC_A)).toBe(3);
    expect(sectionFieldCount(studioLayout(withRetired), SEC_B)).toBe(1);
    expect(SOURCE_OF("./FieldStudio.tsx")).toContain("sectionFieldCount(layout, section.id)");
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
