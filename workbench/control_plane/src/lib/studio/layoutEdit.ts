/**
 * The edits of a page preset (STU-LY-8b, `studio_layouts.md` section 6). Pure: no React, no I/O.
 *
 * The Pages tab calls these. Each one works on `unknown`, keeps each key it does not know, never throws,
 * never changes its input, and returns the SAME object for a request that it refuses. The one validator
 * stays `_check_layout_page` in `studio_manifest.py`: these functions only try not to make a draft that
 * it refuses for a reason that the screen could have said first.
 *
 * - `setPageLayout` turns a grid page into a preset page, or back. It moves the views that the page holds
 *   into the regions that fit them, by block type, and it drops nothing silently: a view that fits no
 *   region is named in `PRESET_DROPS_NOTE`.
 * - `setRegionViews` sets the views of one region, checked against the cap and the block type.
 * - A change that breaks a rail count row that held is refused (`keepCounts`).
 */
import { isObj } from "./frameEdit";
import { LAYOUTS, PRESET_REGIONS, type LayoutName } from "./manifest";
import { PAGE_CAPS } from "./pageModel";
import { keepCounts } from "./tabEdit";

type Obj = Record<string, unknown>;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);

export const FREE_GRID = "";

/** What the picker offers: the free grid, then each preset. `label` is the sentence of the choice. */
export const PRESET_LABELS: Readonly<Record<LayoutName, string>> = {
  table_workspace: "Table workspace",
  list_detail: "List and detail",
  board: "Board",
  dashboard: "Dashboard",
  work_queue: "Work queue",
  approval_queue: "Approval queue",
};

export const GRID_BACK_WHY = "A page needs a view. Put a view in a region before you go back to the free grid.";
export const PRESET_DROPS_NOTE = "A view that fits no region of the preset is left out of the page.";

export function presetOptions(): { value: string; label: string }[] {
  return [{ value: FREE_GRID, label: "Free grid" }, ...LAYOUTS.map((l) => ({ value: l, label: PRESET_LABELS[l] }))];
}

function pageAt(m: unknown, id: string): { m: Obj; i: number; page: Obj } | null {
  if (!isObj(m) || !Array.isArray(m.pages) || typeof id !== "string") return null;
  const i = m.pages.findIndex((p) => isObj(p) && str(p.id) === id);
  return i < 0 ? null : { m, i, page: m.pages[i] as Obj };
}

const withPage = (at: { m: Obj; i: number }, page: Obj): Obj => ({
  ...at.m, pages: (at.m.pages as unknown[]).map((p, i) => (i === at.i ? page : p)),
});

/** The view ids that a grid page holds, in screen order: its cells, then each tab, then the side view. */
function gridViews(page: Obj): string[] {
  const ids = [...list(page.cells), ...list(page.tabs).flatMap((t) => list(t.cells))].map((c) => str(c.view));
  if (isObj(page.side) && str(page.side.view)) ids.push(str(page.side.view));
  return ids.filter(Boolean);
}

/** The block of each declared view. */
const blocksOf = (m: Obj): Map<string, string> => new Map(list(m.views).map((v) => [str(v.id), str(v.block)]));

/**
 * Put views into the regions of a preset, in the order of the table: a view goes to the first region that
 * takes its block and still has room. A view that fits nowhere is dropped, and the count says how many.
 */
export function fillRegions(
  layout: LayoutName, viewIds: readonly string[], blocks: ReadonlyMap<string, string>,
): { regions: Record<string, string[]>; dropped: number } {
  const rules = PRESET_REGIONS[layout];
  const regions: Record<string, string[]> = Object.fromEntries(Object.keys(rules).map((k) => [k, []]));
  let dropped = 0;
  const seen = new Set<string>();
  for (const id of viewIds) {
    if (seen.has(id)) continue;
    seen.add(id);
    const block = blocks.get(id);
    const slot = Object.entries(rules).find(([name, r]) => r.block === block && regions[name].length < r.cap);
    if (slot) regions[slot[0]].push(id);
    else dropped += 1;
  }
  return { regions, dropped };
}

/**
 * Turn a page into a preset page, or back to the free grid (`layout` null).
 * - To a preset: the grid goes (cells, tabs, side), the views move into the regions that fit. Refused for a
 *   preset that is not in the closed list.
 * - Back to the grid: the views of the regions become the cells, one column wide each. Refused when the page
 *   holds no view, because a page needs tabs or cells.
 */
export function setPageLayout(m: unknown, pageId: string, layout: LayoutName | null): unknown {
  const at = pageAt(m, pageId);
  if (!at) return m;
  const current = str(at.page.layout);
  if (layout === null) {
    if (!current) return m;
    const regions = isObj(at.page.regions) ? at.page.regions : {};
    const ids = Object.values(regions).flatMap((v) => (Array.isArray(v) ? v.map(str).filter(Boolean) : []));
    if (ids.length === 0) return m;
    const cells = ids.slice(0, PAGE_CAPS.cellsPerList).map((view) => ({ view }));
    const { layout: _l, regions: _r, ...rest } = at.page;
    void _l; void _r;
    return keepCounts(m, withPage(at, { ...rest, cells }));
  }
  if (!(LAYOUTS as readonly string[]).includes(layout)) return m;
  if (current === layout) return m;
  const source = current
    ? Object.values(isObj(at.page.regions) ? at.page.regions : {}).flatMap((v) => (Array.isArray(v) ? v.map(str).filter(Boolean) : []))
    : gridViews(at.page);
  const { regions } = fillRegions(layout, source, blocksOf(at.m));
  const { tabs: _t, cells: _c, side: _s, ...rest } = at.page;
  void _t; void _c; void _s;
  return keepCounts(m, withPage(at, { ...rest, layout, regions }));
}

/** The views that one region may take: the declared views of its block, not yet in the region. */
export function regionChoices(m: unknown, pageId: string, region: string): string[] {
  const at = pageAt(m, pageId);
  const layout = at ? str(at.page.layout) : "";
  if (!at || !(LAYOUTS as readonly string[]).includes(layout)) return [];
  const rule = PRESET_REGIONS[layout as LayoutName][region];
  if (!rule) return [];
  const held = new Set(Array.isArray((isObj(at.page.regions) ? at.page.regions : {})[region])
    ? ((at.page.regions as Obj)[region] as unknown[]).map(str) : []);
  return list(at.m.views).filter((v) => str(v.block) === rule.block && !held.has(str(v.id))).map((v) => str(v.id));
}

/**
 * Set the views of one region of a preset page. Refused (the same object comes back) for a page with no preset,
 * a region that the preset does not name, more views than the cap, a view that is not declared, or a view of
 * another block. An empty list is allowed for an optional region only.
 */
export function setRegionViews(m: unknown, pageId: string, region: string, ids: readonly string[]): unknown {
  const at = pageAt(m, pageId);
  if (!at || !Array.isArray(ids)) return m;
  const layout = str(at.page.layout);
  if (!(LAYOUTS as readonly string[]).includes(layout)) return m;
  const rule = PRESET_REGIONS[layout as LayoutName][region];
  if (!rule) return m;
  const clean = ids.filter((x): x is string => typeof x === "string" && x !== "");
  if (clean.length > rule.cap || (clean.length === 0 && rule.required)) return m;
  const blocks = blocksOf(at.m);
  if (clean.some((id) => blocks.get(id) !== rule.block)) return m;
  if (new Set(clean).size !== clean.length) return m;
  const regions = { ...(isObj(at.page.regions) ? at.page.regions : {}), [region]: clean };
  return keepCounts(m, withPage(at, { ...at.page, regions }));
}
