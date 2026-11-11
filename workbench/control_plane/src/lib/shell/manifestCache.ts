/**
 * The cache key of the resolved package manifest, and its refresh
 * (`package_host.md` PKG-6 and PKG-8a).
 *
 * Pure of React, so a store that is pure of React too (`lib/studio/packageDelete.ts`)
 * can drop the manifest after a write. `packageManifest.ts` reads the manifest
 * under this key, and re-exports both names.
 *
 * Fence: `installedApp.test.ts`.
 */
import { cacheKey, invalidate } from "@/lib/dataCache";

export const MANIFEST_CACHE = "shell/manifest";
export const manifestKey = (): string => cacheKey(MANIFEST_CACHE);

/**
 * Drop the cached manifest. Each reader of it reads again at once, so the
 * sidebar, the phone drawer and ⌘K show an install that a publish made or a
 * delete removed, with no reload.
 */
export function refreshManifest(): void {
  invalidate(manifestKey());
}
