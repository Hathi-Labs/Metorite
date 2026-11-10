/**
 * The sidebar folds while you work (`navigation_shell.md` §3.2).
 *
 * Owner directive, 2026-10-06: when a member opens an app from the sidebar and
 * starts to work in it, the sidebar folds to its icon rail, so the app gets the
 * width. The fold must announce itself, so the member knows the rail can open
 * again.
 *
 * The rule, in three steps:
 *
 *   1. A plain click on a sidebar link ARMS the fold.
 *   2. The member's first click or key press inside `<main>` FOLDS the sidebar,
 *      and disarms it. A press inside the sidebar itself does nothing.
 *
 * ⚠️ It folds on `click`, never on `pointerdown`. The fold moves the app to
 * the left. A fold on the press moves the button out from under the pointer
 * before the release, and the member's first click lands on nothing.
 *   3. A member who opens the sidebar again keeps it open until the next
 *      sidebar link.
 *
 * ⚠️ The arm is the point. Folding on EVERY click in `<main>` would fold the
 * sidebar the moment a member opened it to look at it, which is a fight. A
 * member only reaches the arm by choosing an app.
 *
 * Three keys in `localStorage`, all per browser, all optional. Every read and
 * write is in try/catch, because private windows throw.
 *
 * Fence: `sidebarFold.test.ts` (the rules) and `e2e/sidebar-fold.spec.ts` (the
 * behaviour in a browser).
 */

/** "off" when the member chose "Keep it open". Absent means ON. */
export const AUTO_FOLD_KEY = "cc-sidebar-autofold";
/** "1" when the sidebar is folded. It survives a reload. */
export const COLLAPSED_KEY = "cc-sidebar-collapsed";
/** How many times the tip has shown. It stops at {@link HINT_LIMIT}. */
export const HINT_COUNT_KEY = "cc-sidebar-fold-hints";

/** The tip shows on the first three folds. After that, only the pulse. */
export const HINT_LIMIT = 3;
/** The tip closes by itself after this many milliseconds. */
export const HINT_DISMISS_MS = 8000;

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage unavailable. The choice lasts for this page only.
  }
}

export function autoFoldEnabled(): boolean {
  return read(AUTO_FOLD_KEY) !== "off";
}

/** Fired on `window` when the choice changes, so every view of it agrees. */
export const AUTO_FOLD_EVENT = "cc-sidebar-autofold-change";

export function setAutoFoldEnabled(on: boolean): void {
  write(AUTO_FOLD_KEY, on ? "on" : "off");
  try {
    window.dispatchEvent(new Event(AUTO_FOLD_EVENT));
  } catch {
    // No window: a test, or the server. Nothing listens there.
  }
}

/**
 * Something floating is open: a menu, a listbox, a dialog or an expanded
 * trigger, outside the sidebar.
 *
 * ⚠️ The fold waits while one is open. Several of our menus measure their
 * trigger ONE time, when they open. The fold then moves the trigger about
 * 200px to the left, and the menu stays where the trigger was.
 *
 * ⚠️ The fold's own control is not a menu. With the full-width shell bar
 * (owner, 2026-10-09) it sits in the bar, outside the sidebar, and it says
 * `aria-expanded="true"` while the sidebar is open. Read as a menu, it held
 * every fold for ever. So anything inside {@link FOLD_CONTROL_ATTR} is skipped.
 */
export function floatingOpen(root: ParentNode, sidebar: Element | null): boolean {
  const open = root.querySelectorAll(
    '[aria-expanded="true"], [role="menu"], [role="listbox"], [role="dialog"], [role="alertdialog"]',
  );
  for (const el of Array.from(open)) {
    if (sidebar?.contains(el)) continue;
    if (typeof el.closest === "function" && el.closest(`[${FOLD_CONTROL_ATTR}]`)) continue;
    return true;
  }
  return false;
}

/** The fold control's wrapper carries this attribute (`SidebarFold.tsx`). */
export const FOLD_CONTROL_ATTR = "data-sidebar-fold";

export function readCollapsed(): boolean {
  return read(COLLAPSED_KEY) === "1";
}

export function writeCollapsed(collapsed: boolean): void {
  write(COLLAPSED_KEY, collapsed ? "1" : "0");
}

/**
 * True when the tip should show for this fold, and counts it.
 *
 * A corrupt count reads as zero, so the member sees the tip again rather than
 * never.
 */
export function takeHint(): boolean {
  const n = Number.parseInt(read(HINT_COUNT_KEY) ?? "0", 10);
  const seen = Number.isFinite(n) && n > 0 ? n : 0;
  if (seen >= HINT_LIMIT) return false;
  write(HINT_COUNT_KEY, String(seen + 1));
  return true;
}

/** The parts of a mouse event that decide whether a link click is plain. */
export interface ClickLike {
  button: number;
  metaKey: boolean;
  ctrlKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  defaultPrevented: boolean;
}

/**
 * A click that navigates THIS tab.
 *
 * ⚠️ A ctrl-click or a middle click opens the app in a new tab, and this tab
 * stays where it is. Arming on it would fold the sidebar on the member's next
 * click here, in an app they did not just open.
 */
export function isPlainNavClick(e: ClickLike): boolean {
  return (
    e.button === 0 &&
    !e.metaKey &&
    !e.ctrlKey &&
    !e.shiftKey &&
    !e.altKey &&
    !e.defaultPrevented
  );
}

/** Keys that are not work: a modifier alone, or Escape. */
const NOT_WORK = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock", "Escape"]);

/** The parts of an input event that decide whether it counts as work. */
export interface InteractionLike {
  type: string;
  /** Present on a key event. */
  key?: string;
  /** Present on a mouse event. 0 is the primary button. */
  button?: number;
}

/**
 * True when the event is the member working: a primary click, or a key that
 * is not a modifier alone.
 *
 * A wheel or a scroll is reading, not working, so it never folds. A right
 * click opens a menu, which is not work either.
 */
export function isWorkEvent(e: InteractionLike): boolean {
  if (e.type === "click") return (e.button ?? 0) === 0;
  if (e.type === "keydown") return !!e.key && !NOT_WORK.has(e.key);
  return false;
}

/** Everything the fold decision reads, so the decision is one pure function. */
export interface FoldInput {
  armed: boolean;
  enabled: boolean;
  collapsed: boolean;
  /** The event target is inside the sidebar. */
  inSidebar: boolean;
  /** The event target is inside the app's `<main>`. */
  inMain: boolean;
  work: boolean;
}

/**
 * Fold now?
 *
 * Only an armed, expanded sidebar folds, and only on work inside the app. A
 * dialog renders in a portal outside `<main>`, so a click in it waits for the
 * next click in the app.
 */
export function shouldFold(i: FoldInput): boolean {
  return i.armed && i.enabled && !i.collapsed && !i.inSidebar && i.inMain && i.work;
}
