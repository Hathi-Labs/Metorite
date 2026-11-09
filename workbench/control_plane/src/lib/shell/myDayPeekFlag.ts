/**
 * The flag for the panel on My Day (PK-1, `my_day_feed.md` §11.5, D204).
 * Off, a task row on My Day opens Projects, as before. On, a plain click
 * on a task row opens the task in a panel on My Day, and the path stays `/`.
 *
 * A file of its own, with no imports, as `todayBandFlag.ts` is. Turning it
 * on in production is the `enforcement-flip` act.
 */

/** Whether My Day opens a task row in its panel, in this browser. */
export function myDayPeekOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_MY_DAY_PEEK === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-my-day-peek"] = "1"`.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-my-day-peek") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function myDayPeekOnServer(): boolean {
  return process.env.NEXT_PUBLIC_MY_DAY_PEEK === "1";
}
