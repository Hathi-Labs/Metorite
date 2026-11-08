/**
 * Projects · the view tab row (WS-64 OP-12, `one_projects_app.md` §5.3, D-OP-17).
 *
 * The six canvases and the project's saved views are ONE row of tabs under the
 * title bar. The row never scrolls sideways. It does three things in this
 * order when the pane is narrow:
 *
 *   1. Below `ICON_ONLY_BELOW` px of window, or when the labels do not fit, the
 *      canvas tabs show their icon alone. The tooltip names the key.
 *   2. The saved views that still do not fit fold into a "More" menu, last
 *      first. The six canvases stay while their icons fit.
 *   3. Only when even the six icons, "More" and "+ View" do not fit do the
 *      canvases fold too, and the canvas on screen is the last to go.
 *
 * The arithmetic is here, as pure functions, because the vitest runner has no
 * layout. The widths are estimates and err wide: a tab that folds too soon is
 * one click further, and a tab that overflows is a scrollbar.
 *
 * Adding a canvas (SP-3, a Backlog) is one entry in `VIEW_MODES`. Nothing here
 * names a canvas.
 */

/** Window width, in px, below which the canvas tabs show their icon alone. */
export const ICON_ONLY_BELOW = 1280;

/** Pixels between two tabs (`gap-1`). */
const GAP = 4;
/** Estimated width of the "More" button. */
export const MORE_WIDTH = 76;
/** Estimated width of the "+ View" button. */
export const ADD_WIDTH = 78;
/** "More" and "+ View" when the row is icon-only: a bare icon button. */
export const ICON_ACTION = 32;
/** The divider between the canvases and the saved views, with its margin. */
export const DIVIDER_WIDTH = 13;
/** An icon-only tab: 16 px glyph and the button padding. */
const ICON_TAB = 30;
/** A labelled tab: padding, glyph and gap, then the label at about 6.7 px a letter (measured 2026-11-08). */
const LABEL_FIXED = 46;
const LABEL_PER_CHAR = 6.7;
/** A saved-view tab is capped, as its name truncates. */
const VIEW_TAB_MAX = 176;

export interface TabSpec {
  id: string;
  label: string;
  kind: "canvas" | "view";
}

/** Estimated width of one tab, in px. */
export function tabWidth(tab: TabSpec, iconOnly: boolean): number {
  if (tab.kind === "canvas" && iconOnly) return ICON_TAB;
  const full = LABEL_FIXED + tab.label.length * LABEL_PER_CHAR;
  return tab.kind === "view" ? Math.min(full, VIEW_TAB_MAX) : full;
}

/** Width of a run of tabs with the gaps between them. */
function runWidth(tabs: readonly TabSpec[], iconOnly: boolean): number {
  if (tabs.length === 0) return 0;
  const sum = tabs.reduce((total, tab) => total + tabWidth(tab, iconOnly), 0);
  const divider = tabs.some((t) => t.kind === "canvas") && tabs.some((t) => t.kind === "view");
  return sum + GAP * (tabs.length - 1) + (divider ? DIVIDER_WIDTH : 0);
}

export interface TabLayout {
  iconOnly: boolean;
  /** The tabs drawn in the row, in order. */
  visible: string[];
  /** The tabs in the "More" menu, in order. Empty when all fit. */
  folded: string[];
}

/**
 * Which tabs fit in `available` px. `available` is the width the row MAY take,
 * and the "+ View" button is counted inside it. `active` names the tabs on
 * screen (the canvas, the saved view), and null entries are ignored.
 *
 * The order of loss: saved views fold first, last first. The six canvases stay
 * while their icons, "More" and "+ View" fit. Only then do canvases fold, last
 * first. A tab on screen is the last to go in its own group.
 */
export function foldTabs({
  tabs,
  active,
  available,
  iconOnly,
}: {
  tabs: readonly TabSpec[];
  active: readonly (string | null)[];
  available: number;
  iconOnly: boolean;
}): { visible: string[]; folded: string[] } {
  const room = (spare: number, list: readonly TabSpec[]) => runWidth(list, iconOnly) + GAP <= spare;
  const addWidth = iconOnly ? ICON_ACTION : ADD_WIDTH;
  const moreWidth = iconOnly ? ICON_ACTION : MORE_WIDTH;
  const whole = available - addWidth;
  if (room(whole, tabs)) return { visible: tabs.map((t) => t.id), folded: [] };

  const spare = whole - moreWidth - GAP;
  const canvases = tabs.filter((t) => t.kind === "canvas");
  const views = tabs.filter((t) => t.kind === "view");
  const isActive = (t: TabSpec) => active.includes(t.id);

  let shown: TabSpec[];
  if (room(spare, canvases)) {
    // Every canvas stays. The saved views take what is left: the one on screen
    // first, then the others in order.
    shown = canvases.slice();
    const order = [...views.filter(isActive), ...views.filter((v) => !isActive(v))];
    for (const view of order) {
      if (room(spare, [...shown, view])) shown.push(view);
    }
  } else {
    // Not even the six icons fit. The canvases fold from the end, and the one on
    // screen stays.
    shown = canvases.slice();
    while (shown.length > 1 && !room(spare, shown)) {
      const at = shown.map((t) => !isActive(t)).lastIndexOf(true);
      shown.splice(at === -1 ? shown.length - 1 : at, 1);
    }
  }
  const ids = new Set(shown.map((t) => t.id));
  return {
    visible: tabs.filter((t) => ids.has(t.id)).map((t) => t.id),
    folded: tabs.filter((t) => !ids.has(t.id)).map((t) => t.id),
  };
}

/**
 * The whole decision. Icons only when the window is narrower than
 * `ICON_ONLY_BELOW`, or when the labelled canvases would fold a tab that the
 * icons would keep.
 */
export function layoutTabs({
  tabs,
  active,
  available,
  viewport,
}: {
  tabs: readonly TabSpec[];
  active: readonly (string | null)[];
  available: number;
  viewport: number;
}): TabLayout {
  const labelled = foldTabs({ tabs, active, available, iconOnly: false });
  const iconOnly = viewport < ICON_ONLY_BELOW || labelled.folded.length > 0;
  if (!iconOnly) return { iconOnly, ...labelled };
  return { iconOnly, ...foldTabs({ tabs, active, available, iconOnly: true }) };
}

/** The icon of a saved view's tab: the canvas it was saved from. */
export function viewTabIcon(viewType: string): string {
  return viewType === "board" ? "Kanban" : "List";
}

/** The tooltip of a canvas tab: its name and its key. */
export function canvasTitle(label: string, key: string | undefined): string {
  return key ? `${label} (${key})` : label;
}
