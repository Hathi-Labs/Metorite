/**
 * The rail of a Studio app, as pure data (STU-AF2b, `ai_studio.md` §5.5).
 *
 * `railFor` turns the built pages and the declared `nav` into sections of rows. It draws nothing.
 * `StudioAppFrame.tsx` draws the rows. A row names a page that was built, so a click can never
 * reach a page that does not exist.
 */
import { MAX_NAV_ROWS, MAX_NAV_SECTIONS, type NavIcon, type NavSection } from "@/lib/studio/manifest";

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

/** Sections of rows for the rail. Unknown page ids drop out, and so do sections left empty. */
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
  for (const section of declared.slice(0, MAX_NAV_SECTIONS)) {
    const rows: RailRowModel[] = [];
    for (const row of (section?.rows ?? []).slice(0, MAX_NAV_ROWS)) {
      const page = row ? byId.get(row.page) : undefined;
      if (!page) continue;
      const label = typeof row.label === "string" ? row.label.trim() : "";
      rows.push({ page: page.id, label: label || pageLabel(page), icon: row.icon ?? DEFAULT_RAIL_ICON });
    }
    if (rows.length > 0) out.push({ title: typeof section.title === "string" ? section.title : "", rows });
  }
  return out;
}
