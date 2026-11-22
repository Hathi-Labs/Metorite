/**
 * The layer order of the shell, in one place (TC-1, D172.4, 2026-11-03).
 *
 * A task card never covers the assistant. The card sits at the lowest layer
 * here, and everything the member needs to chat sits above it. The class
 * strings are literal so Tailwind sees them. `layers.test.ts` reads the
 * numbers and pins the order.
 *
 *   taskCard       a task card (Projects full, Calendar, /tasks). Inside the
 *                  page column on a desktop, above the nav on a phone.
 *   assistantDock  the desktop dock when it floats over the page.
 *   askBar         the Ask bar over the page column.
 *   peekStrip      the phone's assistant strip.
 *   assistantSheet the phone's assistant sheet. Mounted after the card. It
 *                  ends at the bar's top edge (MOB-9).
 *   bottomNav      the phone's bottom bar, with its Ask tab. It paints above
 *                  every overlay of the tree, the shell drawer, the sheets and
 *                  the anchored panels, so a tab stays in reach (MOB-9, task
 *                  2002). A confirmation (`Modal` `alert`, z-[100]) and the
 *                  toasts (z-[110]) paint above it. On a phone the
 *                  confirmation's layer ends at the bar's top edge, like
 *                  every layer, so a tap on a tab cancels it. That fails safe.
 *
 * ⚠️ The bar covers the foot of an overlay. `globals.css` ends every full-height
 * fixed layer at the bar's top edge while the phone layout shows
 * (`:root[data-phone-nav]`), so nothing hides under it. Fence: `layers.test.ts`.
 */

export const LAYER = {
  taskCard: 15,
  assistantDock: 20,
  askBar: 30,
  peekStrip: 40,
  assistantSheet: 50,
  bottomNav: 96,
} as const;

export const LAYER_CLASS = {
  taskCard: "z-[15]",
  assistantDock: "z-[20]",
  askBar: "z-[30]",
  peekStrip: "z-[40]",
  assistantSheet: "z-50",
  bottomNav: "z-[96]",
} as const;

/**
 * A full-screen task card. On a desktop it fills the page column, so the shell
 * bar, the Ask bar and the docked assistant stay in view. The `pb` keeps the
 * Ask bar's own row clear. On a phone the box ends at the bottom nav's top edge
 * (the `:root[data-phone-nav]` rule in globals.css), and `.task-card-phone`
 * pads its foot by the peek strip's height while that shows. So the Ask tab
 * and the strip stay in reach and no band of page shows between card and nav.
 */
export const TASK_CARD_FRAME = {
  desktop: `absolute inset-0 ${LAYER_CLASS.taskCard} sm:pb-20`,
  phone: `fixed inset-x-0 top-0 bottom-0 ${LAYER_CLASS.taskCard} task-card-phone`,
} as const;
