"use client";

/**
 * ThemeProvider — the appearance runtime.
 *
 * Renders nothing. Four jobs, all side effects:
 *
 *   1. Read the member's stored preferences into the store on mount. The boot
 *      script has already applied them to the DOM; this is what tells React
 *      about them.
 *   2. Fetch the organisation's defaults so a member who has never opened
 *      Settings still gets the company look, and cache them locally so the
 *      boot script can apply them on the next load with no flash.
 *   3. Bind the appearance scope to the account `useAccess()` names
 *      (`lib/theme/scope.ts`). When the pointer disagreed, the store and
 *      the colour mode re-read at once. So it mounts INSIDE `AccessProvider`.
 *   4. Copy each colour-mode change made in THIS tab into the tab's own
 *      scope. next-themes writes only its bare `theme` key, and one watcher
 *      here covers every caller of `setTheme`. A mode that arrived from
 *      another tab is not copied (`lib/theme/storage.ts`).
 *
 * ⚠️ A third job — preloading the active theme's icon pack — went with the
 * theming engine on 2026-08-31. There is one pack, it ships in the bundle,
 * and there is nothing to fetch.
 *
 * The name is kept: it is what `layout.tsx` mounts, and "appearance
 * provider" would be a rename for its own sake in a diff already deleting an
 * engine.
 */

import { useEffect, useRef, useState } from "react";
import { useTheme } from "next-themes";
import { useAccess } from "@/components/AccessProvider";
import { reconcileAppearanceScope } from "@/lib/theme/scope";
import { themeStorage, watchAppearanceStorage } from "@/lib/theme/storage";
import { useAppearanceStore } from "@/lib/theme/store";
import type { AppearanceSettings } from "@/lib/theme/types";

export default function ThemeProvider() {
  const hydrate = useAppearanceStore((s) => s.hydrate);
  const rehydrate = useAppearanceStore((s) => s.rehydrate);
  const setOrgDefaults = useAppearanceStore((s) => s.setOrgDefaults);
  const { theme, setTheme } = useTheme();
  const { access, loading } = useAccess();
  const email = access.email ?? null;
  const orgId = access.organization?.id ?? null;
  const [org, setOrg] = useState<AppearanceSettings["org"] | null>(null);
  // ⚠️ Out of the bind's deps on purpose (review round 2, P1). next-themes
  // gives `setTheme` a new identity when another tab's `storage` event
  // changes the mode. In the deps, that re-ran the bind, and two tabs on two
  // accounts moved the pointer back and forth and swapped their modes.
  const setThemeRef = useRef(setTheme);
  useEffect(() => {
    setThemeRef.current = setTheme;
  }, [setTheme]);

  // Hear the other tabs first: a moved pointer makes this tab passive, and a
  // mode another tab chose is not copied into this tab's scope.
  useEffect(() => watchAppearanceStorage(), []);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  // ⚠️ ORDER MATTERS. React runs these effects in the order written. The mode
  // copy runs BEFORE the bind on mount, so it writes the mode the boot script
  // read back into the scope it came from. Written after the bind, it would
  // copy the old account's mode into the new account's scope.
  useEffect(() => {
    if (theme) themeStorage.mirrorMode(theme);
  }, [theme]);

  // Once for each identity: the deps are the account and nothing else.
  useEffect(() => {
    if (loading) return;
    reconcileAppearanceScope(email, orgId, { rehydrate, setMode: (m) => setThemeRef.current(m) });
  }, [loading, email, orgId, rehydrate]);

  // The org defaults land only after the bind, or they would be cached in the
  // scope of the account the pointer named before it moved.
  useEffect(() => {
    if (!loading && org) setOrgDefaults(org);
  }, [loading, org, setOrgDefaults]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/settings/appearance", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: AppearanceSettings | null) => {
        if (data?.org) setOrg(data.org);
      })
      .catch(() => {
        // Signed out, offline, or the gateway is down — the cached org
        // defaults the boot script already applied remain in force.
      });
    return () => controller.abort();
  }, []);

  return null;
}
