/**
 * The pin suggestion rule of `package_host.md` §9.3 item 2 (PKG-19a-1).
 * "You opened Field visits 9 times this week. Pin it?"
 *
 * This file is PURE. It has no React, no fetch and no storage access. The
 * browser counter lives in `pinSuggestStore.ts`. A later slice (PKG-19a-2)
 * draws the card.
 *
 * D111: no learning in silence. This rule only PROPOSES. Nothing here writes
 * the arrangement. The member must say Yes, and Yes is a later slice. The
 * fence is `arrangement.test.ts`, check 4.
 */
import { PANES, type NavPane } from "@/lib/nav";

import { STARRABLE } from "./presets";

/** A pane qualifies at this many opens or more. */
export const SUGGEST_MIN_OPENS = 5;
/** Only opens inside this window count. */
export const SUGGEST_WINDOW_DAYS = 7;
/** A No hides that suggestion for this long. */
export const SUGGEST_HIDE_DAYS = 30;

export const DAY_MS = 24 * 60 * 60 * 1000;

export type PinSuggestion = { href: string; opens: number; why: string };

export type PinSuggestInput = {
  /** Pane href to open times, in epoch ms. */
  opens: Readonly<Record<string, readonly number[]>>;
  /** The hrefs the member has starred already. */
  starred: Iterable<string>;
  /** Pane href to the epoch ms when its hide ends. */
  hidden: Readonly<Record<string, number>>;
  /** The panes the member holds, in nav order. */
  panes: readonly NavPane[];
  now: number;
};

/** How many of `times` fall inside the window that ends at `now`. */
export function opensInWindow(times: readonly number[], now: number): number {
  const from = now - SUGGEST_WINDOW_DAYS * DAY_MS;
  return times.filter((t) => Number.isFinite(t) && t > from && t <= now).length;
}

/** The one-line reason. The minimum is 5, so `n` is always more than 1. */
export function suggestionWhy(label: string, n: number): string {
  return `You opened ${label} ${n} times this week. Pin it?`;
}

/**
 * The pane of the shell that holds `pathname`: the longest `href` that
 * contains it. `/tasks/123` gives `/tasks`. A route outside every pane gives
 * null. Only a pane the member may star counts, so Home, a setting and an
 * install route give null.
 */
export function paneHrefOf(pathname: string | null | undefined): string | null {
  if (!pathname) return null;
  const path = pathname.split(/[?#]/)[0];
  let best: string | null = null;
  for (const p of PANES) {
    if (!STARRABLE.has(p.href)) continue;
    const inside = path === p.href || path.startsWith(`${p.href}/`);
    if (inside && (!best || p.href.length > best.length)) best = p.href;
  }
  return best;
}

/** At most one suggestion, or null. */
export function suggestPin(input: PinSuggestInput): PinSuggestion | null {
  const { opens, hidden, panes, now } = input;
  const starred = new Set(input.starred);
  let best: PinSuggestion | null = null;
  for (const pane of panes) {
    const href = pane.href;
    if (!STARRABLE.has(href)) continue;
    if (!PANES.some((p) => p.href === href)) continue;
    if (starred.has(href)) continue;
    const until = hidden[href];
    if (typeof until === "number" && now < until) continue;
    const times = opens[href];
    if (!Array.isArray(times)) continue;
    const n = opensInWindow(times, now);
    if (n < SUGGEST_MIN_OPENS) continue;
    // A tie keeps the first pane, so the order of `panes` breaks it.
    if (!best || n > best.opens) best = { href, opens: n, why: suggestionWhy(pane.label, n) };
  }
  return best;
}
