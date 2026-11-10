/**
 * The flag for the Do first band on My Day (`package_host.md` §9.3, PKG-18a).
 * Off, My Day draws no band. The band adds no request, because it reads the
 * feed answer that the column reads. So the flag only shows or hides it.
 *
 * It needs the live feed too (`NEXT_PUBLIC_MY_DAY_FEED`). Turning it on in
 * production is the `enforcement-flip` act.
 */

/** Whether My Day draws the Do first band in this browser. */
export function todayBandOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_TODAY_BAND === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-today-band"] = "1"`.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-today-band") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function todayBandOnServer(): boolean {
  return process.env.NEXT_PUBLIC_TODAY_BAND === "1";
}

/**
 * Whether My Day draws the band: the flag is on, the feed is on, and the
 * member holds the needs card that the feed answers. My Day reads this one
 * gate (`MyDayPage.tsx`). Fence: `todayBand.test.ts`, "the gate".
 */
export function showTodayBand(flagOn: boolean, feedOn: boolean, needsCard: boolean): boolean {
  return flagOn && feedOn && needsCard;
}
