"use client";

/**
 * The sidebar's needs counts, by the href of each row (owner, 2026-10-11).
 *
 * It reads the ONE feed through `useNeedsYou`, the hook My Day and the bell
 * read, with the same cache key. It joins the ONE poll (`joinNeedsPoll`), so
 * the bell's host and the sidebar together read the feed once a minute. The
 * rows an act took off live in the one store (`needsActs.ts`), so a Done in
 * My Day or the bell takes the row out of the count at once, with no read.
 *
 * **How often it reads** (`navigation_shell.md` §3.2c): once on mount, once a
 * minute while the tab shows, at most once in `NAV_WAKE_MS` after writes
 * elsewhere, and on a window focus when the answer is older than 5 seconds.
 * Each Projects write drops the feed's key (`NEEDS_CACHE`), and with no
 * throttle a drag on a board read the whole feed after each burst.
 *
 * **A read that failed shows no number.** When the last read of the feed
 * failed as a whole, the cache keeps the last answer, and My Day keeps it
 * with a caveat. A row has no room for a caveat, so it shows nothing.
 *
 * Fence (R7): `navNeeds.test.ts`.
 */
import { useMemo } from "react";

import { floorHrefs, needsByHref } from "./needs";
import { useNeedsYou } from "./NeedsYouCard";
import { useNeedsAllowed, useNeedsPoll } from "./ShellBell";

/** At most one read in this many ms because of writes elsewhere. */
export const NAV_WAKE_MS = 15_000;

export interface NavNeeds {
  /** The count of each row, by href. A zero has no entry. */
  counts: Record<string, number>;
  /** The rows whose count is a floor: "15+", "15 or more need you". */
  floors: ReadonlySet<string>;
}

const NONE: NavNeeds = { counts: {}, floors: new Set() };

/**
 * `on` is false where the rows draw no count (the shell nav is off, or the
 * member has no workspace). `held` is every href the member can open, so a
 * count never lands on an app they cannot reach.
 */
export function useNavNeeds(on: boolean, held: readonly string[]): NavNeeds {
  const { allowed, approvals } = useNeedsAllowed();
  const needs = useNeedsYou(on && allowed, approvals, NAV_WAKE_MS);
  useNeedsPoll(on && allowed, needs.refresh);
  // The hrefs arrive as a new array on each render, so the memo keys on
  // their text.
  const heldKey = held.join(",");
  const { byApp, floors } = needs;
  const failed = needs.error !== null;
  return useMemo(() => {
    if (!on || !allowed || failed) return NONE;
    const set = new Set(heldKey.split(",").filter(Boolean));
    return { counts: needsByHref(byApp, set), floors: floorHrefs(floors, set) };
  }, [on, allowed, failed, byApp, floors, heldKey]);
}
