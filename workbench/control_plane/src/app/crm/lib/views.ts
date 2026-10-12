// ── The rail of views (WS-53 CRM-U1) ──────────────────────────────────────
//
// The CRM opens with `AppTopBar` and a rail of `RailRow`s, the shape every
// app has (AGENTS.md rules 11 and 12). The rail replaced the tab strip, and
// it reads the same URL grammar the tabs did (`urlState.ts`), so every deep
// link that worked before still works.
//
// Pure, so the test can hold the rail to the grammar without a browser.

import { moduleRailRows, type CrmModule, type ModuleRailRow } from "./modules";
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

/** The label of a view, for the phone bar's title. */
export function viewLabel(tab: CrmView["tab"]): string {
  return CRM_RAIL.find((v) => v.id === tab)?.label ?? "Deals";
}

/** One row of the rail: a view, or a generic Zoho module (CRM-Q6b). */
export type RailEntry =
  | { kind: "view"; view: CrmRailView }
  | { kind: "module"; row: ModuleRailRow };

/**
 * The rows of the rail above the divider, in order (spec section 18.4).
 *
 * The generic modules come under the known modules, after the last view that
 * is a collection (Deals), and before Reports. They keep the order of
 * `GET /crm/modules`, which is the profile order. With no module, or a failed
 * read, the group is absent and the rail is the one of CRM-Q3.
 */
export function railEntries(modules: readonly CrmModule[] | null | undefined): RailEntry[] {
  const rows = moduleRailRows(modules);
  const lastKnown = CRM_VIEWS.reduce((at, view, i) => (isEntity(view.id) ? i : at), -1);
  const entries: RailEntry[] = [];
  CRM_VIEWS.forEach((view, i) => {
    entries.push({ kind: "view", view });
    if (i === lastKnown) entries.push(...rows.map((row) => ({ kind: "module" as const, row })));
  });
  return entries;
}
