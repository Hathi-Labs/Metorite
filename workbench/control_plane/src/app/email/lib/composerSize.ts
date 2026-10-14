/**
 * The size of the body of a reply composer (owner request, 2026-10-14).
 *
 * The inline reply of `EmailDetail` and the `DraftCard` of
 * `ConversationView` share this one rule set, through `ComposerSize.tsx`.
 * A body has three sizes:
 *
 * - **auto**: it grows with its text, from `MIN_LINES` lines up to
 *   `AUTO_CAP` of the visible height of the reading pane. Over the cap it
 *   scrolls inside. Under the cap it shows no scrollbar.
 * - **manual**: the member dragged the grip. The height stays between the
 *   minimum and `MANUAL_CAP` of the pane, and the body scrolls inside when
 *   the text is taller. The height is kept per member in `localStorage`.
 * - **expanded**: the composer fills the visible pane, and the body takes the
 *   room that the header, the AI panel and the footer leave. Collapse goes
 *   back to auto or manual, whichever was on before.
 *
 * On a narrow window there is no grip, so a kept manual height does not
 * apply there. Expand still works.
 *
 * Everything here is pure, because vitest runs in node with no DOM.
 * Fence: `composerSize.test.ts`.
 */

/** The fewest lines that a body shows. */
export const MIN_LINES = 8;
/** The share of the visible pane that an auto body may grow to. */
export const AUTO_CAP = 0.6;
/** The share of the visible pane that a dragged body may reach. */
export const MANUAL_CAP = 0.9;
/** The fewest lines that an expanded body keeps, when the AI panel is tall. */
export const EXPANDED_MIN_LINES = 4;
/** The step of ArrowUp and ArrowDown on the grip, in px. */
export const KEY_STEP_PX = 32;
/** The step of PageUp and PageDown on the grip, in px. */
export const PAGE_STEP_PX = 128;

export type SizeMode = "auto" | "manual";

export interface SizeState {
  /** Auto-grow, or the height that the member set. */
  mode: SizeMode;
  /** The height that the member set, in px. Null in auto. */
  manualPx: number | null;
  /** The composer fills the pane. It keeps `mode` for the collapse. */
  expanded: boolean;
}

export const INITIAL_SIZE: SizeState = { mode: "auto", manualPx: null, expanded: false };

export type SizeAction =
  /** The kept height of the member, read once at open. */
  | { type: "load"; px: number | null }
  /** A drag or a key set a height. */
  | { type: "resize"; px: number }
  /** A double-click on the grip: back to auto-grow. */
  | { type: "reset" }
  /** The Expand / Collapse button. */
  | { type: "toggleExpand" }
  /** The composer closed: it opens again unexpanded. */
  | { type: "close" };

/** The one reducer of the size of a composer body. */
export function sizeReducer(state: SizeState, action: SizeAction): SizeState {
  switch (action.type) {
    case "load":
      if (action.px === null || !(action.px > 0)) return state;
      return { ...state, mode: "manual", manualPx: Math.round(action.px) };
    case "resize":
      return { ...state, mode: "manual", manualPx: Math.round(action.px), expanded: false };
    case "reset":
      return { ...state, mode: "auto", manualPx: null, expanded: false };
    case "toggleExpand":
      return { ...state, expanded: !state.expanded };
    case "close":
      return state.expanded ? { ...state, expanded: false } : state;
  }
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.min(Math.max(n, lo), Math.max(lo, hi));
}

/** The smallest body: `MIN_LINES` lines plus its padding and border. */
export function minBodyPx(linePx: number, chromePx: number, lines: number = MIN_LINES): number {
  return Math.round(lines * linePx + chromePx);
}

/** The tallest auto body. Never below the minimum. */
export function autoCapPx(paneHeight: number, minPx: number): number {
  return Math.max(minPx, Math.round(paneHeight * AUTO_CAP));
}

/** The tallest dragged body. Never below the minimum. */
export function manualMaxPx(paneHeight: number, minPx: number): number {
  return Math.max(minPx, Math.round(paneHeight * MANUAL_CAP));
}

export interface BodyMeasure {
  /** The height of the text, with padding and border, in px. */
  contentPx: number;
  /** The smallest body, from `minBodyPx`. */
  minPx: number;
  /** The visible height of the reading pane, in px. */
  paneHeight: number;
  /** A narrow window: no grip, so no manual height. */
  narrow: boolean;
}

export interface BodySize {
  /** The height of the body, in px. */
  heightPx: number;
  /** The text is taller than the body, so the body scrolls inside. */
  scrolls: boolean;
}

/**
 * The height of a body in auto or manual mode. An expanded body has no fixed
 * height, because flex gives it the rest of the composer.
 */
export function bodySize(state: SizeState, m: BodyMeasure): BodySize {
  if (state.mode === "manual" && state.manualPx !== null && !m.narrow) {
    const heightPx = clamp(state.manualPx, m.minPx, manualMaxPx(m.paneHeight, m.minPx));
    return { heightPx, scrolls: m.contentPx > heightPx };
  }
  const cap = autoCapPx(m.paneHeight, m.minPx);
  const heightPx = clamp(Math.ceil(m.contentPx), m.minPx, cap);
  return { heightPx, scrolls: m.contentPx > cap };
}

/**
 * The height after a key on the grip, or null for a key that the grip does
 * not use. The grip is the bottom edge, so ArrowDown makes the body taller.
 * Escape is not here on purpose: the composer already uses it.
 */
export function keyResize(key: string, currentPx: number, minPx: number, maxPx: number): number | null {
  switch (key) {
    case "ArrowUp":
      return clamp(currentPx - KEY_STEP_PX, minPx, maxPx);
    case "ArrowDown":
      return clamp(currentPx + KEY_STEP_PX, minPx, maxPx);
    case "PageUp":
      return clamp(currentPx - PAGE_STEP_PX, minPx, maxPx);
    case "PageDown":
      return clamp(currentPx + PAGE_STEP_PX, minPx, maxPx);
    case "Home":
      return minPx;
    case "End":
      return Math.max(minPx, maxPx);
    default:
      return null;
  }
}

/** The height after a drag that moved the pointer by `dy` px. */
export function dragResize(startPx: number, dy: number, minPx: number, maxPx: number): number {
  return clamp(Math.round(startPx + dy), minPx, maxPx);
}

// ── The kept height, per member ─────────────────────────────────────────────

/** The prefix of the key. The rest is the member scope, `<email>|<orgId>`. */
export const COMPOSER_HEIGHT_PREFIX = "cc-email-composer-height:";

/** The storage calls that this file uses. A test passes a fake. */
export type HeightStore = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** The key of one member's height, or null with no member to name. */
export function composerHeightKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${COMPOSER_HEIGHT_PREFIX}${scope}`;
}

/** The browser's `localStorage`, or null where reading it throws. */
export function browserStore(): HeightStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The kept height of the member, or null. Never throws. */
export function readComposerHeight(
  scope: string | null | undefined,
  store: HeightStore | null = browserStore(),
): number | null {
  const key = composerHeightKey(scope);
  if (!key || !store) return null;
  try {
    const px = Number(store.getItem(key));
    return Number.isFinite(px) && px > 0 ? Math.round(px) : null;
  } catch {
    return null;
  }
}

/** Keep the member's height, or forget it with null. Never throws. */
export function writeComposerHeight(
  scope: string | null | undefined,
  px: number | null,
  store: HeightStore | null = browserStore(),
): void {
  const key = composerHeightKey(scope);
  if (!key || !store) return;
  try {
    if (px === null || !(px > 0)) store.removeItem(key);
    else store.setItem(key, String(Math.round(px)));
  } catch {
    /* storage full or off: the height lasts for this page only */
  }
}
