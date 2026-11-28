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
import type { AccentHue } from "@/lib/statusAccent";
import {
  type FieldSpec, type QuantityConversion, type QuantityFlag, type QuantityRange,
  checkValue, minorDigits, quantityFlag, ratingRange,
} from "./fieldCheck";
import type { FieldType } from "./manifest";

/** What a control holds: text, a tick, a list of picks, or the parts of a compound field. */
export type Draft = string | boolean | string[] | { [part: string]: string };

export const FIELD_TYPE_LIST: readonly FieldType[] = [
  "text", "longText", "number", "money", "date", "datetime", "checkbox", "select",
  "multiSelect", "person", "relation", "file", "url", "email", "phone", "geo",
  "richText", "rating", "duration", "quantity", "percent", "barcode", "autonumber", "coded",
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
      ...extrasOf(r),
    });
  }
  return out;
}

/** STU-C24a. The word of each flag. `normal` and `none` have none. Never the word "Safe". */
export const FLAG_WORDS: Record<QuantityFlag, string> = {
  none: "", normal: "", low: "Low", high: "High",
  critical_low: "Critical low", critical_high: "Critical high", unchecked: "Not checked",
};

/**
 * STU-C24a. One quantity value as words: `text` is the value and its unit, `flag` is the
 * computed flag, and `word` is its word, empty for `normal` and `none`. The one place that
 * joins them: the Table cell and the Detail sheet both read it.
 */
export function quantityReading(
  f: FieldSpec, v: unknown,
): { text: string; flag: QuantityFlag; word: string } | null {
  if (!isObject(v) || typeof v.value !== "string" || typeof v.unit !== "string") return null;
  const flag = quantityFlag(f, v);
  return { text: `${v.value} ${v.unit}`, flag, word: FLAG_WORDS[flag] };
}

/** STU-C24a. The hue of a flag in `statusAccent.ts`, the one status vocabulary. Colour never stands alone: the word is always drawn. */
export const FLAG_HUE: Partial<Record<QuantityFlag, AccentHue>> = {
  low: "amber", high: "amber", critical_low: "red", critical_high: "red", unchecked: "gray",
};

const rangeOf = (x: unknown): QuantityRange | undefined =>
  isObject(x)
    ? {
        low: typeof x.low === "string" ? x.low : null,
        high: typeof x.high === "string" ? x.high : null,
      }
    : undefined;

/** The C14 parts of a field: the bounds of a rating, the units of a quantity, the format of a barcode. */
export function extrasOf(
  r: Record<string, unknown>,
): Pick<FieldSpec, "min" | "max" | "units" | "format" | "normal" | "critical" | "conversions" | "prefix" | "pad" | "reset" | "system"> {
  return {
    system: typeof r.system === "string" ? r.system : undefined,
    prefix: typeof r.prefix === "string" ? r.prefix : undefined,
    pad: typeof r.pad === "number" ? r.pad : undefined,
    reset: typeof r.reset === "string" ? r.reset : undefined,
    normal: rangeOf(r.normal),
    critical: rangeOf(r.critical),
    conversions: Array.isArray(r.conversions)
      ? r.conversions.filter((c): c is QuantityConversion =>
          isObject(c) && typeof c.from === "string" && typeof c.to === "string" && typeof c.factor === "string")
      : undefined,
    min: typeof r.min === "number" ? r.min : undefined,
    max: typeof r.max === "number" ? r.max : undefined,
    units: Array.isArray(r.units) ? r.units.filter((u): u is string => typeof u === "string") : undefined,
    format: typeof r.format === "string" ? r.format : undefined,
  };
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

// --- STU-C14: the words and the drafts of the six more types ----------------

/** Whole seconds as words: 5400 reads "1h 30m", and 0 reads "0s". */
export function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const parts = [h ? `${h}h` : "", m ? `${m}m` : "", s ? `${s}s` : ""].filter(Boolean);
  return parts.length > 0 ? parts.join(" ") : "0s";
}

/**
 * Whole seconds from what a member types: "1h 30m", "45m", "90s", "1:30" (hours and
 * minutes), "1:30:15", or bare digits as seconds. Text that fits none stays text, so
 * the check refuses it.
 */
export function parseDuration(text: string): number | string {
  const t = text.trim();
  if (/^[0-9]+$/.test(t)) return Number(t);
  const clock = /^([0-9]+):([0-5][0-9])(?::([0-5][0-9]))?$/.exec(t);
  if (clock) return Number(clock[1]) * 3600 + Number(clock[2]) * 60 + Number(clock[3] ?? 0);
  const words = /^(?:([0-9]+)\s*h)?\s*(?:([0-9]+)\s*m)?\s*(?:([0-9]+)\s*s)?$/i.exec(t);
  if (words && (words[1] || words[2] || words[3])) {
    return Number(words[1] ?? 0) * 3600 + Number(words[2] ?? 0) * 60 + Number(words[3] ?? 0);
  }
  return t;
}

/** Basis points as percent text: 2550 reads "25.5", and -5 reads "-0.05". */
export function formatPercent(bp: number): string {
  const abs = Math.abs(bp);
  const whole = Math.floor(abs / 100);
  const frac = String(abs % 100).padStart(2, "0").replace(/0+$/, "");
  return `${bp < 0 ? "-" : ""}${whole}${frac ? "." + frac : ""}`;
}

/** Basis points from percent text with up to 2 decimals. Other text stays text. */
export function parsePercent(text: string): number | string {
  const t = text.trim().replace(/%$/, "").trim();
  const m = /^([+-]?)([0-9]+)(?:\.([0-9]{1,2}))?$/.exec(t);
  if (!m) return t;
  const bp = Number(m[2]) * 100 + Number((m[3] ?? "").padEnd(2, "0"));
  return m[1] === "-" ? -bp : bp;
}

/** Markdown as one line of plain words, for a Table cell. */
export function plainMarkdown(md: string): string {
  return md
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[#*_`>~|]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

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
    case "richText":
      return typeof v === "string" ? plainMarkdown(v) : "";
    case "rating":
      return typeof v === "number" ? `${v} / ${ratingRange(f)[1]}` : "";
    case "duration":
      return typeof v === "number" ? formatDuration(v) : "";
    case "quantity": {
      const q = quantityReading(f, v);
      return q ? (q.word ? `${q.text} (${q.word})` : q.text) : "";
    }
    case "percent":
      return typeof v === "number" ? `${formatPercent(v)}%` : "";
    case "coded":
      return isObject(v) && typeof v.code === "string" ? v.code : "";
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
  /**
   * `editable` turns on the inline edit of a cell (STU-D2b). `computed` names
   * the fields that a formula or a roll-up fills, and these never edit.
   */
  opts?: { editable?: boolean; computed?: readonly string[]; openBase?: string },
): { type: "template"; props: Record<string, unknown> } {
  return {
    type: "template",
    props: {
      name: "dataGrid",
      source,
      data: {
        title, fields, filterable: true,
        // The record link of a row (STU-AF5): the grid appends the id of the record.
        ...(opts?.openBase ? { openBase: opts.openBase } : {}),
        ...(opts?.editable ? { editable: true, computed: [...(opts.computed ?? [])] } : {}),
      },
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
    case "quantity": return { value: "", unit: f.units?.[0] ?? "" };
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
  if (f.type === "duration" && typeof v === "number") return formatDuration(v);
  if (f.type === "percent" && typeof v === "number") return formatPercent(v);
  if (f.type === "coded") return isObject(v) && typeof v.code === "string" ? v.code : "";
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
    case "quantity":
      return isObject(v) ? { value: String(v.value ?? ""), unit: String(v.unit ?? "") } : emptyDraft(f);
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
    case "quantity": {
      const p = isObject(d) ? (d as Record<string, string>) : {};
      const value = (p.value ?? "").trim();
      return value === "" ? undefined : { value, unit: p.unit ?? "" };
    }
    case "rating":
      return typeof d === "string" && d.trim() !== "" ? toNumber(d) : undefined;
    case "duration":
      return typeof d === "string" && d.trim() !== "" ? parseDuration(d) : undefined;
    case "percent":
      return typeof d === "string" && d.trim() !== "" ? parsePercent(d) : undefined;
    case "barcode":
      return typeof d === "string" && d.trim() !== "" ? d.trim() : undefined;
    case "autonumber":
      return undefined; // the store fills it: no form sends it
    case "coded":
      // The form holds the code only. The system is the one of the field.
      return typeof d === "string" && d.trim() !== "" ? { system: f.system, code: d.trim() } : undefined;
    default:
      return typeof d === "string" ? scalarValue(f, d) : undefined;
  }
}

/** One stored value checked as a link target: only an http or https address links. */
export function safeHref(v: unknown): string | null {
  const r = checkValue({ key: "u", type: "url" }, v);
  return r.ok && typeof r.value === "string" ? r.value : null;
}
