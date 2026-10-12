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

/** One build-time flag, or its dev-only `localStorage` override. */
function flag(built: string | undefined, key: string): boolean {
  if (built === "1") return true;
  // ⚠️ Development and test builds ONLY, as `shellDockOn` does: a browser may
  // turn a flag on for itself, so the browser suite and the visual rig see
  // both sides in one dev server. Next inlines NODE_ENV, so a production
  // build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * Whether the chat bar and the one assistant are on in this browser.
 *
 * ⚠️ It needs the full frame too: `NEXT_PUBLIC_SHELL_BAR` and
 * `NEXT_PUBLIC_SHELL_NAV` (`desktopFrame` in `shellNav.ts`). Only that frame
 * mounts the bar and the assistant, and this flag takes away the apps' own
 * rails and sends every chat link to `/chat`. Alone, it would leave a member
 * with no way into any assistant (review of 2026-10-12). So the flag is on
 * only where its whole surface is.
 */
export function shellChatOn(): boolean {
  // The literal member expressions, the one form Next inlines (publicFlags.test.ts).
  return (
    flag(process.env.NEXT_PUBLIC_SHELL_CHAT, "cc-shell-chat") &&
    flag(process.env.NEXT_PUBLIC_SHELL_BAR, "cc-shell-bar") &&
    flag(process.env.NEXT_PUBLIC_SHELL_NAV, "cc-shell-nav")
  );
}
