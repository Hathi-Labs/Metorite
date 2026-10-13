// ── The field studio, the shared client (WS-53 CRM-C4p) ─────────────────────
//
// Spec: project-docs/specs/crm_platform.md section 20.7, "CRM-C4p: the shared
// client". C4b (the record UI) and C4c (the studio UI) both read this file.
//
// Pure, so the vitest setup (no DOM) can test every rule. `FieldInput.tsx`
// draws what this file decides.
//
// ⚠️ The gateway is the authority. `checkValue` mirrors the checks of
// `routes/crm/field_studio.py`, so the gateway seldom gets a bad value, but a
// value that passes here can still get 422 there. The limits, the type list
// and the patterns below are numeric and regex literals on purpose:
// `tests/unit/test_crm_field_input_parity.py` reads them out of this file and
// compares them with the Python. A change on one side only turns it red.

import { fieldOptions, isEmail, safeHref, type FieldDef } from "./fields";
import type { EntitySlug } from "./types";

type Row = Record<string, unknown>;

// ── The layout (C4a item 12) ────────────────────────────────────────────────

/** One section of a layout. `FieldSection` in `fields.py`. */
export type FieldSection = { id: string; label: string; position: number };

/** `GET /crm/fields/{entity}?layout=1`. `FieldLayout` in `fields.py`. */
export type FieldLayout = {
  entity: string;
  sections: FieldSection[];
  /** The system fields and the custom fields, in order. */
  fields: FieldDef[];
  /** `CRM_FIELD_STUDIO_ORGS` allows the organization. */
  studio: boolean;
  /** The flag allows the org, the caller is an admin, and the mode is Native. */
  studio_admin: boolean;
};

/**
 * A layout from the body of the read. A body that is not one throws, so the
 * caller falls back to the view of today. A gateway from before C4a sends no
 * booleans, and each one then reads as false.
 */
export function asFieldLayout(raw: unknown): FieldLayout {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("The field layout is not an object.");
  }
  const body = raw as Row;
  if (!Array.isArray(body.sections) || !Array.isArray(body.fields)) {
    throw new Error("The field layout has no sections or no fields.");
  }
  return {
    entity: typeof body.entity === "string" ? body.entity : "",
    sections: body.sections as FieldSection[],
    fields: body.fields as FieldDef[],
    studio: body.studio === true,
    studio_admin: body.studio_admin === true,
  };
}

// ── The mirror of field_studio.py ───────────────────────────────────────────

/** The type words of the studio (section 20.7 item 1). `FIELD_TYPES`. */
export const FIELD_TYPES = [
  "text",
  "textarea",
  "number",
  "decimal",
  "percent",
  "currency",
  "date",
  "datetime",
  "boolean",
  "email",
  "phone",
  "url",
  "select",
  "multiselect",
  "lookup",
] as const;

export type FieldType = (typeof FIELD_TYPES)[number];

/** The targets of a lookup in Native mode. `NATIVE_LOOKUP_TARGETS`. */
export const LOOKUP_TARGETS = ["leads", "contacts", "organizations", "deals"] as const;

// The limits. Each one is a numeric literal, for the parity fence.
export const TEXT_MAX = 255;
export const TEXTAREA_MAX = 32_000;
/** A number of this size or more loses its last digits in a float. */
export const NUMBER_LIMIT = 1e15;
export const EMAIL_MAX = 254;
export const PHONE_MAX = 40;
export const URL_MAX = 2048;

// The patterns. Each one is the text of its Python twin. `isEmail` and
// `safeHref` in `fields.ts` hold the email and URL patterns.
/** `_DATE_RE`. */
export const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
/** `_DATETIME_RE`. */
export const DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:\d{2})$/;
/** `_PHONE_RE`, with its extension group. */
export const PHONE_RE = /^\+?[\d\s().\-]+(\s*(x|ext\.?|#)\s*\d+)?$/i;

/** The fewest digits a phone value holds (`_phone`). */
export const PHONE_MIN_DIGITS = 4;

/**
 * The length of a text as Python counts it: in code points. JS `.length`
 * counts UTF-16 units, so one emoji counts 2 there and 1 in Python.
 */
export function textLength(text: string): number {
  return [...text].length;
}

/** `is_empty` of `field_studio.py`: null, a blank string, an empty list or object. */
export function isEmptyValue(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as Row).length === 0;
  return false;
}

function isLeap(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

/** A real day of the years 1 to 9999, which Python's `date` holds. */
function realDay(year: number, month: number, day: number): boolean {
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1) return false;
  const days = [31, isLeap(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}

/** `_date`: `YYYY-MM-DD`, and a real day. */
export function isDateValue(value: string): boolean {
  if (!DATE_RE.test(value)) return false;
  const [y, m, d] = value.split("-").map(Number);
  return realDay(y, m, d);
}

const DATETIME_PARTS =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-](\d{2}):(\d{2}))$/;

/**
 * `_datetime`: the plain ISO form with `Z` or an offset, and a real date and
 * time. ⚠️ `toISOString()` gives `+010000-…` after the year 9999 and
 * `0000-…` for the year 0. Python refuses both, so the year stays in 1..9999.
 */
export function isDateTimeValue(value: string): boolean {
  if (!DATETIME_RE.test(value)) return false;
  const m = DATETIME_PARTS.exec(value);
  if (!m) return false;
  const [y, mo, d, h, mi] = m.slice(1, 6).map(Number);
  const s = m[6] === undefined ? 0 : Number(m[6]);
  const offH = m[7] === undefined ? 0 : Number(m[7]);
  const offM = m[8] === undefined ? 0 : Number(m[8]);
  return realDay(y, mo, d) && h <= 23 && mi <= 59 && s <= 59 && offH <= 23 && offM <= 59;
}

/** `_phone`: at most 40 characters, 4 digits or more, and `_PHONE_RE`. */
export function isPhoneValue(value: string): boolean {
  const digits = (value.match(/\d/g) ?? []).length;
  return (
    textLength(value) <= PHONE_MAX && digits >= PHONE_MIN_DIGITS && PHONE_RE.test(value.trim())
  );
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** True when `target` is an entity that a Native lookup may name. */
export function isLookupTarget(target: unknown): target is EntitySlug {
  return typeof target === "string" && (LOOKUP_TARGETS as readonly string[]).includes(target);
}

/** The values a member may choose: each active option, and what the record holds. */
function allowedValues(def: FieldDef, held: unknown): Set<string> {
  const allowed = new Set(fieldOptions(def).filter((o) => !o.retired).map((o) => o.value));
  for (const v of Array.isArray(held) ? held : [held]) {
    if (typeof v === "string") allowed.add(v);
  }
  return allowed;
}

/**
 * Why the gateway would refuse `value` for `def`, or null when it passes
 * (CRM-C4p item 7). `value` is the JSON value that the save sends, so run
 * `toWire` first. `held` is what the record holds now: a retired option that
 * it holds stays a choice, as `_select` allows.
 *
 * An empty value passes unless the field is required. The save then sends
 * `null`, which removes the key.
 */
export function checkValue(def: FieldDef, value: unknown, held?: unknown): string | null {
  if (isEmptyValue(value)) return def.required ? "Give a value. This field is required." : null;
  switch (def.type) {
    case "text":
    case "textarea": {
      const limit = def.type === "text" ? TEXT_MAX : TEXTAREA_MAX;
      if (typeof value !== "string") return "Give text.";
      return textLength(value) > limit ? `Use at most ${limit} characters.` : null;
    }
    case "number":
      if (!isNumber(value) || !Number.isInteger(value)) return "Give a whole number.";
      return Math.abs(value) >= NUMBER_LIMIT ? "Give a number smaller than 10^15." : null;
    case "decimal":
    case "currency":
    case "percent":
      if (!isNumber(value)) return "Give a number.";
      return Math.abs(value) >= NUMBER_LIMIT ? "Give a number smaller than 10^15." : null;
    case "date":
      return typeof value === "string" && isDateValue(value) ? null : "Give a date as YYYY-MM-DD.";
    case "datetime":
      return typeof value === "string" && isDateTimeValue(value)
        ? null
        : "Give a date and a time.";
    case "boolean":
      return typeof value === "boolean" ? null : "Give true or false.";
    case "email":
      if (typeof value !== "string") return "Give an email address.";
      if (textLength(value) > EMAIL_MAX) return `Use at most ${EMAIL_MAX} characters.`;
      return isEmail(value.trim()) ? null : "Give an email address.";
    case "phone":
      if (typeof value !== "string") return "Give a phone number.";
      if (textLength(value) > PHONE_MAX) return `Use at most ${PHONE_MAX} characters.`;
      return isPhoneValue(value) ? null : "Give a phone number.";
    case "url":
      if (typeof value !== "string") return "Give an http or https address.";
      if (textLength(value) > URL_MAX) return `Use at most ${URL_MAX} characters.`;
      return safeHref(value) ? null : "Give an http or https address.";
    case "select":
      return typeof value === "string" && allowedValues(def, held).has(value)
        ? null
        : "Choose one of its options.";
    case "multiselect": {
      if (!Array.isArray(value) || !value.every((v) => typeof v === "string")) {
        return "Choose from its options.";
      }
      const allowed = allowedValues(def, held);
      if (!value.every((v) => allowed.has(v))) return "Choose from its options.";
      return new Set(value).size === value.length ? null : "Choose each option once.";
    }
    case "lookup": {
      if (!isLookupTarget(def.lookup_target)) return "This lookup has no target.";
      const id = value && typeof value === "object" ? (value as Row).id : undefined;
      return typeof id === "string" && UUID_RE.test(id) ? null : "Choose a record from the search.";
    }
    default:
      return `The type '${def.type}' takes no value here.`;
  }
}

// ── The text of an input, and the value that the save sends ────────────────

/** A number as a member types it: an optional sign, digits, a point, an exponent. */
const NUMBER_TEXT = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

function pad(n: number, width = 2): string {
  return String(n).padStart(width, "0");
}

/**
 * The JSON value that a save sends for the text of an input (CRM-C4p,
 * "A number is a JSON number"). A blank text sends `null`, which clears the
 * value. A number type sends a JSON number, never text, because `_is_number`
 * refuses a string. A `datetime` sends `toISOString()`, which has the `Z` that
 * the gateway needs. A text that is not a number or a time stays text, so
 * `checkValue` refuses it.
 */
export function toWire(def: FieldDef, text: string): unknown {
  if (text.trim() === "") return null;
  switch (def.type) {
    case "number":
    case "decimal":
    case "currency":
    case "percent": {
      // `en-IN` groups (`1,20,000`) are how a member reads a number here.
      const plain = text.trim().replace(/,/g, "");
      if (!NUMBER_TEXT.test(plain)) return text;
      const n = Number(plain);
      return Number.isFinite(n) ? n : text;
    }
    case "datetime": {
      // A `datetime-local` value has no zone, so the Date reads it as local.
      const at = new Date(text.trim());
      if (Number.isNaN(at.getTime())) return text;
      try {
        return at.toISOString();
      } catch {
        return text;
      }
    }
    case "date":
      return text.trim();
    default:
      return text;
  }
}

/** The text of an input for a stored value. `toWire` is its inverse. */
export function fromWire(def: FieldDef, value: unknown): string {
  if (value === null || value === undefined) return "";
  if (def.type === "datetime" && typeof value === "string") {
    const at = new Date(value);
    if (Number.isNaN(at.getTime())) return value;
    // The `datetime-local` input takes the local time, with no zone.
    const base =
      `${pad(at.getFullYear(), 4)}-${pad(at.getMonth() + 1)}-${pad(at.getDate())}` +
      `T${pad(at.getHours())}:${pad(at.getMinutes())}`;
    return at.getSeconds() ? `${base}:${pad(at.getSeconds())}` : base;
  }
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  return "";
}

// ── The lookup search ───────────────────────────────────────────────────────

/**
 * The name of a search hit, as the gateway names a lookup target
 * (`_LOOKUP_NAMES` in `field_studio.py`).
 */
export function lookupHitName(entity: EntitySlug, row: Row): string {
  const text = (key: string) => (typeof row[key] === "string" ? (row[key] as string) : "");
  switch (entity) {
    case "leads":
      return text("lead_name");
    case "contacts":
      return `${text("first_name")} ${text("last_name")}`.trim();
    default:
      return text("name");
  }
}

/** The `{id, name}` that a lookup stores for a hit (C4a item 8). */
export function lookupValue(entity: EntitySlug, row: Row): { id: string; name: string } {
  return { id: String(row.id ?? ""), name: lookupHitName(entity, row) };
}
