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
import type { FieldLayout, FieldSection } from "./fieldLayout";
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
  /** CRM-C4a: the section of the layout. `null` puts the field in no section. */
  section_id?: string | null;
  /** CRM-C4a: a member write must give a value. */
  required?: boolean;
  /** CRM-C4a: the line under the input. */
  help_text?: string | null;
  /** CRM-C4a: the value that a new record gets. */
  default_value?: unknown;
  /** CRM-C4a: the entity slug that a lookup names. */
  lookup_target?: string | null;
  /**
   * CRM-Q17a-1: the share of records that fill the field, from 0 to 1. The
   * gateway sends `null` outside `CRM_FILL_RANK_ORGS`, and while it has no
   * numbers. No request computes it: the sync writes it.
   */
  fill?: number | null;
  /** CRM-Q17a-1: the count of different filled values, in a sample. */
  distinct?: number | null;
};

// ── The keys a record page leaves out (WS-53 CRM-Q17a-2) ───────────────────

/**
 * The Zoho system keys that the record page leaves out (CRM-Q17a-2 item 6).
 * The sync keeps them in `custom` or in a module's `fields`, because it keeps
 * every key Zoho sends. They tell a member nothing, and `Layout` is filled on
 * each record, so a fill rank would put it first.
 */
export const ZOHO_SYSTEM_KEYS: readonly string[] = ["Layout", "Record_Image", "Tag"];

/**
 * The key in `custom` that keeps the Zoho name of an owner who is not a
 * member (`OWNER_NAME_KEY` in `crm_sources/zoho/mapping.py`). The record
 * shows it as its owner (CRM-Q17a-2 item 4), so it leaves the Fields tab.
 */
export const OWNER_NAME_KEY = "_owner_name";

/** The note after the name of an owner who is not a member. */
export const ZOHO_ONLY = "Zoho only";

/** True for a key that no Fields tab and no Overview draws. */
export function hiddenOnRecordPage(key: string): boolean {
  return key === OWNER_NAME_KEY || ZOHO_SYSTEM_KEYS.includes(key);
}

/**
 * The Zoho name of the owner of a record whose owner is not a member, or
 * null. A record with an `owner_email` names a member, and it wins.
 */
export function zohoOwnerName(record: Row | null | undefined): string | null {
  if (!record) return null;
  const email = record.owner_email;
  if (typeof email === "string" && email.trim()) return null;
  const custom = record.custom;
  if (!custom || typeof custom !== "object" || Array.isArray(custom)) return null;
  const name = (custom as Row)[OWNER_NAME_KEY];
  return typeof name === "string" && name.trim() ? name.trim() : null;
}

/** The owner as text with the note: "Asha Rao (Zoho only)". */
export function zohoOwnerText(name: string): string {
  return `${name} (${ZOHO_ONLY})`;
}

/** One option of a picklist, with its retired flag (CRM-C4p). */
export type FieldOption = { value: string; label: string; retired: boolean };

/**
 * The options of a picklist definition, in order. The studio writes
 * `{label, value, retired?}`, and an older sync row can hold a plain string.
 * A retired option stays in the list, so a record that holds it reads its
 * label (spec section 20.7, CRM-C4p item 10).
 */
export function fieldOptions(def: FieldDef | null | undefined): FieldOption[] {
  const out: FieldOption[] = [];
  for (const raw of def?.options ?? []) {
    if (typeof raw === "string") {
      out.push({ value: raw, label: raw, retired: false });
    } else if (raw && typeof raw === "object") {
      const o = raw as Row;
      const value =
        typeof o.value === "string" ? o.value : typeof o.label === "string" ? o.label : null;
      if (value) {
        const label = typeof o.label === "string" && o.label ? o.label : value;
        out.push({ value, label, retired: o.retired === true });
      }
    }
  }
  return out;
}

/** The label of a stored option value. A value with no option reads as it is. */
function optionText(def: FieldDef, item: unknown): string {
  if (typeof item === "string") {
    const found = fieldOptions(def).find((o) => o.value === item);
    if (found) return found.label;
  }
  return itemText(item);
}

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

/** The note after the name of a lookup whose target is gone (CRM-C4p item 10). */
export const LOOKUP_MISSING = "missing";

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
      // CRM-C4p: the option label, also for a retired option.
      const items = (Array.isArray(value) ? value : [value])
        .map((item) => optionText(def, item))
        .filter(Boolean);
      return items.length > 0 ? { kind: "chips", items } : EMPTY;
    }
    case "select":
      return { kind: "text", text: optionText(def, value) };
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
    case "lookup": {
      // CRM-C4a gives a target that is gone, or that RLS hides, `missing: true`.
      // The record keeps the stored name, and the note says why it is stale.
      const missing =
        !!value && typeof value === "object" && (value as Row).missing === true;
      const text = itemText(value);
      return { kind: "text", text: missing ? `${text} (${LOOKUP_MISSING})` : text };
    }
    case "textarea":
      return { kind: "text", text: customValue(value), wrap: true };
    default:
      // text and any type a later provider adds.
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
  // CRM-Q17a-2 item 4: an owner who is not a member shows by the Zoho name.
  const zohoOwner = zohoOwnerName(record);
  const owner: FieldValue = zohoOwner
    ? { kind: "text", text: zohoOwnerText(zohoOwner) }
    : text(record.owner_email);
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
      { key: "owner_email", label: "Owner", value: owner },
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
      { key: "owner_email", label: "Owner", value: owner },
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
      { key: "owner_email", label: "Owner", value: owner },
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
 * Only a key that the record carries is a row. A key of `hiddenOnRecordPage`
 * is never a row (CRM-Q17a-2 items 4 and 6).
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
    // CRM-C4a: a retire keeps `visible`, so the retired flag hides the field.
    .filter((d) => d.visible && !d.retired && d.key in values && !hiddenOnRecordPage(d.key))
    .sort((a, b) => a.position - b.position);
  const unknown = Object.keys(values).filter((k) => !byKey.has(k) && !hiddenOnRecordPage(k));
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

/**
 * The custom keys of a record that no definition describes, ready to draw
 * (CRM-C4b). The Fields tab by layout shows them after its sections, read
 * only, as `fieldRows` does.
 */
export function undefinedRows(
  record: Row | null | undefined,
  defs: FieldDef[] | null | undefined,
): FieldRow[] {
  const custom = record?.custom;
  if (!custom || typeof custom !== "object" || Array.isArray(custom)) return [];
  const known = new Set((defs ?? []).map((d) => d.key));
  return Object.entries(custom as Row)
    .filter(([key]) => !known.has(key) && !hiddenOnRecordPage(key))
    .map(([key, value]) => ({ key, label: humanizeKey(key), value: formatField(null, value) }));
}

/** One section of a layout and its fields. `section` is null for no section. */
export type FieldGroup = { section: FieldSection | null; fields: FieldDef[] };

/**
 * The fields of a layout, by section (WS-53 CRM-C4b, section 20.7 item 12).
 *
 * - Only a visible field that is not retired, and only a field that `keep`
 *   passes. Each field shows also when the record holds no value for it.
 * - The fields of no section come first, with no heading. A `section_id`
 *   that the layout does not list counts as no section.
 * - Then each section in the order of `position`, with its fields in the
 *   order of `position`. A section with no field to show is left out.
 */
export function layoutGroups(
  layout: FieldLayout,
  keep: (def: FieldDef) => boolean = () => true,
): FieldGroup[] {
  // A stable sort, so a tie keeps the order that the gateway gave.
  const shown = layout.fields
    .filter((d) => d.visible && !d.retired && keep(d))
    .sort((a, b) => a.position - b.position);
  const sections = [...layout.sections].sort((a, b) => a.position - b.position);
  const known = new Set(sections.map((s) => s.id));
  const groups: FieldGroup[] = [];
  const loose = shown.filter((d) => !d.section_id || !known.has(d.section_id));
  if (loose.length > 0) groups.push({ section: null, fields: loose });
  for (const section of sections) {
    const fields = shown.filter((d) => d.section_id === section.id);
    if (fields.length > 0) groups.push({ section, fields });
  }
  return groups;
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

/**
 * The keys this browser chose for one entity. Never throws.
 *
 * ⚠️ `null` is "nothing saved", and `[]` is "saved empty" (CRM-Q17a-2 item
 * 2). Only `null` lets the fill rank choose the columns. A member who took
 * each column off keeps a list with none. A value that is not a list of keys
 * reads as nothing saved.
 */
export function readColumns(
  entity: ColumnScope,
  store: Store | null = browserStore(),
): string[] | null {
  try {
    const raw = store?.getItem(columnsKey(entity));
    if (raw === null || raw === undefined) return null;
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((k): k is string => typeof k === "string").slice(0, MAX_COLUMNS)
      : null;
  } catch {
    return null;
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
 * The fields of `pickable`, by the sections of the layout (CRM-C4b). The
 * Columns control draws a heading for each section. The set is the set of
 * `pickable`: no retired field and no system row.
 */
export function columnGroups(layout: FieldLayout): FieldGroup[] {
  return layoutGroups(layout, (d) => !d.is_system);
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

// ── The fill rank on screen (WS-53 CRM-Q17a-2) ─────────────────────────────

/**
 * True when the gateway sent fill numbers (CRM-Q17a-1). It sends them only
 * for an org on `CRM_FILL_RANK_ORGS`, after a sync wrote them. With no
 * number, each surface of CRM-Q17a-2 that reads the fill rank draws as before.
 */
export function fillRankOn(defs: readonly FieldDef[] | null | undefined): boolean {
  return (defs ?? []).some((d) => typeof d.fill === "number");
}

/** The fill of a definition, or -1 for none. Sorts a field with no number last. */
export function fillOf(def: FieldDef | undefined): number {
  return typeof def?.fill === "number" ? def.fill : -1;
}

/**
 * The search of the Columns picker (CRM-Q17b item 6): a field matches when
 * its label or its key holds the words, with no regard to case. Empty words
 * match each field.
 */
export function fieldMatches(def: FieldDef, query: string): boolean {
  const words = query.trim().toLowerCase();
  if (!words) return true;
  return def.label.toLowerCase().includes(words) || def.key.toLowerCase().includes(words);
}

/**
 * "94% filled" beside a field of the Columns picker (CRM-Q17b item 6). A
 * field with no number gives `null`, and the picker draws no figure. A fill
 * above 0 never reads as "0%".
 */
export function fillLabel(def: FieldDef): string | null {
  if (typeof def.fill !== "number" || !Number.isFinite(def.fill)) return null;
  const share = Math.min(Math.max(def.fill, 0), 1);
  const percent = share > 0 && share < 0.01 ? 1 : Math.round(share * 100);
  return `${percent}% filled`;
}

/**
 * The custom columns of a list with no saved choice (CRM-Q17a-2 item 2): the
 * pickable fields with the highest fill, up to `MAX_COLUMNS`. A tie goes to
 * the field with more different values, then to the order of `position`. A
 * field with no fill, or a fill of 0, is never a default. A Zoho system key
 * (`ZOHO_SYSTEM_KEYS`) is never one either. With no fill numbers, it is `[]`,
 * which is the list of before.
 */
export function defaultColumns(defs: FieldDef[] | null | undefined): string[] {
  return pickable(defs)
    .filter((d) => fillOf(d) > 0 && !hiddenOnRecordPage(d.key))
    .map((d, index) => ({ d, index }))
    .sort(
      (a, b) =>
        fillOf(b.d) - fillOf(a.d) ||
        (b.d.distinct ?? 0) - (a.d.distinct ?? 0) ||
        a.index - b.index,
    )
    .slice(0, MAX_COLUMNS)
    .map(({ d }) => d.key);
}

/**
 * The custom columns a list shows: the saved choice, else the defaults of
 * the fill rank. `saved` is `null` for nothing saved. A saved `[]` wins, and
 * the list shows no custom column.
 */
export function effectiveColumns(
  saved: string[] | null,
  defs: FieldDef[] | null | undefined,
): string[] {
  return saved ?? defaultColumns(defs);
}

/**
 * The rows of a record page with the filled ones first (CRM-Q17a-2 item 5).
 * The filled rows go by the fill of their field, the highest first. A tie,
 * and a field with no number, keep the order of `rows`. The empty rows follow
 * in their own order. `defOf` gives the definition of a row.
 */
export function filledFirst<R>(
  rows: readonly R[],
  filled: (row: R) => boolean,
  defOf: (row: R) => FieldDef | undefined,
): R[] {
  const ranked = rows.map((row, index) => ({ row, index, fill: fillOf(defOf(row)) }));
  const full = ranked
    .filter((r) => filled(r.row))
    .sort((a, b) => b.fill - a.fill || a.index - b.index);
  const empty = ranked.filter((r) => !filled(r.row));
  return [...full, ...empty].map((r) => r.row);
}

/**
 * True when a row of the Fields tab matches the filter box. The text matches
 * the label or the key, in any case. An empty filter matches each row.
 */
export function matchesFieldFilter(row: { key: string; label: string }, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return row.label.toLowerCase().includes(q) || row.key.toLowerCase().includes(q);
}

/**
 * The rows of the Fields tab after the filter box and the fold (CRM-Q17a-2
 * item 5). `shown` keeps the order of `rows`. `empty` counts the empty rows
 * that match the filter. Without `showEmpty`, they stay behind "Show n empty
 * fields", and `folded` is the same count. With `showEmpty`, `folded` is 0.
 */
export function foldFields<R extends { key: string; label: string }>(
  rows: readonly R[],
  query: string,
  showEmpty: boolean,
  isEmpty: (row: R) => boolean,
): { shown: R[]; folded: number; empty: number } {
  const matching = rows.filter((row) => matchesFieldFilter(row, query));
  const filled = matching.filter((row) => !isEmpty(row));
  const empty = matching.length - filled.length;
  return showEmpty
    ? { shown: matching, folded: 0, empty }
    : { shown: filled, folded: empty, empty };
}

/** The words of the fold control. */
export function foldLabel(empty: number, showEmpty: boolean): string {
  if (showEmpty) return "Hide empty fields";
  return empty === 1 ? "Show 1 empty field" : `Show ${empty} empty fields`;
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
