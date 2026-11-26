/**
 * Column hide of a table workspace (STU-LY-2b, `studio_layouts.md` L2). Pure: no React, no I/O.
 *
 * A member hides the columns that they do not need. The choice lives in the URL as `hide=<key>,<key>` (a closed
 * grammar of field keys, at most 12), so a link opens the same view and Back works. It is a view of the
 * member, not a change of the app: nothing is written. Three rules:
 * - The FIRST column never hides. It is the name of the row (L2 bar: "the first column is a name").
 * - A key that is not a field of the table is ignored, so a stale link shows every column.
 * - The table keeps at least one column.
 */
import type { PageState } from "./pageState";

export const MAX_HIDDEN = 12;
const KEY = /^[a-z][a-z0-9_]{0,47}$/;

/** The hidden keys of a `hide` value: valid keys only, once each, at most `MAX_HIDDEN`. */
export function parseHidden(value: string | null | undefined): string[] {
  if (typeof value !== "string" || !value) return [];
  const out: string[] = [];
  for (const k of value.split(",")) {
    if (KEY.test(k) && !out.includes(k)) out.push(k);
    if (out.length >= MAX_HIDDEN) break;
  }
  return out;
}

/** The keys that may hide: every column but the first. */
export function hideableKeys(fields: readonly { key: string }[]): string[] {
  return fields.slice(1).map((f) => f.key);
}

/** The columns that show: the first one always, and each other one that is not hidden. */
export function visibleFields<T extends { key: string }>(fields: readonly T[], hidden: readonly string[]): T[] {
  const off = new Set(hidden);
  return fields.filter((f, i) => i === 0 || !off.has(f.key));
}

/** The state with one column hidden or shown again. A key that may not hide changes nothing. */
export function toggleHidden(state: PageState, key: string, fields: readonly { key: string }[]): PageState {
  if (!hideableKeys(fields).includes(key)) return state;
  const now = parseHidden(state.hide);
  const next = now.includes(key) ? now.filter((k) => k !== key) : [...now, key];
  if (next.length > MAX_HIDDEN) return state;
  const out = { ...state };
  if (next.length === 0) delete out.hide;
  else out.hide = next.join(",");
  return out;
}

/** The state with every column shown again. */
export function showAll(state: PageState): PageState {
  const out = { ...state };
  delete out.hide;
  return out;
}

type FieldNode = { props?: { data?: { fields?: unknown } & Record<string, unknown> } & Record<string, unknown> } & Record<string, unknown>;

/** The table node with the hidden columns left out of its `fields`. The input is not changed. A node with no field list comes back as it is. */
export function hideNode<T extends FieldNode>(node: T, hidden: readonly string[]): T {
  const data = node?.props?.data;
  const fields = data?.fields;
  if (!data || !Array.isArray(fields) || hidden.length === 0) return node;
  const kept = visibleFields(fields as { key: string }[], hidden);
  if (kept.length === fields.length) return node;
  return { ...node, props: { ...node.props, data: { ...data, fields: kept } } };
}
