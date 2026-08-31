"use client";

/**
 * ThemeProvider — the appearance runtime.
 *
 * Renders nothing. Two jobs, both side effects:
 *
 *   1. Read the member's stored preferences into the store on mount. The boot
 *      script has already applied them to the DOM; this is what tells React
 *      about them.
 *   2. Fetch the organisation's defaults so a member who has never opened
 *      Settings still gets the company look, and cache them locally so the
 *      boot script can apply them on the next load with no flash.
 *
 * ⚠️ A third job — preloading the active theme's icon pack — went with the
 * theming engine on 2026-08-31. There is one pack, it ships in the bundle,
 * and there is nothing to fetch.
 *
 * The name is kept: it is what `layout.tsx` mounts, and "appearance
 * provider" would be a rename for its own sake in a diff already deleting an
 * engine.
 */

import { useEffect } from "react";
import { useAppearanceStore } from "@/lib/theme/store";
import type { AppearanceSettings } from "@/lib/theme/types";

export default function ThemeProvider() {
  const hydrate = useAppearanceStore((s) => s.hydrate);
  const setOrgDefaults = useAppearanceStore((s) => s.setOrgDefaults);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/settings/appearance", { signal: controller.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data: AppearanceSettings | null) => {
        if (data?.org) setOrgDefaults(data.org);
      })
      .catch(() => {
        // Signed out, offline, or the gateway is down — the cached org
        // defaults the boot script already applied remain in force.
      });
    return () => controller.abort();
  }, [setOrgDefaults]);

  return null;
}
