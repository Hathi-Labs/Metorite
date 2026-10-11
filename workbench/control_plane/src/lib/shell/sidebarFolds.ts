/**
 * Which sidebar categories a member folded, kept in this browser per
 * ACCOUNT (owner, 2026-10-11). Pure, with no React, so `lib/sessions.ts` can
 * clear it on sign-out without importing the sidebar.
 *
 * The rules:
 *
 * - **One key per account,** `cc-sidebar-folds:<email>|<organizationId>`,
 *   the scope of `chatScope` in `lib/sessions.ts`. Two members on one
 *   browser never read each other's folds.
 * - **A sign-out clears it** with the chat caches and the shell layout,
 *   through the same two functions (`clearSignedOutAccount`,
 *   `clearAccountNamespaces`).
 * - **The category of the current route never stays folded**
 *   (`isFolded`). A member must always see the row they are on.
 * - It holds category ids only, and never a tenant row.
 *
 * Fence: `sidebarFolds.test.ts`.
 */
import { clearAccountKeys } from "./shellCache";

export const SIDEBAR_FOLDS_PREFIX = "cc-sidebar-folds:";

/** Category id → folded. An id that is not here is open. */
export type Folds = Record<string, boolean>;

/** The key of one account's folds, or null with no account to name. */
export function sidebarFoldsKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${SIDEBAR_FOLDS_PREFIX}${scope}`;
}

function storage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** The account's folds. Empty with no account, or on any fault. */
export function readFolds(scope: string | null | undefined): Folds {
  const key = sidebarFoldsKey(scope);
  const store = storage();
  if (!key || !store) return {};
  try {
    const raw = JSON.parse(store.getItem(key) ?? "{}");
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
    return Object.fromEntries(Object.entries(raw).filter(([, v]) => v === true)) as Folds;
  } catch {
    return {};
  }
}

/** Keep the account's folds. Never throws. */
export function writeFolds(scope: string | null | undefined, folds: Folds): void {
  const key = sidebarFoldsKey(scope);
  const store = storage();
  if (!key || !store) return;
  try {
    const kept = Object.fromEntries(Object.entries(folds).filter(([, v]) => v));
    store.setItem(key, JSON.stringify(kept));
  } catch {
    /* storage full or off: the folds last for this page only */
  }
}

/** Remove the folds of these emails, in every organization. Never throws. */
export function clearSidebarFolds(emails: readonly string[]): void {
  clearAccountKeys(SIDEBAR_FOLDS_PREFIX, emails);
}

/**
 * Whether a category draws folded. A stored fold applies, except on the
 * category that holds the current route: that one is always open.
 */
export function isFolded(folds: Folds, id: string, holdsCurrent: boolean): boolean {
  return !!folds[id] && !holdsCurrent;
}

/** The heading of a category. Folded, it carries its count: "Apps (3)". */
export function foldHeading(label: string, count: number, folded: boolean): string {
  return folded ? `${label} (${count})` : label;
}
