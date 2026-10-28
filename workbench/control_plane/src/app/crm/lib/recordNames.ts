// ── The names of the core records (WS-53 CRM-Z10g, D133.1) ─────────────────
//
// An admin of a Native CRM can rename the four core record types: "Existing
// Sites" in place of "Contacts", for example. `PUT /crm/settings/labels`
// stores the names, and `GET /crm/mode` serves them as `labels`, in the shape
// of the source names of Mirror mode (`SourceLabels`). The rail, the list
// headings and the record pages read them through `viewLabel` and
// `sourceSingular`, so they need no change.
//
// Pure, so the test holds the rules without a browser. The card in
// `components/RecordNamesCard.tsx` only draws them.

import type { EntitySlug } from "./types";
import { CRM_VIEWS, ENTITY_NOUN, type SourceLabel, type SourceLabels } from "./views";

/** The longest name, after the trim. The gateway refuses a longer one (422). */
export const RECORD_NAME_MAX = 60;

export const RECORD_NAMES_TITLE = "Record names";
export const RECORD_NAMES_NOTE = "Rename the record types. Two empty boxes keep the default names.";
export const RECORD_NAMES_MIRROR_NOTE =
  "Zoho names these. You can rename them after you switch to Metorite.";
export const USE_DEFAULT_LABEL = "Use the default";
export const SAVE_NAMES_LABEL = "Save names";
export const LIST_NAME_PROBLEM = "Fill in the name of the list, or use the default.";
export const TOO_LONG_PROBLEM = `Use ${RECORD_NAME_MAX} characters or fewer.`;

/** One record type, with the CRM's own names. */
export type RecordType = { slug: EntitySlug; plural: string; singular: string };

/** The four record types, in the order of the rail. */
export const RECORD_TYPES: readonly RecordType[] = CRM_VIEWS.filter(
  (view): view is typeof view & { id: EntitySlug } => view.id in ENTITY_NOUN,
).map((view) => ({
  slug: view.id,
  plural: view.label,
  singular: ENTITY_NOUN[view.id],
}));

/** The two boxes of one record type, as the admin typed them. */
export type NameRow = { plural: string; singular: string };
export type NameRows = Record<EntitySlug, NameRow>;

/** The boxes of each type, from the stored names. A type with none is empty. */
export function rowsOf(labels: SourceLabels | null | undefined): NameRows {
  const rows = {} as NameRows;
  for (const type of RECORD_TYPES) {
    const stored = labels?.[type.slug];
    const plural = typeof stored?.label === "string" ? stored.label : "";
    const singular = typeof stored?.singular_label === "string" ? stored.singular_label : "";
    rows[type.slug] = { plural, singular };
  }
  return rows;
}

/** True when either box of the row holds text. */
export function rowIsSet(row: NameRow): boolean {
  return row.plural.trim() !== "" || row.singular.trim() !== "";
}

/**
 * What is wrong with one row, or null. Two empty boxes use the default.
 *
 * The name of the list alone is enough. The server stores it with no
 * singular name, and the record page then reads the plural (`sourceSingular`).
 * CRM-Z10b stores that shape when the Zoho profile has no singular name. The
 * name of one record alone is a problem, because the rail needs a list name.
 */
export function rowProblem(row: NameRow): string | null {
  if (!rowIsSet(row)) return null;
  if (row.plural.trim() === "") return LIST_NAME_PROBLEM;
  if (row.plural.trim().length > RECORD_NAME_MAX || row.singular.trim().length > RECORD_NAME_MAX) {
    return TOO_LONG_PROBLEM;
  }
  return null;
}

/** The problem of each type that has one. */
export function problemsOf(rows: NameRows): Partial<Record<EntitySlug, string>> {
  const out: Partial<Record<EntitySlug, string>> = {};
  for (const type of RECORD_TYPES) {
    const problem = rowProblem(rows[type.slug]);
    if (problem) out[type.slug] = problem;
  }
  return out;
}

/**
 * The `labels` of the PUT body: each set row, trimmed. A type that is left
 * out takes the default name again, so the body is the whole map. An empty
 * name of one record is `null`, because the server refuses an empty name.
 */
export function labelsBody(rows: NameRows): Partial<Record<EntitySlug, Required<SourceLabel>>> {
  const out: Partial<Record<EntitySlug, Required<SourceLabel>>> = {};
  for (const type of RECORD_TYPES) {
    const row = rows[type.slug];
    if (!rowIsSet(row)) continue;
    out[type.slug] = { label: row.plural.trim(), singular_label: row.singular.trim() || null };
  }
  return out;
}

/**
 * The placeholder of the box for one record. With a list name and no name of
 * one record, the record page shows the list name, so the box says that.
 */
export function singularPlaceholder(type: RecordType, row: NameRow): string {
  return row.plural.trim() || type.singular;
}

/** True when the boxes differ from the stored names. */
export function namesChanged(rows: NameRows, stored: SourceLabels | null | undefined): boolean {
  return JSON.stringify(labelsBody(rows)) !== JSON.stringify(labelsBody(rowsOf(stored)));
}
