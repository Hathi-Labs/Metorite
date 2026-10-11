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
};

/** What a cell shows. The component maps each kind to markup. */
export type FieldValue =
  | { kind: "empty" }
  | { kind: "text"; text: string; wrap?: boolean }
  | { kind: "chips"; items: string[] }
  | { kind: "link"; href: string; text: string }
  | { kind: "mailto"; href: string; text: string };

const EMPTY: FieldValue = { kind: "empty" };

/** Only an http or https address may reach an `href`. The value comes from Zoho. */
export function safeHref(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const url = value.trim();
  return /^https?:\/\/[^\s]+$/i.test(url) ? url : null;
}

function isEmail(value: string): boolean {
  return /^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$/.test(value);
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
    case "number": {
      const n = toNumber(value);
      return {
        kind: "text",
        text: n === null ? customValue(value) : new Intl.NumberFormat("en-IN").format(n),
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
    case "lookup":
      return { kind: "text", text: itemText(value) };
    case "textarea":
      return { kind: "text", text: customValue(value), wrap: true };
    default:
      // text, phone, select and any type a later provider adds.
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
  const byKey = new Map((defs ?? []).map((d) => [d.key, d]));
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

export function columnsKey(entity: EntitySlug): string {
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
export function readColumns(entity: EntitySlug, store: Store | null = browserStore()): string[] {
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
  entity: EntitySlug,
  keys: string[],
  store: Store | null = browserStore(),
): void {
  try {
    store?.setItem(columnsKey(entity), JSON.stringify(keys.slice(0, MAX_COLUMNS)));
  } catch {
    // Private window or blocked site data: the choice lasts until the next visit.
  }
}

/** The fields a member may pick: visible ones, in the order of `position`. */
export function pickable(defs: FieldDef[] | null | undefined): FieldDef[] {
  return [...(defs ?? [])].filter((d) => d.visible).sort((a, b) => a.position - b.position);
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
