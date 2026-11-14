/**
 * The layout edits of a page (STU-AF11a). Pure: no React, no context, no I/O.
 *
 * Each function takes a manifest and returns a manifest. It never throws and never changes its
 * input. For a bad request it returns the INPUT, the same reference, so a caller tests `next === m`.
 * The canvas (AF-11b) calls these. The one validator stays `_check_pages` in
 * `acb_common/studio_manifest.py`. These functions only make results that it accepts.
 *
 * What `_check_pages` says about an empty list: a page needs `tabs` or `cells`. So a page-level
 * `cells` list may never be empty. A tab with no cells passes, because the check never reads it.
 * This file follows the server. A move or a remove that empties a page-level list is refused.
 * A move that empties a tab is allowed.
 *
 * Cell order is the list index and nothing else. There is no row and no column.
 */

import type { CellDef, PageDef, StudioManifest, TabDef } from "./manifest";
import { PAGE_CAPS } from "./pageModel";

/** Where a cell sits. `tabId` is null for the `cells` of a page with no tabs. */
export type Loc = { tabId: string | null; index: number };

/** A page with no `cols` has two. The server default is the same. */
const DEFAULT_COLS = 2;

function pageIndex(m: StudioManifest, pageId: string): number {
  return Array.isArray(m.pages) ? m.pages.findIndex((p) => p && p.id === pageId) : -1;
}

function colsOf(page: PageDef): number {
  return typeof page.cols === "number" ? page.cols : DEFAULT_COLS;
}

function tabbed(page: PageDef): boolean {
  return Array.isArray(page.tabs) && page.tabs.length > 0;
}

/** A page that has tabs and cells both is not valid. No edit works on it. */
function broken(page: PageDef): boolean {
  return tabbed(page) && Array.isArray(page.cells) && page.cells.length > 0;
}

/** The cell list at a place. Null when the page, the tab or the kind of list does not fit. */
function listAt(page: PageDef, tabId: string | null): CellDef[] | null {
  if (broken(page)) return null;
  if (tabId === null) return tabbed(page) ? null : Array.isArray(page.cells) ? page.cells : null;
  if (!tabbed(page)) return null;
  const tab = (page.tabs as TabDef[]).find((t) => t && t.id === tabId);
  return tab && Array.isArray(tab.cells) ? tab.cells : null;
}

/** A copy of the manifest where one list holds new cells. */
function withList(m: StudioManifest, pi: number, tabId: string | null, cells: CellDef[]): StudioManifest {
  const pages = (m.pages as PageDef[]).slice();
  const page = pages[pi];
  pages[pi] =
    tabId === null ? { ...page, cells } : { ...page, tabs: (page.tabs as TabDef[]).map((t) => (t.id === tabId ? { ...t, cells } : t)) };
  return { ...m, pages };
}

/** A copy of the manifest where two tabs hold new cells. */
function withTwoLists(m: StudioManifest, pi: number, a: [string, CellDef[]], b: [string, CellDef[]]): StudioManifest {
  const pages = (m.pages as PageDef[]).slice();
  const page = pages[pi];
  pages[pi] = {
    ...page,
    tabs: (page.tabs as TabDef[]).map((t) => (t.id === a[0] ? { ...t, cells: a[1] } : t.id === b[0] ? { ...t, cells: b[1] } : t)),
  };
  return { ...m, pages };
}

function isIndex(n: unknown): n is number {
  return typeof n === "number" && Number.isInteger(n) && n >= 0;
}

/** Free slots in one list, up to the cap of a list. An unknown place has none. */
export function cellCapRoom(m: StudioManifest, pageId: string, tabId: string | null): number {
  const pi = pageIndex(m, pageId);
  if (pi < 0) return 0;
  const list = listAt((m.pages as PageDef[])[pi], tabId);
  return list ? Math.max(0, PAGE_CAPS.cellsPerList - list.length) : 0;
}

/**
 * Move a cell inside one list, or from one tab to another tab of the same page.
 * `to.index` is the insert index in the list after the cell leaves it. It is clamped into 0..length.
 */
export function moveCell(m: StudioManifest, pageId: string, from: Loc, to: Loc): StudioManifest {
  const pi = pageIndex(m, pageId);
  if (pi < 0 || !from || !to) return m;
  const page = (m.pages as PageDef[])[pi];
  const src = listAt(page, from.tabId);
  const dst = listAt(page, to.tabId);
  if (!src || !dst || !isIndex(from.index) || from.index >= src.length || !isIndex(to.index)) return m;
  const cell = src[from.index];
  if (from.tabId === to.tabId) {
    const rest = src.filter((_, i) => i !== from.index);
    const at = Math.min(to.index, rest.length);
    if (at === from.index) return m;
    return withList(m, pi, from.tabId, [...rest.slice(0, at), cell, ...rest.slice(at)]);
  }
  // Two lists means two tabs. A page-level list has no second list to go to.
  if (from.tabId === null || to.tabId === null) return m;
  if (dst.length >= PAGE_CAPS.cellsPerList) return m;
  const at = Math.min(to.index, dst.length);
  return withTwoLists(
    m,
    pi,
    [from.tabId, src.filter((_, i) => i !== from.index)],
    [to.tabId, [...dst.slice(0, at), cell, ...dst.slice(at)]],
  );
}

/** Move a cell to the end of another tab. It never makes a tab. That is the form of AF-8. */
export function moveCellToTab(m: StudioManifest, pageId: string, from: Loc, toTabId: string): StudioManifest {
  const pi = pageIndex(m, pageId);
  if (pi < 0 || !from || from.tabId === null || typeof toTabId !== "string") return m;
  const dst = listAt((m.pages as PageDef[])[pi], toTabId);
  if (!dst) return m;
  return moveCell(m, pageId, from, { tabId: toTabId, index: dst.length });
}

/** Set the span of a cell. `null` clears it. A span wider than the page `cols` is refused, not clamped. */
export function setCellSpan(m: StudioManifest, pageId: string, loc: Loc, span: number | null): StudioManifest {
  const pi = pageIndex(m, pageId);
  if (pi < 0 || !loc) return m;
  const page = (m.pages as PageDef[])[pi];
  const list = listAt(page, loc.tabId);
  if (!list || !isIndex(loc.index) || loc.index >= list.length) return m;
  const cell = list[loc.index];
  let next: CellDef;
  if (span === null) {
    if (cell.span === undefined || cell.span === null) return m;
    const { span: _gone, ...rest } = cell;
    next = rest as CellDef;
  } else {
    if (typeof span !== "number" || !Number.isInteger(span) || span < 1) return m;
    if (span > Math.min(colsOf(page), PAGE_CAPS.maxCols) || span === cell.span) return m;
    next = { ...cell, span };
  }
  return withList(m, pi, loc.tabId, list.map((c, i) => (i === loc.index ? next : c)));
}

/** Put a new cell at an index. The view must be declared. A view may sit in many cells. */
export function addCell(m: StudioManifest, pageId: string, loc: Loc, viewId: string): StudioManifest {
  const pi = pageIndex(m, pageId);
  if (pi < 0 || !loc) return m;
  const list = listAt((m.pages as PageDef[])[pi], loc.tabId);
  if (!list || !isIndex(loc.index) || typeof viewId !== "string") return m;
  if (!Array.isArray(m.views) || !m.views.some((v) => v && v.id === viewId)) return m;
  if (list.length >= PAGE_CAPS.cellsPerList) return m;
  const at = Math.min(loc.index, list.length);
  return withList(m, pi, loc.tabId, [...list.slice(0, at), { view: viewId }, ...list.slice(at)]);
}

/** Take a cell out. A page-level list keeps one cell at least, because the server needs it. */
export function removeCell(m: StudioManifest, pageId: string, loc: Loc): StudioManifest {
  const pi = pageIndex(m, pageId);
  if (pi < 0 || !loc) return m;
  const list = listAt((m.pages as PageDef[])[pi], loc.tabId);
  if (!list || !isIndex(loc.index) || loc.index >= list.length) return m;
  if (loc.tabId === null && list.length === 1) return m;
  return withList(m, pi, loc.tabId, list.filter((_, i) => i !== loc.index));
}

/** A page whose cells span at most `cols`. It returns the same page when no span is too wide. */
export function clampSpans(page: PageDef): PageDef {
  const cols = Math.min(colsOf(page), PAGE_CAPS.maxCols);
  const wide = (c: CellDef) => c && typeof c.span === "number" && c.span > cols;
  const fix = (cells: CellDef[] | undefined): CellDef[] | undefined =>
    Array.isArray(cells) && cells.some(wide) ? cells.map((c) => (wide(c) ? { ...c, span: cols } : c)) : cells;
  const cells = fix(page.cells);
  const tabs = Array.isArray(page.tabs)
    ? page.tabs.map((t) => {
        const tc = fix(t.cells);
        return tc === t.cells ? t : { ...t, cells: tc as CellDef[] };
      })
    : page.tabs;
  const sameTabs = !Array.isArray(tabs) || tabs.every((t, i) => t === (page.tabs as TabDef[])[i]);
  if (cells === page.cells && sameTabs) return page;
  return { ...page, ...(cells !== page.cells ? { cells } : {}), ...(Array.isArray(tabs) ? { tabs } : {}) };
}
