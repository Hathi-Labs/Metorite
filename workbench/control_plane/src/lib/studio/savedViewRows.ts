/**
 * The saved rows of a Studio list (STU-AF-10b-1, `ai_studio.md` AF-10b row).
 * Pure: no React, no I/O. It never throws, never changes its input and gives the same output for
 * the same input.
 *
 * It maps the page state of the list bar (AF-4, `filterBarModel.ts`) to the spec of a saved view
 * (AF-10a, `views.py`) and back. It also maps a wire view to the seam `SavedView`
 * (`lib/savedViews`), so `ViewChips` needs no new type.
 *
 * **One mapping.** `stateToSpec` calls `toListArgs`. This file does not copy the chip rules. The
 * inverse `specToState` builds a chip value, then asks `toListArgs` and `boundPageState` if they
 * keep it. A condition that no chip can show is dropped and counted.
 *
 * **What a spec stores.** The filter, the sort and the layout. The `group`, the `me` flag and the
 * `columns` are not stored. They keep their default when a member opens a view.
 */
import { dirtyState, stableJson, type SavedView } from "@/lib/savedViews";

import {
  MAX_CONDITIONS, barOf, chipsOf, rangeValue, searchKeyOf, toListArgs,
  type BarField, type BarState, type Chip,
} from "./filterBarModel";
import { boundPageState, type PageState } from "./pageState";

/** The page state that the functions read. Any key may be absent. */
export type PageStateLike = Partial<PageState>;
export type FieldLike = BarField;

export interface Cond {
  field: string;
  op: string;
  value: unknown;
}

/** The spec of a saved view, as `views.py` takes it. `columns` is read and never written here. */
export interface Spec {
  filter?: Cond[];
  sort?: { field: string; dir: "asc" | "desc" } | null;
  layout?: string;
  columns?: string[];
}

/** What `stateToSpec` writes. */
export interface StoredSpec {
  filter: Cond[];
  sort?: { field: string; dir: "asc" | "desc" };
  layout?: string;
}

const hasOwn = (o: object, k: string): boolean => Object.prototype.hasOwnProperty.call(o, k);
const isPlain = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

function barFrom(state: PageStateLike | null | undefined): BarState {
  const s = isPlain(state) ? (state as Record<string, unknown>) : {};
  const filters: Record<string, string> = {};
  if (isPlain(s.filters)) {
    for (const k of Object.keys(s.filters)) {
      const v = s.filters[k];
      if (typeof v === "string" && v) filters[k] = v;
    }
  }
  const layout = s.layout === "table" || s.layout === "board" ? s.layout : undefined;
  return barOf({
    selected: null,
    tab: null,
    filters,
    q: typeof s.q === "string" ? s.q : undefined,
    sort: typeof s.sort === "string" ? s.sort : undefined,
    layout,
  });
}

/**
 * The spec of the page state. The filter and the sort come from `toListArgs`, so the chip rules
 * stay in one place. The search text is a `contains` on the search key, as `toListArgs` emits it.
 */
export function stateToSpec(state: PageStateLike, fields: readonly FieldLike[]): StoredSpec {
  try {
    const bar = barFrom(state);
    const args = toListArgs(fields, bar);
    const out: StoredSpec = { filter: (args.filter ?? []).map((c) => ({ field: c.field, op: c.op, value: c.value })) };
    if (args.sort) out.sort = { field: args.sort.field, dir: args.sort.dir };
    if (bar.layout) out.layout = bar.layout;
    return out;
  } catch {
    return { filter: [] };
  }
}

// --- the inverse ------------------------------------------------------------

const emptyState = (): PageState => ({ selected: null, filters: {}, tab: null });
const BARE: BarState = { q: "", filters: {}, sort: null, layout: null, group: null };

/** The chip text of one condition, and the slot that it fills. `null` when no chip shows it. */
function fragment(chip: Chip, c: Cond): { slot: "v" | "from" | "to"; text: string } | null {
  const v = c.value;
  switch (chip.kind) {
    case "text":
      return c.op === "contains" && typeof v === "string" ? { slot: "v", text: v } : null;
    case "select":
      if (c.op === "eq" && typeof v === "string") return { slot: "v", text: v };
      if (c.op === "in" && Array.isArray(v) && v.length === 1 && typeof v[0] === "string") return { slot: "v", text: v[0] };
      return null;
    case "checkbox":
      return c.op === "eq" && typeof v === "boolean" ? { slot: "v", text: v ? "yes" : "no" } : null;
    case "number":
      if (typeof v !== "number" || !Number.isFinite(v)) return null;
      return c.op === "gte" ? { slot: "from", text: String(v) } : c.op === "lte" ? { slot: "to", text: String(v) } : null;
    case "date":
      if (typeof v !== "string") return null;
      return c.op === "gte" ? { slot: "from", text: v } : c.op === "lte" ? { slot: "to", text: v } : null;
  }
}

const textOf = (slot: "v" | "from" | "to", text: string): string =>
  slot === "v" ? text : slot === "from" ? rangeValue(text, "") : rangeValue("", text);

/** Does the bar keep this one condition, exactly as it stands? The bar decides, not this file. */
function kept(fields: readonly FieldLike[], key: string | null, c: Cond, text: string): boolean {
  const bar: BarState = key === null ? { ...BARE, q: text } : { ...BARE, filters: { [key]: text } };
  const bounded = boundPageState({ ...emptyState(), ...(key === null ? { q: text } : { filters: { [key]: text } }) });
  if (key === null ? bounded.q !== text : bounded.filters[key] !== text) return false;
  const got = toListArgs(fields, bar).filter ?? [];
  return got.length === 1 && got[0].field === c.field && got[0].op === c.op && stableJson(got[0].value) === stableJson(c.value);
}

/**
 * The page state of a spec, and how many parts of it no chip can show. Never throws. A `gte` and a
 * `lte` on one field make one range value. A hostile spec gives a valid empty state.
 */
export function specToState(spec: unknown, fields: readonly FieldLike[]): { state: PageState; dropped: number } {
  const state = emptyState();
  let dropped = 0;
  try {
    if (spec === null || spec === undefined) return { state, dropped };
    if (!isPlain(spec)) return { state, dropped: 1 };
    for (const k of Object.keys(spec)) {
      if (k !== "filter" && k !== "sort" && k !== "layout" && k !== "columns") dropped += 1;
    }
    const search = searchKeyOf(fields);
    const chips = new Map<string, Chip>(chipsOf(fields).map((c) => [c.key, c]));
    const slots = new Map<string, { v?: string; from?: string; to?: string }>();
    if (hasOwn(spec, "filter") && spec.filter !== null && spec.filter !== undefined) {
      const raw = spec.filter;
      if (!Array.isArray(raw)) {
        dropped += 1;
      } else {
        const items = raw.slice(0, MAX_CONDITIONS);
        dropped += raw.length - items.length;
        for (const item of items) {
          if (!isPlain(item) || typeof item.field !== "string" || typeof item.op !== "string") {
            dropped += 1;
            continue;
          }
          const c: Cond = { field: item.field, op: item.op, value: item.value };
          if (search !== null && c.field === search) {
            const ok = c.op === "contains" && typeof c.value === "string" && state.q === undefined && kept(fields, null, c, c.value);
            if (ok) state.q = c.value as string;
            else dropped += 1;
            continue;
          }
          const chip = chips.get(c.field);
          const frag = chip ? fragment(chip, c) : null;
          const slot = chip && frag ? slots.get(chip.key) ?? {} : null;
          if (!chip || !frag || !slot || slot[frag.slot] !== undefined || !kept(fields, chip.key, c.op === "in" ? { field: c.field, op: "eq", value: frag.text } : c, textOf(frag.slot, frag.text))) {
            dropped += 1;
            continue;
          }
          slot[frag.slot] = frag.text;
          slots.set(chip.key, slot);
        }
      }
    }
    for (const chip of chipsOf(fields)) {
      const s = slots.get(chip.key);
      if (!s) continue;
      if (s.v !== undefined) state.filters[chip.key] = s.v;
      else state.filters[chip.key] = rangeValue(s.from ?? "", s.to ?? "");
    }
    if (hasOwn(spec, "sort") && spec.sort !== null && spec.sort !== undefined) {
      const sort = spec.sort;
      const dir = isPlain(sort) && hasOwn(sort, "dir") ? sort.dir : "asc";
      const field = isPlain(sort) ? sort.field : undefined;
      const text = typeof field === "string" ? (dir === "desc" ? `-${field}` : field) : "";
      const ok = typeof field === "string" && (dir === "asc" || dir === "desc")
        && toListArgs(fields, { ...BARE, sort: text }).sort !== undefined && boundPageState({ ...emptyState(), sort: text }).sort === text;
      if (ok) state.sort = text;
      else dropped += 1;
    }
    if (hasOwn(spec, "layout") && spec.layout !== null && spec.layout !== undefined) {
      if (spec.layout === "table" || spec.layout === "board") state.layout = spec.layout;
      else dropped += 1;
    }
    return { state, dropped };
  } catch {
    return { state: emptyState(), dropped: dropped + 1 };
  }
}

// --- the wire view ----------------------------------------------------------

/** A view as `GET /api/studio/packages/{id}/views` gives it (`views.py` `_out`). */
export interface WireView {
  id: string;
  object: string;
  name: string;
  shared: boolean;
  mine: boolean;
  spec: unknown;
  position: number;
  dropped: number;
  created_at?: string | null;
  updated_at?: string | null;
}

/** The seam view, with the object, the spec and the dropped count that the server sent. */
export type StudioSavedView = SavedView & { object: string; spec: Spec; dropped: number };

/** The seam view of a wire view. Never throws. A bad part takes a safe value. */
export function toSeamView(wire: WireView): StudioSavedView {
  const w: Partial<WireView> = isPlain(wire) ? wire : {};
  return {
    id: typeof w.id === "string" ? w.id : "",
    name: typeof w.name === "string" ? w.name : "",
    shared: w.shared === true,
    mine: w.mine === true,
    position: typeof w.position === "number" && Number.isFinite(w.position) ? w.position : 0,
    object: typeof w.object === "string" ? w.object : "",
    spec: isPlain(w.spec) ? (w.spec as Spec) : {},
    dropped: typeof w.dropped === "number" && Number.isInteger(w.dropped) && w.dropped > 0 ? w.dropped : 0,
  };
}

/** The line under a view that holds conditions the viewer cannot read. Only a viewer sees it. */
export function droppedNote(n: number, isViewer: boolean): string | null {
  if (!isViewer || !Number.isInteger(n) || n <= 0) return null;
  return n === 1 ? "1 condition does not apply to you" : `${n} conditions do not apply to you`;
}

/** The views of one object, in the order given. */
export function viewsForObject<V extends { object: string }>(views: readonly V[] | null | undefined, object: string): V[] {
  return (views ?? []).filter((v) => v.object === object);
}

/**
 * The id of the view whose spec is the current state, or `null`. The spec of the view goes through
 * the same round trip, so key order and a part that the bar cannot show do not matter. The compare is
 * the seam's `dirtyState`.
 */
export function activeViewId(
  state: PageStateLike,
  views: readonly { id: string; spec: unknown }[] | null | undefined,
  fields: readonly FieldLike[],
): string | null {
  const screen = stateToSpec(state, fields);
  for (const v of views ?? []) {
    const saved = stateToSpec(specToState(v.spec, fields).state, fields);
    if (dirtyState({ state: saved }, { state: screen, blank: false }) === "clean") return v.id;
  }
  return null;
}
