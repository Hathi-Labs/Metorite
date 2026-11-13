/**
 * The page model of a Studio app (STU-D11a). Pure: no React, no context, no I/O.
 *
 * The one validator of a manifest is the pydantic model in `acb_common/studio_manifest.py`.
 * This reader does not validate a second way. It assumes a manifest that A2 passed. It drops
 * each part that A2 refuses, the way `parseFields` in `blocks.ts` does, and it never repairs:
 * it does not clamp a span and it does not invent a default for a bad part. The renderer
 * (STU-D11b) draws the page that `buildPage` returns.
 */

import type { PageDef, ViewDef } from "./manifest";

/** The caps of A2. `pageModel.test.ts` holds them equal to the `maxItems` of the schema file. */
export const PAGE_CAPS = { pages: 8, tabsPerPage: 8, cellsPerList: 12, maxCols: 4 } as const;

const DEFAULT_COLS = 2;
const DASHED = /^[a-z][a-z0-9_-]{0,47}$/;

export interface CellModel {
  /** The id of a view. */
  view: string;
  /** Columns the cell spans, 1 to the page `cols`. */
  span: number;
}

export interface TabModel {
  id: string;
  title: string;
  cells: CellModel[];
}

export interface PageModel {
  id: string;
  title: string | null;
  cols: number;
  /** Empty when the page has no tabs. */
  tabs: TabModel[];
  /** The cells of a page with no tabs. Empty when the page has tabs. */
  cells: CellModel[];
  /** The view that opens when a record is selected, or `null`. */
  side: string | null;
}

/** The manifest parts that a page reads. `actions` and `objects` serve the default pages. */
export interface PageManifest {
  views?: readonly ViewDef[] | null;
  pages?: readonly PageDef[] | null;
  actions?: readonly { id: string; uses: string }[] | null;
  objects?: readonly { name: string; label?: string | null }[] | null;
}

export interface BuiltPage {
  page: PageModel;
  /** The active tab id, or `null` when the page has no tabs. */
  tab: string | null;
  /** The cells to draw now: those of the active tab, or those of the page. */
  cells: CellModel[];
  /** Every page of the manifest, or the default pages. The first is the default pick. */
  pages: PageModel[];
}

function obj(v: unknown): Record<string, unknown> | null {
  return typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function dashed(v: unknown): v is string {
  return typeof v === "string" && DASHED.test(v);
}

function readCols(v: unknown): number {
  return typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= PAGE_CAPS.maxCols ? v : DEFAULT_COLS;
}

/** Read a cell list. A bad cell, a span over `cols`, an unknown view and any cell over the cap are dropped. */
function readCells(v: unknown, cols: number, views: ReadonlySet<string> | null): CellModel[] {
  if (!Array.isArray(v)) return [];
  const out: CellModel[] = [];
  for (const raw of v) {
    const c = obj(raw);
    if (!c || !dashed(c.view) || (views && !views.has(c.view))) continue;
    let span = 1;
    if (c.span !== undefined && c.span !== null) {
      if (typeof c.span !== "number" || !Number.isInteger(c.span) || c.span < 1 || c.span > cols) continue;
      span = c.span;
    }
    out.push({ view: c.view, span });
    if (out.length >= PAGE_CAPS.cellsPerList) break;
  }
  return out;
}

/**
 * Read one page. Returns `null` when the page has no usable id, when it has both tabs and
 * cells, or when it has neither (A2 refuses each). `views`, when given, is the set of
 * declared view ids, and a cell of another view is dropped.
 */
export function readPage(def: unknown, views: ReadonlySet<string> | null = null): PageModel | null {
  const d = obj(def);
  if (!d || !dashed(d.id)) return null;
  const hasTabs = Array.isArray(d.tabs) && d.tabs.length > 0;
  const hasCells = Array.isArray(d.cells) && d.cells.length > 0;
  if (hasTabs === hasCells) return null;
  const cols = readCols(d.cols);
  const tabs: TabModel[] = [];
  if (Array.isArray(d.tabs)) {
    const seen = new Set<string>();
    for (const raw of d.tabs) {
      const t = obj(raw);
      if (!t || !dashed(t.id) || seen.has(t.id) || typeof t.title !== "string" || !t.title.trim()) continue;
      seen.add(t.id);
      tabs.push({ id: t.id, title: t.title, cells: readCells(t.cells, cols, views) });
      if (tabs.length >= PAGE_CAPS.tabsPerPage) break;
    }
  }
  const side = obj(d.side);
  const sideView = side && dashed(side.view) && (!views || views.has(side.view)) ? side.view : null;
  return {
    id: d.id,
    title: typeof d.title === "string" ? d.title : null,
    cols,
    tabs,
    cells: hasTabs ? [] : readCells(d.cells, cols, views),
    side: sideView,
  };
}

/** The page of a manifest with no `pages`: every view, one column, in order. The cell caps bind declared pages only. */
export function defaultPage(views: readonly Pick<ViewDef, "id">[] = []): PageModel {
  return {
    id: "default",
    title: null,
    cols: 1,
    tabs: [],
    cells: views
      .filter((v) => dashed(v.id))
      .map((v) => ({ view: v.id, span: 1 })),
    side: null,
  };
}

const OBJECT_OF_OP = /^object:([a-z][a-z0-9_]{0,47})\./;

/**
 * The object that a view reads: its `object`, else `source.object`, else the object of the
 * action that `source.action` names. `null` when the view reads none (a markdown view).
 */
export function viewObjectName(
  view: Pick<ViewDef, "object" | "source" | "submit">,
  actions: readonly { id: string; uses: string }[] = [],
): string | null {
  if (typeof view.object === "string" && view.object) return view.object;
  if (typeof view.source?.object === "string" && view.source.object) return view.source.object;
  for (const id of [view.source?.action, view.submit]) {
    const uses = id ? actions.find((a) => a.id === id)?.uses : undefined;
    const m = uses ? OBJECT_OF_OP.exec(uses) : null;
    if (m) return m[1];
  }
  return null;
}

/**
 * The default pages of a manifest with no `pages` (`ai_studio.md` §5.5 rule 3, STU-AF1).
 * Each object gets one page with its views. A chart or a stat view goes to a `dashboard`
 * page, with each view that reads no object. The generated pages keep the caps of A2: at most
 * 8 pages and 12 cells in a page. A manifest with no object view and no chart or stat gets
 * `defaultPage(views)`, the one page of every view.
 */
export function defaultPages(manifest: {
  views?: readonly ViewDef[] | null;
  actions?: readonly { id: string; uses: string }[] | null;
  objects?: readonly { name: string; label?: string | null }[] | null;
}): PageModel[] {
  const views = (manifest.views ?? []).filter((v) => dashed(v.id));
  const byObject = new Map<string, string[]>();
  const board: string[] = [];
  let hasAggregate = false;
  for (const v of views) {
    if (v.block === "chart" || v.block === "stat") {
      hasAggregate = true;
      board.push(v.id);
      continue;
    }
    const name = viewObjectName(v, manifest.actions ?? []);
    if (name && dashed(name)) byObject.set(name, [...(byObject.get(name) ?? []), v.id]);
    else board.push(v.id);
  }
  if (byObject.size === 0 && !hasAggregate) return [defaultPage(views)];
  const cells = (ids: string[]): CellModel[] =>
    ids.slice(0, PAGE_CAPS.cellsPerList).map((view) => ({ view, span: 1 }));
  const pages: PageModel[] = [];
  for (const [name, ids] of byObject) {
    const label = manifest.objects?.find((o) => o.name === name)?.label;
    pages.push({ id: name, title: label || name, cols: 1, tabs: [], cells: cells(ids), side: null });
  }
  if (board.length > 0) {
    // An object that is named "dashboard" keeps its own page, and the dashboard takes the next id.
    pages.push({ id: byObject.has("dashboard") ? "dashboard-2" : "dashboard", title: "Dashboard", cols: 2, tabs: [], cells: cells(board), side: null });
  }
  return pages.slice(0, PAGE_CAPS.pages);
}

/** The tab to show: `tab` when the page declares it, else the first tab, else `null`. */
export function resolveTab(page: Pick<PageModel, "tabs">, tab: string | null | undefined): string | null {
  if (!page.tabs.length) return null;
  return page.tabs.find((t) => t.id === tab)?.id ?? page.tabs[0].id;
}

/**
 * Build the page that the renderer draws. `pageId` picks a page, and the first page of that id
 * wins (A2 refuses a repeated id, so a later one is dropped). The first page is the default pick. With no `pages` (or no readable page) the result is `defaultPage(views)`.
 */
export function buildPage(
  manifest: PageManifest,
  pageId?: string | null,
  tab?: string | null,
): BuiltPage {
  const views = manifest.views ?? [];
  const ids = new Set(views.map((v) => v.id));
  const pages = (Array.isArray(manifest.pages) ? manifest.pages : [])
    .slice(0, PAGE_CAPS.pages)
    .map((p) => readPage(p, ids))
    .filter((p): p is PageModel => p !== null);
  // With no readable page the manifest gets the default pages (STU-AF1).
  const all = pages.length > 0 ? pages : defaultPages({ ...manifest, views });
  const page = all.find((p) => p.id === pageId) ?? all[0];
  const active = resolveTab(page, tab);
  const cells = active === null ? page.cells : (page.tabs.find((t) => t.id === active)?.cells ?? []);
  return { page, tab: active, cells, pages: all };
}
