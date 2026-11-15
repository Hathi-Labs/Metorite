/**
 * The model of the Pages tab of the Studio IDE (`ai_studio.md` §5.5, slice AF-8b-2).
 *
 * The tab draws the pages and the rail of the draft. This file holds the reads and the one act
 * rule, as pure functions, so a test can run them with no DOM. An edit is NEVER written here. Each
 * act calls a function of `frameEdit.ts`, and `applyFrame` sends the result only when it differs.
 */
import { colsFloor } from "./frameEdit";
import { MAX_NAV_ROWS, MAX_NAV_SECTIONS, type StudioManifest } from "./manifest";
import { cellCapRoom, type Loc } from "./pageLayout";
import { defaultPages, PAGE_CAPS, readPage, type PageModel } from "./pageModel";
import { removeCellWhy, removeTabWhy } from "./tabEdit";
import { DEFAULT_RAIL_ICON, railFor, type RailSectionModel } from "./railModel";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** The words under the tab title. The IDE Preview does not draw the frame, so the line says where to look. */
export const PAGES_TAB_LINE = "The pages and the rail of your app. Save, then open the app to see them.";
export const MATERIALIZE_NOTE = "Once you edit, a new view no longer joins a page by itself.";
export const COLS_HINT = "A cell is wider than that";
export const CAP_NOTE = `An app holds at most ${PAGE_CAPS.pages} pages.`;

/**
 * Send `fn(manifest)` to the page when it changed the manifest. A bad request gives back the same
 * object, and then nothing is sent. Returns true when a change was sent.
 */
export function applyFrame(
  manifest: unknown,
  onChange: (next: unknown) => void,
  fn: (m: unknown) => unknown,
): boolean {
  const next = fn(manifest);
  if (next === manifest) return false;
  onChange(next);
  return true;
}

export interface PageRow {
  id: string;
  /** The title, or "" when the page has none. */
  title: string;
  cols: number;
  /** The least columns that the cells allow. */
  floor: number;
  /** One line for each tab or cell, as read-only text. */
  parts: string[];
}

export interface PagesView {
  /** False when the manifest declares no page, and so the rows are the built-in pages. */
  declared: boolean;
  pages: PageRow[];
  full: boolean;
}

function partsOf(page: PageModel): string[] {
  if (page.tabs.length > 0) {
    return page.tabs.map((t) => `${t.title}: ${t.cells.map((c) => c.view).join(", ") || "no cell"}`);
  }
  return page.cells.map((c) => (c.span > 1 ? `${c.view} (spans ${c.span})` : c.view));
}

function rowOf(page: PageModel, raw: unknown): PageRow {
  return { id: page.id, title: page.title ?? "", cols: page.cols, floor: colsFloor(raw), parts: partsOf(page) };
}

/** The pages to draw. A manifest with no `pages` gets the built-in pages, which the tab shows read-only. */
export function pagesView(manifest: unknown): PagesView {
  const m = isObj(manifest) ? manifest : {};
  const declared = Array.isArray(m.pages) && m.pages.length > 0;
  if (!declared) {
    const built = defaultPages({
      views: list(m.views) as never, actions: list(m.actions) as never, objects: list(m.objects) as never,
    });
    return { declared: false, pages: built.map((p) => rowOf(p, p)), full: false };
  }
  const pages: PageRow[] = [];
  for (const raw of list(m.pages)) {
    const id = str(raw.id);
    if (!id) continue;
    const page = readPage(raw) ?? { id, title: str(raw.title) || null, cols: 2, tabs: [], cells: [], side: null };
    pages.push(rowOf(page, raw));
  }
  return { declared: true, pages, full: pages.length >= PAGE_CAPS.pages };
}

export interface RailRowView {
  page: string;
  /** The title or the id of the page that the row names. */
  pageName: string;
  label: string;
  icon: string;
}
export interface RailSectionView {
  title: string;
  rows: RailRowView[];
  full: boolean;
}
export interface RailView {
  /** False when the manifest has no `nav`, and so the sections are the default rail. */
  declared: boolean;
  sections: RailSectionView[];
  full: boolean;
}

/** The rail to draw. Indices match `nav` in the manifest, so an act can name a section and a row. */
export function railView(manifest: unknown): RailView {
  const m = isObj(manifest) ? manifest : {};
  const nav = list(m.nav);
  const pages = pagesView(manifest).pages;
  const name = (id: string) => pages.find((p) => p.id === id)?.title || id;
  if (!Array.isArray(m.nav) || nav.length === 0) {
    const fallback: RailSectionModel[] = railFor(pages.map((p) => ({ id: p.id, title: p.title })));
    return {
      declared: false, full: false,
      sections: fallback.map((s) => ({
        title: s.title,
        full: false,
        rows: s.rows.map((r) => ({ page: r.page, pageName: name(r.page), label: r.label, icon: r.icon })),
      })),
    };
  }
  return {
    declared: true,
    full: nav.length >= MAX_NAV_SECTIONS,
    sections: nav.map((s) => {
      const rows = list(s.rows).map((r) => ({
        page: str(r.page), pageName: name(str(r.page)),
        label: str(r.label), icon: str(r.icon) || DEFAULT_RAIL_ICON,
      }));
      return { title: str(s.title), rows, full: rows.length >= MAX_NAV_ROWS };
    }),
  };
}

/** The choices of columns: 1 to the cap. A choice under the widest cell is offered but not choosable. */
export function colsOptions(floor: number): { value: string; label: string; disabled?: boolean; hint?: string }[] {
  return Array.from({ length: PAGE_CAPS.maxCols }, (_, i) => {
    const n = i + 1;
    return n < floor ? { value: String(n), label: String(n), disabled: true, hint: COLS_HINT } : { value: String(n), label: String(n) };
  });
}

// ── The layout of one page (AF-8b-3b) ───────────────────────────────────────

export const CELL_CAP_NOTE = `A list holds at most ${PAGE_CAPS.cellsPerList} cells.`;
export const TAB_CAP_NOTE = `A page holds at most ${PAGE_CAPS.tabsPerPage} tabs.`;
export const BROKEN_NOTE = "This page has tabs and cells together. Fix it in the code.";
export const USE_TABS_HINT = "All current cells go into the first tab. A blank title gets a default name.";
export const TAB_NAME_HINT = "A blank title gets a default name.";

/** The title of a new tab: what the member typed, or "Tab N" when it is blank. `count` is the tabs now on the page. */
export function tabTitleOrDefault(typed: string, count: number): string {
  const t = typeof typed === "string" ? typed.trim() : "";
  const n = Number.isInteger(count) && count >= 0 ? count : 0;
  return t || `Tab ${n + 1}`;
}

/** The two places of one cell move, one step up (-1) or down (+1) inside its list. The editor calls `moveCell` with these only. */
export function moveCellArgs(tabId: string | null, index: number, delta: -1 | 1): { from: Loc; to: Loc } {
  return { from: { tabId, index }, to: { tabId, index: index + delta } };
}

export interface LayoutCell {
  index: number;
  view: string;
  /** The title of the view, or its id when the view has no title. */
  viewLabel: string;
  span: number | null;
  /** The sentence that says why Remove is refused, or null when it is allowed. */
  removeWhy: string | null;
  canUp: boolean;
  canDown: boolean;
}
export interface LayoutList {
  /** Null for the cells of a flat page. */
  tabId: string | null;
  title: string;
  cells: LayoutCell[];
  /** Free slots in this list. */
  room: number;
  /** Tab acts. Always false and null for the list of a flat page. */
  tabCanUp: boolean;
  tabCanDown: boolean;
  tabWhy: string | null;
}
export interface LayoutView {
  flat: boolean;
  cols: number;
  lists: LayoutList[];
  tabCount: number;
  tabFull: boolean;
  /** The page has tabs and cells together, or neither. */
  broken: boolean;
}

const EMPTY_LAYOUT: LayoutView = { flat: true, cols: 2, lists: [], tabCount: 0, tabFull: false, broken: false };

/** The views of the draft as choices: the id, and the title when the view has one. */
export function viewChoices(manifest: unknown): { id: string; label: string }[] {
  const m = isObj(manifest) ? manifest : {};
  return list(m.views).map((v) => ({ id: str(v.id), label: str(v.title).trim() || str(v.id) })).filter((v) => v.id);
}

/** The cells and tabs of one page, for the cells editor. Never throws. Junk gives an empty view. */
export function layoutView(
  manifest: unknown,
  pageId: string,
  views: { id: string; label: string }[] = viewChoices(manifest),
): LayoutView {
  try {
    const m = isObj(manifest) ? manifest : null;
    const page = m && Array.isArray(m.pages) ? list(m.pages).find((p) => str(p.id) === pageId) : undefined;
    if (!m || !page) return EMPTY_LAYOUT;
    const labels = new Map(views.map((v) => [v.id, v.label]));
    const cols = typeof page.cols === "number" && Number.isInteger(page.cols) && page.cols >= 1 && page.cols <= PAGE_CAPS.maxCols ? page.cols : 2;
    const tabs = Array.isArray(page.tabs) ? page.tabs : [];
    const flatCells = Array.isArray(page.cells) ? page.cells : [];
    const hasTabs = tabs.length > 0;
    const hasCells = flatCells.length > 0;
    if (hasTabs === hasCells) {
      return { flat: !hasTabs, cols, lists: [], tabCount: tabs.length, tabFull: tabs.length >= PAGE_CAPS.tabsPerPage, broken: true };
    }
    const typed = m as unknown as StudioManifest;
    const cellsOf = (raw: unknown[], tabId: string | null): LayoutCell[] => {
      // The index is the place in the RAW list, as `pageLayout` reads it. A junk entry is skipped in the output only.
      const out: LayoutCell[] = [];
      raw.forEach((c, index) => {
        if (!isObj(c)) return;
        out.push({
          index,
          view: str(c.view),
          viewLabel: labels.get(str(c.view)) || str(c.view),
          span: typeof c.span === "number" ? c.span : null,
          removeWhy: removeCellWhy(m, pageId, { tabId, index }),
          canUp: index > 0,
          canDown: index < raw.length - 1,
        });
      });
      return out;
    };
    if (!hasTabs) {
      return {
        flat: true, cols, tabCount: 0, tabFull: false, broken: false,
        lists: [{ tabId: null, title: "Cells", cells: cellsOf(flatCells, null), room: cellCapRoom(typed, pageId, null),
          tabCanUp: false, tabCanDown: false, tabWhy: null }],
      };
    }
    const real = tabs.filter(isObj);
    return {
      flat: false, cols, tabCount: real.length, tabFull: real.length >= PAGE_CAPS.tabsPerPage, broken: false,
      lists: real.map((t, i) => {
        const tabId = str(t.id);
        return {
          tabId, title: str(t.title) || tabId, cells: cellsOf(Array.isArray(t.cells) ? t.cells : [], tabId),
          room: cellCapRoom(typed, pageId, tabId), tabCanUp: i > 0, tabCanDown: i < real.length - 1,
          tabWhy: removeTabWhy(m, pageId, tabId),
        };
      }),
    };
  } catch {
    return EMPTY_LAYOUT;
  }
}
