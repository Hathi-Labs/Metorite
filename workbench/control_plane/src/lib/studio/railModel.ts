/**
 * The rail of a Studio app, as pure data (STU-AF2b, `ai_studio.md` §5.5).
 *
 * `railFor` turns the built pages and the declared `nav` into sections of rows. It draws nothing.
 * `StudioAppFrame.tsx` draws the rows. A row names a page that was built, so a click can never
 * reach a page that does not exist.
 */
import { MAX_NAV_COUNTS, MAX_NAV_ROWS, MAX_NAV_SECTIONS, type NavIcon, type NavSection } from "@/lib/studio/manifest";

/** The icon of a row that names none. */
export const DEFAULT_RAIL_ICON: NavIcon = "FileText";

/** The part of a built page that the rail reads. */
export interface RailPage {
  id: string;
  title?: string | null;
}

export interface RailRowModel {
  page: string;
  label: string;
  icon: NavIcon;
  /** The row shows a count (STU-AF3b). At most `MAX_NAV_COUNTS` rows of a rail hold it. */
  count?: boolean;
}

export interface RailSectionModel {
  /** Empty for the one untitled section of an app that declares no `nav`. */
  title: string;
  rows: RailRowModel[];
}

function pageLabel(page: RailPage): string {
  const title = typeof page.title === "string" ? page.title.trim() : "";
  return title || page.id;
}

/** The rail draws for two pages or more. An app with one page has nothing to pick. */
export function hasRail(pages: readonly RailPage[]): boolean {
  return pages.length > 1;
}

/** Sections of rows for the rail. Unknown page ids drop out, and so do sections left empty. A row keeps `count` for the first 8 only. */
export function railFor(
  pages: readonly RailPage[],
  nav?: readonly NavSection[] | null,
): RailSectionModel[] {
  const byId = new Map(pages.map((p) => [p.id, p] as const));
  const declared = Array.isArray(nav) ? nav : [];
  if (declared.length === 0) {
    const rows = pages.slice(0, MAX_NAV_ROWS).map((p) => ({
      page: p.id, label: pageLabel(p), icon: DEFAULT_RAIL_ICON,
    }));
    return rows.length > 0 ? [{ title: "", rows }] : [];
  }
  const out: RailSectionModel[] = [];
  let counts = 0;
  for (const section of declared.slice(0, MAX_NAV_SECTIONS)) {
    const rows: RailRowModel[] = [];
    for (const row of (section?.rows ?? []).slice(0, MAX_NAV_ROWS)) {
      const page = row ? byId.get(row.page) : undefined;
      if (!page) continue;
      const label = typeof row.label === "string" ? row.label.trim() : "";
      const counted = row.count === true && counts < MAX_NAV_COUNTS;
      if (counted) counts += 1;
      rows.push({
        page: page.id, label: label || pageLabel(page), icon: row.icon ?? DEFAULT_RAIL_ICON,
        ...(counted ? { count: true } : {}),
      });
    }
    if (rows.length > 0) out.push({ title: typeof section.title === "string" ? section.title : "", rows });
  }
  return out;
}

/** What `visibleRail` needs to decide who reads a page. */
export interface RailReadContext {
  /** The member owns the app. The owner reads every installed object. */
  owner: boolean;
  /** The installed objects, by name, with the `access` of each. No `access` means an open object. */
  objects: ReadonlyMap<string, readonly Record<string, unknown>[] | null | undefined>;
  /** The object that a page lists, or null when the page lists none. */
  objectOf: (page: string) => string | null;
}

/** C8a: a grant reads every row when its `read` is the string "all". */
const readsAll = (grant: Record<string, unknown>): boolean => grant.read === "all";

/**
 * C8 (`ai_studio.md` §5.5 rule 5): can this viewer read the object? An object with no `access` is
 * open. An object with `access` is open only through a grant that reads `all`. A viewer who is not
 * the owner holds no role yet (`invoke.py _row_viewer`, C8b), so a `where` grant reads nothing.
 * ⚠️ This only decides what the rail shows. The door holds the rule, and C8b will make the door answer it.
 */
export function objectReadable(access: readonly Record<string, unknown>[] | null | undefined, owner: boolean): boolean {
  if (owner) return true;
  if (!Array.isArray(access) || access.length === 0) return true;
  return access.some((g) => !!g && typeof g === "object" && readsAll(g));
}

/**
 * Drop the rows that the viewer cannot read, and the sections left empty. A row whose page lists
 * an object that is not installed drops out too. A page that lists no object stays.
 */
export function visibleRail(sections: readonly RailSectionModel[], ctx: RailReadContext): RailSectionModel[] {
  const out: RailSectionModel[] = [];
  for (const section of sections) {
    const rows = section.rows.filter((row) => {
      const name = ctx.objectOf(row.page);
      if (name === null) return true;
      if (!ctx.objects.has(name)) return false;
      return objectReadable(ctx.objects.get(name), ctx.owner);
    });
    if (rows.length > 0) out.push({ ...section, rows });
  }
  return out;
}
