/**
 * The nav fold of the Studio IDE (`ai_studio.md` §6.3, G3a fix 2).
 *
 * The IDE folds the app nav to give the editor its width, and gives the nav back on leave. Three rules:
 * it folds only when the member allows the auto fold (`autoFoldEnabled`, as `Sidebar.tsx` does), it
 * marks its fold in `sessionStorage` so a reload inside the IDE still restores, and it does not
 * reopen a nav that the dock rule (`dockFoldsNav`) holds folded. Pure, with the storage passed in.
 */
export const IDE_FOLD_KEY = "studio-ide-folded";

const safe = <T,>(fn: () => T, fallback: T): T => {
  try { return fn(); } catch { return fallback; }
};

const session = (): Storage | undefined => (typeof sessionStorage === "undefined" ? undefined : sessionStorage);

/** Fold on open? Only an open nav, and only when the member allows the fold. */
export function foldOnOpen(a: { collapsed: boolean; enabled: boolean }): boolean {
  return !a.collapsed && a.enabled;
}

export function markFolded(s: Storage | undefined = session()): void {
  safe(() => s?.setItem(IDE_FOLD_KEY, "1"), undefined);
}

export function clearFoldMark(s: Storage | undefined = session()): void {
  safe(() => s?.removeItem(IDE_FOLD_KEY), undefined);
}

export function foldMarked(s: Storage | undefined = session()): boolean {
  return safe(() => s?.getItem(IDE_FOLD_KEY) === "1", false);
}

/** Give the nav back on leave? Only a fold that the IDE made, and not one that the dock rule holds. */
export function restoreOnLeave(a: { marked: boolean; collapsed: boolean; dockHolds: boolean }): boolean {
  return a.marked && a.collapsed && !a.dockHolds;
}
