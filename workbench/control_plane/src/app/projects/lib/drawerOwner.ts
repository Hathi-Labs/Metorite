/**
 * Projects · who may close the shell drawer (WS-64 #8).
 *
 * On a phone the page hands a sheet (the tree, the view switch) to the
 * shell's one drawer. The shell's own Menu tab opens the SAME drawer with the
 * page's `sheet` null. The page's feed effect re-runs when the tree refetches,
 * and it used to close the drawer whenever `sheet` was null, so a refetch shut
 * the Menu by itself. The page closes the drawer only when it opened it.
 */
export type DrawerStep = "show" | "close" | "leave";

/** What the feed effect does for a `sheet` state, given who opened the drawer. */
export function drawerStep(sheetOpen: boolean, pageOwnsDrawer: boolean): DrawerStep {
  if (sheetOpen) return "show";
  return pageOwnsDrawer ? "close" : "leave";
}
