// ── The rail of views (WS-53 CRM-U1) ──────────────────────────────────────
//
// The CRM opens with `AppTopBar` and a rail of `RailRow`s, the shape every
// app has (AGENTS.md rules 11 and 12). The rail replaced the tab strip, and
// it reads the same URL grammar the tabs did (`urlState.ts`), so every deep
// link that worked before still works.
//
// Pure, so the test can hold the rail to the grammar without a browser.

import { moduleRailRows, type CrmModule, type ModuleRailRow } from "./modules";
import { memberEmptyLine } from "./suggestions";
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
 * The state of one core entity, from the `entities` of `GET /crm/mode`
 * (CRM-Q17b). The gateway sends it in Mirror mode, for an org on
 * `CRM_FILL_RANK_ORGS`. Each other case has none, and the rail and the first
 * screen draw as before.
 */
export type CoreState = {
  /** The org holds at least one record of the entity. */
  records: boolean;
  /** Zoho refused the module the last time the sync asked: it is turned off. */
  turned_off: boolean;
};

export type CoreStates = Partial<Record<EntitySlug, CoreState>>;

/**
 * The `entities` block of the mode body, checked. A slug that the client does
 * not know, or a state that is not two booleans, is left out. No block, or an
 * empty one, gives `null`.
 */
export function coreStatesOf(block: unknown): CoreStates | null {
  if (!block || typeof block !== "object") return null;
  const out: CoreStates = {};
  for (const [slug, state] of Object.entries(block as Record<string, unknown>)) {
    if (!isEntity(slug) || !state || typeof state !== "object") continue;
    const { records, turned_off } = state as Record<string, unknown>;
    if (typeof records === "boolean" && typeof turned_off === "boolean") {
      out[slug] = { records, turned_off };
    }
  }
  return Object.keys(out).length > 0 ? out : null;
}

/**
 * A core row leaves the rail when Zoho turned it off and it holds no record
 * (CRM-Q17b item 2). A row that a module plays stays, because it opens the
 * module's records. With no state, each row stays.
 */
export function hiddenCoreRow(
  slug: EntitySlug,
  states: CoreStates | null | undefined,
  overlays?: Partial<Record<EntitySlug, string>> | null,
): boolean {
  const state = states?.[slug];
  return Boolean(state && state.turned_off && !state.records && !overlays?.[slug]);
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
 *
 * With `states` (CRM-Q17b item 2), a core row that Zoho turned off and that
 * holds no record leaves the rail (`hiddenCoreRow`).
 */
export function railEntries(
  modules: readonly CrmModule[] | null | undefined,
  labels?: SourceLabels | null,
  overlays?: Partial<Record<EntitySlug, string>> | null,
  states?: CoreStates | null,
): RailEntry[] {
  // CRM-Q20-2: a module that plays a list has that list's row, so its own
  // row leaves the group. One list, one row.
  const playing = new Set(Object.values(overlays ?? {}));
  const rows = moduleRailRows(modules).filter((row) => !playing.has(row.module));
  const views = labelledViews(labels);
  const lastKnown = views.reduce((at, view, i) => (isEntity(view.id) ? i : at), -1);
  const entries: RailEntry[] = [];
  views.forEach((view, i) => {
    const played = isEntity(view.id) ? overlays?.[view.id] : undefined;
    const hidden = isEntity(view.id) && hiddenCoreRow(view.id, states, overlays);
    if (!hidden) entries.push(played ? { kind: "view", view, module: played } : { kind: "view", view });
    if (i === lastKnown) entries.push(...rows.map((row) => ({ kind: "module" as const, row })));
  });
  return entries;
}

/**
 * The rail in three parts (CRM-Q17b item 1): the core rows, the one group of
 * the generic modules, then the rows after it (Reports). In the group, the
 * modules that hold records come first, each part in the order of the
 * gateway. So the rail stays short, and Reports and CRM settings stay in view.
 */
export type RailSections = {
  head: RailEntry[];
  modules: ModuleRailRow[];
  tail: RailEntry[];
};

export function railSections(entries: readonly RailEntry[]): RailSections {
  const head: RailEntry[] = [];
  const tail: RailEntry[] = [];
  const rows: ModuleRailRow[] = [];
  for (const entry of entries) {
    if (entry.kind === "module") rows.push(entry.row);
    else if (isEntity(entry.view.id)) head.push(entry);
    else tail.push(entry);
  }
  const modules = [...rows.filter((r) => r.count > 0), ...rows.filter((r) => !(r.count > 0))];
  return { head, modules, tail };
}

/** The label of the group of the generic modules in the rail. */
export const MODULE_GROUP_LABEL = "More modules";

/** Where `/crm` opens (CRM-Q17b item 3): a core list, or a module's list. */
export type Landing = { kind: "view"; tab: EntitySlug } | { kind: "module"; module: string };

/** The records of a module, from `GET /crm/modules`. Unknown is 0. */
function moduleCount(modules: readonly CrmModule[] | null | undefined, name: string): number {
  return modules?.find((m) => m.api_name === name)?.count ?? 0;
}

/**
 * Where `/crm` opens with no view in its address (CRM-Q17b item 3).
 *
 * With `states`, it is the first row of the rail that holds records: a core
 * row by its state, a row that a module plays by the count of that module,
 * then the modules of the group, records first. With no row that holds
 * records, it is `null`, and the CRM opens Deals as before.
 *
 * With no `states` (an org off `CRM_FILL_RANK_ORGS`, or Native mode), only
 * the overlays count: when a module plays Deals, `/crm` opens the list of
 * that module, because the Deals row opens it too. Else `null`.
 */
export function landing(
  entries: readonly RailEntry[],
  states: CoreStates | null | undefined,
  modules: readonly CrmModule[] | null | undefined,
  overlays?: Partial<Record<EntitySlug, string>> | null,
): Landing | null {
  if (!states) {
    const deals = overlays?.deals;
    return deals ? { kind: "module", module: deals } : null;
  }
  const { head, modules: group } = railSections(entries);
  for (const entry of head) {
    if (entry.kind !== "view" || !isEntity(entry.view.id)) continue;
    if (entry.module) {
      if (moduleCount(modules, entry.module) > 0) return { kind: "module", module: entry.module };
    } else if (states[entry.view.id]?.records) {
      return { kind: "view", tab: entry.view.id };
    }
  }
  const first = group.find((row) => row.count > 0);
  return first ? { kind: "module", module: first.module } : null;
}

/**
 * What the page does about the landing on this render (CRM-Q17b item 3).
 * `decide`: the landing is settled for this mount of the page. `redirect`:
 * the address to replace the bare one with. `pending`: hold the list, because
 * the first screen is not known yet, or the redirect has not arrived.
 */
export type LandingPlan = { decide: boolean; redirect: string | null; pending: boolean };

/**
 * The landing happens ONCE for each mount of the page (fix round 1, P1).
 *
 * A bare `/crm` is also the address of the Deals board, because the URL
 * grammar writes no `tab` for it. So a member who clicks Deals, or a read of
 * the mode that comes again, must never land a second time. The page keeps
 * `decided`, and sets it on the first render whose `decide` is true:
 *
 * - The page opened on an address with a view, or in Native mode: decided,
 *   with no redirect.
 * - A bare address while the reads wait: pending.
 * - A bare address with an answer: a redirect when the target differs, and
 *   pending until the address changes. The render after the change decides.
 *   With no redirect, decided at once.
 */
export function landingPlan(input: {
  decided: boolean;
  /** The query of the address, as `URLSearchParams.toString()` gives it. */
  search: string;
  mirror: boolean;
  /** The reads that `landing` needs have answered. */
  known: boolean;
  /** The address of the landing, or null for "stay". */
  target: string | null;
  /** The address of the view on screen. */
  here: string;
}): LandingPlan {
  const { decided, search, mirror, known, target, here } = input;
  if (decided) return { decide: false, redirect: null, pending: false };
  if (search !== "" || !mirror) return { decide: true, redirect: null, pending: false };
  if (!known) return { decide: false, redirect: null, pending: true };
  if (target && target !== here) return { decide: false, redirect: target, pending: true };
  return { decide: true, redirect: null, pending: false };
}

/** The sentence of a list that a filter empties (CRM-Q17b item 4). */
export const NOTHING_MATCHES = "Nothing matches this view.";

/**
 * Why a list or a board holds no record (CRM-Q17b item 4). "Nothing matches
 * this view." is for a filter only. With no filter, the line says why:
 * Zoho turned the module off, Zoho sent no record (Mirror mode, CRM-Q20-2),
 * or the CRM holds none yet.
 */
export function emptyListLine(
  label: string,
  { filtered, mirror, turnedOff }: { filtered: boolean; mirror: boolean; turnedOff: boolean },
): string {
  if (filtered) return NOTHING_MATCHES;
  if (turnedOff) return `${label} is turned off in your Zoho.`;
  return mirror ? memberEmptyLine(label) : "No records yet.";
}
