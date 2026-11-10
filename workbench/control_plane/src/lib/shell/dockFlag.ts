/**
 * The flag for NS-6, the one bell and (from slice 6b) the one dock
 * (`navigation_shell.md` §7, flag `NEXT_PUBLIC_SHELL_DOCK`). Off, nothing
 * changes: Projects and My Tasks mount their own `NotificationBell`, and the
 * shell draws no bell.
 *
 * A file of its own, with no imports, because the Projects and My Tasks
 * pages, the sidebar and `AppShell` all read it. None of them should pull
 * the bell's feed and its clients into its bundle to ask a yes or no.
 *
 * Turning it on in production is owner-only (`navigation_shell.md` §13.1).
 * The `enforcement-flip` grant does not cover it.
 */

/** Whether the shell's bell (and later its dock) is on in this browser. */
export function shellDockOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_SHELL_DOCK === "1") return true;
  // ⚠️ Development and test builds ONLY, as `shellBarOn` and `myDayOn` do: a
  // browser may turn the bell on for itself (`localStorage["cc-shell-dock"] =
  // "1"`), so the browser suite and the visual rig see both sides in one dev
  // server. Next inlines NODE_ENV, so a production build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-shell-dock") === "1";
    } catch {
      return false;
    }
  }
  return false;
}
