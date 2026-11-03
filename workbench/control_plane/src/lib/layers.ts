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
 *   bottomNav      the phone's bottom bar, with its Ask tab.
 *   assistantSheet the phone's assistant sheet. Mounted after the card.
 */

export const LAYER = {
  taskCard: 15,
  assistantDock: 20,
  askBar: 30,
  peekStrip: 40,
  bottomNav: 50,
  assistantSheet: 50,
} as const;

export const LAYER_CLASS = {
  taskCard: "z-[15]",
  assistantDock: "z-[20]",
  askBar: "z-[30]",
  peekStrip: "z-[40]",
  bottomNav: "z-50",
  assistantSheet: "z-50",
} as const;

/**
 * A full-screen task card. On a desktop it fills the page column, so the shell
 * bar, the Ask bar and the docked assistant stay in view. The `pb` keeps the
 * Ask bar's own row clear. On a phone it ends above the bottom nav, so the
 * Ask tab stays in reach.
 */
export const TASK_CARD_FRAME = {
  desktop: `absolute inset-0 ${LAYER_CLASS.taskCard} sm:pb-20`,
  phone: `fixed inset-x-0 top-0 ${LAYER_CLASS.taskCard} bottom-[calc(3.5rem+env(safe-area-inset-bottom,0px))]`,
} as const;
