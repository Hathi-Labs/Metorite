/**
 * The tab acts of a page, the adapter to the cell acts, and the guard of the rail counts
 * (`ai_studio.md` §5.5, slice AF-8b-3a).
 *
 * Every function here is pure. It never throws and never changes its input. A bad request returns
 * the SAME input object, so a caller tests `next === m`. The functions work on `unknown`, as
 * `frameEdit.ts` does, so each key that they do not know survives an edit.
 *
 * **The server rules that bind each result** (`studio_manifest.py`, `_check_pages`):
 * - A tab id matches `_DASHED` (`[a-z][a-z0-9_-]{0,47}`) and is unique in its page.
 * - A tab title is required and holds one non-space character. An empty tab is valid.
 * - A page has tabs OR cells, never both. A page needs one of them.
 * - A page holds at most `PAGE_CAPS.tabsPerPage` tabs and `PAGE_CAPS.cellsPerList` cells in a list.
 *
 * **The rail count.** A rail row with `count: true` needs a page that holds a table or board view
 * (AF-3a). The key may not exist on the server yet. So `countHolds` reads it from `unknown`. It is
 * true for a manifest with no such row, before and after AF-3a.
 */
import { MAX_LABEL, nameFrom } from "./draftEdit";
import { isObj, put, shifted } from "./frameEdit";
import type { StudioManifest } from "./manifest";
import { removeCell, type Loc } from "./pageLayout";
import { PAGE_CAPS } from "./pageModel";

type Obj = Record<string, unknown>;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const clean = (t: string): string => t.replace(/\s+/g, " ").trim().slice(0, MAX_LABEL);
const DASHED = /^[a-z][a-z0-9_-]{0,47}$/;

/** The sentence for a page that would have no layout. */
export const LAST_CELL_WHY = "A page needs one cell. Delete the page instead.";
/** The sentence for a removal that leaves a counted rail row with no table or board. */
export const COUNT_WHY = "The rail counts the rows of this page. Keep a table or board on it first.";

/** The block kinds that read the rows of an object. Only these give a count. */
const COUNT_BLOCKS: ReadonlySet<string> = new Set(["table", "board"]);

// ── The adapter ─────────────────────────────────────────────────────────────

/**
 * The ONE adapter to the cell acts of `pageLayout.ts`. It runs `fn` on a manifest object and
 * returns the input for junk. It returns the same reference when `fn` does. The cast lives here.
 * Warning for the UI: a bare `removeCell` through this adapter skips the rail count guard.
 * Call `removeCellGuarded` to remove a cell, never `layoutAct(..., removeCell)`.
 */
export function layoutAct(m: unknown, fn: (typed: StudioManifest) => StudioManifest): unknown {
  if (!isObj(m)) return m;
  try {
    return fn(m as unknown as StudioManifest);
  } catch {
    return m;
  }
}

// ── The count guard ─────────────────────────────────────────────────────────

const cellsOfPage = (page: Obj): Obj[] => [...list(page.cells), ...list(page.tabs).flatMap((t) => list(t.cells))];

/** True when the page holds a view of a counted block in its cells, in any tab, or in `side`. */
function pageCountable(m: Obj, page: Obj): boolean {
  const blocks = new Map(list(m.views).map((v) => [str(v.id), str(v.block)]));
  const ids = cellsOfPage(page).map((c) => str(c.view));
  if (isObj(page.side)) ids.push(str(page.side.view));
  return ids.some((id) => COUNT_BLOCKS.has(blocks.get(id) ?? ""));
}

/** The ids of the pages that a `count: true` row names and that hold no table or board. */
function brokenCountPages(m: unknown): Set<string> {
  const out = new Set<string>();
  if (!isObj(m) || !Array.isArray(m.nav)) return out;
  const pages = new Map(list(m.pages).map((p) => [str(p.id), p]));
  for (const section of list(m.nav)) {
    for (const row of list(section.rows)) {
      if (row.count !== true) continue;
      const page = pages.get(str(row.page));
      if (page && !pageCountable(m, page)) out.add(str(row.page));
    }
  }
  return out;
}

/** True when each rail row with `count: true` names a page that holds a table or board view. */
export function countHolds(m: unknown): boolean {
  return brokenCountPages(m).size === 0;
}

/**
 * `prev` when `next` breaks a count row that `prev` held. A row that `prev` already broke is not a
 * new refusal. So `next` comes back for it.
 */
export function keepCounts(prev: unknown, next: unknown): unknown {
  if (next === prev) return prev;
  const before = brokenCountPages(prev);
  for (const id of brokenCountPages(next)) if (!before.has(id)) return prev;
  return next;
}

// ── Pages and tabs ──────────────────────────────────────────────────────────

const pageAt = (m: unknown, id: string): { m: Obj; i: number; page: Obj } | null => {
  if (!isObj(m) || !Array.isArray(m.pages) || typeof id !== "string") return null;
  const i = m.pages.findIndex((p) => isObj(p) && str(p.id) === id);
  return i < 0 ? null : { m, i, page: m.pages[i] as Obj };
};

const withPage = (at: { m: Obj; i: number }, page: Obj): Obj => ({ ...at.m, pages: put(at.m.pages as unknown[], at.i, page) });

const hasCells = (page: Obj): boolean => Array.isArray(page.cells) && page.cells.length > 0;
const hasTabs = (page: Obj): boolean => Array.isArray(page.tabs) && page.tabs.length > 0;

/** The page with the key `k` deleted. */
function without(page: Obj, k: string): Obj {
  const { [k]: _gone, ...rest } = page;
  void _gone;
  return rest;
}

/**
 * A flat page, rewritten with one first tab that holds ALL its cells in order.
 * Null for a page that is not flat, or that has no cell, because an empty first tab is not allowed.
 */
export function tabsFromCells(page: unknown, tab: { id: string; title: string }): Obj | null {
  if (!isObj(page) || hasTabs(page) || !hasCells(page)) return null;
  const rest = without(without(page, "cells"), "tabs");
  return { ...rest, tabs: [{ id: tab.id, title: tab.title, cells: page.cells }] };
}

/**
 * The flat form of a page with ONE tab: its cells go back to the `cells` list and `tabs` goes.
 * Null for a page with more or fewer tabs, or when the tab holds no cell.
 */
export function cellsFromTabs(page: unknown): Obj | null {
  if (!isObj(page) || hasCells(page) || !Array.isArray(page.tabs) || page.tabs.length !== 1) return null;
  const tab = page.tabs[0];
  if (!isObj(tab) || !Array.isArray(tab.cells) || tab.cells.length === 0) return null;
  return { ...without(page, "tabs"), cells: tab.cells };
}

/**
 * Add a tab with a title. On a flat page it is the first-tab act: all current cells move into the
 * new tab in order, and `cells` goes. On a tabbed page it adds an empty tab at the end.
 * A blank title, the 9th tab, a page with both lists, and a page with neither are refused.
 */
export function addTab(m: unknown, pageId: string, title: string): unknown {
  const at = pageAt(m, pageId);
  if (!at || typeof title !== "string") return m;
  const shown = clean(title);
  if (!shown) return m;
  if (hasTabs(at.page) && hasCells(at.page)) return m;
  const taken = new Set(list(at.page.tabs).map((t) => str(t.id)));
  let id = nameFrom(shown, taken, "tab", "-");
  if (!DASHED.test(id)) id = nameFrom("tab", taken, "tab", "-");
  if (!DASHED.test(id)) return m;
  if (hasTabs(at.page)) {
    const tabs = at.page.tabs as unknown[];
    if (tabs.length >= PAGE_CAPS.tabsPerPage) return m;
    return withPage(at, { ...at.page, tabs: [...tabs, { id, title: shown, cells: [] }] });
  }
  const page = tabsFromCells(at.page, { id, title: shown });
  return page ? withPage(at, page) : m;
}

const tabAt = (m: unknown, pageId: string, tabId: string) => {
  const at = pageAt(m, pageId);
  if (!at || typeof tabId !== "string" || !hasTabs(at.page) || hasCells(at.page)) return null;
  const tabs = at.page.tabs as unknown[];
  const ti = tabs.findIndex((t) => isObj(t) && str(t.id) === tabId);
  return ti < 0 || !isObj(tabs[ti]) ? null : { ...at, tabs, ti, tab: tabs[ti] as Obj };
};

/** Set the title of a tab. The id stays. A blank title is refused. The same title returns the input. */
export function renameTab(m: unknown, pageId: string, tabId: string, title: string): unknown {
  const at = tabAt(m, pageId, tabId);
  if (!at || typeof title !== "string") return m;
  const shown = clean(title);
  if (!shown || shown === at.tab.title) return m;
  return withPage(at, { ...at.page, tabs: put(at.tabs, at.ti, { ...at.tab, title: shown }) });
}

/** The tab removal before the count guard. */
function removeTabRaw(m: unknown, pageId: string, tabId: string): unknown {
  const at = tabAt(m, pageId, tabId);
  if (!at) return m;
  if (at.tabs.length > 1) return withPage(at, { ...at.page, tabs: at.tabs.filter((_, j) => j !== at.ti) });
  const flat = cellsFromTabs(at.page);
  return flat ? withPage(at, flat) : m;
}

/**
 * Remove a tab. A tab that is not the last one goes with its cells, and the views stay in `views`.
 * The LAST tab returns its cells to the flat `cells` list and `tabs` goes. An EMPTY last tab is
 * refused, because the page would have no layout. A removal that leaves a counted rail row with
 * no table or board is refused too.
 */
export function removeTab(m: unknown, pageId: string, tabId: string): unknown {
  return keepCounts(m, removeTabRaw(m, pageId, tabId));
}

/** Why `removeTab` refuses, as one sentence for the member. Null when no sentence applies. */
export function removeTabWhy(m: unknown, pageId: string, tabId: string): string | null {
  const at = tabAt(m, pageId, tabId);
  if (!at) return null;
  if (at.tabs.length === 1 && (!Array.isArray(at.tab.cells) || at.tab.cells.length === 0)) return LAST_CELL_WHY;
  const raw = removeTabRaw(m, pageId, tabId);
  return raw !== m && keepCounts(m, raw) === m ? COUNT_WHY : null;
}

/** Move a tab up (`delta` below 0) or down. The order is the only change. Ends clamp. */
export function moveTab(m: unknown, pageId: string, tabId: string, delta: number): unknown {
  const at = tabAt(m, pageId, tabId);
  const tabs = at && shifted(at.tabs, at.ti, delta);
  return at && tabs ? withPage(at, { ...at.page, tabs }) : m;
}

// ── The cell removal ────────────────────────────────────────────────────────

/** Remove a cell. The count guard binds it, so it refuses a removal that leaves a counted row with no table or board. */
export function removeCellGuarded(m: unknown, pageId: string, loc: Loc): unknown {
  return keepCounts(m, layoutAct(m, (t) => removeCell(t, pageId, loc)));
}

/** Why a cell removal is refused, as one sentence. Null when it is allowed, or when the request is bad. */
export function removeCellWhy(m: unknown, pageId: string, loc: Loc): string | null {
  const at = pageAt(m, pageId);
  if (!at || !isObj(loc)) return null;
  if (loc.tabId === null && !hasTabs(at.page) && Array.isArray(at.page.cells) && at.page.cells.length === 1 && loc.index === 0) {
    return LAST_CELL_WHY;
  }
  const raw = layoutAct(m, (t) => removeCell(t, pageId, loc));
  return raw !== m && keepCounts(m, raw) === m ? COUNT_WHY : null;
}
