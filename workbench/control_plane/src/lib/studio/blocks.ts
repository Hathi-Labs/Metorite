/**
 * The pure half of the Table, Form and Detail blocks (`ai_studio.md` §5.1,
 * slice STU-D2a).
 *
 * A form control holds a *draft*, the text or the choice a member sees. This
 * file turns a draft into the value that `fieldCheck.ts` checks, and a stored
 * value into a draft or into the words of a cell. No React import and no
 * `createContext`: a server route reaches `lib/studio` (see `fieldCheck.ts`).
 * Fence: `blocks.test.ts`.
 */
import { formatCardDate } from "@/lib/cardFields";
import { type FieldSpec, checkValue, minorDigits } from "./fieldCheck";
import type { FieldType } from "./manifest";

/** What a control holds: text, a tick, a list of picks, or the parts of a compound field. */
export type Draft = string | boolean | string[] | { [part: string]: string };

export const FIELD_TYPE_LIST: readonly FieldType[] = [
  "text", "longText", "number", "money", "date", "datetime", "checkbox", "select",
  "multiSelect", "person", "relation", "file", "url", "email", "phone", "geo",
];

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** "due_date" reads "Due date" when a field has no label. */
export function labelOf(f: FieldSpec): string {
  const own = f.label?.trim();
  if (own) return own;
  const words = f.key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Read `fields` from a node's props. A model writes it, so each part is checked. */
export function parseFields(raw: unknown): FieldSpec[] {
  if (!Array.isArray(raw)) return [];
  const out: FieldSpec[] = [];
  for (const r of raw) {
    if (!isObject(r) || typeof r.key !== "string" || !FIELD_TYPE_LIST.includes(r.type as FieldType)) continue;
    out.push({
      key: r.key,
      type: r.type as FieldType,
      label: typeof r.label === "string" ? r.label : undefined,
      required: r.required === true,
      many: r.many === true,
      options: Array.isArray(r.options) ? r.options.filter((o): o is string => typeof o === "string") : undefined,
      to: typeof r.to === "string" ? r.to : undefined,
    });
  }
  return out;
}

// --- the Table ---------------------------------------------------------------

/**
 * The cell kind of `dataGridLayout.ts` that draws a field type. A date has no
 * kind of its own: `displayValue` words it, once, for the Table and the Detail.
 */
export function cellKindFor(type: FieldType): "text" | "tag" | "person" {
  switch (type) {
    case "select":
    case "multiSelect":
      return "tag";
    case "person":
      return "person";
    default:
      return "text";
  }
}

const LOCALE = "en-IN";

export { minorDigits };

const pad = (n: number): string => String(n).padStart(2, "0");

function scalarText(f: FieldSpec, v: unknown): string {
  if (v === null || v === undefined) return "";
  switch (f.type) {
    case "checkbox":
      return v === true ? "Yes" : "No";
    case "date":
      return typeof v === "string" ? formatCardDate(v) : "";
    case "datetime": {
      // The viewer's local time, with the time, as the Table words a day.
      if (typeof v !== "string") return "";
      const d = new Date(v);
      if (Number.isNaN(d.getTime())) return v;
      return formatCardDate(
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`,
      );
    }
    case "money": {
      if (!isObject(v) || typeof v.minor !== "number" || typeof v.currency !== "string") return "";
      const d = minorDigits(v.currency);
      try {
        return new Intl.NumberFormat(LOCALE, { style: "currency", currency: v.currency }).format(v.minor / 10 ** d);
      } catch {
        return `${v.currency} ${(v.minor / 10 ** d).toFixed(d)}`;
      }
    }
    case "geo":
      return isObject(v) ? `${v.lat}, ${v.lng}` : "";
    case "relation":
    case "file":
      return isObject(v) && typeof v.id === "string" ? v.id : "";
    case "number":
      return typeof v === "number" ? new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 6 }).format(v) : "";
    default:
      return typeof v === "string" ? v : "";
  }
}

/**
 * The words of one stored value. The ONE formatter: a Table cell and the
 * Detail sheet both call it, and `blocks.test.ts` holds them to the same text.
 */
export function displayValue(f: FieldSpec, v: unknown): string {
  if (Array.isArray(v)) return v.map((x) => scalarText(f, x)).filter(Boolean).join(", ");
  return scalarText(f, v);
}

/**
 * Door rows as `dataGrid` data, for a Table with `data.fields`. A column is
 * a field. Its header is the label, its kind comes from the type, and its
 * cell is the words of the value under the key.
 */
export function fieldGrid(
  fields: readonly FieldSpec[],
  rows: readonly Record<string, unknown>[],
): { columns: string[]; kinds: string[]; rows: { id?: unknown; cells: string[] }[] } {
  return {
    columns: fields.map(labelOf),
    kinds: fields.map((f) => cellKindFor(f.type)),
    rows: rows
      .filter((r) => r && typeof r === "object")
      .map((r) => ({
        ...(r.id !== undefined ? { id: r.id } : {}),
        cells: fields.map((f) => displayValue(f, r[f.key])),
      })),
  };
}

/** The node that draws a Table: the `dataGrid` template on the D1 bind path. */
export function tableBlock(
  fields: readonly FieldSpec[],
  source: { action: string; args?: Record<string, unknown> },
  title?: string,
): { type: "template"; props: Record<string, unknown> } {
  return {
    type: "template",
    props: {
      name: "dataGrid",
      source,
      data: { title, fields, filterable: true },
    },
  };
}

// --- the Form ----------------------------------------------------------------

/** The types that a `many` field takes as one value for each line. */
const LINE_TYPES: ReadonlySet<FieldType> = new Set([
  "text", "longText", "number", "date", "datetime", "select", "person", "relation",
  "file", "url", "email", "phone",
]);

/** A `many` money or geo field has no control in this slice. */
export const manyUnsupported = (f: FieldSpec): boolean => f.many === true && !LINE_TYPES.has(f.type);

export const isLines = (f: FieldSpec): boolean => f.many === true && LINE_TYPES.has(f.type);

export function emptyDraft(f: FieldSpec): Draft {
  if (isLines(f)) return "";
  switch (f.type) {
    case "checkbox": return false;
    case "multiSelect": return [];
    case "money": return { amount: "", currency: "INR" };
    case "geo": return { lat: "", lng: "" };
    default: return "";
  }
}

function scalarDraft(f: FieldSpec, v: unknown): string {
  if (v === null || v === undefined) return "";
  if (f.type === "datetime" && typeof v === "string") {
    const d = new Date(v);
    return Number.isNaN(d.getTime())
      ? v
      : `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  if (f.type === "relation" || f.type === "file") return isObject(v) && typeof v.id === "string" ? v.id : "";
  return String(v);
}

/** The draft of a stored value, for an edit form. */
export function valueToDraft(f: FieldSpec, v: unknown): Draft {
  if (v === null || v === undefined) return emptyDraft(f);
  if (isLines(f)) return Array.isArray(v) ? v.map((x) => scalarDraft(f, x)).join("\n") : "";
  switch (f.type) {
    case "checkbox": return v === true;
    case "multiSelect": return Array.isArray(v) ? v.map(String) : [];
    case "money": {
      if (!isObject(v) || typeof v.minor !== "number" || typeof v.currency !== "string") return emptyDraft(f);
      const d = minorDigits(v.currency);
      return { amount: (v.minor / 10 ** d).toFixed(d), currency: v.currency };
    }
    case "geo":
      return isObject(v) ? { lat: String(v.lat ?? ""), lng: String(v.lng ?? "") } : emptyDraft(f);
    default:
      return scalarDraft(f, v);
  }
}

const NUMBER_TEXT = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

/** A number from text. Text that is no number stays text, so the check refuses it. */
const toNumber = (s: string): number | string => {
  const t = s.trim();
  return NUMBER_TEXT.test(t) ? Number(t) : t;
};

function scalarValue(f: FieldSpec, s: string): unknown {
  switch (f.type) {
    case "number":
      return s.trim() === "" ? undefined : toNumber(s);
    case "datetime": {
      if (s.trim() === "") return undefined;
      const d = new Date(s);
      return Number.isNaN(d.getTime()) ? s : d.toISOString();
    }
    case "relation":
    case "file":
      return s.trim() === "" ? undefined : { id: s.trim() };
    case "person":
    case "email":
    case "url":
    case "phone":
    case "date":
    case "select":
      return s.trim() === "" ? undefined : s.trim();
    default:
      return s === "" ? undefined : s;
  }
}

/** The value that a draft stands for, or `undefined` when the control is blank. */
export function draftToValue(f: FieldSpec, d: Draft): unknown {
  if (isLines(f)) {
    const lines = String(d).split("\n").map((l) => l.trim()).filter(Boolean);
    return lines.length === 0 ? undefined : lines.map((l) => scalarValue(f, l));
  }
  switch (f.type) {
    case "checkbox":
      return d === true;
    case "multiSelect":
      return Array.isArray(d) && d.length > 0 ? d : undefined;
    case "money": {
      const p = isObject(d) ? (d as Record<string, string>) : {};
      const amount = (p.amount ?? "").trim();
      if (amount === "") return undefined;
      const currency = (p.currency ?? "").trim().toUpperCase();
      const n = toNumber(amount);
      const minor = typeof n === "number" ? n * 10 ** minorDigits(currency || "INR") : NaN;
      // An amount with more digits than the currency has is no whole minor unit.
      return { minor: Math.abs(minor - Math.round(minor)) < 1e-6 ? Math.round(minor) : NaN, currency };
    }
    case "geo": {
      const p = isObject(d) ? (d as Record<string, string>) : {};
      const lat = (p.lat ?? "").trim();
      const lng = (p.lng ?? "").trim();
      return lat === "" && lng === "" ? undefined : { lat: toNumber(lat), lng: toNumber(lng) };
    }
    default:
      return typeof d === "string" ? scalarValue(f, d) : undefined;
  }
}

/** One stored value checked as a link target: only an http or https address links. */
export function safeHref(v: unknown): string | null {
  const r = checkValue({ key: "u", type: "url" }, v);
  return r.ok && typeof r.value === "string" ? r.value : null;
}
