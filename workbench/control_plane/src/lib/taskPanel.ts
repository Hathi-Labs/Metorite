/**
 * The task panel's shared words and width, for Projects and My Tasks.
 *
 * Both apps dock a task beside the list, and until 2026-09-24 the two panels
 * differed in every one of these:
 *
 * - the close button said "Close task" in Projects and "Close detail" in My
 *   Tasks;
 * - the expand said "Open as a full card" in one and "Open full page" in the
 *   other;
 * - the panel was `max-w-md` (448px) in Projects and `w-[380px]` in My Tasks.
 *
 * One value each now. `components/TaskPanelHeader.tsx` draws the words, and
 * `app/projects/lib/panelMode.ts` reads the width and the expand's words and
 * glyphs from here. Fence: `taskPanel.test.ts`.
 */

/** The close button's name, in both apps. */
export const CLOSE_TASK_LABEL = "Close task";

/** The expand toggle's name, by the panel's current stop. */
export const EXPAND_LABELS = {
  side: "Open as a full card",
  full: "Back to the side panel",
} as const;

/** The expand toggle's glyph, by the panel's current stop. */
export const EXPAND_ICONS = {
  side: "Maximize2",
  full: "Minimize2",
} as const;

/**
 * The docked panel's width, in both apps: `max-w-sm`, 24rem.
 *
 * That is 384px at the default density, `DESIGN_SYSTEM.md` §6's 380px side
 * panel written as a named step. A named step and not `w-[380px]`, because a
 * rem width follows the member's density and a pixel width does not
 * (`panelMode.test.ts` holds every stop to a named `max-w-*`).
 *
 * Not Projects' old `max-w-md` (448px): the list beside the panel pays for
 * every pixel, and on a laptop with both rails open the list is what the
 * member came to read. A `max-w-*`, not a `w-*`: the panel is `w-full` in its
 * column, and Projects' phone branch lifts the cap with
 * `[&>aside]:max-w-none`.
 */
export const TASK_PANEL_WIDTH = "max-w-sm";
