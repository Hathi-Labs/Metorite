/**
 * The flag for My Day at `/` (`navigation_shell.md` NS-3). Off, `/` is the
 * "Welcome back" grid as before, and the sidebar's first item says "Home".
 *
 * A file of its own, with no imports, because the sidebar reads it too
 * (`homePane` in `shellNav.ts`). The sidebar must not pull My Day's cards
 * and their clients into its bundle.
 *
 * Turning it on in production is owner-only (`navigation_shell.md` §13.1).
 * The `enforcement-flip` grant does not cover it.
 */

/** Whether My Day is on in this browser. */
export function myDayOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_MY_DAY === "1") return true;
  // ⚠️ Development and test builds ONLY, as `shellBarOn` does: a browser may
  // turn My Day on for itself (`localStorage["cc-my-day"] = "1"`), so the
  // visual rig and the browser suite see both sides. Next inlines NODE_ENV,
  // so a production build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-my-day") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/** The flag as the SERVER render sees it: the build-time value only. */
export function myDayOnServer(): boolean {
  return process.env.NEXT_PUBLIC_MY_DAY === "1";
}
