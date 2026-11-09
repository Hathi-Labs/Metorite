/**
 * The panel on My Day — the pure rules (PK-1, task 2469, D204,
 * `my_day_feed.md` §11.5).
 *
 * The owner, 2026-11-09: "when I open any task card ... it opens up the
 * projects app itself, and I need to go back to My Day to look at the rest
 * of my day." So a plain click on a task row of My Day opens the task in a
 * panel on My Day, and the path stays `/`.
 *
 * This file decides these things, and does no I/O:
 *
 * 1. **Which link opens in the panel** ({@link peekTargetOf}). A KIND
 *    registry answers, so PK-2 (email) and PK-3 (reminders) each add one row
 *    to {@link PEEK_KINDS} and change nothing else here.
 * 2. **Which click the panel takes** ({@link peekTakesClick}). Only a plain
 *    left click. Ctrl, Cmd, Shift and Alt, a middle click, a `target` and a
 *    `download` keep the browser's own act, so Projects opens in a new tab.
 * 3. **How the URL carries the panel** ({@link peekHref}). `?peek=task:<id>`
 *    on `/`. So Back closes the panel, and a reload opens it again (M13).
 * 4. **How a close walks back** ({@link closeMove}). The page counts its own
 *    history entries in `history.state`, and a close returns to the entry
 *    before the first one ({@link landingClears} for a link that came in).
 * 5. **Where the card stands** ({@link MY_DAY_PAGE_MIN_REM}, {@link isDocked}).
 *    The frame is the record card's (`components/CardPanel.tsx`, D181).
 * 6. **Which row is open** ({@link PEEK_ROW}, {@link PEEK_OPEN_ATTR}).
 *
 * Fence: `myDayPeek.test.ts`.
 */

import type { CardStop } from "@/components/CardPanel";
import { shouldIntercept } from "@/lib/controlLink";
import { isInAppPath } from "@/lib/inAppLink";

/** What the panel shows. PK-1 knows a task. A later kind joins the union. */
export type PeekTarget = { kind: "task"; id: string };

export type PeekKind = PeekTarget["kind"];

/** The query key of the open panel, on `/`. */
export const PEEK_PARAM = "peek";

/** The key of the page's own entry count, in `history.state`. */
export const PEEK_DEPTH_KEY = "myDayPeekDepth";

/** The attribute on the panel's body, inside the card. Its value is the kind. */
export const PEEK_FRAME_ATTR = "data-my-day-peek";

/** The one label of the way out to the owning app (D101 point 3). */
export const OPEN_IN_PROJECTS = "Open in Projects";

/** The attribute of the way out. A click on it leaves My Day, always. */
export const OPEN_APP_ATTR = "data-peek-open-app";

/**
 * The attribute on the row whose task is open. `globals.css` gives it the
 * house selected tint (`bg-primary/10`, AGENTS.md rule 6), and the link in
 * it carries `aria-current`.
 */
export const PEEK_OPEN_ATTR = "data-peek-open";

/**
 * The row of a link: the feed row, the framed feed card, the row box of a
 * list, or the list item. The nearest one wins, so each card keeps its
 * markup and My Day marks it from one place.
 */
export const PEEK_ROW = "[data-feed-row], [data-home-card], li > div, li";

/**
 * The narrowest page My Day keeps beside a docked card, in rem. A feed row
 * keeps its acts at its end (`sm:` is the WINDOW width), so a page of 20
 * rem (the card's default) left a row with no title at 1024 (PK-1 verifier,
 * 2026-11-09). With 36, a docked page keeps about 200 px of title. A
 * narrower page gets the card over it (`sideCardFits`).
 */
export const MY_DAY_PAGE_MIN_REM = 36;

/**
 * The card stands beside the page as a column, so the page is narrower.
 * Only then does My Day draw its cards in one column. Over the page and on a
 * phone, the page keeps its grid.
 */
export function isDocked(open: boolean, stop: CardStop | null): boolean {
  return open && stop === "side";
}

/**
 * The narrowest card that draws the task body in two columns, in rem. The
 * body pairs its sections at the `full` stop (`TaskBody`'s `twoColumn`). A
 * card that stands over a narrow page is narrower than `max-w-3xl`: at 768
 * it was 430 px, and two columns cut each field (PK-1 round 2).
 */
export const TWO_COLUMN_MIN_REM = 40;

/**
 * The width stop `TaskPanel` draws at. `full` only for a card over the page
 * that is wide enough for two columns. A docked card and a phone are `side`.
 */
export function panelModeFor(stop: CardStop, cardWidth: number, rem: number): "side" | "full" {
  return stop === "full" && cardWidth >= TWO_COLUMN_MIN_REM * rem ? "full" : "side";
}

/**
 * An id the panel will put in its URL and read: letters, digits, `-` and
 * `_`. A task id is a UUID. Anything else is not a row of ours.
 */
const ID = /^[A-Za-z0-9_-]{1,80}$/;

/** A parsed in-app link: its path and its query. */
interface InAppUrl {
  pathname: string;
  params: URLSearchParams;
  hash: string;
}

/** One kind of thing the panel can show. */
export interface PeekKindRule {
  kind: PeekKind;
  /** The target of an in-app link, or null when this kind does not take it. */
  of: (url: InAppUrl) => PeekTarget | null;
}

/** The only key of a link, or null when it has another key or none. */
function soleKey(params: URLSearchParams, allowed: readonly string[]): string | null {
  const keys = [...params.keys()];
  if (keys.length !== 1 || !allowed.includes(keys[0])) return null;
  const value = params.get(keys[0]) ?? "";
  return ID.test(value) ? value : null;
}

/**
 * A task: `/projects?task=<id>`, the deep link of `taskDeepLink`, and
 * `/tasks?task=<id>` or `/tasks?item=<id>`, which `tasksRedirect.ts` sends
 * to the same place. A link with any other key (an org, a tab, an app) is
 * not a plain task link, so it keeps opening Projects.
 */
const TASK_KIND: PeekKindRule = {
  kind: "task",
  of: ({ pathname, params, hash }) => {
    if (hash) return null;
    const id =
      pathname === "/projects"
        ? soleKey(params, ["task"])
        : pathname === "/tasks"
          ? soleKey(params, ["task", "item"])
          : null;
    return id ? { kind: "task", id } : null;
  },
};

/**
 * The kinds the panel can show, in order. PK-2 adds the email kind and PK-3
 * the reminder kind here, each with its own panel in `MyDayPeekPanel.tsx`.
 */
export const PEEK_KINDS: readonly PeekKindRule[] = [TASK_KIND];

/** The fixed origin a link is read against. No link can name it. */
const BASE = "https://in-app.invalid";

/**
 * What the panel would show for a link, or null when the link opens its app.
 * Only an in-app path counts (`isInAppPath`, the one rule).
 */
export function peekTargetOf(href: string | null | undefined): PeekTarget | null {
  if (!href || !isInAppPath(href)) return null;
  let url: URL;
  try {
    url = new URL(href, BASE);
  } catch {
    return null;
  }
  const parsed: InAppUrl = { pathname: url.pathname, params: url.searchParams, hash: url.hash };
  for (const rule of PEEK_KINDS) {
    const target = rule.of(parsed);
    if (target) return target;
  }
  return null;
}

/** The value of the `peek` key: `task:<id>`. */
export function peekParamOf(target: PeekTarget): string {
  return `${target.kind}:${target.id}`;
}

/** The target in a `peek` value, or null for a value no kind reads. */
export function peekFromParam(value: string | null | undefined): PeekTarget | null {
  if (!value) return null;
  const at = value.indexOf(":");
  if (at <= 0) return null;
  const kind = value.slice(0, at);
  const id = value.slice(at + 1);
  if (!ID.test(id)) return null;
  if (!PEEK_KINDS.some((rule) => rule.kind === kind)) return null;
  return { kind: kind as PeekKind, id };
}

/** True when two targets name the same thing. */
export function samePeek(a: PeekTarget | null, b: PeekTarget | null): boolean {
  return a?.kind === b?.kind && a?.id === b?.id;
}

/**
 * The address with the panel set, or with it gone for `null`. Every other
 * key and the hash stay as they are.
 */
export function peekHref(
  location: { pathname: string; search: string; hash: string },
  target: PeekTarget | null,
): string {
  const params = new URLSearchParams(location.search);
  if (target) params.set(PEEK_PARAM, peekParamOf(target));
  else params.delete(PEEK_PARAM);
  const query = params.toString();
  return `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
}

/** The facts of a click that decide whether the panel takes it. */
export interface ClickFacts {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
}

/** The facts of the link that was clicked. */
export interface LinkFacts {
  /** The `target` attribute, or "" when the link has none. */
  target: string;
  /** The link has a `download` attribute. */
  download: boolean;
}

/**
 * Whether the panel takes a click. Only a plain left click that nothing
 * else took. Every other click keeps the browser's own act: Ctrl or Cmd
 * opens a new tab, Shift a new window, Alt saves. A middle click fires no
 * `click` at all, so it never reaches this.
 */
export function peekTakesClick(click: ClickFacts, link: LinkFacts): boolean {
  // The one rule of a click the app may take (`lib/controlLink.ts`).
  if (!shouldIntercept(click)) return false;
  if (link.download) return false;
  if (link.target && link.target !== "_self") return false;
  return true;
}

/** What the panel reads of the link that was clicked. */
export interface ClickedLink extends LinkFacts {
  /** The raw `href` attribute. */
  href: string | null;
  /** The link is the way out to the app ({@link OPEN_APP_ATTR}). */
  openApp: boolean;
}

/** The parts of an element that {@link clickedLinkOf} reads. A test passes a plain object. */
interface LinkLike {
  getAttribute(name: string): string | null;
  hasAttribute(name: string): boolean;
  closest(selector: string): unknown;
}

/**
 * The link a click landed in, inside the element that holds the handler, or
 * null. It reads the DOM through four calls only, so the node runner can
 * hold it with plain objects.
 */
export function clickedLinkOf(event: {
  target: unknown;
  currentTarget: { contains(node: never): boolean };
}): ClickedLink | null {
  const from = event.target as { closest?: (selector: string) => LinkLike | null } | null;
  const link = from?.closest?.("a[href]") ?? null;
  if (!link || !event.currentTarget.contains(link as never)) return null;
  return {
    href: link.getAttribute("href"),
    target: link.getAttribute("target") ?? "",
    download: link.hasAttribute("download"),
    openApp: link.closest(`[${OPEN_APP_ATTR}]`) !== null,
  };
}

/**
 * What a click opens in the panel, or null when the click is the browser's.
 * The capture handler of the page column and the one of the card both ask
 * this, so the click rule and the link rule cannot part.
 */
export function peekOfClick(click: ClickFacts, link: ClickedLink | null): PeekTarget | null {
  if (!link || link.openApp) return null;
  if (!peekTakesClick(click, link)) return null;
  return peekTargetOf(link.href);
}

/** The phone bar tab that opens the assistant (`AppShell`'s `sendBarTap("ask")`). */
export const ASK_TAB = "ask";

/**
 * Whether a tap on a phone bar tab closes the panel. Every tab does, but
 * Ask: the assistant's sheet paints above a task card (D172.4,
 * `lib/layers.ts`), so the member asks about the task and comes back to it.
 */
export function barTapCloses(tab: string | null): boolean {
  return tab !== ASK_TAB;
}

/** How many entries the page pushed for the panel, read off `history.state`. */
export function peekDepthOf(state: unknown): number {
  if (!state || typeof state !== "object") return 0;
  const value = (state as Record<string, unknown>)[PEEK_DEPTH_KEY];
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : 0;
}

/**
 * How a close moves through history. With entries the page pushed, it goes
 * back over all of them, so ✕ after two tasks closes the panel, and Forward
 * opens it again. With none (the member came in on a link that had `peek`),
 * it replaces the entry with the address that has no panel.
 */
export function closeMove(depth: number): { go: number } | { replace: true } {
  return depth > 0 ? { go: -depth } : { replace: true };
}

/**
 * Whether the entry a close walked back to must lose its panel too. A
 * member who came in on `/?peek=task:A` and then opened B walks back to the
 * entry of A, which the page did not push (depth 0). ✕ means "close", so
 * that entry is replaced with the address that has no panel.
 */
export function landingClears(closing: boolean, depth: number, holdsPeek: boolean): boolean {
  return closing && depth === 0 && holdsPeek;
}

/**
 * A server message ends with a stop most of the time. The panel adds its own
 * sentence after it, so it drops one trailing stop and never prints "..".
 */
export function sentenceOf(message: string): string {
  return message.trim().replace(/[.\s]+$/, "");
}
