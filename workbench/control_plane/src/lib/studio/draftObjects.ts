/**
 * The objects of a DRAFT manifest, as the blocks read them (`ai_studio.md` §6.3, G3a).
 *
 * `packagePreview.fieldSpecs` reads the object rows of the gateway, which exist only after a
 * publish. The IDE draws the draft, so this reads `manifest.objects`. Pure, and it never throws:
 * a bad entry is dropped.
 */
import type { FieldSpec } from "./fieldCheck";
import { fieldSpecs } from "./packagePreview";

export interface DraftObject {
  name: string;
  label: string | null;
  fields: FieldSpec[];
  rules: Array<Record<string, unknown>>;
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);

export function draftObjects(manifest: unknown): DraftObject[] {
  if (!isObject(manifest) || !Array.isArray(manifest.objects)) return [];
  const out: DraftObject[] = [];
  for (const o of manifest.objects) {
    if (!isObject(o) || typeof o.name !== "string" || !o.name.trim()) continue;
    const fields = fieldSpecs({ fields: Array.isArray(o.fields) ? o.fields : [] } as never);
    out.push({
      name: o.name.trim(),
      label: typeof o.label === "string" && o.label.trim() ? o.label.trim() : null,
      fields,
      rules: Array.isArray(o.rules) ? o.rules.filter(isObject) : [],
    });
  }
  return out;
}
