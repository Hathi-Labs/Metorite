// ── The rail of views (WS-53 CRM-U1) ──────────────────────────────────────
//
// The CRM opens with `AppTopBar` and a rail of `RailRow`s, the shape every
// app has (AGENTS.md rules 11 and 12). The rail replaced the tab strip, and
// it reads the same URL grammar the tabs did (`urlState.ts`), so every deep
// link that worked before still works.
//
// Pure, so the test can hold the rail to the grammar without a browser.

import type { CrmView } from "./urlState";

export interface CrmRailView {
  /** The `?tab=` value. It never changes, so an old link still opens. */
  id: CrmView["tab"];
  /** What the member reads. */
  label: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
}

/**
 * The views, in rail order.
 *
 * ⚠️ The `organizations` view reads "Companies" (D95.2), and its id stays
 * `organizations`. The id is the URL and the gateway's collection name. A
 * rename there would break each saved link and each agent that names it.
 */
export const CRM_VIEWS: readonly CrmRailView[] = [
  { id: "board", label: "Pipeline", icon: "Kanban" },
  { id: "deals", label: "Deals", icon: "Handshake" },
  { id: "leads", label: "Leads", icon: "Target" },
  { id: "contacts", label: "Contacts", icon: "Contact" },
  { id: "organizations", label: "Companies", icon: "Building2" },
  { id: "reports", label: "Reports", icon: "BarChart3" },
];

/**
 * The settings row, apart from the views below a divider. It is a place to
 * change the pipeline, not a view of the data. CRM-U6 moves it to
 * Settings → CRM, and until then the rail keeps it in reach.
 */
export const CRM_SETTINGS_VIEW: CrmRailView = {
  id: "settings",
  label: "Pipeline settings",
  icon: "Settings",
};

/** Every row the rail draws, settings last. */
export const CRM_RAIL: readonly CrmRailView[] = [...CRM_VIEWS, CRM_SETTINGS_VIEW];

/** The label of a view, for the phone bar's title. */
export function viewLabel(tab: CrmView["tab"]): string {
  return CRM_RAIL.find((v) => v.id === tab)?.label ?? "Pipeline";
}
