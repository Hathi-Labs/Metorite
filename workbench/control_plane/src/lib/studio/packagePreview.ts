/**
 * The pure half of `/studio/package-preview` (`ai_studio.md` §14 D2b-1, slice STU-D2b).
 *
 * The route reads a package and its object rows from the gateway, and turns an
 * object into the fields that the Table, the Form and the Detail take. It maps
 * no view to a node and reads no page of the manifest (PKG-8 owns that).
 */
import type { FieldSpec } from "./fieldCheck";
import { FIELD_TYPE_LIST } from "./blocks";
import type { FieldType } from "./manifest";
import type { ObjectRow } from "./realDoor";

/** The most columns that the Table draws. The Form and the Detail show every field. */
export const MAX_TABLE_COLUMNS = 8;

/** The fields of an object row of the gateway, as the blocks read them. */
export function fieldSpecs(object: Pick<ObjectRow, "fields">): FieldSpec[] {
  const out: FieldSpec[] = [];
  for (const f of object.fields ?? []) {
    if (!f || typeof f.key !== "string" || !FIELD_TYPE_LIST.includes(f.type as FieldType)) continue;
    out.push({
      key: f.key,
      type: f.type as FieldType,
      label: f.label ?? undefined,
      required: f.required === true,
      many: f.many === true,
      options: Array.isArray(f.options) ? f.options : undefined,
      to: f.to ?? undefined,
    });
  }
  return out;
}

/**
 * The columns of the Table: the first fields, with a field of many values last.
 * A long text stays out, as in the sample Table. It wraps into a tall, narrow
 * cell, and the Detail sheet shows it whole.
 */
export function tableFields(fields: readonly FieldSpec[]): FieldSpec[] {
  const shown = fields.filter((f) => f.type !== "longText");
  const single = shown.filter((f) => !f.many);
  const many = shown.filter((f) => f.many);
  return [...single, ...many].slice(0, MAX_TABLE_COLUMNS);
}

/**
 * The fields of a Form. A field that a `formula` or `rollup` rule fills is left out:
 * the store refuses a write to it (422 `computed_field`), even `null`. The Detail
 * sheet and the Table still show it.
 */
export function formFields(fields: readonly FieldSpec[], computed: ReadonlySet<string>): FieldSpec[] {
  return fields.filter((f) => !computed.has(f.key));
}

/** The sentence for a query that names no object, or null. The page then shows the first object. */
export function missingObjectNote(
  objects: readonly Pick<ObjectRow, "name" | "label">[], wanted: string | null,
): string | null {
  if (!wanted || objects.length === 0 || objects.some((o) => o.name === wanted)) return null;
  return `No object named ${wanted}. Showing ${objects[0].label ?? objects[0].name}.`;
}

/** The field that names a record: the first text field, else the first field. */
export function titleKeyOf(fields: readonly FieldSpec[]): string | null {
  return (fields.find((f) => f.type === "text" && !f.many) ?? fields[0])?.key ?? null;
}

/** The words that name a record in a list or a dialog. */
export function recordTitle(fields: readonly FieldSpec[], row: Record<string, unknown>): string {
  const key = titleKeyOf(fields);
  const v = key ? row[key] : undefined;
  return typeof v === "string" && v.trim() ? v.trim() : String(row.id ?? "");
}

/** The picks for the package, the object and the record, from the query. */
export function pickObject(objects: readonly ObjectRow[], wanted: string | null): ObjectRow | undefined {
  return objects.find((o) => o.name === wanted) ?? objects[0];
}

export function previewHref(pkg: string, object?: string): string {
  const q = new URLSearchParams({ pkg });
  if (object) q.set("object", object);
  return `/studio/package-preview?${q.toString()}`;
}
