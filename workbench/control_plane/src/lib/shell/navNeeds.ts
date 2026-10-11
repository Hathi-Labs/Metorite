/**
 * What a sidebar row says about the needs feed: the quiet count of the rows
 * that need the member in that app (owner, 2026-10-11). Pure, so the fences
 * read it. `useNavNeeds.ts` feeds it, and `Sidebar.tsx` and the phone
 * drawer in `AppShell.tsx` draw it.
 *
 * The count comes from the ONE feed that My Day and the bell read. Which app
 * owns a row is `NEEDS_APP_HREF` in `needs.ts`, and the count per app is
 * `countsByApp` there. This file decides only what a row shows.
 *
 * **One slot, one rule** (`rowMeta`). A row has one trailing slot:
 *
 *   1. a run badge wins it: amber (an assistant needs an answer), blue (a
 *      new reply) or green (running), the one rule of `runBadge`;
 *   2. else the needs count, a muted number with no fill;
 *   3. else nothing.
 *
 * A run badge is a coloured, live signal, and the needs count is a quiet
 * one, so the live one wins. The needs count stays in the row's spoken name
 * when the run badge takes its place, so it is never lost.
 *
 * The number is capped as the bell caps it (`bellBadge`, "99+"). A count
 * that is a FLOOR (the feed's `capped`: the source stopped at 15 and has no
 * count) prints "15+" and says "15 or more need you".
 *
 * Fence (R7): `navNeeds.test.ts` and `e2e/sidebar-row-counts.spec.ts`.
 */
import { bellBadge } from "./ShellBell";

/**
 * The count's spoken words, as My Day says them: "1 needs you", "3 need
 * you". A floor says "15 or more need you".
 */
export function needsLabel(n: number, floor = false): string {
  const count = Math.max(0, Math.floor(n));
  if (floor) return `${count} or more need you`;
  return `${count} ${count === 1 ? "needs" : "need"} you`;
}

/** What "Add apps" says for the rows in apps the sidebar does not show. */
export function strayNeedsLabel(n: number, floor = false): string {
  return `${needsLabel(n, floor)} in hidden apps`;
}

/** The printed count: "3", "99+", and "15+" for a floor. */
export function needsText(needs: number | undefined, floor = false): string | null {
  const text = bellBadge(needs);
  if (!text || !floor || text.endsWith("+")) return text;
  return `${text}+`;
}

/** What a row's trailing slot shows. */
export type RowMeta = { kind: "run" } | { kind: "needs"; text: string; label: string } | null;

/**
 * THE precedence of a row's trailing slot. `badge` is the row's run badge
 * count (`paneBadge`, `runBadge`), `needs` its needs count, and `floor` true
 * when that count is a floor.
 */
export function rowMeta(badge: number | undefined, needs: number | undefined, floor = false): RowMeta {
  if (badge !== undefined && badge > 0) return { kind: "run" };
  const text = needsText(needs, floor);
  if (!text || needs === undefined) return null;
  return { kind: "needs", text, label: needsLabel(needs, floor) };
}

/**
 * The row's spoken suffix, ", 3 need you", or "" for none. It is said even
 * when a run badge holds the slot, so the count is never out of hearing.
 */
export function needsSuffix(needs: number | undefined, floor = false): string {
  return bellBadge(needs) && needs !== undefined ? `, ${needsLabel(needs, floor)}` : "";
}

/**
 * The row link's whole name when it carries a needs count: "My Email, 3
 * need you", with the run badge's words between when one shows. `undefined`
 * keeps the name the link's text gives.
 *
 * ⚠️ The row sets it as `aria-label`. A screen-reader-only span after the
 * label does not work: the label and the slot are flex items, and Chrome
 * puts a space between flex items, so the name read "My Email , 3 need you".
 */
export function rowName(
  label: string,
  needs: number | undefined,
  badgeLabel?: string,
  floor = false,
): string | undefined {
  const spoken = needsSuffix(needs, floor);
  if (!spoken) return undefined;
  return `${label}${badgeLabel ? `, ${badgeLabel}` : ""}${spoken}`;
}

/**
 * The name of "Add apps": "Add apps", then the run badge's words, then the
 * needs in hidden apps. "Add apps, 1 assistant running, 2 need you in hidden
 * apps". It is the button's `aria-label` open and folded, so neither form
 * loses the run badge's words.
 */
export function addAppsName(needs: number, badgeLabel?: string, floor = false): string {
  const parts = ["Add apps"];
  if (badgeLabel) parts.push(badgeLabel);
  if (needsSuffix(needs, floor)) parts.push(strayNeedsLabel(needs, floor));
  return parts.join(", ");
}
