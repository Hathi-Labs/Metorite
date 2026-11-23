/**
 * The tabs of My Day's "Needs you" card (WS-MDC CC-11, D-MD-9).
 *
 * One pure rule: which tabs the card draws, in which order, with which count.
 * "All" comes first. Then "AI questions", then one tab for each app with rows,
 * the app with the most rows first. "Reminders" comes last. A tab with no row
 * is not drawn. A tab's count is the number of rows it shows. "All" shows no count.
 *
 * `FeedCard.tsx` draws what this file decides. A new app adds no tab code: its
 * rows carry an `app`, and its tab appears.
 */
import { NAV_SECTIONS } from "@/lib/nav";

import { FEED_APP_NAMES, type FeedApp, feedAppIcon } from "./feed";

/**
 * The name of an app tab. The feed's own names come first, then the label of
 * the app's nav pane (`/<key>`), then the key in title case. A member never
 * reads a raw key.
 */
export function appTabLabel(app: string): string {
  const named = FEED_APP_NAMES[app as FeedApp];
  if (named) return named;
  const pane = NAV_SECTIONS.flatMap((s) => s.items).find((p) => p.href === `/${app}`);
  if (pane) return pane.label;
  return app
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((w) => w[0].toUpperCase() + w.slice(1))
    .join(" ");
}

/** The rows of each app in the groups: one count rule for the tabs. */
export function appRowCounts(groups: readonly { cards: readonly { app: string }[] }[]): Map<string, number> {
  const counts = new Map<string, number>();
  for (const g of groups) for (const c of g.cards) counts.set(c.app, (counts.get(c.app) ?? 0) + 1);
  return counts;
}

/** The id of a tab: "all", "ai", "reminders", or `app:<key>`. */
export type NeedsTabId = string;

export interface NeedsTab {
  id: NeedsTabId;
  label: string;
  /** A Lucide icon name. */
  icon: string;
  /** The rows of the tab. "All" has none: the page summary counts another way, and two numbers would disagree. */
  count?: number;
  /** The app key of an app tab. The card reads its hue from it. */
  app?: string;
  /** The tab holds the assistant's runs, drawn in amber. */
  ai?: boolean;
}

export const ALL_TAB = "all";
export const AI_TAB = "ai";
export const REMINDERS_TAB = "reminders";
export const appTabId = (app: string): NeedsTabId => `app:${app}`;

/**
 * The tabs of the card, or `[]` when one tab would only repeat "All".
 * `apps` holds the rows of each app in the groups. `ai` is the count of the
 * assistant runs that wait. `reminders` is the count of rows in the band.
 */
export function buildNeedsTabs(input: {
  apps: ReadonlyMap<string, number>;
  ai: number;
  reminders: number;
}): NeedsTab[] {
  const kinds: NeedsTab[] = [];
  if (input.ai > 0) kinds.push({ id: AI_TAB, label: "AI questions", icon: "Sparkles", count: input.ai, ai: true });
  const apps = [...input.apps.entries()]
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1])
    .map(
      ([app, n]): NeedsTab => ({
        id: appTabId(app),
        label: appTabLabel(app),
        icon: feedAppIcon(app),
        count: n,
        app,
      }),
    );
  kinds.push(...apps);
  if (input.reminders > 0) kinds.push({ id: REMINDERS_TAB, label: "Reminders", icon: "Bell", count: input.reminders });
  if (kinds.length < 2) return [];
  return [{ id: ALL_TAB, label: "All", icon: "" }, ...kinds];
}

/** The tab that is in force: the chosen one, or "All" when it emptied or never was. */
export function activeNeedsTab(tabs: readonly NeedsTab[], chosen: NeedsTabId): NeedsTabId {
  return tabs.some((t) => t.id === chosen) ? chosen : ALL_TAB;
}

/**
 * Where "See all" goes. `/waiting` (WS-AP, `waiting_on_me.md`) does not exist
 * yet, so only the Approvals tab links, to its full list. Every other tab
 * draws no link. When `/waiting` ships, return it for every tab.
 */
export function seeAllHref(active: NeedsTabId): string | null {
  return active === appTabId("approvals") ? "/approvals" : null;
}
