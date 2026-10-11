"use client";

/**
 * The sidebar's needs counts, by the href of each row (owner, 2026-10-11).
 *
 * It reads the ONE feed through `useNeedsYou`, the hook My Day and the bell
 * read, with the same cache key, so a second fetch of `/api/shell/needs`
 * never exists. It joins the ONE poll (`joinNeedsPoll`), so the bell's host
 * and the sidebar together read the feed once a minute. The rows an act
 * took off live in the one store (`needsActs.ts`), so a Done in My Day or
 * the bell takes the row out of the count at once.
 *
 * Fence (R7): `navNeeds.test.ts`.
 */
import { useMemo } from "react";

import { needsByHref } from "./needs";
import { useNeedsYou } from "./NeedsYouCard";
import { useNeedsAllowed, useNeedsPoll } from "./ShellBell";

/**
 * `on` is false where the rows draw no count (the shell nav is off, or the
 * member has no workspace). `held` is every href the member can open, so a
 * count never lands on an app they cannot reach.
 */
export function useNavNeeds(on: boolean, held: readonly string[]): Record<string, number> {
  const { allowed, approvals } = useNeedsAllowed();
  const needs = useNeedsYou(on && allowed, approvals);
  useNeedsPoll(on && allowed, needs.refresh);
  // The hrefs arrive as a new array on each render, so the memo keys on
  // their text.
  const heldKey = held.join(",");
  const { byApp } = needs;
  return useMemo(
    () => (on && allowed ? needsByHref(byApp, new Set(heldKey.split(",").filter(Boolean))) : {}),
    [on, allowed, byApp, heldKey],
  );
}
