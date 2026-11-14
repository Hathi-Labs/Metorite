/**
 * The direct edits of the frame of a Studio app: its pages and its rail (`ai_studio.md` §5.5, slice AF-8b-1).
 *
 * Every function here is pure. It takes a manifest, returns a new one, and never changes its input.
 * It never throws. A bad request (an unknown page, an index out of range, a cap that is full, a
 * manifest of a bad shape) returns the SAME input object. The caller can test `next === m`.
 *
 * The functions work on `unknown`, the way `draftEdit.ts` does, and not on the typed manifest. So an
 * edit keeps each key that it does not know. A row that carries a key of a later slice (such as the
 * `count` of a rail row) keeps it through every edit.
 *
 * **The server rules that bind each result** (`studio_manifest.py`, `_check_pages` and `_check_nav`):
 * - A page id is unique and matches `_DASHED`. A page needs tabs or cells, never both.
 * - A page title is optional, so a blank title deletes the key.
 * - A section title is required and unique. A section with no rows is valid.
 * - A row names a declared page. The same page twice in one section is valid.
 * - The caps are 8 pages, 4 sections and 12 rows in one section. `PAGE_CAPS` and `manifest.ts` hold them.
 * - A `nav` needs `pages`, because no row can name a default page that only the client draws.
 *
 * `checkDraft` never checks pages or nav, so these functions keep the result valid themselves.
 * The edit is only a draft. One save (`draftSave.ts`) sends it.
 */
import { dropNavRows, MAX_LABEL, nameFrom } from "./draftEdit";
import { MAX_NAV_ROWS, MAX_NAV_SECTIONS, NAV_ICONS } from "./manifest";
import { defaultPages, PAGE_CAPS } from "./pageModel";

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === "object" && v !== null && !Array.isArray(v);
const list = (v: unknown): Obj[] => (Array.isArray(v) ? v.filter(isObj) : []);
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const isIndex = (n: unknown, len: number): n is number => typeof n === "number" && Number.isInteger(n) && n >= 0 && n < len;
const clean = (t: string): string => t.replace(/\s+/g, " ").trim().slice(0, MAX_LABEL);
const DASHED = /^[a-z][a-z0-9_-]{0,47}$/;

/** The title of the section that holds the rows of a default rail. The server needs a title in each section. */
export const DEFAULT_SECTION_TITLE = "Pages";

/** The same array with item `i` replaced. */
const put = <T,>(xs: readonly T[], i: number, v: T): T[] => xs.map((x, j) => (j === i ? v : x));

/** The array with item `i` moved by `delta`, clamped to the ends. `null` when nothing moves. */
function shifted<T>(xs: readonly T[], i: number, delta: unknown): T[] | null {
  if (typeof delta !== "number" || !Number.isInteger(delta) || delta === 0 || !isIndex(i, xs.length)) return null;
  const to = Math.min(xs.length - 1, Math.max(0, i + delta));
  if (to === i) return null;
  const out = [...xs];
  const [item] = out.splice(i, 1);
  out.splice(to, 0, item);
  return out;
}

const viewIds = (m: Obj): Set<string> => new Set(list(m.views).map((v) => str(v.id)).filter(Boolean));
const pageIds = (m: Obj): Set<string> => new Set(list(m.pages).map((p) => str(p.id)).filter(Boolean));

// ── Pages ───────────────────────────────────────────────────────────────────

/**
 * Write the default pages of an app that declares none into `pages`, so the member can edit them.
 * The input comes back when the manifest has a page already, has no view, or has a default page
 * that the server would refuse (more than the cap of cells, or no cell).
 */
export function materializePages(m: unknown): unknown {
  if (!isObj(m) || (Array.isArray(m.pages) && m.pages.length > 0)) return m;
  const views = list(m.views).filter((v) => DASHED.test(str(v.id)));
  if (views.length === 0) return m;
  const built = defaultPages({
    views: views as never,
    actions: list(m.actions) as never,
    objects: list(m.objects) as never,
  });
  const pages: Obj[] = [];
  for (const p of built) {
    if (!DASHED.test(p.id) || p.cells.length === 0 || p.cells.length > PAGE_CAPS.cellsPerList) return m;
    pages.push({
      id: p.id,
      ...(p.title ? { title: p.title } : {}),
      cols: p.cols,
      cells: p.cells.map((c) => (c.span > 1 ? { view: c.view, span: c.span } : { view: c.view })),
    });
  }
  if (pages.length === 0 || pages.length > PAGE_CAPS.pages) return m;
  return { ...m, pages };
}

/** Add a page with one cell that shows `viewId`. The id comes from the title. The 9th page is refused. */
export function addPage(m: unknown, title: string, viewId: string): unknown {
  if (!isObj(m) || !Array.isArray(m.pages) || typeof title !== "string") return m;
  if (m.pages.length >= PAGE_CAPS.pages || !viewIds(m).has(viewId)) return m;
  const shown = clean(title);
  const id = nameFrom(shown || viewId, pageIds(m), "page", "-");
  const page: Obj = { id, ...(shown ? { title: shown } : {}), cells: [{ view: viewId }] };
  return { ...m, pages: [...m.pages, page] };
}

const pageAt = (m: unknown, id: string): { m: Obj; i: number; page: Obj } | null => {
  if (!isObj(m) || !Array.isArray(m.pages) || typeof id !== "string") return null;
  const i = m.pages.findIndex((p) => isObj(p) && str(p.id) === id);
  return i < 0 ? null : { m, i, page: m.pages[i] as Obj };
};

/** Set the title of a page. The id stays, because rail rows name it. A blank title deletes the key. */
export function renamePage(m: unknown, id: string, title: string): unknown {
  const at = pageAt(m, id);
  if (!at || typeof title !== "string") return m;
  const shown = clean(title);
  if (shown === str(at.page.title)) return m;
  const { title: _old, ...rest } = at.page;
  void _old;
  return { ...at.m, pages: put(at.m.pages as unknown[], at.i, shown ? { ...rest, title: shown } : rest) };
}

/**
 * Delete a page. Every rail row that names it goes too, and the views stay. A section that loses
 * its last row stays, because the server accepts an empty section.
 */
export function removePage(m: unknown, id: string): unknown {
  const at = pageAt(m, id);
  if (!at) return m;
  const pages = (at.m.pages as unknown[]).filter((_, j) => j !== at.i);
  return dropNavRows({ ...at.m, pages }, new Set([id]));
}

/** Move a page up (`delta` below 0) or down in the list. The order is the only change. */
export function movePage(m: unknown, id: string, delta: number): unknown {
  const at = pageAt(m, id);
  const pages = at && shifted(at.m.pages as unknown[], at.i, delta);
  return at && pages ? { ...at.m, pages } : m;
}

/** The widest span among the cells of a page, in its cells and in its tabs. At least 1. */
export function colsFloor(page: unknown): number {
  if (!isObj(page)) return 1;
  const cells = [...list(page.cells), ...list(page.tabs).flatMap((t) => list(t.cells))];
  return cells.reduce((w, c) => (typeof c.span === "number" && Number.isInteger(c.span) && c.span > w ? c.span : w), 1);
}

/** Set the columns of a page, 1 to the cap. A value under the widest span is refused, and no span is cut. */
export function setPageCols(m: unknown, id: string, cols: number): unknown {
  const at = pageAt(m, id);
  if (!at || typeof cols !== "number" || !Number.isInteger(cols)) return m;
  if (cols < 1 || cols > PAGE_CAPS.maxCols || cols < colsFloor(at.page)) return m;
  if (cols === at.page.cols) return m;
  return { ...at.m, pages: put(at.m.pages as unknown[], at.i, { ...at.page, cols }) };
}

// ── The rail ────────────────────────────────────────────────────────────────

/** The sections of a `nav` that holds some. A `nav` of no section reads as no `nav`. */
const navOf = (m: Obj): unknown[] | null => (Array.isArray(m.nav) && m.nav.length > 0 ? m.nav : null);

const withNav = (m: Obj, nav: unknown[]): Obj => {
  if (nav.length > 0) return { ...m, nav };
  const { nav: _old, ...rest } = m;
  void _old;
  return rest;
};

const sameTitle = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();

/**
 * Add a section. The first act on an app with no `nav` writes the default rail first: one section
 * titled `DEFAULT_SECTION_TITLE` with a row for each page, up to the row cap (`railFor` shows the
 * same rail). It writes the default pages too when the app has none, because a row needs a declared
 * page. A title of another section, in any case, is refused, and so is the 5th section.
 */
export function addSection(m: unknown, title: string): unknown {
  if (!isObj(m) || typeof title !== "string") return m;
  const shown = clean(title);
  if (!shown) return m;
  let base: Obj = m;
  let sections = navOf(m);
  if (!sections) {
    const full = materializePages(m);
    base = isObj(full) ? full : m;
    const rows = list(base.pages)
      .map((p) => str(p.id))
      .filter(Boolean)
      .slice(0, MAX_NAV_ROWS)
      .map((page) => ({ page }));
    sections = rows.length > 0 ? [{ title: DEFAULT_SECTION_TITLE, rows }] : [];
  }
  if (sections.length >= MAX_NAV_SECTIONS) return m;
  if (sections.some((s) => isObj(s) && sameTitle(str(s.title), shown))) return m;
  return withNav(base, [...sections, { title: shown, rows: [] }]);
}

const sectionAt = (m: unknown, si: number): { m: Obj; nav: unknown[]; section: Obj } | null => {
  if (!isObj(m)) return null;
  const nav = navOf(m);
  if (!nav || !isIndex(si, nav.length) || !isObj(nav[si])) return null;
  return { m, nav, section: nav[si] as Obj };
};

/** Set the title of a section. A blank title, or the title of another section, is refused. */
export function renameSection(m: unknown, sectionIndex: number, title: string): unknown {
  const at = sectionAt(m, sectionIndex);
  if (!at || typeof title !== "string") return m;
  const shown = clean(title);
  if (!shown || shown === str(at.section.title)) return m;
  const clash = at.nav.some((s, j) => j !== sectionIndex && isObj(s) && sameTitle(str(s.title), shown));
  if (clash) return m;
  return withNav(at.m, put(at.nav, sectionIndex, { ...at.section, title: shown }));
}

/** Delete a section with its rows. The pages stay. The last section goes with the `nav` key, so the default rail returns. */
export function removeSection(m: unknown, sectionIndex: number): unknown {
  const at = sectionAt(m, sectionIndex);
  return at ? withNav(at.m, at.nav.filter((_, j) => j !== sectionIndex)) : m;
}

/** Move a section up or down. The order is the only change. */
export function moveSection(m: unknown, sectionIndex: number, delta: number): unknown {
  const at = sectionAt(m, sectionIndex);
  const nav = at && shifted(at.nav, sectionIndex, delta);
  return at && nav ? withNav(at.m, nav) : m;
}

const rowsOf = (section: Obj): unknown[] => (Array.isArray(section.rows) ? section.rows : []);

const rowAt = (m: unknown, si: number, ri: number) => {
  const at = sectionAt(m, si);
  const rows = at ? rowsOf(at.section) : [];
  if (!at || !isIndex(ri, rows.length) || !isObj(rows[ri])) return null;
  return { ...at, rows, row: rows[ri] as Obj };
};

const withRows = (at: { m: Obj; nav: unknown[]; section: Obj }, si: number, rows: unknown[]): Obj =>
  withNav(at.m, put(at.nav, si, { ...at.section, rows }));

/**
 * Add a row that names a declared page. The section holds at most `MAX_NAV_ROWS` rows.
 * The server accepts the same page twice in one section, so this does too.
 */
export function addRow(m: unknown, sectionIndex: number, pageId: string): unknown {
  const at = sectionAt(m, sectionIndex);
  if (!at || typeof pageId !== "string" || !pageIds(at.m).has(pageId)) return m;
  const rows = rowsOf(at.section);
  if (rows.length >= MAX_NAV_ROWS) return m;
  return withRows(at, sectionIndex, [...rows, { page: pageId }]);
}

/** Delete one row. The page stays. */
export function removeRow(m: unknown, sectionIndex: number, rowIndex: number): unknown {
  const at = rowAt(m, sectionIndex, rowIndex);
  return at ? withRows(at, sectionIndex, at.rows.filter((_, j) => j !== rowIndex)) : m;
}

/** Move a row inside its section. The order is the only change. */
export function moveRow(m: unknown, sectionIndex: number, rowIndex: number, delta: number): unknown {
  const at = rowAt(m, sectionIndex, rowIndex);
  const rows = at && shifted(at.rows, rowIndex, delta);
  return at && rows ? withRows(at, sectionIndex, rows) : m;
}

/** Set the label of a row. A blank label, or `null`, deletes the key, because the schema needs one character. */
export function setRowLabel(m: unknown, sectionIndex: number, rowIndex: number, label: string | null): unknown {
  const at = rowAt(m, sectionIndex, rowIndex);
  if (!at || (label !== null && typeof label !== "string")) return m;
  const shown = label === null ? "" : clean(label);
  if (shown === str(at.row.label)) return m;
  const { label: _old, ...rest } = at.row;
  void _old;
  return withRows(at, sectionIndex, put(at.rows, rowIndex, shown ? { ...rest, label: shown } : rest));
}

/** Set the icon of a row to a name of `NAV_ICONS`. `null` deletes the key. Another name is refused. */
export function setRowIcon(m: unknown, sectionIndex: number, rowIndex: number, icon: string | null): unknown {
  const at = rowAt(m, sectionIndex, rowIndex);
  if (!at || (icon !== null && !(NAV_ICONS as readonly unknown[]).includes(icon))) return m;
  if ((icon ?? undefined) === at.row.icon || (icon === null && !("icon" in at.row))) return m;
  const { icon: _old, ...rest } = at.row;
  void _old;
  return withRows(at, sectionIndex, put(at.rows, rowIndex, icon === null ? rest : { ...rest, icon }));
}
