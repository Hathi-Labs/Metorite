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
 *   session still names the member. It never throws and gives up after a
 *   short time, so it can never block a sign-out.
 * - ⚠️ **One browser, several accounts.** Every account of the switcher
 *   shares this origin, so ONE browser subscription serves them all. The
 *   browser alone cannot say whether THIS member gets the pushes, so the
 *   section asks the gateway (`registeredHere`). And only the last account
 *   to leave this browser may unsubscribe it. A path that leaves another
 *   account behind deletes this member's row and KEEPS the subscription:
 *   `signOutThis` (`keepSubscription`), the toggle and the refused enable
 *   (`othersStay`). "Remove from this browser" deletes the removed
 *   account's row on the server, as that account (`api/accounts/[action]`).
 *
 * ⚠️ **The one switch is the SERVER's, `WEB_PUSH_ENABLED`.** There is no
 * build-time flag: a `NEXT_PUBLIC_*` value is inlined at build, so each box
 * would need an owner rebuild for no safety gain. The section asks
 * `GET /api/push/public-key` once on mount (`loadPushView`). A 404 (the
 * switch is off) or any failure draws nothing, and nothing registers.
 * `forgetThisDevice` still runs on sign-out: it only reads, so it costs
 * nothing when no device is subscribed.
 *
 * Fence: `push.test.ts` and `app/people/components/notificationsSection.test.ts`.
 */

/** The service worker script, at the root so its scope can be `/`. */
export const SERVICE_WORKER_URL = "/sw.js";

/** The BFF of the gateway's `/push` routes (`app/api/push/[...path]`). */
const API = "/api/push";

/** How long a sign-out waits for this device to be forgotten, at most. */
export const FORGET_TIMEOUT_MS = 3000;

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
 * True when another account of the switcher stays on this browser. Only then
 * must a path keep the browser subscription for it. The section passes the
 * switcher's own list (`accountSwitch.ts`, `fetchAccounts`). This module
 * does not import that file, because that file imports this one.
 */
export type OthersStay = () => Promise<boolean>;

const NO_OTHERS: OthersStay = async () => false;

/**
 * Turn push on for this device. **Call it from a click handler only**: the
 * browser shows its permission prompt here, and only here.
 *
 * With the permission already granted, it asks nothing. That is the case of
 * a second account on a browser that the first account subscribed: the
 * browser holds the subscription, and this member only needs a row, so it
 * POSTs the subscription that exists and shows no second prompt.
 */
export async function enablePush(othersStay: OthersStay = NO_OTHERS): Promise<EnableResult> {
  if (pushSupport() !== "supported") {
    return { ok: false, reason: "unsupported", message: "This browser cannot show notifications from Metorite." };
  }
  // FIRST, while the tap still counts (see the module note). A granted
  // permission needs no prompt, and a browser shows none for it.
  const permission =
    Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
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
    // with the new one, for ANY account. Replace it.
    if (held && !sameKey(held, key)) await held.unsubscribe();
    const reuse = held !== null && sameKey(held, key);
    const sub = reuse
      ? held
      : await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const res = await fetch(`${API}/subscriptions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...sub.toJSON(), user_agent: navigator.userAgent }),
    });
    if (!res.ok) {
      // The server did not keep it. Unsubscribe only a subscription this tap
      // made, and only when no other account of this browser needs it.
      if (!reuse && !(await othersStay().catch(() => true))) {
        await sub.unsubscribe().catch(() => false);
      }
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

/**
 * Turn push off for this member on this device: delete the member's row.
 * Unsubscribe the browser only when no other account stays on it.
 */
export async function disablePush(othersStay: OthersStay = NO_OTHERS): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await deleteRow(sub.endpoint, AbortSignal.timeout(10_000));
  if (!(await othersStay().catch(() => true))) await sub.unsubscribe();
}

/**
 * Does the signed-in member hold a row of `endpoint`. The browser's own
 * subscription may belong to another account of the switcher. A POST, so
 * the endpoint stays out of the URL. A failed read answers false.
 */
export async function registeredHere(endpoint: string): Promise<boolean> {
  try {
    const res = await fetch(`${API}/subscriptions/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ endpoint }),
      cache: "no-store",
    });
    if (!res.ok) return false;
    const body = (await res.json()) as { registered?: unknown };
    return body.registered === true;
  } catch {
    return false;
  }
}

/**
 * This browser's push endpoint, or null, within `timeoutMs`. It reads only.
 * "Remove from this browser" sends it, so the server can delete the removed
 * account's row as that account.
 */
export async function thisDeviceEndpoint(timeoutMs = 1000): Promise<string | null> {
  const read = currentSubscription().then((s) => s?.endpoint ?? null);
  return within(read, timeoutMs, null);
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
 * Forget this device at sign-out (S7 item 7), in this order and all within
 * `timeoutMs`: read the endpoint, delete the member's row while the session
 * still names the member, then unsubscribe the browser, but only when this
 * account is the last one on it. It NEVER throws and never waits past the
 * cap, so a sign-out never waits on it.
 *
 * `keepSubscription`: another account stays on this browser, so its pushes
 * must keep coming (`signOutThis`).
 */
export async function forgetThisDevice(
  timeoutMs: number = FORGET_TIMEOUT_MS,
  { keepSubscription = false }: { keepSubscription?: boolean } = {},
): Promise<void> {
  const work = (async () => {
    const sub = await currentSubscription();
    if (!sub) return;
    try {
      await deleteRow(sub.endpoint, AbortSignal.timeout(timeoutMs));
    } catch {
      /* the unsubscribe below still runs */
    }
    if (!keepSubscription) await sub.unsubscribe().catch(() => false);
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
  | { kind: "blocked" }
  | { kind: "ready"; on: boolean };

/**
 * The pure rule of what `NotificationsSection` draws. ON needs THREE facts:
 * the permission, the browser's subscription, and this member's row of it.
 * A browser that another account subscribed reads OFF for this member.
 *
 * The server decides whether the section draws at all: only a public key
 * (a 200) draws it. A 404 (`WEB_PUSH_ENABLED` off), a 503 (no keys) and any
 * fault draw nothing.
 */
export function pushView(input: {
  support: PushSupport;
  key: KeyAnswer | null;
  permission: NotificationPermission | "unsupported";
  /** The browser holds a subscription (for any account). */
  subscribed: boolean;
  /** The gateway holds THIS member's row of that subscription. */
  registered: boolean;
}): PushView {
  if (input.key === null) return { kind: "loading" };
  if (input.key.kind !== "key") return { kind: "hidden" };
  if (input.support === "ios-tab") return { kind: "ios-tab" };
  if (input.support === "unsupported") return { kind: "unsupported" };
  if (input.permission === "denied") return { kind: "blocked" };
  return {
    kind: "ready",
    on: input.subscribed && input.registered && input.permission === "granted",
  };
}

/**
 * The mount decision of `NotificationsSection`. It READS only: no prompt and
 * no registration. It asks the server first, and a server that does not
 * serve push ends it there, with no read of the browser.
 */
export async function loadPushView(): Promise<PushView> {
  const key = await fetchPublicKey();
  if (key.kind !== "key") return { kind: "hidden" };
  const sub = await currentSubscription().catch(() => null);
  const registered = sub ? await registeredHere(sub.endpoint) : false;
  return pushView({
    support: pushSupport(),
    key,
    permission: permissionState(),
    subscribed: sub !== null,
    registered,
  });
}
