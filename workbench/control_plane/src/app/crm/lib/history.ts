/**
 * Change the CRM's view with no server request.
 *
 * Every CRM view lives in the URL (`?tab=`, the filters, the open record),
 * and the page is ONE client component. A `router.push` to a new query asks
 * the workbench for a fresh React Server Components payload before it
 * changes anything on screen. Next.js 16 gives that request no timeout and
 * no pending state. So while the workbench or Caddy holds it (a deploy, a
 * restart, a busy box), a click on the rail does nothing at all, and every
 * later click waits the same way. When the request then fails, Next falls
 * back to a full page load and freezes the page until it arrives.
 *
 * `history.pushState` changes the address and nothing else. Next patches it
 * (`app-router.js`, `applyUrlFromHistoryPushReplace`) and updates
 * `useSearchParams` from it synchronously, so the view changes at once and
 * no server can block it. The data the view needs still comes from
 * `/api/crm/*`, which has its own timeout and its own error line.
 *
 * `shell/doJob.tsx` uses the same idiom for the same reason. Fence:
 * `history.test.ts`.
 */

type HistoryLike = Pick<History, "pushState" | "replaceState">;

/** A new history entry, so Back returns to the previous view. */
export function pushView(href: string, history: HistoryLike = window.history): void {
  history.pushState(null, "", href);
}

/** The same entry, rewritten. For a URL the page corrects by itself. */
export function replaceView(href: string, history: HistoryLike = window.history): void {
  history.replaceState(null, "", href);
}
