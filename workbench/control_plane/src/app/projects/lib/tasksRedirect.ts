/**
 * WS-64 OP-4 (`one_projects_app.md` §9 OP-4 item 2) — where `/tasks` goes once
 * My work is live. The redirect runs on the client, behind
 * `NEXT_PUBLIC_PROJECTS_MY_WORK`, and never in `next.config`. With the flag off
 * every function here answers "no change".
 *
 * | From                               | To                                 |
 * |------------------------------------|------------------------------------|
 * | `/tasks`, `/tasks#today`           | `/projects?app=my-today`           |
 * | `/tasks?do=capture`                | `/projects?app=my-inbox&do=capture`|
 * | `/tasks?task=<id>` or `?item=<id>` | `/projects?task=<id>`              |
 *
 * Any other `?do=` (the WS-51 `open-chat` job) is NOT redirected, because the
 * `/tasks` page still serves it and the target would lose it.
 * Fence: `tasksRedirect.test.ts`.
 */

import { myWorkEnabled } from "./myWorkFlag";

/** The query value that asks the Projects page to open capture once. */
export const CAPTURE_DO = "capture";

/** Keys the redirect reads itself. Every other query key passes through. */
const OWN_KEYS = new Set(["task", "item", "do", "app"]);

/** The target of a `/tasks` address, or null when the address stays. */
export function tasksRedirectTarget(
  url: { search?: string | null; hash?: string | null },
  flag: boolean,
): string | null {
  if (!flag) return null;
  const params = new URLSearchParams(url.search ?? "");
  const task = params.get("task") || params.get("item");
  const action = params.get("do");
  if (!task && action && action !== CAPTURE_DO) return null;
  // Unknown keys and every NS-4b `fill.*` key travel on, in their order.
  const rest = new URLSearchParams();
  for (const [k, v] of params) if (!OWN_KEYS.has(k)) rest.append(k, v);
  const head = task
    ? `task=${encodeURIComponent(task)}`
    : action === CAPTURE_DO
      ? `app=my-inbox&do=${CAPTURE_DO}`
      : "app=my-today";
  const tail = rest.toString();
  const q = tail ? `${head}&${tail}` : head;
  // `#today` names the view the target already opens. Another fragment stays.
  const hash = url.hash && url.hash !== "#today" && url.hash !== "#" ? url.hash : "";
  return `/projects?${q}${hash}`;
}

/**
 * An in-app link that names `/tasks`, repointed when the flag is on. A link
 * that does not name `/tasks` comes back unchanged. `flag` defaults to the
 * build flag.
 */
export function tasksLink(href: string = "/tasks", flag: boolean = myWorkEnabled()): string {
  const m = /^\/tasks(\?[^#]*)?(#.*)?$/.exec(href);
  if (!m) return href;
  return tasksRedirectTarget({ search: m[1] ?? "", hash: m[2] ?? "" }, flag) ?? href;
}
