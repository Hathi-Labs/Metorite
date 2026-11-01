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
 *   It reads six keys of the payload: `title`, `body`, `url`, `tag`,
 *   `reminder_id` and `kind`. A `tag` replaces an earlier notification with
 *   the same tag, so a check-back does not stack. A push of a reminder that
 *   waits (`kind` is `reminder_due` or `reminder_check`) gets two buttons,
 *   "Done" and "In 1 hour" (RX-4). iOS shows no buttons, and a tap covers it.
 * - On `notificationclick`, a plain tap focuses an open Metorite window and
 *   navigates it to the payload's `url`, or opens a new window there. The
 *   default is `/`. A url off this origin opens `/`. A tap on a button sends
 *   ONE same-origin request to the reminder routes of the BFF
 *   (`/api/projects/my/reminders/<id>/resolve` or `/snooze`) with the
 *   session cookie, and closes. If the request fails, or the session is
 *   signed out, it opens the chat card of the reminder, so nothing is lost.
 *   The BFF has no origin check: the session cookie is SameSite=Lax, and the
 *   request is same-site.
 *
 * ⚠️ It has NO fetch HANDLER. It never sees a request of a page, so it cannot
 * cache one account's page for another. The one request it makes is its own,
 * after the member taps a button.
 *
 * `proxy.ts` serves this file signed out (PUBLIC_PAGES), because a browser
 * fetches it again with no cookie to check for an update.
 *
 * Fence: `src/lib/push.test.ts` runs this file against a fake worker scope.
 */

const DEFAULT_TITLE = "Metorite";
const ICON = "/icon-192.png";
const REMINDER_ROUTE = "/api/projects/my/reminders/";
/** The card kinds of a reminder that waits on the member. Only these get buttons. */
const BUTTON_KINDS = ["reminder_due", "reminder_check"];
/** The reminder id is a UUID, and it goes into a path. Nothing else passes. */
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SNOOZE_MS = 60 * 60 * 1000;

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
  // RX-4: the buttons of a reminder that waits. The data keeps the id and the
  // kind for the click. A device that shows no buttons ignores `actions`.
  const id = typeof payload.reminder_id === "string" ? payload.reminder_id : "";
  if (UUID.test(id) && BUTTON_KINDS.includes(payload.kind) && buttonsFit()) {
    options.actions = [
      { action: "done", title: "Done" },
      { action: "snooze", title: "In 1 hour" },
    ];
    options.data.reminder_id = id.toLowerCase();
  }
  return { title, options };
}

/** Whether the device shows two buttons. Where the limit is unknown, try. */
function buttonsFit() {
  const max = self.Notification && self.Notification.maxActions;
  return typeof max !== "number" || max >= 2;
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

/** The request of one button: the route and the body. Null for any other action. */
function buttonRequest(action, id, nowMs) {
  const base = REMINDER_ROUTE + encodeURIComponent(id);
  if (action === "done") return { path: base + "/resolve", body: { action: "done" } };
  if (action === "snooze") {
    return { path: base + "/snooze", body: { fire_at: new Date(nowMs + SNOOZE_MS).toISOString() } };
  }
  return null;
}

/**
 * Run one button. A good answer is the end. A 409 means the reminder is
 * already closed, so nothing is left to do. Any other answer opens the chat
 * card, so the member can do it there: a 401 (signed out), a 404 (the
 * reminder belongs to another account of this device, which the routes answer
 * as "not found"), a 5xx, a 422 (a device clock that is wrong), and a network
 * fault.
 */
async function pressButton(action, id, url) {
  const req = buttonRequest(action, id, Date.now());
  if (!req) return openAt(url);
  try {
    const res = await self.fetch(req.path, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body),
    });
    if (res.ok || res.status === 409) return undefined;
  } catch (_e) {
    // Offline or the server is down. Fall through.
  }
  return openAt(url);
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const url = sameOriginPath(data.url);
  const id = typeof data.reminder_id === "string" ? data.reminder_id : "";
  if ((event.action === "done" || event.action === "snooze") && UUID.test(id)) {
    event.waitUntil(pressButton(event.action, id, url));
    return;
  }
  event.waitUntil(openAt(url));
});
