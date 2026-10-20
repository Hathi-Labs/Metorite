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

import type { RailEntry } from "./views";

/** The lists that fill the bar, in the order of the spec's "most opened". */
const PRIMARY: readonly string[] = ["deals", "leads", "contacts", "organizations"];

/** How many lists the bar names. The fourth slot is "More". */
export const PHONE_TABS = 3;

/** The id of the "More" tab. It is not a view. */
export const MORE_TAB = "more";

export type PhoneTab = { id: string; label: string; icon: string };

/** What the page tells the shell. `null` (an unmount) removes the tabs. */
export type PhoneNav = {
  tabs: PhoneTab[];
  /** A tab id, or `MORE_TAB` when the view on screen is not one of the tabs. */
  active: string;
  /** The open count to show on "More", or null. Questions wait in settings. */
  badge: number | null;
};

/**
 * The tabs for the rows the rail draws. A row that the source turned off
 * and that holds no record is already gone from `entries`, so it gets no
 * tab. With fewer than three lists, the bar is shorter and "More" stays.
 */
export function phoneNav(
  entries: readonly RailEntry[],
  current: string,
  badge: number | null,
): PhoneNav {
  const rows = new Map<string, PhoneTab>();
  for (const entry of entries) {
    if (entry.kind !== "view") continue;
    const { id, label, icon } = entry.view;
    rows.set(id, { id, label, icon });
  }
  const tabs = PRIMARY.flatMap((id) => rows.get(id) ?? []).slice(0, PHONE_TABS);
  const active = tabs.some((t) => t.id === current) ? current : MORE_TAB;
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
