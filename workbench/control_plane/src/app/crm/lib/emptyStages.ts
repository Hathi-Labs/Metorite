// ── Empty stages on the Deals board (owner report, 2026-10-12) ─────────────
//
// A mirrored Zoho account has stages that hold no deal. In Mirror mode no card
// moves, so an empty column is only noise, and the board hides it. In Native
// mode an empty column is a drop target, so it shows. A "Show empty stages"
// toggle in the title bar changes it, and this browser keeps the choice for
// each member and organization.
//
// Pure, so the rules have tests without a browser. The store is injected, and
// every read and write is in a try/catch: a blocked store loses the choice and
// nothing else.

import type { CrmMode } from "./connect";

/** The key prefix. The scope `<email>|<organizationId>` follows it. */
export const EMPTY_STAGES_PREFIX = "metorite:crm:empty-stages:";

type Store = Pick<Storage, "getItem" | "setItem">;

/** The browser's local store, or null where reading it throws. */
export function localStore(): Store | null {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

/** The key of one member's choice, or null with no member to name. */
export function emptyStagesKey(scope: string | null | undefined): string | null {
  if (!scope || !scope.includes("|")) return null;
  return `${EMPTY_STAGES_PREFIX}${scope}`;
}

/** What the board does with no stored choice: hide in Mirror, show in Native. */
export function defaultShowEmpty(mode: CrmMode): boolean {
  return mode !== "mirror";
}

/** The member's stored choice, or null when there is none. Never throws. */
export function readShowEmpty(
  scope: string | null | undefined,
  store: Store | null = localStore()
): boolean | null {
  const key = emptyStagesKey(scope);
  if (!key || !store) return null;
  try {
    const raw = store.getItem(key);
    return raw === "show" ? true : raw === "hide" ? false : null;
  } catch {
    return null;
  }
}

/** Keep the member's choice. Never throws. */
export function writeShowEmpty(
  scope: string | null | undefined,
  show: boolean,
  store: Store | null = localStore()
): void {
  const key = emptyStagesKey(scope);
  if (!key || !store) return;
  try {
    store.setItem(key, show ? "show" : "hide");
  } catch {
    // Private window or blocked site data: the choice lasts for this visit.
  }
}

/** The stored choice wins. With none, the mode decides. */
export function showEmptyStages(mode: CrmMode, stored: boolean | null): boolean {
  return stored ?? defaultShowEmpty(mode);
}

/**
 * The lanes the board draws. A lane that holds no deal under the current
 * filters is hidden unless `showEmpty` is on. `count` covers the whole lane
 * under those filters, so it is the right test, and `rows` is not.
 */
export function visibleLanes<T extends { count: number }>(
  lanes: readonly T[],
  showEmpty: boolean
): T[] {
  return showEmpty ? [...lanes] : lanes.filter((lane) => lane.count > 0);
}
