// ── Field definitions: how a custom field reads (WS-53 CRM-Q4) ─────────────
//
// Pure, so the vitest setup (no DOM) can test every rule. `FieldValue.tsx`
// draws what this file decides. The gateway serves the definitions at
// `GET /crm/fields/{entity}`. The sync writes them from Zoho, and they say
// what each key of a record's `custom` object means.
//
// Spec: project-docs/specs/crm_platform.md section 18.3 step 2 item 3.
//
// ⚠️ A key with no definition falls back to the humanised key, as before.
// A definition never hides a value that the record carries under another key.

import { customValue, humanizeKey } from "./connect";
import { dateTime, money, shortDate } from "./format";
import type { EntitySlug } from "./types";

type Row = Record<string, unknown>;

/** One row of `GET /crm/fields/{entity}`. */
export type FieldDef = {
  key: string;
  label: string;
  type: string;
  options: unknown[] | null;
  position: number;
  visible: boolean;
  source: "zoho" | "native";
  api_name: string | null;
  /** CRM-C4a: a row that overlays a fixed column. It is never a custom key. */
  is_system?: boolean;
  /** CRM-C4a: retired. A record keeps its value, and no picker offers it. */
  retired?: boolean;
};

/**
 * The definitions that describe a custom key (WS-53 CRM-C4a, spec section
 * 20.7 item 11). A row with `is_system` overlays a fixed column, so it is
 * left out.
 */
function customDefs(defs: FieldDef[] | null | undefined): FieldDef[] {
  return (defs ?? []).filter((d) => !d.is_system);
}

/** What a cell shows. The component maps each kind to markup. */
export type FieldValue =
  | { kind: "empty" }
  | { kind: "text"; text: string; wrap?: boolean }
  | { kind: "chips"; items: string[] }
  | { kind: "link"; href: string; text: string }
  | { kind: "mailto"; href: string; text: string }
  | { kind: "tel"; href: string; text: string };

const EMPTY: FieldValue = { kind: "empty" };

/** Only an http or https address may reach an `href`. The value comes from Zoho. */
export function safeHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const url = value.trim();
  return /^https?:\/\/[^\s]+$/i.test(url) ? url : null;
}

/** The guard of every `mailto:` link the CRM draws. */
export function isEmail(value: string): boolean {
  // `?`, `&`, `#` and `%` would let a value add a bcc or a subject to the draft.
  // `,` and `;` would make a list of addresses one link (review P2).
  return /^[^\s@<>?&#%,;]+@[^\s@<>?&#%,;]+\.[^\s@<>?&#%,;]+$/.test(value);
}

/** The fewest digits a `tel:` link needs. A shorter value stays text. */
export const TEL_MIN_DIGITS = 6;

/** The most digits one phone number holds (E.164). More is two numbers. */
export const TEL_MAX_DIGITS = 15;

/** Where an extension starts: `x`, `ext` or `#`, in any case. */
const TEL_EXTENSION = /ext|x|#/i;

/** What one number may hold: a leading `+`, digits, and the usual spacers. */
const ONE_NUMBER = /^\+?[\d\s().-]+$/;

/**
 * A `tel:` address for a phone number, or null (WS-53 CRM-X4 item 6).
 *
 * The `href` keeps only the digits and a leading `+`, so a value from Zoho
 * can never put a letter, a `;` or a query into the link.
 *
 * ⚠️ One link dials one number (review P2). An extension (`x56`, `ext. 56`,
 * `#2`) is cut off, because `tel:` cannot dial it after a wait. A value that
 * holds `/`, `,` or `;`, a word, a second `+`, or more than 15 digits holds
 * two numbers, and it stays text. Joined digits dial a number nobody has.
 */
export function telHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const whole = value.trim();
  if (/[/,;]/.test(whole)) return null;
  const at = whole.search(TEL_EXTENSION);
  const text = (at < 0 ? whole : whole.slice(0, at)).trim();
  if (!ONE_NUMBER.test(text)) return null;
  const digits = text.replace(/\D/g, "");
  if (digits.length < TEL_MIN_DIGITS || digits.length > TEL_MAX_DIGITS) return null;
  return `tel:${text.startsWith("+") ? "+" : ""}${digits}`;
}

/**
 * A fixed field that holds an email or a phone (CRM-X4 item 6). The same rules
 * as a custom field of that type: `mailto:` only when `isEmail` passes, and
 * `tel:` only for 6 digits or more.
 */
export function contactValue(kind: "email" | "phone", value: unknown): FieldValue {
  if (isBlank(value)) return EMPTY;
  const text = typeof value === "string" ? value.trim() : customValue(value);
  if (kind === "email") {
    return isEmail(text) ? { kind: "mailto", href: `mailto:${text}`, text } : { kind: "text", text };
  }
  const href = telHref(text);
  return href ? { kind: "tel", href, text } : { kind: "text", text };
}

/** The name of a lookup value `{name, id}`, or null when it is not one. */
function lookupName(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const named = (value as Row).name ?? (value as Row).display_value;
  return typeof named === "string" && named ? named : null;
}

function isBlank(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    value === "" ||
    (Array.isArray(value) && value.length === 0)
  );
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function itemText(item: unknown): string {
  return lookupName(item) ?? customValue(item);
}

/** A value, by the type of its definition. A null definition reads as before. */
export function formatField(def: FieldDef | null, value: unknown): FieldValue {
  if (isBlank(value)) return EMPTY;
  if (!def) return { kind: "text", text: customValue(value) };
  switch (def.type) {
    case "date":
      return { kind: "text", text: typeof value === "string" ? shortDate(value) : customValue(value) };
    case "datetime":
      return { kind: "text", text: typeof value === "string" ? dateTime(value) : customValue(value) };
    case "currency": {
      const n = toNumber(value);
      return { kind: "text", text: n === null ? customValue(value) : money(n) };
    }
    case "number":
    case "decimal": {
      // CRM-C4a: a Zoho double is now `decimal` (spec section 20.7 item 1).
      const n = toNumber(value);
      return {
        kind: "text",
        text: n === null ? customValue(value) : new Intl.NumberFormat("en-IN").format(n),
      };
    }
    case "percent": {
      const n = toNumber(value);
      return {
        kind: "text",
        text: n === null ? customValue(value) : `${new Intl.NumberFormat("en-IN").format(n)}%`,
      };
    }
    case "boolean": {
      if (value === true || value === "true") return { kind: "text", text: "Yes" };
      if (value === false || value === "false") return { kind: "text", text: "No" };
      return { kind: "text", text: customValue(value) };
    }
    case "multiselect": {
      const items = (Array.isArray(value) ? value : [value]).map(itemText).filter(Boolean);
      return items.length > 0 ? { kind: "chips", items } : EMPTY;
    }
    case "url": {
      const href = safeHref(value);
      // An address that is not http or https stays text. It is never a link.
      return href
        ? { kind: "link", href, text: href }
        : { kind: "text", text: customValue(value) };
    }
    case "email": {
      const text = typeof value === "string" ? value.trim() : "";
      return isEmail(text)
        ? { kind: "mailto", href: `mailto:${text}`, text }
        : { kind: "text", text: customValue(value) };
    }
    case "phone":
      return contactValue("phone", value);
    case "lookup":
      return { kind: "text", text: itemText(value) };
    case "textarea":
      return { kind: "text", text: customValue(value), wrap: true };
    default:
      // text, select and any type a later provider adds.
      return { kind: "text", text: itemText(value) };
  }
}

/** The plain text of a value, for a place that has no markup (a title, a test). */
export function fieldText(value: FieldValue): string {
  switch (value.kind) {
    case "empty":
      return "—";
    case "chips":
      return value.items.join(", ");
    default:
      return value.text;
  }
}

export type FieldRow = { key: string; label: string; value: FieldValue };

/**
 * The facts of the record page's summary header (CRM-X4 item 1), in order.
 * A fact with no value is left out. The stage or the status is not a fact
 * here: the header draws it as the `StatusPill`.
 *
 * `companyName` is the name of a contact's company, which the contact row
 * does not carry. The sheet reads it, and null leaves the fact out.
 */
export function summaryFacts(
  entity: EntitySlug,
  record: Row,
  companyName: string | null,
): FieldRow[] {
  const text = (value: unknown): FieldValue =>
    isBlank(value) ? EMPTY : { kind: "text", text: customValue(value) };
  const facts: Record<EntitySlug, FieldRow[]> = {
    deals: [
      {
        key: "amount",
        label: "Amount",
        value:
          typeof record.amount === "number"
            ? { kind: "text", text: money(record.amount, (record.currency as string) || "INR") }
            : EMPTY,
      },
      { key: "owner_email", label: "Owner", value: text(record.owner_email) },
      {
        key: "expected_close_date",
        label: "Close",
        value: record.expected_close_date
          ? { kind: "text", text: shortDate(record.expected_close_date as string) }
          : EMPTY,
      },
    ],
    leads: [
      { key: "organization_name", label: "Company", value: text(record.organization_name) },
      { key: "owner_email", label: "Owner", value: text(record.owner_email) },
    ],
    contacts: [
      { key: "title", label: "Title", value: text(record.title) },
      { key: "company", label: "Company", value: text(companyName) },
      { key: "email", label: "Email", value: contactValue("email", record.email) },
      { key: "phone", label: "Phone", value: contactValue("phone", record.phone ?? record.mobile) },
    ],
    organizations: [
      { key: "industry", label: "Industry", value: text(record.industry) },
      { key: "phone", label: "Phone", value: contactValue("phone", record.phone) },
      { key: "owner_email", label: "Owner", value: text(record.owner_email) },
    ],
  };
  return facts[entity].filter((fact) => fact.value.kind !== "empty");
}

/**
 * The custom fields of a record, ready to draw.
 *
 * With definitions: the order of `position`, the definition's label, and only
 * the visible ones. A key without a definition follows them, under its
 * humanised key. Without definitions: the keys in the record's order, as before.
 * Only a key that the record carries is a row.
 */
export function fieldRows(
  record: Row | null | undefined,
  defs: FieldDef[] | null | undefined,
): FieldRow[] {
  const custom = record?.custom;
  if (!custom || typeof custom !== "object" || Array.isArray(custom)) return [];
  const values = custom as Row;
  const byKey = new Map(customDefs(defs).map((d) => [d.key, d]));
  const known = [...byKey.values()]
    .filter((d) => d.visible && d.key in values)
    .sort((a, b) => a.position - b.position);
  const unknown = Object.keys(values).filter((k) => !byKey.has(k));
  return [
    ...known.map((d) => ({
      key: d.key,
      label: d.label,
      value: formatField(d, values[d.key]),
    })),
    ...unknown.map((k) => ({
      key: k,
      label: humanizeKey(k),
      value: formatField(null, values[k]),
    })),
  ];
}

// ── The list columns a member picks ─────────────────────────────────────────

/** The most custom columns a list shows. */
export const MAX_COLUMNS = 5;

export const COLUMNS_KEY_PREFIX = "cc.crm.columns.";

/**
 * Whose column choice it is: one of the four entities, or one generic module
 * (`module:Products`, WS-53 CRM-Q6b). Each keeps its own choice.
 */
export type ColumnScope = EntitySlug | `module:${string}`;

export function columnsKey(entity: ColumnScope): string {
  return `${COLUMNS_KEY_PREFIX}${entity}`;
}

type Store = Pick<Storage, "getItem" | "setItem">;

function browserStore(): Store | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** The keys this browser chose for one entity. Never throws. */
export function readColumns(entity: ColumnScope, store: Store | null = browserStore()): string[] {
  try {
    const raw = store?.getItem(columnsKey(entity));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed)
      ? parsed.filter((k): k is string => typeof k === "string").slice(0, MAX_COLUMNS)
      : [];
  } catch {
    return [];
  }
}

/** Keep the choice. A blocked store loses it and nothing else. */
export function writeColumns(
  entity: ColumnScope,
  keys: string[],
  store: Store | null = browserStore(),
): void {
  try {
    store?.setItem(columnsKey(entity), JSON.stringify(keys.slice(0, MAX_COLUMNS)));
  } catch {
    // Private window or blocked site data: the choice lasts until the next visit.
  }
}

/**
 * The fields a member may pick: visible ones, in the order of `position`. A
 * retired field and a system row are never offered (CRM-C4a).
 */
export function pickable(defs: FieldDef[] | null | undefined): FieldDef[] {
  return customDefs(defs)
    .filter((d) => d.visible && !d.retired)
    .sort((a, b) => a.position - b.position);
}

/**
 * The chosen columns that still exist, in the order the member chose them.
 * A key that left the definitions, or went hidden, drops out.
 */
export function chosenDefs(chosen: string[], defs: FieldDef[] | null | undefined): FieldDef[] {
  const live = new Map(pickable(defs).map((d) => [d.key, d]));
  return chosen
    .map((k) => live.get(k))
    .filter((d): d is FieldDef => d !== undefined)
    .slice(0, MAX_COLUMNS);
}

/** Add or remove one key. A sixth key is refused and the list stays as it is. */
export function toggleColumn(chosen: string[], key: string): string[] {
  if (chosen.includes(key)) return chosen.filter((k) => k !== key);
  return chosen.length >= MAX_COLUMNS ? chosen : [...chosen, key];
}

/** The cell of one list row for one chosen field. */
export function listCell(row: Row, def: FieldDef): FieldValue {
  const custom = row.custom;
  const value =
    custom && typeof custom === "object" && !Array.isArray(custom)
      ? (custom as Row)[def.key]
      : undefined;
  return formatField(def, value);
}
