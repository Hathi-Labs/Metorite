// ── The field studio, the studio UI (WS-53 CRM-C4c) ─────────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4c: the studio
// UI". The owner asked for "a good system for creating and setting up the
// fields like it is there on ZOHO CRM". An admin of a Native org with the flag
// `CRM_FIELD_STUDIO_ORGS` on edits the fields of leads, contacts, companies and
// deals in the `fields` settings section.
//
// Pure, except for the studio calls at the end, so the vitest setup (no DOM)
// can test every rule. `components/FieldStudio*.tsx` draws what this file
// decides.
//
// ⚠️ The gateway is the authority (`routes/crm/field_studio.py`). Each call
// below goes through `call` of `api.ts`, the one way to call the gateway, so
// a refusal keeps its status, its code and its field. A second `fetch` path is
// a defect (CRM-C4p item 5).
//
// ⚠️ `TYPE_CHANGES` and the three label limits are literals on purpose.
// `tests/unit/test_crm_field_type_change_parity.py` reads them out of this
// file and compares them with the Python. A change on one side only turns it
// red.

import { call, CrmError } from "./api";
import { type FieldDef } from "./fields";
import {
  FIELD_TYPES,
  LOOKUP_TARGETS,
  checkValue,
  isEmptyValue,
  isLookupTarget,
  textLength,
  type FieldLayout,
  type FieldSection,
  type FieldType,
} from "./fieldLayout";
import type { EntitySlug } from "./types";

// ── The mirror of field_studio.py ───────────────────────────────────────────

/** `LABEL_MAX`: the label of a field or a section. */
export const LABEL_MAX = 80;
/** `OPTION_LABEL_MAX`: the label of a picklist option. */
export const OPTION_LABEL_MAX = 120;
/** `HELP_MAX`: the help text of a field. */
export const HELP_MAX = 500;

/**
 * The type change matrix (section 20.7 item 9), `TYPE_CHANGES` of
 * `field_studio.py`. The gateway allows a change only when each stored value
 * stays valid, so the type menu of a field offers only these. A type with no
 * row changes to nothing.
 */
export const TYPE_CHANGES: Readonly<Record<string, readonly FieldType[]>> = {
  text: ["textarea"],
  number: ["decimal", "currency", "percent"],
  decimal: ["percent", "currency"],
  percent: ["decimal", "currency"],
  currency: ["decimal", "percent"],
  select: ["multiselect"],
  email: ["text"],
  phone: ["text"],
  url: ["textarea"],
};

/** The studio label of each type word (section 20.7 item 1). */
export const TYPE_LABELS: Record<FieldType, string> = {
  text: "Text",
  textarea: "Long text",
  number: "Whole number",
  decimal: "Decimal",
  percent: "Percent",
  currency: "Currency",
  date: "Date",
  datetime: "Date and time",
  boolean: "Checkbox",
  email: "Email",
  phone: "Phone",
  url: "URL",
  select: "Picklist",
  multiselect: "Multi-select",
  lookup: "Lookup",
};

export type LookupTarget = (typeof LOOKUP_TARGETS)[number];

/** The label of each lookup target. `organizations` reads "Companies", as in the rail. */
export const TARGET_LABELS: Record<LookupTarget, string> = {
  leads: "Leads",
  contacts: "Contacts",
  organizations: "Companies",
  deals: "Deals",
};

/** The label of a type word. A word that the studio does not know reads as it is. */
export function typeLabel(type: string): string {
  return (TYPE_LABELS as Record<string, string>)[type] ?? type;
}

/** True for a type word of the studio. */
export function isFieldType(type: unknown): type is FieldType {
  return typeof type === "string" && (FIELD_TYPES as readonly string[]).includes(type);
}

/** True for a picklist or a multi-select. */
export function hasOptions(type: string): boolean {
  return type === "select" || type === "multiselect";
}

/**
 * The types that the type menu offers (scope item 2). At create: each of the
 * 15 types. On a field that exists: its type, then the changes of the matrix.
 * A system field has no type menu, so it gets its own type only.
 */
export function typeChoices(def: FieldDef | null): string[] {
  if (def === null) return [...FIELD_TYPES];
  if (def.is_system) return [def.type];
  return [def.type, ...(TYPE_CHANGES[def.type] ?? [])];
}

// ── What the studio may change on a field (done-when 9) ────────────────────

/** The parts of the field form. `form` is a refusal that names no part. */
export type FormPart =
  | "label"
  | "type"
  | "target"
  | "options"
  | "section"
  | "required"
  | "visible"
  | "help"
  | "default"
  | "form";

export type FieldParts = Record<Exclude<FormPart, "form">, boolean> & { retire: boolean };

/**
 * What the studio offers for a field. A system field takes a new label, a
 * hide and an order only. A system field that the gateway needs is one that
 * the layout marks `required` (`fields.py`, `field_wire`), and it offers no
 * hide. The order of a field changes by drag, so it is not a part here.
 */
export function editableParts(def: FieldDef): FieldParts {
  if (def.is_system) {
    return {
      label: true,
      visible: !def.required,
      type: false,
      target: false,
      options: false,
      section: false,
      required: false,
      help: false,
      default: false,
      retire: false,
    };
  }
  return {
    label: true,
    visible: true,
    type: typeChoices(def).length > 1,
    target: def.type === "lookup",
    options: hasOptions(def.type),
    section: true,
    required: true,
    help: true,
    // A lookup has no default (`check_value`, `allow_lookup=False`).
    default: def.type !== "lookup",
    retire: true,
  };
}

// ── The draft of the field form ────────────────────────────────────────────

/** One option in the editor. A new option has no `value` until the gateway makes it. */
export type OptionDraft = { value: string | null; label: string; retired: boolean };

export type FieldDraft = {
  label: string;
  type: string;
  lookup_target: LookupTarget | null;
  options: OptionDraft[];
  section_id: string | null;
  required: boolean;
  visible: boolean;
  help_text: string;
  default_value: unknown;
};

/** The draft of a new field. It starts as Text, in the section the admin chose. */
export function newDraft(section: string | null = null): FieldDraft {
  return {
    label: "",
    type: "text",
    lookup_target: null,
    options: [],
    section_id: section,
    required: false,
    visible: true,
    help_text: "",
    default_value: null,
  };
}

/** The options of a stored field, with the retired flag. */
function storedOptions(def: FieldDef): OptionDraft[] {
  const out: OptionDraft[] = [];
  for (const raw of def.options ?? []) {
    if (typeof raw === "string") {
      out.push({ value: raw, label: raw, retired: false });
    } else if (raw && typeof raw === "object") {
      const o = raw as { value?: unknown; label?: unknown; retired?: unknown };
      if (typeof o.value !== "string") continue;
      const label = typeof o.label === "string" && o.label ? o.label : o.value;
      out.push({ value: o.value, label, retired: o.retired === true });
    }
  }
  return out;
}

/** The draft of a field that exists. */
export function draftOf(def: FieldDef): FieldDraft {
  return {
    label: def.label,
    type: def.type,
    lookup_target: isLookupTarget(def.lookup_target) ? def.lookup_target : null,
    options: storedOptions(def),
    section_id: def.section_id ?? null,
    required: def.required === true,
    visible: def.visible,
    help_text: def.help_text ?? "",
    default_value: def.default_value ?? null,
  };
}

/**
 * The draft after a type change. A picklist that becomes a multi-select keeps
 * its default as a list of one, as the gateway converts each stored value
 * (item 9). A change to a type with no options or no target drops them.
 */
export function retype(draft: FieldDraft, type: string): FieldDraft {
  let defaultValue = draft.default_value;
  if (draft.type === "select" && type === "multiselect" && typeof defaultValue === "string") {
    defaultValue = [defaultValue];
  } else if (type === "lookup" || (draft.type !== type && !compatibleDefault(draft.type, type))) {
    defaultValue = null;
  }
  return {
    ...draft,
    type,
    default_value: defaultValue,
    options: hasOptions(type) ? draft.options : [],
    lookup_target: type === "lookup" ? draft.lookup_target : null,
  };
}

/** A default that stays valid over a matrix change. At create, another type clears it. */
function compatibleDefault(from: string, to: string): boolean {
  return (TYPE_CHANGES[from] ?? []).includes(to as FieldType);
}

/**
 * The draft as a field definition, for `FieldInput` and `checkValue`.
 * `required` is false, because a default may be empty. Only an option with a
 * `value` can be a default, so a new option is not one until the save.
 */
export function probeDef(draft: FieldDraft, key = "default"): FieldDef {
  return {
    key,
    label: draft.label.trim() || "Default value",
    type: draft.type,
    options: draft.options
      .filter((o) => o.value !== null)
      .map((o) => ({ value: o.value, label: o.label, ...(o.retired ? { retired: true } : {}) })),
    position: 0,
    visible: true,
    source: "native",
    api_name: null,
    required: false,
    lookup_target: draft.lookup_target,
  };
}

/** A label of 1 to `limit` characters, with no control character. `_clean_label`. */
function labelProblem(text: string, what: string, limit: number): string | null {
  const label = text.trim();
  // eslint-disable-next-line no-control-regex
  if (!label || textLength(label) > limit || /[\x00-\x1f\x7f]/.test(label)) {
    return `${what} needs 1 to ${limit} characters.`;
  }
  return null;
}

/** The problem of a section label, or null. */
export function sectionLabelProblem(text: string): string | null {
  return labelProblem(text, "A section label", LABEL_MAX);
}

export type DraftProblems = Partial<Record<FormPart, string>>;

/**
 * What the form refuses before the save, by part. The gateway checks each
 * part again. `def` is the field that exists, or null at create.
 */
export function checkDraft(draft: FieldDraft, def: FieldDef | null): DraftProblems {
  const problems: DraftProblems = {};
  const label = labelProblem(draft.label, "A field label", LABEL_MAX);
  if (label) problems.label = label;
  if (def?.is_system) return problems;
  if (!typeChoices(def).includes(draft.type)) {
    problems.type = "Choose a type from the menu.";
  }
  if (draft.type === "lookup" && !isLookupTarget(draft.lookup_target)) {
    problems.target = "Choose the record type that the lookup finds.";
  }
  if (hasOptions(draft.type)) {
    const bad = draft.options.find((o) => labelProblem(o.label, "An option", OPTION_LABEL_MAX));
    if (bad) problems.options = `An option needs 1 to ${OPTION_LABEL_MAX} characters.`;
  }
  if (textLength(draft.help_text.trim()) > HELP_MAX) {
    problems.help = `Use at most ${HELP_MAX} characters.`;
  }
  if (draft.type !== "lookup" && !isEmptyValue(draft.default_value)) {
    const bad = checkValue(probeDef(draft), draft.default_value, def?.default_value);
    if (bad) problems.default = bad;
  }
  return problems;
}

/**
 * The options as a create or a PATCH sends them (item 6). An option that
 * exists names its `value`, which never changes, so a rename keeps it. A
 * retired option stays in the list with `retired: true`. A new option sends
 * its label only, and the gateway makes its `value`.
 */
export function optionsBody(
  options: readonly OptionDraft[],
): Array<{ label: string; value?: string; retired?: boolean }> {
  return options.map((o) => {
    const label = o.label.trim();
    if (o.value === null) return { label };
    return o.retired ? { label, value: o.value, retired: true } : { label, value: o.value };
  });
}

/** One option moves from `from` to the place of `to`. */
export function moveOption(options: readonly OptionDraft[], from: number, to: number): OptionDraft[] {
  if (from === to || from < 0 || to < 0 || from >= options.length || to >= options.length) {
    return [...options];
  }
  const next = [...options];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

/**
 * The body of `POST /crm/fields/{entity}` (done-when 1). The gateway makes the
 * key from the label. A lookup sends one of the four entity slugs as
 * `lookup_target`, and it has no default.
 */
export function createBody(draft: FieldDraft): Record<string, unknown> {
  const body: Record<string, unknown> = {
    label: draft.label.trim(),
    type: draft.type,
    required: draft.required,
    visible: draft.visible,
    section_id: draft.section_id,
  };
  const help = draft.help_text.trim();
  if (help) body.help_text = help;
  if (hasOptions(draft.type)) body.options = optionsBody(draft.options);
  if (draft.type === "lookup") body.lookup_target = draft.lookup_target;
  else if (!isEmptyValue(draft.default_value)) body.default_value = draft.default_value;
  return body;
}

function same(a: unknown, b: unknown): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}

/**
 * The body of `PATCH /crm/fields/{entity}/{key}`: only the parts that
 * changed (done-when 2 and 7). A rename sends `label` only, and the key
 * stays. A system field sends a label and `visible` only. A cleared default
 * sends `null`, which removes it.
 */
export function patchBody(def: FieldDef, draft: FieldDraft): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const was = draftOf(def);
  const label = draft.label.trim();
  if (label !== def.label) body.label = label;
  const parts = editableParts(def);
  if (parts.visible && draft.visible !== was.visible) body.visible = draft.visible;
  if (def.is_system) return body;
  if (draft.type !== was.type) body.type = draft.type;
  if (draft.type === "lookup" && draft.lookup_target !== was.lookup_target) {
    body.lookup_target = draft.lookup_target;
  }
  if (hasOptions(draft.type) && !same(optionsBody(draft.options), optionsBody(was.options))) {
    body.options = optionsBody(draft.options);
  }
  if (draft.section_id !== was.section_id) body.section_id = draft.section_id;
  if (draft.required !== was.required) body.required = draft.required;
  if (draft.help_text.trim() !== was.help_text.trim()) body.help_text = draft.help_text.trim() || null;
  if (draft.type !== "lookup") {
    const next = isEmptyValue(draft.default_value) ? null : draft.default_value;
    const before = isEmptyValue(was.default_value) ? null : was.default_value;
    if (!same(next, before)) body.default_value = next;
  }
  return body;
}

// ── A refusal of the gateway, beside its control ───────────────────────────

/** The part of the form that each refusal code of `field_studio.py` names. */
const CODE_PARTS: Record<string, FormPart> = {
  label: "label",
  field_key: "label",
  field_key_taken: "label",
  field_type_change: "type",
  lookup_target: "target",
  lookup_target_change: "target",
  options: "options",
  option_removed: "options",
  option_unknown: "options",
  option_twice: "options",
  section_unknown: "section",
  field_value: "default",
};

/**
 * Where the form shows a refusal (the 422 rule). A value refusal names the
 * field in `CrmError.field`, and the only value that the studio sends is the
 * default, so it shows beside the default editor. A known code shows beside
 * its control. Any other refusal shows at the top of the form.
 */
export function errorPart(err: unknown): { part: FormPart; message: string } {
  const message = err instanceof Error ? err.message : String(err);
  if (err instanceof CrmError) {
    const byCode = err.code ? CODE_PARTS[err.code] : undefined;
    if (byCode) return { part: byCode, message };
    if (err.field) return { part: "default", message };
  }
  return { part: "form", message };
}

// ── The layout on screen, and the order a drag sends ───────────────────────

/** One block of the studio: a section, or the fields in no section. */
export type FieldGroup = { section: FieldSection | null; fields: FieldDef[] };

export type StudioLayout = {
  /** The fields in no section first, then each section in order. */
  groups: FieldGroup[];
  /** The retired custom fields. A layout write leaves them out, and they keep their place. */
  retired: FieldDef[];
};

/**
 * The layout as the studio draws it. A field whose section is not in the
 * layout sits in no section, as the readers treat it.
 */
export function studioLayout(layout: FieldLayout): StudioLayout {
  const sections = [...layout.sections].sort((a, b) => a.position - b.position);
  const known = new Set(sections.map((s) => s.id));
  const live = layout.fields.filter((f) => f.is_system || !f.retired);
  const sectionOf = (f: FieldDef) => (f.section_id && known.has(f.section_id) ? f.section_id : null);
  return {
    groups: [
      { section: null, fields: live.filter((f) => sectionOf(f) === null) },
      ...sections.map((section) => ({
        section,
        fields: live.filter((f) => sectionOf(f) === section.id),
      })),
    ],
    retired: layout.fields.filter((f) => !f.is_system && f.retired),
  };
}

/** `item` put into `list` at `index`, or last when `index` is past the end. */
function insertAt<T>(list: readonly T[], index: number, item: T): T[] {
  const at = Math.max(0, Math.min(index, list.length));
  return [...list.slice(0, at), item, ...list.slice(at)];
}

/**
 * The layout after a drag of one field (done-when 3), as the stage grid
 * moves a row (`PipelineSettings.tsx`). The field takes the place `index` of
 * the group of `section` (null: no section). A drop on a row takes that row's
 * place. A drop on the end of a group passes the length of the group. A drop
 * on its own place gives the same layout back.
 */
export function moveField(
  layout: StudioLayout,
  key: string,
  to: { section: string | null; index: number },
): StudioLayout {
  const moving = layout.groups.flatMap((g) => g.fields).find((f) => f.key === key);
  const target = layout.groups.find((g) => (g.section?.id ?? null) === to.section);
  if (!moving || !target) return layout;
  const groups = layout.groups.map((g) => {
    const rest = g.fields.filter((f) => f.key !== key);
    if (g !== target) return { ...g, fields: rest };
    return { ...g, fields: insertAt(rest, to.index, { ...moving, section_id: to.section }) };
  });
  const next = { ...layout, groups };
  return same(layoutBody(next), layoutBody(layout)) ? layout : next;
}

/**
 * The layout after a drag of one section (done-when 4). The section takes the
 * place `index` among the sections. The fields in no section stay first.
 */
export function moveSection(layout: StudioLayout, id: string, index: number): StudioLayout {
  const [loose, ...sections] = layout.groups;
  const moving = sections.find((g) => g.section?.id === id);
  if (!loose || !moving) return layout;
  const ordered = insertAt(
    sections.filter((g) => g !== moving),
    index,
    moving,
  );
  const next = { ...layout, groups: [loose, ...ordered] };
  return same(layoutBody(next), layoutBody(layout)) ? layout : next;
}

export type LayoutBody = {
  sections: string[];
  fields: Array<{ key: string; section_id: string | null }>;
};

/**
 * The body of `PUT /crm/field-layout/{entity}`: the section order and the
 * field order of each section, in one body (done-when 3). It names each
 * section once, and each system field and each live custom field once. The
 * gateway numbers the fields in this order, so the fields in no section come
 * first, as the studio draws them.
 */
export function layoutBody(layout: StudioLayout): LayoutBody {
  return {
    sections: layout.groups.flatMap((g) => (g.section ? [g.section.id] : [])),
    fields: layout.groups.flatMap((g) =>
      g.fields.map((f) => ({ key: f.key, section_id: g.section?.id ?? null })),
    ),
  };
}

// ── The words of the confirms ──────────────────────────────────────────────

/** What a section delete does (done-when 4). The gateway moves the fields in the same transaction. */
export function sectionDeleteBody(count: number): string {
  if (count === 0) return "The section holds no field. No field and no value is lost.";
  const fields = count === 1 ? "Its 1 field moves" : `Its ${count} fields move`;
  return `${fields} to no section. No field and no value is lost.`;
}

/** What a retire does (done-when 6). */
export const RETIRE_BODY =
  "Each record keeps its value, and no form or list shows the field. Restore it to bring it back.";

// ── Who sees the studio (done-when 12) ─────────────────────────────────────

export type FieldsCardShape = "studio" | "read-only" | "loading";

/**
 * What the `fields` section draws. The section is for an admin only, so a
 * member never gets here. The studio needs `studio_admin`: the flag allows the
 * org, the caller is an admin, and the mode is Native. Mirror mode reads only,
 * because Zoho owns the fields until CRM-C5. A failed read draws the card of
 * today.
 */
export function fieldsCardShape(
  readOnly: boolean,
  state: { layout: FieldLayout | null; loading: boolean },
): FieldsCardShape {
  if (readOnly) return "read-only";
  if (state.layout?.studio_admin === true) return "studio";
  if (state.loading && state.layout === null) return "loading";
  return "read-only";
}

// ── The studio calls ───────────────────────────────────────────────────────
//
// Each one goes through `call`. After each write, the studio calls `refresh`
// of `useFieldLayout`.

/** `/{entity}/{part}/…`, with each part encoded. */
function path(entity: EntitySlug, ...rest: string[]): string {
  return [`/${entity}`, ...rest.map(encodeURIComponent)].join("/");
}

function send<T>(url: string, method: string, body?: unknown): Promise<T> {
  return call<T>(url, {
    method,
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
}

/** `POST /crm/fields/{entity}`. */
export function createField(entity: EntitySlug, body: Record<string, unknown>): Promise<FieldDef> {
  return send<FieldDef>(`/fields${path(entity)}`, "POST", body);
}

/** `PATCH /crm/fields/{entity}/{key}`. */
export function patchField(
  entity: EntitySlug,
  key: string,
  body: Record<string, unknown>,
): Promise<FieldDef> {
  return send<FieldDef>(`/fields${path(entity, key)}`, "PATCH", body);
}

/** `DELETE /crm/fields/{entity}/{key}`: retire. Each record keeps its value. */
export function retireField(entity: EntitySlug, key: string): Promise<FieldDef> {
  return send<FieldDef>(`/fields${path(entity, key)}`, "DELETE");
}

/** `POST /crm/fields/{entity}/{key}/restore`. */
export function restoreField(entity: EntitySlug, key: string): Promise<FieldDef> {
  return send<FieldDef>(`/fields${path(entity, key, "restore")}`, "POST");
}

/** `PUT /crm/field-layout/{entity}`: the whole order in one body. */
export function putFieldLayout(entity: EntitySlug, body: LayoutBody): Promise<FieldLayout> {
  return send<FieldLayout>(`/field-layout${path(entity)}`, "PUT", body);
}

/** `POST /crm/field-sections/{entity}`. The new section goes last. */
export function createSection(entity: EntitySlug, label: string): Promise<FieldSection> {
  return send<FieldSection>(`/field-sections${path(entity)}`, "POST", { label: label.trim() });
}

/** `PATCH /crm/field-sections/{entity}/{id}`. */
export function renameSection(entity: EntitySlug, id: string, label: string): Promise<FieldSection> {
  return send<FieldSection>(`/field-sections${path(entity, id)}`, "PATCH", { label: label.trim() });
}

/** `DELETE /crm/field-sections/{entity}/{id}`. Its fields move to no section. */
export function deleteSection(
  entity: EntitySlug,
  id: string,
): Promise<{ deleted: string; moved: number }> {
  return send<{ deleted: string; moved: number }>(`/field-sections${path(entity, id)}`, "DELETE");
}
