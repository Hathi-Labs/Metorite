// ── The CRM's tabs in the phone's bottom bar (CRM-U10) ────────────────────
//
// On a phone the shell's bottom bar is the one place a thumb reaches. The
// CRM had only "Menu" there, so a member who wanted Leads tapped the title,
// then a drawer row, then waited. Now the bar names the three lists that
// most people open, and a "More" tab opens the whole rail in the drawer.
//
// Pure, so the vitest setup (no DOM) can test it. The page emits the result
// as `cc-crm-nav`, and `AppShell` draws it. The page owns the list because
// it knows the mode, the source's labels and which rows Zoho turned off.

import { isEntity } from "./urlState";
import type { CrmModule } from "./modules";
import type { CoreStates, RailEntry } from "./views";

/** The core lists, in the order they win a slot. */
const PRIMARY: readonly string[] = [
  "deals",
  "leads",
  "contacts",
  "organizations",
];

/** How many lists the bar names. The fourth slot is "More". */
export const PHONE_TABS = 3;

/** The id of the "More" tab. It is not a view. */
export const MORE_TAB = "more";

/** The id of a tab that opens a generic module. */
export const MODULE_TAB = "module:";

export type PhoneTab = { id: string; label: string; icon: string };

/** What the page tells the shell. `null` (an unmount) removes the tabs. */
export type PhoneNav = {
  tabs: PhoneTab[];
  /** A tab id, or `MORE_TAB` when the view on screen is not one of the tabs. */
  active: string;
  /** The open count to show on "More", or null. Questions wait in settings. */
  badge: number | null;
};

/** The view on screen: its tab, and the module when the tab is `module`. */
export type PhoneView = { tab: string; module?: string | null };

/** The tab id of a generic module. */
export function moduleTabId(module: string): string {
  return `${MODULE_TAB}${module}`;
}

/** The module in a tab id, or null for a core list. */
export function moduleOfTab(id: string): string | null {
  return id.startsWith(MODULE_TAB) ? id.slice(MODULE_TAB.length) : null;
}

type Candidate = PhoneTab & { holds: boolean; count: number; rank: number };

/**
 * The tabs for the rows the rail draws. Three slots go to the lists that hold
 * data, so an org that lives in custom modules sees them and not three empty
 * built-in lists:
 *
 * 1. the core lists that hold records, in the order Deals, Leads, Contacts,
 *    Companies. With no state from the source, a core list counts as full.
 * 2. then the generic modules, the biggest first.
 * 3. then the core lists that hold nothing, if a slot is left.
 *
 * A core list that a module plays keeps the label of the list and opens the
 * module, as its rail row does (CRM-Q20-2). A list that the source turned off
 * and that holds no record is already gone from `entries`.
 */
export function phoneNav(
  entries: readonly RailEntry[],
  current: PhoneView,
  badge: number | null,
  modules?: readonly CrmModule[] | null,
  states?: CoreStates | null,
): PhoneNav {
  const countOf = (name: string) =>
    modules?.find((m) => m.api_name === name)?.count ?? 0;
  const core: Candidate[] = [];
  const mods: Candidate[] = [];
  for (const entry of entries) {
    if (entry.kind === "module") {
      const { module, label, icon, count } = entry.row;
      mods.push({
        id: moduleTabId(module),
        label,
        icon,
        holds: count > 0,
        count,
        rank: 0,
      });
      continue;
    }
    const { id, label, icon } = entry.view;
    if (!PRIMARY.includes(id)) continue;
    const played = entry.module;
    const holds = played
      ? countOf(played) > 0
      : isEntity(id)
        ? (states?.[id]?.records ?? true)
        : true;
    core.push({
      id: played ? moduleTabId(played) : id,
      label,
      icon,
      holds,
      count: 0,
      rank: PRIMARY.indexOf(id),
    });
  }
  const byRank = (a: Candidate, b: Candidate) => a.rank - b.rank;
  const full = core.filter((c) => c.holds).sort(byRank);
  const empty = core.filter((c) => !c.holds).sort(byRank);
  const biggest = mods.filter((m) => m.holds).sort((a, b) => b.count - a.count);
  const tabs = [...full, ...biggest, ...empty]
    .slice(0, PHONE_TABS)
    .map(({ id, label, icon }) => ({ id, label, icon }));
  const onScreen =
    current.tab === "module" && current.module
      ? moduleTabId(current.module)
      : current.tab;
  const active = tabs.some((t) => t.id === onScreen) ? onScreen : MORE_TAB;
  return { tabs, active, badge: badge && badge > 0 ? badge : null };
}

/** The `cc-mobile-nav` detail a tab sends, and the page's reading of it. */
export const NAV_PREFIX = "crm:";

export function navDetail(id: string): string {
  return `${NAV_PREFIX}${id}`;
}

/** The tab id in a `cc-mobile-nav` detail, or null when it is not the CRM's. */
export function readNavDetail(detail: unknown): string | null {
  return typeof detail === "string" && detail.startsWith(NAV_PREFIX)
    ? detail.slice(NAV_PREFIX.length)
    : null;
}
