/*
 * The Metorite service worker (WS-66 PN-1).
 *
 * Spec: project-docs/specs/calendar_events_reminders.md §5.9, and
 * chat_run_continuity.md §S7 item 2. `src/lib/push.ts` registers it at scope
 * `/`, and only after the member turns notifications on.
 *
 * It does two things and nothing else:
 *
 * - On `push`, it ALWAYS shows a notification. The subscription is
 *   `userVisibleOnly`, and iOS revokes a push permission that shows nothing.
 *   It reads four keys of the payload: `title`, `body`, `url` and `tag`. A
 *   `tag` replaces an earlier notification with the same tag, so a check-back
 *   does not stack.
 * - On `notificationclick`, it focuses an open Metorite window and navigates
 *   it to the payload's `url`, or opens a new window there. The default is
 *   `/`. A url off this origin opens `/`.
 *
 * ⚠️ It has NO fetch handler. It never sees a request, a cookie or a
 * response, so it cannot cache one account's page for another.
 *
 * `proxy.ts` serves this file signed out (PUBLIC_PAGES), because a browser
 * fetches it again with no cookie to check for an update.
 *
 * Fence: `src/lib/push.test.ts` runs this file against a fake worker scope.
 */

const DEFAULT_TITLE = "Metorite";
const ICON = "/icon-192.png";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

/** A path on this origin, or "/" for anything else. */
function sameOriginPath(raw) {
  if (typeof raw !== "string" || raw === "") return "/";
  try {
    const url = new URL(raw, self.location.origin);
    if (url.origin !== self.location.origin) return "/";
    return url.pathname + url.search + url.hash;
  } catch (_e) {
    return "/";
  }
}

/** The notification of one push. It never throws. */
function notificationOf(data) {
  let payload = {};
  try {
    payload = data ? data.json() : {};
  } catch (_e) {
    payload = {};
  }
  if (!payload || typeof payload !== "object") payload = {};
  const title =
    typeof payload.title === "string" && payload.title.trim() ? payload.title : DEFAULT_TITLE;
  const options = {
    icon: ICON,
    badge: ICON,
    data: { url: sameOriginPath(payload.url) },
  };
  if (typeof payload.body === "string" && payload.body) options.body = payload.body;
  if (typeof payload.tag === "string" && payload.tag) options.tag = payload.tag;
  return { title, options };
}

self.addEventListener("push", (event) => {
  const { title, options } = notificationOf(event.data);
  event.waitUntil(self.registration.showNotification(title, options));
});

/** Focus a Metorite window at `path`, or open one. */
async function openAt(path) {
  const target = new URL(path, self.location.origin).href;
  const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const win of windows) {
    let origin = "";
    try {
      origin = new URL(win.url).origin;
    } catch (_e) {
      continue;
    }
    if (origin !== self.location.origin) continue;
    try {
      const focused = await win.focus();
      const moved = await (focused || win).navigate(target);
      if (moved) return moved;
    } catch (_e) {
      // A window this worker does not control cannot navigate. Open one.
    }
    break;
  }
  return self.clients.openWindow(target);
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  event.waitUntil(openAt(sameOriginPath(data.url)));
});
