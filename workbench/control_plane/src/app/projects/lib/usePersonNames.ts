"use client";

/**
 * The directory's names for a set of addresses, through the shared read cache.
 *
 * For a panel whose payload carries addresses and no names — the Load panel
 * (owner, 2026-10-10: names, not email IDs). The board resolves its own set
 * in `page.tsx`. A panel mounts far from there, so it asks here, and two
 * panels asking for the same people share one read.
 *
 * ⚠️ Only a HINT, as `grouping.PersonNames` says: every label still renders
 * before the answer arrives and if it never does (`personDisplayName` falls
 * back to the left half of the address).
 */

import { useMemo } from "react";

import { useCachedResource } from "@/lib/useCachedResource";

import { projectsApi, projectsKey } from "./api";

export function usePersonNames(emails: readonly (string | null | undefined)[]): ReadonlyMap<string, string> {
  // Sorted and de-duplicated, so the same people are the same cache key.
  const wanted = useMemo(
    () =>
      [
        ...new Set(
          emails
            .filter((e): e is string => !!e && !e.startsWith("agent:"))
            .map((e) => e.toLowerCase()),
        ),
      ].sort(),
    [emails],
  );
  const { data } = useCachedResource(
    wanted.length ? projectsKey("people/names", { emails: wanted.join(",") }) : null,
    () => projectsApi.personNames(wanted),
  );
  return useMemo(() => new Map(Object.entries(data?.names ?? {})), [data]);
}
