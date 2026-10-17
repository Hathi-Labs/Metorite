// ── The rail of views (WS-53 CRM-U1) ──────────────────────────────────────
//
// The CRM opens with `AppTopBar` and a rail of `RailRow`s, the shape every
// app has (AGENTS.md rules 11 and 12). The rail replaced the tab strip, and
// it reads the same URL grammar the tabs did (`urlState.ts`), so every deep
// link that worked before still works.
//
// Pure, so the test can hold the rail to the grammar without a browser.

import { moduleRailRows, type CrmModule, type ModuleRailRow } from "./modules";
import type { EntitySlug } from "./types";
import { isEntity, type CrmView } from "./urlState";

export interface CrmRailView {
  /** The `?tab=` value. It never changes, so an old link still opens. */
  id: CrmView["tab"];
  /** What the member reads. */
  label: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
}

/**
 * The views, in rail order (spec crm_platform.md section 18.4). There is no
 * "Pipeline" row: the board is a layout of Deals. Activities has no view yet,
 * so it has no row.
 *
 * ⚠️ The `organizations` view reads "Companies" (D95.2), and its id stays
 * `organizations`. The id is the URL and the gateway's collection name. A
 * rename there would break each saved link and each agent that names it.
 */
export const CRM_VIEWS: readonly CrmRailView[] = [
  { id: "leads", label: "Leads", icon: "Target" },
  { id: "contacts", label: "Contacts", icon: "Contact" },
  { id: "organizations", label: "Companies", icon: "Building2" },
  { id: "deals", label: "Deals", icon: "Handshake" },
  { id: "reports", label: "Reports", icon: "BarChart3" },
];

/**
 * The settings row, apart from the views below a divider. It is the place to
 * set the CRM up, not a view of the data. The id stays `settings`, so every
 * saved link to the old "Pipeline settings" still opens it.
 */
export const CRM_SETTINGS_VIEW: CrmRailView = {
  id: "settings",
  label: "CRM settings",
  icon: "Settings",
};

/** Every row the rail draws, settings last. */
export const CRM_RAIL: readonly CrmRailView[] = [...CRM_VIEWS, CRM_SETTINGS_VIEW];

/** One core entity as the source names it (`EntityLabel` in `fill_rank.py`). */
export type SourceLabel = { label: string; singular_label?: string | null };

/**
 * The source's names of the core entities, by API slug (CRM-Q17a-2 item 7).
 * `GET /crm/mode` sends them as `labels` in Mirror mode, for an org on
 * `CRM_FILL_RANK_ORGS`. Each other case has none, and the CRM keeps its own
 * names. ⚠️ Only the label changes. The id of a view stays the URL.
 */
export type SourceLabels = Partial<Record<EntitySlug, SourceLabel>>;

/** The plural label of the source, or null when it sent none. */
function sourcePlural(id: CrmView["tab"], labels: SourceLabels | null | undefined): string | null {
  if (!labels || !isEntity(id)) return null;
  const label = labels[id]?.label;
  return typeof label === "string" && label.trim() ? label.trim() : null;
}

/**
 * The singular label of the source, for the record page, or null. A source
 * with no singular label gives its plural one.
 */
export function sourceSingular(
  entity: EntitySlug,
  labels: SourceLabels | null | undefined,
): string | null {
  const singular = labels?.[entity]?.singular_label;
  if (typeof singular === "string" && singular.trim()) return singular.trim();
  return sourcePlural(entity, labels);
}

/** The label of a view, for the phone bar's title and the list heading. */
export function viewLabel(tab: CrmView["tab"], labels?: SourceLabels | null): string {
  return sourcePlural(tab, labels) ?? CRM_RAIL.find((v) => v.id === tab)?.label ?? "Deals";
}

/** The views of the rail, with the source's labels in place of the CRM's. */
export function labelledViews(labels: SourceLabels | null | undefined): CrmRailView[] {
  return CRM_VIEWS.map((view) => ({ ...view, label: viewLabel(view.id, labels) }));
}

/**
 * One row of the rail: a view, or a generic Zoho module (CRM-Q6b). A view
 * with `module` is a core list that a Zoho module plays (CRM-Q20-2, option
 * A): the row keeps the label of the list and opens the module's list.
 */
export type RailEntry =
  | { kind: "view"; view: CrmRailView; module?: string }
  | { kind: "module"; row: ModuleRailRow };

/**
 * The rows of the rail above the divider, in order (spec section 18.4).
 *
 * The generic modules come under the known modules, after the last view that
 * is a collection (Deals), and before Reports. They keep the order of
 * `GET /crm/modules`, which is the profile order. With no module, or a failed
 * read, the group is absent and the rail is the one of CRM-Q3.
 *
 * With `labels`, a known view takes the source's plural label (CRM-Q17a-2
 * item 7). Contacts can read "Existing Sites". With none, it reads as before.
 *
 * With `overlays` (CRM-Q20-2), the row of a list that a module plays opens
 * that module, and the module's own row leaves the group.
 */
export function railEntries(
  modules: readonly CrmModule[] | null | undefined,
  labels?: SourceLabels | null,
  overlays?: Partial<Record<EntitySlug, string>> | null,
): RailEntry[] {
  // CRM-Q20-2: a module that plays a list has that list's row, so its own
  // row leaves the group. One list, one row.
  const playing = new Set(Object.values(overlays ?? {}));
  const rows = moduleRailRows(modules).filter((row) => !playing.has(row.module));
  const views = labelledViews(labels);
  const lastKnown = views.reduce((at, view, i) => (isEntity(view.id) ? i : at), -1);
  const entries: RailEntry[] = [];
  views.forEach((view, i) => {
    const module = isEntity(view.id) ? overlays?.[view.id] : undefined;
    entries.push(module ? { kind: "view", view, module } : { kind: "view", view });
    if (i === lastKnown) entries.push(...rows.map((row) => ({ kind: "module" as const, row })));
  });
  return entries;
}
