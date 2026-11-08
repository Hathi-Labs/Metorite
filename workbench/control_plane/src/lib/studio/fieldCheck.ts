/**
 * The client check of a Studio field value (`ai_studio.md` §3.1, slice STU-D2a).
 *
 * The twin of `check_value` and `validate_record` in
 * `packages/acb_common/acb_common/studio_fields.py`. **Python is the one
 * source.** `tests/unit/test_studio_field_cases_parity.py` writes the golden
 * file `studio_field_cases.json`, and `fieldCheck.test.ts` holds this file to
 * it: the same accept, the same reject and the same normalized value.
 *
 * The server stays the authority. This check lets a member see a bad value
 * on blur, with no round trip. Where it differs from Python on a form that a
 * member never types (a UUID with braces, an IPv6 host), it is stricter or
 * plainer, and never looser. A 422 from the server shows under the field.
 *
 * ⚠️ Pure. No React import and no `createContext`: `chatPlacement.ts` (a
 * server route) reaches `lib/studio`, and a server module may not import a
 * client file. `fieldCheck.test.ts` fences it.
 *
 * A message never echoes the value, because a record holds member data.
 */
import type { FieldType } from "./manifest";

// --- the limits: `studio_fields.py` holds the same numbers (parity test) ----

export const MAX_TEXT_CHARS = 2_000;
export const MAX_LONG_TEXT_CHARS = 20_000;
export const MAX_ABS_NUMBER = 1_000_000_000_000_000;
export const MAX_MANY_ITEMS = 500;
export const MAX_URL_CHARS = 2_048;
export const MAX_EMAIL_CHARS = 254;
// STU-C14: the limits of the six more types.
export const MAX_RATING_BOUND = 100;
export const MAX_DURATION_SECONDS = 10_000_000_000;
export const MAX_PERCENT_BP = 1_000_000_000;
export const MAX_UNITS = 30;
export const MAX_UNIT_CHARS = 20;
export const MAX_CODE_CHARS = 80;
export const MAX_QR_CHARS = 2_000;
/** A rating with no `min` and `max` runs from 1 to 5. */
export const DEFAULT_RATING_MIN = 1;
export const DEFAULT_RATING_MAX = 5;
export const BARCODE_FORMATS = ["ean13", "code128", "qr"] as const;

/** One field of an object, as the form needs it. */
export interface FieldSpec {
  key: string;
  type: FieldType;
  label?: string;
  required?: boolean;
  many?: boolean;
  options?: readonly string[];
  /** The target object of a relation. The check does not read it. */
  to?: string;
  /** STU-C14. The bounds of a rating. */
  min?: number;
  max?: number;
  /** STU-C14. The units of a quantity. */
  units?: readonly string[];
  /** STU-C14. The format of a barcode: `ean13`, `code128` or `qr`. */
  format?: string;
}

export type CheckResult = { ok: true; value: unknown } | { ok: false; message: string };

export interface Problem {
  field: string;
  message: string;
}

const isObject = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

/** Count by code point, as Python `len` does. */
const chars = (s: string): number => {
  let n = 0;
  for (const _ of s) n += 1; // eslint-disable-line @typescript-eslint/no-unused-vars
  return n;
};

const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
/** Postgres takes no NUL and no lone surrogate in text. */
const storable = (s: string): boolean => !s.includes("\u0000") && !LONE_SURROGATE.test(s);

/** The same set as Python `str.isspace`. */
const SPACE = /[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/;

/** Python `str.strip()` removes exactly `SPACE`. JS `trim()` differs (U+FEFF, the C0 separators, NEL). */
const ONLY_SPACE = new RegExp("^" + SPACE.source + "*$");

const fail = (message: string): CheckResult => ({ ok: false, message });
const pass = (value: unknown): CheckResult => ({ ok: true, value });

/** A whole number or a float below 10^15 in size. A tiny float would need an exponent. */
function plainNumber(v: unknown): v is number {
  if (typeof v !== "number" || !Number.isFinite(v)) return false;
  if (v !== 0 && Math.abs(v) < 1e-4) return false;
  return Math.abs(v) < MAX_ABS_NUMBER;
}

// --- the types ---------------------------------------------------------------

function text(v: unknown, limit: number): CheckResult {
  if (typeof v !== "string" || chars(v) > limit) {
    return fail(`Enter text of at most ${limit.toLocaleString("en-US")} characters.`);
  }
  if (!storable(v)) return fail("This text holds a broken character. Remove it.");
  return pass(v);
}

function number(v: unknown): CheckResult {
  return plainNumber(v)
    ? pass(v)
    : fail("Enter a number below 10^15, written with no exponent.");
}

/** The digits after the point of a currency: 2 for INR, 0 for JPY. */
export function minorDigits(currency: string): number {
  try {
    return new Intl.NumberFormat("en-IN", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

function money(v: unknown): CheckResult {
  if (
    !isObject(v) || !sameKeys(v, ["minor", "currency"]) ||
    typeof v.minor !== "number" || !Number.isInteger(v.minor) ||
    Math.abs(v.minor) >= MAX_ABS_NUMBER
  ) {
    const d = isObject(v) && typeof v.currency === "string" ? minorDigits(v.currency) : 2;
    return fail(d === 0 ? "Enter a whole amount, with no decimals." : `Enter an amount with at most ${d} decimals.`);
  }
  if (typeof v.currency !== "string" || !/^[A-Z]{3}$/.test(v.currency)) {
    return fail("Enter a currency of three capital letters, as INR.");
  }
  return pass({ minor: v.minor, currency: v.currency });
}

const sameKeys = (o: Record<string, unknown>, keys: string[]): boolean => {
  const have = Object.keys(o);
  return have.length === keys.length && keys.every((k) => have.includes(k));
};

const daysIn = (y: number, m: number): number =>
  m === 2 ? (y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 29 : 28)
    : [4, 6, 9, 11].includes(m) ? 30 : 31;

const realDate = (y: number, m: number, d: number): boolean =>
  y >= 1 && m >= 1 && m <= 12 && d >= 1 && d <= daysIn(y, m);

function date(v: unknown): CheckResult {
  const m = typeof v === "string" ? /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(v) : null;
  if (m && realDate(+m[1], +m[2], +m[3])) return pass(v);
  return fail("Enter a real date as YYYY-MM-DD.");
}

const DATETIME =
  /^([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?(?:[Zz]|([+-])([0-9]{2}):([0-9]{2}))$/;

const pad = (n: number, width: number): string => String(n).padStart(width, "0");

function datetime(v: unknown): CheckResult {
  const bad = fail("Enter a date and time with an offset, as 1999-12-31T23:59:00+05:30.");
  const m = typeof v === "string" ? DATETIME.exec(v) : null;
  if (!m) return bad;
  const [y, mo, d, h, mi, s] = [+m[1], +m[2], +m[3], +m[4], +m[5], +m[6]];
  if (!realDate(y, mo, d) || h > 23 || mi > 59 || s > 59) return bad;
  const micro = m[7] ? +m[7].padEnd(6, "0") : 0;
  // Python accepts any two-digit offset minutes, and refuses an offset of a day.
  const offset = m[8] ? (m[8] === "-" ? -1 : 1) * (+m[9] * 60 + +m[10]) : 0;
  if (Math.abs(offset) >= 24 * 60) return bad;
  const when = new Date(0);
  when.setUTCFullYear(y, mo - 1, d);
  when.setUTCHours(h, mi, s, 0);
  const utc = new Date(when.getTime() - offset * 60_000);
  const year = utc.getUTCFullYear();
  if (year < 1 || year > 9999) return bad; // Python: OverflowError
  const day = `${pad(year, 4)}-${pad(utc.getUTCMonth() + 1, 2)}-${pad(utc.getUTCDate(), 2)}`;
  const time = `${pad(utc.getUTCHours(), 2)}:${pad(utc.getUTCMinutes(), 2)}:${pad(utc.getUTCSeconds(), 2)}`;
  return pass(`${day}T${time}${micro ? "." + pad(micro, 6) : ""}Z`);
}

function checkbox(v: unknown): CheckResult {
  return typeof v === "boolean" ? pass(v) : fail("Choose yes or no.");
}

function select(f: FieldSpec, v: unknown): CheckResult {
  return typeof v === "string" && (f.options ?? []).includes(v)
    ? pass(v)
    : fail("Choose one of the listed options.");
}

function multiSelect(f: FieldSpec, v: unknown): CheckResult {
  const options = f.options ?? [];
  if (
    !Array.isArray(v) || v.length > MAX_MANY_ITEMS ||
    !v.every((x) => typeof x === "string" && options.includes(x)) ||
    new Set(v).size !== v.length
  ) {
    return fail("Choose distinct options from the list.");
  }
  return pass([...v]);
}

const EMAIL =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$/;

const address = (v: unknown): v is string =>
  typeof v === "string" && v.length <= MAX_EMAIL_CHARS && EMAIL.test(v);

function person(v: unknown): CheckResult {
  return address(v) && v === v.toLowerCase()
    ? pass(v)
    : fail("Enter the lower-case email address of a member.");
}

function email(v: unknown): CheckResult {
  return address(v)
    ? pass(v.toLowerCase())
    : fail(`Enter an email address of at most ${MAX_EMAIL_CHARS} characters.`);
}

function relation(v: unknown): CheckResult {
  if (
    isObject(v) && sameKeys(v, ["id"]) && typeof v.id === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$/.test(v.id)
  ) {
    return pass({ id: v.id });
  }
  return fail("Choose a linked record.");
}

function file(v: unknown): CheckResult {
  if (isObject(v) && sameKeys(v, ["id"]) && typeof v.id === "string") {
    const hex = v.id.replace(/^urn:uuid:/i, "").replace(/^\{|\}$/g, "").replaceAll("-", "");
    if (/^[0-9a-fA-F]{32}$/.test(hex)) {
      const h = hex.toLowerCase();
      return pass({
        id: `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`,
      });
    }
  }
  return fail("Choose an uploaded file.");
}

function url(v: unknown): CheckResult {
  const bad = fail(`Enter an http or https address of at most ${MAX_URL_CHARS} characters.`);
  if (
    typeof v !== "string" || chars(v) > MAX_URL_CHARS || !storable(v) ||
    SPACE.test(v) || /[\x00-\x1f\x7f]/.test(v)
  ) return bad;
  const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(v);
  if (!scheme || !["http", "https"].includes(scheme[1].toLowerCase())) return bad;
  const rest = v.slice(scheme[0].length);
  if (!rest.startsWith("//")) return bad; // no host
  const netloc = /^\/\/([^/?#]*)/.exec(rest)?.[1] ?? "";
  if (netloc.includes("@")) {
    return fail("Enter an address with no user name or password in it.");
  }
  let host = netloc;
  let port = "";
  if (netloc.startsWith("[")) {
    const end = netloc.indexOf("]");
    if (end < 0) return bad;
    host = netloc.slice(1, end);
    port = netloc.slice(end + 1).replace(/^:/, "");
    if (!/^[0-9A-Fa-f:.]+$/.test(host)) return bad;
  } else {
    const colon = netloc.indexOf(":");
    if (colon >= 0) {
      host = netloc.slice(0, colon);
      port = netloc.slice(colon + 1);
    }
  }
  if (port && (!/^[0-9]+$/.test(port) || +port > 65535)) return bad;
  return host ? pass(v) : bad;
}

function phone(v: unknown): CheckResult {
  return typeof v === "string" && /^\+[1-9][0-9]{7,14}$/.test(v)
    ? pass(v)
    : fail("Enter a phone number in E.164 form: + then 8 to 15 digits.");
}

function geo(v: unknown): CheckResult {
  if (
    isObject(v) && sameKeys(v, ["lat", "lng"]) && plainNumber(v.lat) && plainNumber(v.lng) &&
    v.lat >= -90 && v.lat <= 90 && v.lng >= -180 && v.lng <= 180
  ) {
    return pass({ lat: v.lat, lng: v.lng });
  }
  return fail("Enter a latitude from -90 to 90 and a longitude from -180 to 180.");
}

// --- STU-C14: the six more types ---------------------------------------------------

/** A tag or a comment in a rich text. The text is markdown, and never HTML. */
const RAW_HTML = /<[A-Za-z/!?]/;

function richText(v: unknown): CheckResult {
  const r = text(v, MAX_LONG_TEXT_CHARS);
  if (!r.ok) return r;
  return RAW_HTML.test(v as string)
    ? fail("Enter markdown. Remove the HTML tag, as <b>.")
    : r;
}

const wholeNumber = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

/** The bounds of a rating: its own, or 1 to 5. */
export const ratingRange = (f: FieldSpec): [number, number] => [
  f.min ?? DEFAULT_RATING_MIN, f.max ?? DEFAULT_RATING_MAX,
];

function rating(f: FieldSpec, v: unknown): CheckResult {
  const [lo, hi] = ratingRange(f);
  return wholeNumber(v) && v >= lo && v <= hi
    ? pass(v)
    : fail(`Enter a whole number from ${lo} to ${hi}.`);
}

function duration(v: unknown): CheckResult {
  return wholeNumber(v) && v >= 0 && v < MAX_DURATION_SECONDS
    ? pass(v)
    : fail("Enter a length of time as 1h 30m, 45m or 1:30:00.");
}

const QUANTITY = /^-?[0-9]{1,15}(?:\.[0-9]{1,6})?$/;

function quantity(f: FieldSpec, v: unknown): CheckResult {
  if (!isObject(v) || !sameKeys(v, ["value", "unit"]) || typeof v.value !== "string" || !QUANTITY.test(v.value)) {
    return fail("Enter a quantity: a number with up to 6 decimals, and a unit.");
  }
  if (typeof v.unit !== "string" || !(f.units ?? []).includes(v.unit)) {
    return fail("Choose one of the listed units.");
  }
  return pass({ value: v.value, unit: v.unit });
}

function percent(v: unknown): CheckResult {
  return wholeNumber(v) && Math.abs(v) < MAX_PERCENT_BP
    ? pass(v)
    : fail("Enter a percent with up to 2 decimals, as 12.5.");
}

/** True when `code` is 13 digits and the last one is the EAN-13 check digit. */
export function ean13Valid(code: string): boolean {
  if (!/^[0-9]{13}$/.test(code)) return false;
  let total = 0;
  for (let i = 0; i < 12; i++) total += Number(code[i]) * (i % 2 ? 3 : 1);
  return (10 - (total % 10)) % 10 === Number(code[12]);
}

function barcode(f: FieldSpec, v: unknown): CheckResult {
  const format = f.format ?? "qr";
  if (format === "ean13") {
    return typeof v === "string" && ean13Valid(v)
      ? pass(v)
      : fail("Enter an EAN-13: 13 digits with a correct check digit.");
  }
  if (format === "code128") {
    return typeof v === "string" && v.length >= 1 && v.length <= MAX_CODE_CHARS && /^[\x20-\x7e]+$/.test(v)
      ? pass(v)
      : fail(`Enter 1 to ${MAX_CODE_CHARS} printable ASCII characters.`);
  }
  return typeof v === "string" && chars(v) >= 1 && chars(v) <= MAX_QR_CHARS && storable(v) && !/[\x00-\x1f\x7f]/.test(v)
    ? pass(v)
    : fail(`Enter text of 1 to ${MAX_QR_CHARS.toLocaleString("en-US")} characters, with no control character.`);
}

const CHECKS: Record<FieldType, (f: FieldSpec, v: unknown) => CheckResult> = {
  text: (_f, v) => text(v, MAX_TEXT_CHARS),
  longText: (_f, v) => text(v, MAX_LONG_TEXT_CHARS),
  number: (_f, v) => number(v),
  money: (_f, v) => money(v),
  date: (_f, v) => date(v),
  datetime: (_f, v) => datetime(v),
  checkbox: (_f, v) => checkbox(v),
  select,
  multiSelect,
  person: (_f, v) => person(v),
  relation: (_f, v) => relation(v),
  file: (_f, v) => file(v),
  url: (_f, v) => url(v),
  email: (_f, v) => email(v),
  phone: (_f, v) => phone(v),
  geo: (_f, v) => geo(v),
  richText: (_f, v) => richText(v),
  rating,
  duration: (_f, v) => duration(v),
  quantity,
  percent: (_f, v) => percent(v),
  barcode,
};

/** One value of one field, as the store keeps it. The twin of `check_value`. */
export function checkValue(field: FieldSpec, value: unknown): CheckResult {
  const check = CHECKS[field.type];
  if (!check) return fail("This field type is unknown.");
  if (field.many) {
    if (!Array.isArray(value) || value.length > MAX_MANY_ITEMS) {
      return fail(`Enter a list of at most ${MAX_MANY_ITEMS} values.`);
    }
    if (value.some((v) => v === null || v === undefined)) {
      return fail("The list holds an empty value. Remove it.");
    }
    const out: unknown[] = [];
    for (const v of value) {
      const r = check(field, v);
      if (!r.ok) return r;
      out.push(r.value);
    }
    return pass(out);
  }
  return check(field, value);
}

/** A blank value: nothing, only white space, or an empty list. */
export const isBlank = (v: unknown): boolean =>
  v === null || v === undefined || (typeof v === "string" && ONLY_SPACE.test(v)) ||
  (Array.isArray(v) && v.length === 0);

/** A key of a record, safe to name in a message. */
const safeKey = (k: string): string => (/^[a-z][a-z0-9_]{0,47}$/.test(k) ? k : "?");

export type RecordResult =
  | { ok: true; value: Record<string, unknown> }
  | { ok: false; problems: Problem[]; notObject?: true };

/**
 * Every problem of a record, in the order of the Python check: an unknown
 * key first, then each field in its order. `validateRecord` keeps the first.
 * The form shows all of them, so a member fixes every field in one pass.
 */
export function validateRecord(fields: readonly FieldSpec[], data: unknown): RecordResult {
  if (!isObject(data)) {
    return { ok: false, notObject: true, problems: [{ field: "", message: "A record is a set of field values." }] };
  }
  const known = new Set(fields.map((f) => f.key));
  const problems: Problem[] = [];
  for (const key of Object.keys(data)) {
    if (!known.has(key)) {
      const safe = safeKey(key);
      problems.push({ field: safe, message: `Field \`${safe}\` is not a field of this object.` });
    }
  }
  const value: Record<string, unknown> = {};
  for (const f of fields) {
    const v = Object.hasOwn(data, f.key) ? data[f.key] : undefined;
    if (isBlank(v)) {
      if (f.required) {
        problems.push({ field: f.key, message: "This field is required." });
        continue;
      }
      if (v === null || v === undefined) continue;
    }
    const r = checkValue(f, v);
    if (r.ok) value[f.key] = r.value;
    else problems.push({ field: f.key, message: r.message });
  }
  return problems.length > 0 ? { ok: false, problems } : { ok: true, value };
}
