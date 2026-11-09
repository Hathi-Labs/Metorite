/**
 * The panel on My Day — the pure rules (PK-1, task 2469, D204,
 * `my_day_feed.md` §11.5).
 *
 * The owner, 2026-11-09: "when I open any task card ... it opens up the
 * projects app itself, and I need to go back to My Day to look at the rest
 * of my day." So a plain click on a task row of My Day opens the task in a
 * panel on My Day, and the path stays `/`.
 *
 * This file decides four things, and does no I/O:
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
 *    before the first one.
 *
 * Fence: `myDayPeek.test.ts`.
 */

import { isInAppPath } from "@/lib/inAppLink";

/** What the panel shows. PK-1 knows a task. A later kind joins the union. */
export type PeekTarget = { kind: "task"; id: string };

export type PeekKind = PeekTarget["kind"];

/** The query key of the open panel, on `/`. */
export const PEEK_PARAM = "peek";

/** The key of the page's own entry count, in `history.state`. */
export const PEEK_DEPTH_KEY = "myDayPeekDepth";

/** The attribute on the panel's frame. Escape from inside it is the panel's own. */
export const PEEK_FRAME_ATTR = "data-my-day-peek";

/** The one label of the way out to the owning app (D101 point 3). */
export const OPEN_IN_PROJECTS = "Open in Projects";

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
  if (click.defaultPrevented) return false;
  if (click.button !== 0) return false;
  if (click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return false;
  if (link.download) return false;
  if (link.target && link.target !== "_self") return false;
  return true;
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
