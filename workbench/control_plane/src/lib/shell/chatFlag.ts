/**
 * The flag for the one assistant and its chat bar (D99, `navigation_shell.md`
 * §7.1, NS-6 slice 6b, flag `NEXT_PUBLIC_SHELL_CHAT`). Off, nothing changes:
 * Chat keeps its sidebar door, and Projects, My Tasks and Email keep their
 * own rails.
 *
 * A flag of its own, apart from `NEXT_PUBLIC_SHELL_DOCK`, so the owner can
 * turn on the bell without the assistant (NS-6 note of 2026-10-12).
 *
 * A file of its own, with no imports, because the sidebar, `AppShell` and
 * three app pages read it, and none of them should pull the assistant into
 * its bundle to ask a yes or no.
 *
 * Turning it on in production is owner-only (`navigation_shell.md` §13.1).
 * The `enforcement-flip` grant does not cover it.
 */

/** Whether the chat bar and the one assistant are on in this browser. */
export function shellChatOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_SHELL_CHAT === "1") return true;
  // ⚠️ Development and test builds ONLY, as `shellDockOn` does: a browser may
  // turn it on for itself (`localStorage["cc-shell-chat"] = "1"`), so the
  // browser suite and the visual rig see both sides in one dev server. Next
  // inlines NODE_ENV, so a production build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-shell-chat") === "1";
    } catch {
      return false;
    }
  }
  return false;
}
