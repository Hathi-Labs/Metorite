/**
 * Sample rows for the Preview of a DRAFT (`ai_studio.md` §6.3, slice G3a).
 *
 * Object rows exist only after a publish, so the Studio IDE draws a draft on sample rows.
 * `sampleRowsFor` is pure and fixed: the same fields always give the same rows, so the
 * preview does not flicker at each refresh. `makeDraftDoor` reads those rows and refuses
 * every write. No call reaches app data. The pattern is `sampleDoor.ts`, which holds one
 * fixed object for the block preview.
 */
import type { Row, StudioDataDoor } from "./dataDoor";
import type { FieldSpec } from "./fieldCheck";

export const SAMPLE_COUNT = 5;
export const SAMPLE_LABEL = "Sample data. Nothing is saved.";
export const SAMPLE_REFUSAL = "This is a sample. Nothing is saved.";

const PEOPLE = ["asha@example.com", "ravi@example.com", "meera@example.com"];

function one(f: FieldSpec, i: number): unknown {
  const n = i + 1;
  const day = String(Math.min(28, n * 2)).padStart(2, "0");
  switch (f.type) {
    case "text": return `${f.label || f.key} ${n}`;
    case "longText": return `Sample notes ${n}.\nA second line.`;
    case "number": return n * 3;
    case "money": return { minor: n * 125000, currency: "INR" };
    case "date": return `2026-11-${day}`;
    case "datetime": return `2026-11-${day}T09:30:00Z`;
    case "checkbox": return i % 2 === 0;
    case "select": return f.options?.length ? f.options[i % f.options.length] : `Option ${String.fromCharCode(65 + (i % 3))}`;
    case "multiSelect": return f.options?.length ? [f.options[i % f.options.length]] : [];
    case "person": return PEOPLE[i % PEOPLE.length];
    case "relation": return { id: `sample-${f.to || "record"}-${n}` };
    case "file": return { id: `00000000-0000-4000-8000-00000000000${n % 10}` };
    case "url": return `https://example.com/${f.key}/${n}`;
    case "email": return `contact${n}@example.com`;
    case "phone": return `+9198765432${String(10 + n).padStart(2, "0")}`;
    case "geo": return { lat: 18.5204 + i / 100, lng: 73.8567 + i / 100 };
    case "richText": return `**Sample report ${n}**\n\n- First point\n- Second point`;
    case "rating": return Math.min(f.max ?? 5, Math.max(f.min ?? 1, (f.min ?? 1) + (i % 5)));
    case "duration": return 900 * n;
    case "quantity": return { value: String(n * 5), unit: f.units?.[0] ?? "kg" };
    case "percent": return n * 250;
    case "barcode": return f.format === "ean13" ? "4006381333931" : f.format === "code128" ? `SAMPLE-${n}` : `https://example.com/${n}`;
    default: return null;
  }
}

/** `count` rows for `fields`. A field of many values holds a list. Every row has an id `s1`, `s2`, and so on. */
export function sampleRowsFor(fields: readonly FieldSpec[], count: number = SAMPLE_COUNT): Row[] {
  const rows: Row[] = [];
  for (let i = 0; i < Math.max(0, count); i++) {
    const row: Row = { id: `s${i + 1}` };
    for (const f of fields) {
      const v = one(f, i);
      row[f.key] = f.many && f.type !== "multiSelect" ? [v] : v;
    }
    rows.push(row);
  }
  return rows;
}

/** The object name in `object:<name>.list`, or null. */
export function listedObject(action: string): string | null {
  const m = /^object:([^.]+)\.list$/.exec(action);
  return m ? m[1] : null;
}

/** A door over the sample rows of each object. `read` lists. `run` refuses with a sentence. */
export function makeDraftDoor(
  scope: string, objects: Readonly<Record<string, readonly FieldSpec[]>>,
): StudioDataDoor {
  return {
    scope: `sample:${scope}`,
    async read(source) {
      const name = listedObject(source.action);
      const fields = name ? objects[name] : undefined;
      return fields ? sampleRowsFor(fields) : [];
    },
    async run() {
      return { ok: false, message: SAMPLE_REFUSAL };
    },
  };
}
