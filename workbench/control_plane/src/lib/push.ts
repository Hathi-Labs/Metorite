/**
 * Web push in the browser (WS-66 PN-1).
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §5.9 and §10.2
 * items 7 and 8, and `chat_run_continuity.md` §S7 items 3 and 7.
 *
 * - **The prompt shows only after a tap.** `enablePush` is the ONE caller of
 *   `Notification.requestPermission`, and only the toggle of
 *   `NotificationsSection` calls `enablePush`, from its click handler. Every
 *   other function here reads state and never asks. It asks for permission
 *   FIRST, inside the tap, because Safari refuses a prompt that comes after
 *   an `await` has spent the tap.
 * - **The worker registers on the tap too.** `/sw.js` registers at scope `/`
 *   inside `enablePush`. A read uses `getRegistration`, which registers
 *   nothing.
 * - **iOS.** Push works only in a web app on the Home Screen (iOS 16.4 or
 *   later). In a Safari tab, `pushSupport` answers `ios-tab`, and the section
 *   shows the three steps instead of a toggle.
 * - **Sign-out.** `forgetThisDevice` deletes this device's row while the
 *   session still names the member, then unsubscribes. It never throws and
 *   gives up after a short time, so it can never block a sign-out.
 *
 * The flag is `NEXT_PUBLIC_WEB_PUSH`, default off. Off, the section draws
 * nothing and nothing registers. `forgetThisDevice` still runs on sign-out:
 * it only reads, so it costs nothing when no device is subscribed.
 *
 * Fence: `push.test.ts`.
 */

/** The service worker script, at the root so its scope can be `/`. */
export const SERVICE_WORKER_URL = "/sw.js";

/** The BFF of the gateway's `/push` routes (`app/api/push/[...path]`). */
const API = "/api/push";

/** How long a sign-out waits for this device to be forgotten, at most. */
export const FORGET_TIMEOUT_MS = 3000;

/** Whether web push is on in this build. */
export function webPushOn(): boolean {
  // The literal member expression, the one form Next inlines (publicFlags.test.ts).
  if (process.env.NEXT_PUBLIC_WEB_PUSH === "1") return true;
  // ⚠️ Development and test builds ONLY: `localStorage["cc-web-push"] = "1"`.
  // Next inlines NODE_ENV, so a production build drops this branch.
  if (process.env.NODE_ENV !== "production") {
    try {
      return typeof localStorage !== "undefined" && localStorage.getItem("cc-web-push") === "1";
    } catch {
      return false;
    }
  }
  return false;
}

// ── What this browser can do ────────────────────────────────────────────────

export type PushSupport = "supported" | "ios-tab" | "unsupported";

/** The parts of the browser that `pushSupport` reads. */
export interface BrowserFacts {
  userAgent: string;
  platform: string;
  maxTouchPoints: number;
  /** `navigator.standalone`, Safari's own flag for a Home Screen app. */
  standalone: boolean | undefined;
  /** `matchMedia("(display-mode: standalone)").matches`. */
  displayStandalone: boolean;
  hasServiceWorker: boolean;
  hasPushManager: boolean;
  hasNotification: boolean;
}

/** An iPhone, an iPod or an iPad. iPadOS asks for the desktop site, so an
 * iPad reads as a Mac with a touch screen. */
export function isIos(f: Pick<BrowserFacts, "userAgent" | "platform" | "maxTouchPoints">): boolean {
  if (/iPad|iPhone|iPod/.test(f.userAgent)) return true;
  return f.platform === "MacIntel" && f.maxTouchPoints > 1;
}

/** The pure rule. A Safari tab on iOS is `ios-tab` even where the Push API
 * exists, because iOS delivers push to a Home Screen app only. */
export function supportOf(f: BrowserFacts): PushSupport {
  const installed = f.standalone === true || f.displayStandalone;
  if (isIos(f) && !installed) return "ios-tab";
  if (!f.hasServiceWorker || !f.hasPushManager || !f.hasNotification) return "unsupported";
  return "supported";
}

function browserFacts(): BrowserFacts | null {
  if (typeof window === "undefined" || typeof navigator === "undefined") return null;
  const nav = navigator as Navigator & { standalone?: boolean };
  let displayStandalone = false;
  try {
    displayStandalone = window.matchMedia?.("(display-mode: standalone)").matches === true;
  } catch {
    displayStandalone = false;
  }
  return {
    userAgent: nav.userAgent ?? "",
    platform: nav.platform ?? "",
    maxTouchPoints: nav.maxTouchPoints ?? 0,
    standalone: nav.standalone,
    displayStandalone,
    hasServiceWorker: "serviceWorker" in nav,
    hasPushManager: "PushManager" in window,
    hasNotification: "Notification" in window,
  };
}

/** What this browser can do. It reads only: no prompt, no registration. */
export function pushSupport(): PushSupport {
  const facts = browserFacts();
  return facts ? supportOf(facts) : "unsupported";
}

/** The permission as the browser holds it now. Reading it never prompts. */
export function permissionState(): NotificationPermission | "unsupported" {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission;
}

// ── The subscription ────────────────────────────────────────────────────────

/** The existing registration at `/`, or null. It registers nothing. */
async function existingRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return null;
  return (await navigator.serviceWorker.getRegistration("/")) ?? null;
}

/** This browser's push subscription, or null. It reads only. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  const reg = await existingRegistration();
  if (!reg || !reg.pushManager) return null;
  return (await reg.pushManager.getSubscription()) ?? null;
}

/** The bytes of a base64url string (the VAPID public key). */
export function base64UrlToBytes(value: string): Uint8Array<ArrayBuffer> {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (value.length % 4)) % 4);
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

function sameKey(sub: PushSubscription, key: Uint8Array): boolean {
  const held = sub.options?.applicationServerKey;
  if (!held) return false;
  const bytes = new Uint8Array(held);
  return bytes.length === key.length && bytes.every((b, i) => b === key[i]);
}

/** The gateway's answer to `GET /push/public-key`. */
export type KeyAnswer =
  | { kind: "key"; key: string }
  | { kind: "off" }
  | { kind: "not-configured" }
  | { kind: "error" };

export async function fetchPublicKey(): Promise<KeyAnswer> {
  try {
    const res = await fetch(`${API}/public-key`, { cache: "no-store" });
    if (res.status === 404) return { kind: "off" };
    if (res.status === 503) return { kind: "not-configured" };
    if (!res.ok) return { kind: "error" };
    const body = (await res.json()) as { public_key?: unknown };
    return typeof body.public_key === "string" && body.public_key
      ? { kind: "key", key: body.public_key }
      : { kind: "error" };
  } catch {
    return { kind: "error" };
  }
}

export type EnableResult =
  | { ok: true }
  | { ok: false; reason: "unsupported" | "denied" | "dismissed" | "unavailable" | "refused"; message: string };

/**
 * Turn push on for this device. **Call it from a click handler only**: the
 * browser shows its permission prompt here, and only here.
 */
export async function enablePush(): Promise<EnableResult> {
  if (pushSupport() !== "supported") {
    return { ok: false, reason: "unsupported", message: "This browser cannot show notifications from Metorite." };
  }
  // FIRST, while the tap still counts (see the module note).
  const permission = await Notification.requestPermission();
  if (permission === "denied") {
    return { ok: false, reason: "denied", message: BLOCKED_MESSAGE };
  }
  if (permission !== "granted") {
    return { ok: false, reason: "dismissed", message: "Notifications stay off. Tap again to allow them." };
  }
  const answer = await fetchPublicKey();
  if (answer.kind !== "key") {
    return { ok: false, reason: "unavailable", message: "Notifications are not set up on this server yet." };
  }
  try {
    await navigator.serviceWorker.register(SERVICE_WORKER_URL, { scope: "/" });
    const reg = await navigator.serviceWorker.ready;
    const key = base64UrlToBytes(answer.key);
    const held = await reg.pushManager.getSubscription();
    // A subscription made with an older key cannot receive a push signed
    // with the new one. Replace it.
    if (held && !sameKey(held, key)) await held.unsubscribe();
    const sub =
      held && sameKey(held, key)
        ? held
        : await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const res = await fetch(`${API}/subscriptions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...sub.toJSON(), user_agent: navigator.userAgent }),
    });
    if (!res.ok) {
      // The server did not keep it, so do not keep it here either.
      await sub.unsubscribe().catch(() => false);
      const detail = await res.json().then((b: { detail?: unknown }) => b.detail).catch(() => null);
      return {
        ok: false,
        reason: "refused",
        message: typeof detail === "string" ? detail : "Metorite could not save this device. Try again.",
      };
    }
    return { ok: true };
  } catch {
    return { ok: false, reason: "unavailable", message: "This browser could not turn notifications on. Try again." };
  }
}

export const BLOCKED_MESSAGE =
  "Notifications are blocked for Metorite in this browser. Allow them in the site settings, then come back.";

/** Turn push off for this device: delete its row, then unsubscribe. */
export async function disablePush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await deleteRow(sub.endpoint, AbortSignal.timeout(10_000));
  await sub.unsubscribe();
}

async function deleteRow(endpoint: string, signal: AbortSignal): Promise<void> {
  await fetch(`${API}/subscriptions`, {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ endpoint }),
    signal,
  });
}

/** Resolve with `p`, or with `fallback` after `ms`. Never rejects. */
function within<T>(p: Promise<T>, ms: number, fallback: T): Promise<T> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(fallback), ms);
    p.then(
      (v) => {
        clearTimeout(timer);
        resolve(v);
      },
      () => {
        clearTimeout(timer);
        resolve(fallback);
      },
    );
  });
}

/**
 * Forget this device at sign-out (S7 item 7). Delete its row while the
 * session still names the member, then unsubscribe. It NEVER throws, and it
 * gives up after `FORGET_TIMEOUT_MS`, so a sign-out never waits on it.
 */
export async function forgetThisDevice(timeoutMs: number = FORGET_TIMEOUT_MS): Promise<void> {
  const work = (async () => {
    const sub = await currentSubscription();
    if (!sub) return;
    try {
      await deleteRow(sub.endpoint, AbortSignal.timeout(timeoutMs));
    } catch {
      /* the unsubscribe below still runs */
    }
    await sub.unsubscribe().catch(() => false);
  })();
  await within(work, timeoutMs, undefined);
}

// ── The test push ───────────────────────────────────────────────────────────

/** The gateway's counts for `POST /push/test`. */
export interface TestCounts {
  devices: number;
  sent: number;
  removed: number;
  failed: number;
}

/** What the section says after "Send a test". */
export function testMessage(status: number, counts: Partial<TestCounts> | null): { ok: boolean; message: string } {
  if (status === 429) return { ok: false, message: "A test notification is already on its way." };
  if (status === 503) return { ok: false, message: "Notifications are not set up on this server yet." };
  if (status < 200 || status >= 300 || !counts) {
    return { ok: false, message: "The test notification did not go. Try again." };
  }
  const sent = counts.sent ?? 0;
  if ((counts.devices ?? 0) === 0) return { ok: false, message: "No device of yours has notifications on." };
  if (sent === 0) return { ok: false, message: "The test notification did not reach a device. Try again." };
  return { ok: true, message: sent === 1 ? "Sent to 1 device." : `Sent to ${sent} devices.` };
}

/** Send the test push to my own devices. */
export async function sendTestPush(): Promise<{ ok: boolean; message: string }> {
  try {
    const res = await fetch(`${API}/test`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    const counts = (await res.json().catch(() => null)) as Partial<TestCounts> | null;
    return testMessage(res.status, counts);
  } catch {
    return testMessage(0, null);
  }
}

// ── The section's view ──────────────────────────────────────────────────────

export type PushView =
  | { kind: "hidden" }
  | { kind: "loading" }
  | { kind: "ios-tab" }
  | { kind: "unsupported" }
  | { kind: "not-configured" }
  | { kind: "blocked" }
  | { kind: "ready"; on: boolean };

/** The pure rule of what `NotificationsSection` draws. */
export function pushView(input: {
  flag: boolean;
  support: PushSupport;
  key: KeyAnswer | null;
  permission: NotificationPermission | "unsupported";
  subscribed: boolean;
}): PushView {
  if (!input.flag) return { kind: "hidden" };
  if (input.key === null) return { kind: "loading" };
  if (input.key.kind === "off") return { kind: "hidden" };
  if (input.support === "ios-tab") return { kind: "ios-tab" };
  if (input.support === "unsupported") return { kind: "unsupported" };
  if (input.key.kind === "not-configured" || input.key.kind === "error") return { kind: "not-configured" };
  if (input.permission === "denied") return { kind: "blocked" };
  return { kind: "ready", on: input.subscribed && input.permission === "granted" };
}
