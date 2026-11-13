/**
 * The model of the Studio list bar (STU-AF4, `ai_studio.md` §5.5 and the AF-4 row).
 * Pure: no React, no I/O.
 *
 * The bar sits over the table or the board of a page. It holds a search box, one chip for each
 * field that the door can filter, a sort, a layout switch and, on a board, a group. This file
 * maps those choices to the `list` input of the door and to and from the URL (`pageState.ts`).
 *
 * **One grammar.** `acb_common/studio_query.py` owns the ops of each field type and the hot
 * rule. This file copies the closed table `CHIP_TYPES` and `SORT_TYPES`. A parity test reads the
 * Python source, so the two cannot drift. The store refuses what the grammar refuses, so a chip
 * is a courtesy and never the guard.
 *
 * **The hot rule.** Only a hot field filters and sorts. A field that is not hot gets no chip and
 * no sort. A field type that the door cannot filter (a many field, a long text, a rating) gets no
 * chip either. A money field gets none: its value is a whole number of minor units with no
 * currency here, so a member could not type it right.
 *
 * **The search.** The door has no full-text search. The box filters with `contains` on the title
 * field of the object: its first hot text or email field. No such field means no box.
 *
 * **The URL.** Chips live in `filters` (`f.<field>=<value>`). The search, the sort, the layout and
 * the group live in `q`, `sort`, `layout` and `group` of `PageState`. A value that names no chip
 * is ignored, so a link cannot put a field in the query that the bar does not offer.
 *
 * **The group.** A group is the field that makes the lanes of a board. A table does not group:
 * the door has no group read for rows, and the loaded page is at most 100 records.
 */
import { ANY } from "@/lib/filterModel";

import type { PageState } from "./pageState";

/** The most conditions of one filter. `studio_query.MAX_FILTERS` holds the same number. */
export const MAX_CONDITIONS = 8;

export type ChipKind = "text" | "select" | "checkbox" | "number" | "date";
export type Layout = "table" | "board";

/** The field types that take a chip, and the chip of each. A type that is not here has none. */
export const CHIP_TYPES: Readonly<Record<string, ChipKind>> = {
  text: "text",
  email: "text",
  select: "select",
  checkbox: "checkbox",
  number: "number",
  date: "date",
  datetime: "date",
};

/** The field types that take a sort. A checkbox has no index, an id has no order. */
export const SORT_TYPES: ReadonlySet<string> = new Set(["text", "email", "select", "number", "money", "date", "datetime"]);

/** One field of an object, as the gateway gives it. `hot` is the index flag. */
export interface BarField {
  key: string;
  type: string;
  label?: string | null;
  many?: boolean;
  hot?: boolean;
  options?: readonly string[] | null;
  lines?: string | null;
}

export interface Chip {
  key: string;
  label: string;
  kind: ChipKind;
  /** The options of a select chip. */
  options: string[];
}

const labelOf = (f: BarField): string => (typeof f.label === "string" && f.label.trim() ? f.label.trim() : f.key);
const usable = (f: BarField): boolean => f.hot === true && f.many !== true && typeof f.lines !== "string";

/** The chip of one field, or `null` when the door cannot filter it. */
export function chipFor(f: BarField): Chip | null {
  const kind = CHIP_TYPES[f.type];
  if (!kind || !usable(f)) return null;
  const options = f.type === "select" ? [...(f.options ?? [])] : [];
  if (kind === "select" && options.length === 0) return null;
  return { key: f.key, label: labelOf(f), kind, options };
}

/** The title field that the search box filters, or `null`. */
export function searchKeyOf(fields: readonly BarField[]): string | null {
  return fields.find((f) => (f.type === "text" || f.type === "email") && usable(f))?.key ?? null;
}

/** The chips of an object, in field order. The search field has no chip, because the box covers it. */
export function chipsOf(fields: readonly BarField[]): Chip[] {
  const search = searchKeyOf(fields);
  return fields.filter((f) => f.key !== search).map(chipFor).filter((c): c is Chip => c !== null);
}

/** The fields that a sort takes. */
export function sortOptions(fields: readonly BarField[]): { key: string; label: string }[] {
  return fields.filter((f) => SORT_TYPES.has(f.type) && usable(f)).map((f) => ({ key: f.key, label: labelOf(f) }));
}

/**
 * The layouts of a view: the table always, and the board when the object has a select field that
 * can make lanes. One layout means no switch. Gallery and calendar join when D19 and D5 are built.
 */
export function layoutsFor(laneFields: readonly string[]): Layout[] {
  return laneFields.length > 0 ? ["table", "board"] : ["table"];
}

// --- the chip values ----------------------------------------------------

/** A range value: `from..to`. An end may be empty, and two empty ends make no value. */
export function rangeValue(from: string, to: string): string {
  return from === "" && to === "" ? ANY : `${from}..${to}`;
}

export function parseRange(value: string | undefined): { from: string; to: string } {
  const i = (value ?? "").indexOf("..");
  if (i < 0) return { from: "", to: "" };
  return { from: value!.slice(0, i), to: value!.slice(i + 2) };
}

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const NUMBER = /^-?\d+(\.\d+)?$/;

/** What the member picked in the bar. Read from the URL state. */
export interface BarState {
  q: string;
  filters: Readonly<Record<string, string>>;
  sort: string | null;
  layout: Layout | null;
  group: string | null;
}

export function barOf(s: PageState): BarState {
  return { q: s.q ?? "", filters: s.filters, sort: s.sort ?? null, layout: s.layout ?? null, group: s.group ?? null };
}

/** The `list` input that the bar adds: a filter and a sort. Never throws. */
export interface ListArgs {
  filter?: { field: string; op: string; value: unknown }[];
  sort?: { field: string; dir: "asc" | "desc" };
}

/**
 * The door input of the bar's choices. Search first, then each chip in field order, at most
 * `MAX_CONDITIONS`. A choice that its chip cannot hold (an option that is gone, a bad number, a bad
 * day) is dropped, so a hostile URL gives a valid query.
 */
export function toListArgs(fields: readonly BarField[], bar: BarState): ListArgs {
  const filter: NonNullable<ListArgs["filter"]> = [];
  const search = searchKeyOf(fields);
  const q = bar.q.trim();
  if (search && q) filter.push({ field: search, op: "contains", value: q });
  for (const chip of chipsOf(fields)) {
    const v = bar.filters[chip.key];
    if (!v) continue;
    switch (chip.kind) {
      case "text":
        filter.push({ field: chip.key, op: "contains", value: v });
        break;
      case "select":
        if (chip.options.includes(v)) filter.push({ field: chip.key, op: "eq", value: v });
        break;
      case "checkbox":
        if (v === "yes" || v === "no") filter.push({ field: chip.key, op: "eq", value: v === "yes" });
        break;
      case "number": {
        const { from, to } = parseRange(v);
        if (NUMBER.test(from)) filter.push({ field: chip.key, op: "gte", value: Number(from) });
        if (NUMBER.test(to)) filter.push({ field: chip.key, op: "lte", value: Number(to) });
        break;
      }
      case "date": {
        const { from, to } = parseRange(v);
        if (DAY.test(from)) filter.push({ field: chip.key, op: "gte", value: from });
        if (DAY.test(to)) filter.push({ field: chip.key, op: "lte", value: to });
        break;
      }
    }
  }
  const out: ListArgs = {};
  if (filter.length) out.filter = filter.slice(0, MAX_CONDITIONS);
  if (bar.sort) {
    const desc = bar.sort.startsWith("-");
    const key = desc ? bar.sort.slice(1) : bar.sort;
    if (sortOptions(fields).some((o) => o.key === key)) out.sort = { field: key, dir: desc ? "desc" : "asc" };
  }
  return out;
}

/** How many choices the bar holds, for the "Clear" control. Sort and layout are not filters. */
export function activeCount(fields: readonly BarField[], bar: BarState): number {
  const args = toListArgs(fields, bar);
  const keys = new Set((args.filter ?? []).map((c) => c.field));
  return keys.size;
}

/** The state with every filter of the bar cleared. The sort and the layout stay. */
export function clearedBar(s: PageState): PageState {
  const { q: _q, ...rest } = s; // eslint-disable-line @typescript-eslint/no-unused-vars
  return { ...rest, filters: {} };
}

/** Set one chip. An empty value clears it. */
export function withFilter(s: PageState, key: string, value: string): PageState {
  const filters = { ...s.filters };
  if (value) filters[key] = value;
  else delete filters[key];
  return { ...s, filters };
}

export function withQ(s: PageState, q: string): PageState {
  const { q: _q, ...rest } = s; // eslint-disable-line @typescript-eslint/no-unused-vars
  const next = q.trim();
  return next ? { ...rest, q: next } : rest;
}

export function withSort(s: PageState, sort: string | null): PageState {
  const { sort: _s, ...rest } = s; // eslint-disable-line @typescript-eslint/no-unused-vars
  return sort ? { ...rest, sort } : rest;
}

export function withLayout(s: PageState, layout: Layout | null): PageState {
  const { layout: _l, ...rest } = s; // eslint-disable-line @typescript-eslint/no-unused-vars
  return layout ? { ...rest, layout } : rest;
}

export function withGroup(s: PageState, group: string | null): PageState {
  const { group: _g, ...rest } = s; // eslint-disable-line @typescript-eslint/no-unused-vars
  return group ? { ...rest, group } : rest;
}

// --- the target of the bar ------------------------------------------------

/** The view that the bar acts on: the first table or board of the page that reads an object. */
export function barTarget<V extends { id: string; block: string }>(
  cells: readonly { view: string }[],
  views: readonly V[],
  objectOf: (view: V) => string | null,
): { view: V; object: string } | null {
  for (const c of cells) {
    const v = views.find((x) => x.id === c.view);
    if (!v || (v.block !== "table" && v.block !== "board")) continue;
    const object = objectOf(v);
    if (object) return { view: v, object };
  }
  return null;
}

/**
 * Does the list show the "No match" line? Only when the bar holds a filter and the read gave no rows.
 * With no filter the block keeps its own "No <Object> yet".
 */
export function showNoMatch(rows: readonly unknown[] | null | undefined, active: number): boolean {
  return Array.isArray(rows) && rows.length === 0 && active > 0;
}
