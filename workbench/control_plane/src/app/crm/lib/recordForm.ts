// ── The record forms of the field studio (WS-53 CRM-C4b) ────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4b: the record
// UI". Pure, so the vitest setup (no DOM) tests every rule.
// `QuickCreateModal.tsx` (the create form) and `FieldsPanel.tsx` (the Fields
// tab) draw what this file decides, and `FieldInput` draws each value.
//
// Two duties shape this file:
//
// - A save sends only the keys that changed (C4p review round 1 item 8). So a
//   lookup that reads `"missing": true` and that the member does not touch
//   never reaches a save, and the gateway never checks its old id again.
// - A boolean has no empty state in a form (C4p review item 5). `formValue`
//   gives the stored value, else the default, else `false`, and the form
//   sends that value. So a required boolean never blocks a save.

import { checkValue, formValue, isEmptyValue, type FieldLayout } from "./fieldLayout";
import { layoutGroups, type FieldDef, type FieldGroup, type FieldValue } from "./fields";
import type { CrmRefusal } from "./store";
import type { Status } from "./types";

type Row = Record<string, unknown>;

/** The value that a record holds for a field: its column, or its key of `custom`. */
export function storedValue(def: FieldDef, record: Row | null | undefined): unknown {
  if (!record) return undefined;
  if (def.is_system) return record[def.key];
  const custom = record.custom;
  return custom && typeof custom === "object" && !Array.isArray(custom)
    ? (custom as Row)[def.key]
    : undefined;
}

function lookupId(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const id = (value as Row).id;
  return typeof id === "string" ? id : null;
}

/**
 * Two values of one field are the same. A blank text, an empty list and
 * `null` are the same. A lookup compares its `id` only, because a read adds
 * the live name and `missing`. A boolean compares its `formValue`.
 */
export function sameValue(def: FieldDef, a: unknown, b: unknown): boolean {
  if (def.type === "boolean") return formValue(def, a) === formValue(def, b);
  if (def.type === "lookup") return lookupId(a) === lookupId(b);
  const plain = (v: unknown) => (isEmptyValue(v) ? null : v);
  return JSON.stringify(plain(a)) === JSON.stringify(plain(b));
}

/**
 * The body of an edit of one field (done-when 2), or null when the value did
 * not change. It names only that key. A custom field goes in `custom` as a
 * partial map, and a cleared value sends a JSON `null`. A system field is its
 * own column.
 */
export function fieldPatch(def: FieldDef, stored: unknown, next: unknown): Row | null {
  if (sameValue(def, stored, next)) return null;
  const value = def.type === "boolean" ? formValue(def, next) : isEmptyValue(next) ? null : next;
  return def.is_system ? { [def.key]: value } : { custom: { [def.key]: value } };
}

/** What became of an edit of one field. */
export type EditOutcome = { done: true } | { done: false; problem: string | null };

/**
 * Save the edit of one field of the Fields tab (done-when 2 and 8).
 *
 * - No change: nothing is sent, and the edit closes.
 * - The client check fails: nothing is sent, and its message shows.
 * - The gateway refuses (`onPatch` gives `false`): the edit stays open with
 *   the typed value. The caller shows the refusal of the store.
 */
export async function saveEdit(
  def: FieldDef,
  stored: unknown,
  draft: unknown,
  onPatch: (body: Row) => void | Promise<boolean>,
): Promise<EditOutcome> {
  const body = fieldPatch(def, stored, draft);
  if (!body) return { done: true };
  const problem = checkValue(def, formValue(def, draft), stored);
  if (problem) return { done: false, problem };
  const ok = await onPatch(body);
  return ok === false ? { done: false, problem: null } : { done: true };
}

/**
 * A system field that names another row: a stage, a pipeline, a company, a
 * lead or a lost reason. The member changes it with its own control (the
 * stage pill, the convert flow), so the Fields tab draws it read only.
 */
export function isReference(def: FieldDef): boolean {
  return def.is_system === true && (def.type === "select" || def.type === "lookup");
}

/**
 * What the Fields tab shows for a reference field, or null to leave the row
 * out. The stage and the company show their names. A reference whose name
 * the record does not carry is left out, because its id means nothing to a
 * member.
 */
export function referenceValue(
  def: FieldDef,
  record: Row,
  statuses: ReadonlyArray<Status>,
  companyName: string | null,
): FieldValue | null {
  const raw = record[def.key];
  if (raw === null || raw === undefined || raw === "") return { kind: "empty" };
  const name =
    def.key === "status_id"
      ? (statuses.find((s) => s.id === raw)?.name ?? record.status_name)
      : def.key === "company_id"
        ? (record.organization_name ?? companyName)
        : null;
  return typeof name === "string" && name ? { kind: "text", text: name } : null;
}

// ── The create form ─────────────────────────────────────────────────────────

/** The custom fields of the create form, by section: no system field. */
export function createGroups(layout: FieldLayout): FieldGroup[] {
  return layoutGroups(layout, (d) => !d.is_system);
}

/**
 * The value that the create form holds for a custom field: the member's
 * edit, else the default (done-when 1). A boolean is never empty.
 */
export function createValue(def: FieldDef, edited: Row): unknown {
  const value = Object.hasOwn(edited, def.key) ? edited[def.key] : def.default_value;
  return def.type === "boolean" ? formValue(def, value) : (value ?? null);
}

/**
 * The `custom` map of a create. A value goes when it is not empty. A boolean
 * always goes. A default that the member cleared goes as `null`, because the
 * gateway fills only an absent key with the default.
 */
export function createCustom(defs: readonly FieldDef[], edited: Row): Row {
  const out: Row = {};
  for (const def of defs) {
    const value = createValue(def, edited);
    if (def.type === "boolean" || !isEmptyValue(value)) out[def.key] = value;
    else if (def.default_value !== null && def.default_value !== undefined) out[def.key] = null;
  }
  return out;
}

/**
 * The labels of the required fields with no value. A boolean is never on
 * the list, because `formValue` always gives it a value.
 */
export function missingRequired(
  defs: readonly FieldDef[],
  valueOf: (def: FieldDef) => unknown,
): string[] {
  return defs
    .filter((d) => d.required && d.type !== "boolean" && isEmptyValue(valueOf(d)))
    .map((d) => d.label);
}

/** The message of the client check for each field that fails it, by key. */
export function formErrors(
  defs: readonly FieldDef[],
  valueOf: (def: FieldDef) => unknown,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const def of defs) {
    const problem = checkValue(def, valueOf(def));
    if (problem) out[def.key] = problem;
  }
  return out;
}

/**
 * The custom part of a create: the client check of each field, then the
 * `custom` map. A Mirror form sends no custom value, because Zoho owns the
 * fields until CRM-C5. `custom` is null when nothing goes.
 */
export function createSubmission(
  defs: readonly FieldDef[],
  edited: Row,
  locked: boolean,
): { custom: Row | null; errors: Record<string, string> } {
  if (locked || defs.length === 0) return { custom: null, errors: {} };
  const errors = formErrors(defs, (def) => createValue(def, edited));
  if (Object.keys(errors).length > 0) return { custom: null, errors };
  const custom = createCustom(defs, edited);
  return { custom: Object.keys(custom).length > 0 ? custom : null, errors };
}

/**
 * Where a refusal shows (the 422 rule): beside the field that it names, or
 * at the top of the form when it names no field of the form.
 */
export function placeRefusal(
  refusal: CrmRefusal | null,
  keys: readonly string[],
): { field: string | null; top: string | null; message: string | null } {
  if (!refusal) return { field: null, top: null, message: null };
  if (refusal.field && keys.includes(refusal.field)) {
    return { field: refusal.field, top: null, message: refusal.message };
  }
  return { field: null, top: refusal.message, message: refusal.message };
}
