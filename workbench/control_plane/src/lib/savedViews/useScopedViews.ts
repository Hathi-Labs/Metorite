"use client";

// ── The React face of the shared saved-view seam (WS-53 CRM-U8) ─────────────
//
// One cached read for each scope. The app gives the read, and a cache prefix
// that no other app uses. After a write the app calls `invalidateViews`.

import { invalidate } from "@/lib/dataCache";
import { useCachedResource } from "@/lib/useCachedResource";

import type { SavedView, ViewScope } from "./index";

/** The cache key of one scope. */
export function viewsKey(prefix: string, scope: ViewScope): string {
  return `${prefix}${scope}`;
}

/** Read every scope of this app again after a write. */
export function invalidateViews(prefix: string): void {
  invalidate(prefix);
}

/**
 * The saved views of one scope. A `null` scope holds still and gives none.
 * A failed read gives none too, so a gateway without the route shows no chips.
 */
export function useScopedViews<V extends SavedView>(
  prefix: string,
  scope: ViewScope | null,
  list: (scope: ViewScope) => Promise<V[]>,
): V[] {
  const res = useCachedResource<V[]>(scope ? viewsKey(prefix, scope) : null, () =>
    list(scope as ViewScope).catch(() => []),
  );
  return res.data ?? [];
}
