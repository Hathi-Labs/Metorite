/**
 * Web push in the browser (WS-66 PN-1).
 *
 * Spec: `project-docs/specs/calendar_events_reminders.md` §5.9 and §10.2, and
 * `chat_run_continuity.md` §S7 (the fence names this file).
 *
 * - **No prompt without a tap.** No read calls `Notification.requestPermission`
 *   or registers the worker. Only `enablePush` asks, it asks FIRST, and only
 *   the checkbox's change handler of `NotificationsSection` calls it.
 * - **The test payload has no content field.** The shared fixture
 *   `tests/fixtures/push_test_payload.json` holds the title, the url and the
 *   tag only. The Python suite proves the gateway sends exactly that.
 * - **The service worker** (`public/sw.js`) always shows a notification, and a
 *   click focuses and navigates a Metorite window, or opens one.
 * - **iOS.** A Safari tab on iOS gets the Home Screen steps, never a toggle.
 * - **Sign-out.** Every sign-out path forgets this device first, and the
 *   forget never throws and never waits past its timeout.
 */
import fs from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({ signOut: vi.fn(async () => undefined) }));

import { signOut } from "next-auth/react";

import { removeAccount, resetLeaving, signOutAll, signOutThis } from "@/lib/accountSwitch";
import {
  FORGET_TIMEOUT_MS,
  base64UrlToBytes,
  currentSubscription,
  disablePush,
  enablePush,
  fetchPublicKey,
  forgetThisDevice,
  isIos,
  permissionState,
  pushSupport,
  pushView,
  registeredHere,
  sendTestPush,
  supportOf,
  testMessage,
  thisDeviceEndpoint,
  type BrowserFacts,
} from "@/lib/push";

const SRC = path.join(__dirname, "..");
const ROOT = path.join(SRC, "..");
const FIXTURE = path.join(ROOT, "..", "..", "tests", "fixtures", "push_test_payload.json");

const ENDPOINT = "https://fcm.googleapis.com/fcm/send/device-1";
const KEY = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36";
const IPHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) Version/17.4 Mobile Safari/604.1";

/**
 * Strip comments, so a fence never matches the text that explains it. Line
 * ends become LF first: a Windows checkout holds CRLF, and the slices below
 * look for "\n".
 */
function code(source: string): string {
  return source
    .replace(/\r\n/g, "\n")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function read(rel: string): string {
  return fs.readFileSync(path.join(SRC, rel), "utf8").replace(/\r\n/g, "\n");
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name)) out.push(full);
  }
  return out;
}

function rel(file: string): string {
  return path.relative(SRC, file).split(path.sep).join("/");
}

// ── A fake browser ──────────────────────────────────────────────────────────

interface Fake {
  requestPermission: ReturnType<typeof vi.fn>;
  register: ReturnType<typeof vi.fn>;
  subscribe: ReturnType<typeof vi.fn>;
  unsubscribe: ReturnType<typeof vi.fn>;
  fetch: ReturnType<typeof vi.fn>;
  order: string[];
}

function fakeBrowser(opts: {
  ua?: string;
  subscribed?: boolean;
  registered?: boolean;
  permission?: NotificationPermission;
  answer?: NotificationPermission;
  standalone?: boolean;
  fetchImpl?: (url: string, init?: RequestInit) => Promise<Response>;
} = {}): Fake {
  const order: string[] = [];
  const unsubscribe = vi.fn(async () => {
    order.push("unsubscribe");
    return true;
  });
  const keyBytes = base64UrlToBytes(KEY);
  const sub = {
    endpoint: ENDPOINT,
    unsubscribe,
    options: { applicationServerKey: keyBytes.buffer },
    toJSON: () => ({ endpoint: ENDPOINT, expirationTime: null, keys: { p256dh: "p", auth: "a" } }),
  };
  let held: typeof sub | null = opts.subscribed ? sub : null;
  const subscribe = vi.fn(async () => {
    order.push("subscribe");
    held = sub;
    return sub;
  });
  const reg = { pushManager: { getSubscription: vi.fn(async () => held), subscribe } };
  const register = vi.fn(async () => {
    order.push("register");
    return reg;
  });
  const serviceWorker = {
    getRegistration: vi.fn(async () => (opts.registered || opts.subscribed ? reg : undefined)),
    register,
    ready: Promise.resolve(reg),
  };
  const requestPermission = vi.fn(async () => {
    order.push("requestPermission");
    return opts.answer ?? "granted";
  });
  const notification = { permission: opts.permission ?? "default", requestPermission };
  vi.stubGlobal("navigator", {
    userAgent: opts.ua ?? DESKTOP_UA,
    platform: opts.ua === IPHONE_UA ? "iPhone" : "Win32",
    maxTouchPoints: opts.ua === IPHONE_UA ? 5 : 0,
    standalone: opts.standalone,
    serviceWorker,
  });
  vi.stubGlobal("window", {
    matchMedia: () => ({ matches: opts.standalone === true }),
    PushManager: function PushManager() {},
    Notification: notification,
  });
  vi.stubGlobal("Notification", notification);
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    order.push(`fetch ${init?.method ?? "GET"} ${url}`);
    if (opts.fetchImpl) return opts.fetchImpl(url, init);
    if (url.endsWith("/public-key")) return Response.json({ public_key: KEY });
    if (url.endsWith("/test")) return Response.json({ devices: 1, sent: 1, removed: 0, failed: 0 });
    return Response.json({ ok: true });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { requestPermission, register, subscribe, unsubscribe, fetch: fetchMock, order };
}

beforeEach(() => {
  vi.mocked(signOut).mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ── No prompt without a tap ─────────────────────────────────────────────────

describe("no permission prompt without a tap", () => {
  it("no read asks for permission or registers the worker", async () => {
    const b = fakeBrowser({ subscribed: true });
    pushSupport();
    permissionState();
    await currentSubscription();
    await fetchPublicKey();
    await sendTestPush();
    await registeredHere(ENDPOINT);
    await thisDeviceEndpoint();
    pushView({
      flag: true, support: "supported", key: { kind: "key", key: KEY },
      permission: "default", subscribed: false, registered: false,
    });
    await disablePush();
    await forgetThisDevice();
    expect(b.requestPermission).not.toHaveBeenCalled();
    expect(b.register).not.toHaveBeenCalled();
    expect(b.subscribe).not.toHaveBeenCalled();
  });

  it("enablePush asks FIRST, before any fetch or registration, and once", async () => {
    const b = fakeBrowser();
    expect(await enablePush()).toEqual({ ok: true });
    expect(b.requestPermission).toHaveBeenCalledTimes(1);
    expect(b.order[0]).toBe("requestPermission");
    expect(b.order).toEqual([
      "requestPermission",
      "fetch GET /api/push/public-key",
      "register",
      "subscribe",
      "fetch POST /api/push/subscriptions",
    ]);
    const post = b.fetch.mock.calls.find(([u]) => u === "/api/push/subscriptions")!;
    const body = JSON.parse(String(post[1]!.body));
    expect(body.endpoint).toBe(ENDPOINT);
    expect(body).not.toHaveProperty("member_email");
    expect(body).not.toHaveProperty("organization_id");
    expect(b.subscribe.mock.calls[0][0].userVisibleOnly).toBe(true);
  });

  it("a refused prompt registers nothing and subscribes nothing", async () => {
    const b = fakeBrowser({ answer: "denied" });
    expect((await enablePush()).ok).toBe(false);
    expect(b.register).not.toHaveBeenCalled();
    expect(b.fetch).not.toHaveBeenCalled();
  });

  it("a device the server refuses is unsubscribed again when no other account needs it", async () => {
    const refuse = async (url: string) =>
      url.endsWith("/public-key")
        ? Response.json({ public_key: KEY })
        : Response.json({ detail: "Metorite could not keep it." }, { status: 503 });
    const alone = fakeBrowser({ fetchImpl: refuse });
    const res = await enablePush(async () => false);
    expect(res).toMatchObject({ ok: false, reason: "refused", message: "Metorite could not keep it." });
    expect(alone.unsubscribe).toHaveBeenCalled();
    vi.unstubAllGlobals();
    // Another account stays on this browser: the subscription is theirs too.
    const shared = fakeBrowser({ fetchImpl: refuse });
    expect((await enablePush(async () => true)).ok).toBe(false);
    expect(shared.unsubscribe).not.toHaveBeenCalled();
  });

  it("a browser another account subscribed turns on for me with no second prompt", async () => {
    // One subscription serves every account of the switcher. The first
    // account granted the permission and subscribed. I only need my row.
    const b = fakeBrowser({ subscribed: true, permission: "granted" });
    expect(await enablePush()).toEqual({ ok: true });
    expect(b.requestPermission).not.toHaveBeenCalled();
    expect(b.subscribe).not.toHaveBeenCalled();
    expect(b.unsubscribe).not.toHaveBeenCalled();
    const post = b.fetch.mock.calls.find(([u]) => u === "/api/push/subscriptions")!;
    expect(JSON.parse(String(post[1]!.body)).endpoint).toBe(ENDPOINT);
  });

  it("the status read asks for my row with the endpoint in the body, never in the URL", async () => {
    const b = fakeBrowser({ fetchImpl: async () => Response.json({ registered: true }) });
    expect(await registeredHere(ENDPOINT)).toBe(true);
    const [url, init] = b.fetch.mock.calls[0];
    expect(url).toBe("/api/push/subscriptions/status");
    expect(init!.method).toBe("POST");
    expect(JSON.parse(String(init!.body))).toEqual({ endpoint: ENDPOINT });
    vi.unstubAllGlobals();
    fakeBrowser({ fetchImpl: async () => Response.json({}, { status: 404 }) });
    expect(await registeredHere(ENDPOINT)).toBe(false);
  });

  it("the toggle turns me off and keeps the subscription while another account stays", async () => {
    const shared = fakeBrowser({ subscribed: true });
    await disablePush(async () => true);
    expect(shared.order).toEqual(["fetch DELETE /api/push/subscriptions"]);
    vi.unstubAllGlobals();
    const alone = fakeBrowser({ subscribed: true });
    await disablePush(async () => false);
    expect(alone.order).toEqual(["fetch DELETE /api/push/subscriptions", "unsubscribe"]);
  });

  it("in the source, only enablePush calls requestPermission, and only the tap calls enablePush", () => {
    const pushSrc = code(read("lib/push.ts"));
    const start = pushSrc.indexOf("export async function enablePush(");
    const end = pushSrc.indexOf("\nexport ", start + 1);
    expect(start).toBeGreaterThan(-1);
    const asks = [...pushSrc.matchAll(/requestPermission\s*\(/g)].map((m) => m.index!);
    expect(asks).toHaveLength(1);
    expect(asks[0]).toBeGreaterThan(start);
    expect(asks[0]).toBeLessThan(end);

    const offenders = walk(SRC)
      .map((f) => ({ rel: rel(f), text: code(fs.readFileSync(f, "utf8")) }))
      .filter((f) => f.rel !== "lib/push.ts");
    expect(offenders.filter((f) => /requestPermission\s*\(/.test(f.text)).map((f) => f.rel)).toEqual([]);
    expect(offenders.filter((f) => /serviceWorker\s*\.\s*register\s*\(/.test(f.text)).map((f) => f.rel)).toEqual([]);
    expect(offenders.filter((f) => /\benablePush\s*\(/.test(f.text)).map((f) => f.rel)).toEqual([
      "app/people/components/NotificationsSection.tsx",
    ]);

    const section = code(read("app/people/components/NotificationsSection.tsx"));
    const calls = [...section.matchAll(/\benablePush\s*\(/g)];
    expect(calls).toHaveLength(1);
    const toggle = section.indexOf("async function onToggle(");
    const toggleEnd = section.indexOf("\n  }\n", toggle);
    expect(calls[0].index!).toBeGreaterThan(toggle);
    expect(calls[0].index!).toBeLessThan(toggleEnd);
    // onToggle runs from the checkbox's change handler, and from nowhere else.
    const uses = [...section.matchAll(/\bonToggle\s*\(/g)].map((m) => section.slice(m.index! - 40, m.index!));
    expect(uses).toHaveLength(2); // the declaration and the one handler
    expect(uses.some((u) => /onChange=\{\(e\) => void $/.test(u))).toBe(true);
    // The mount effect only reads.
    const effect = section.slice(section.indexOf("useEffect("), section.indexOf("}, [reload]);"));
    expect(effect).not.toMatch(/enablePush|onToggle|requestPermission/);
  });
});

// ── The payload and the service worker ──────────────────────────────────────

interface WorkerScope {
  listeners: Record<string, (e: unknown) => void>;
  shown: Array<[string, NotificationOptions & { data?: { url?: string } }]>;
  windows: Array<{ url: string; focus: ReturnType<typeof vi.fn>; navigate: ReturnType<typeof vi.fn> }>;
  openWindow: ReturnType<typeof vi.fn>;
}

function loadWorker(origin = "https://app.metorite.test"): WorkerScope {
  const listeners: WorkerScope["listeners"] = {};
  const shown: WorkerScope["shown"] = [];
  const windows: WorkerScope["windows"] = [];
  const openWindow = vi.fn(async (url: string) => ({ url }));
  const scope = {
    location: { origin },
    addEventListener: (type: string, fn: (e: unknown) => void) => {
      listeners[type] = fn;
    },
    skipWaiting: vi.fn(),
    registration: {
      showNotification: vi.fn(async (title: string, options: NotificationOptions) => {
        shown.push([title, options]);
      }),
    },
    clients: { claim: vi.fn(async () => undefined), matchAll: vi.fn(async () => windows), openWindow },
  };
  const source = fs.readFileSync(path.join(ROOT, "public", "sw.js"), "utf8");
  new Function("self", source)(scope);
  return { listeners, shown, windows, openWindow };
}

async function dispatch(scope: WorkerScope, type: string, event: Record<string, unknown>) {
  const pending: Promise<unknown>[] = [];
  scope.listeners[type]({ ...event, waitUntil: (p: Promise<unknown>) => pending.push(p) });
  await Promise.all(pending);
}

describe("the test payload has no content field", () => {
  const payload = JSON.parse(fs.readFileSync(FIXTURE, "utf8")) as Record<string, unknown>;

  it("holds a fixed title, the url and the tag, and nothing else", () => {
    expect(Object.keys(payload).sort()).toEqual(["tag", "title", "url"]);
    expect(payload.title).toBe("Metorite test notification");
    for (const content of ["body", "text", "message", "question", "chat_title"]) {
      expect(payload).not.toHaveProperty(content);
    }
  });

  it("the worker shows it with no body, and with the icon, the tag and the url", async () => {
    const w = loadWorker();
    await dispatch(w, "push", { data: { json: () => payload } });
    expect(w.shown).toHaveLength(1);
    const [title, options] = w.shown[0];
    expect(title).toBe("Metorite test notification");
    expect(options).not.toHaveProperty("body");
    expect(options).toMatchObject({ tag: "metorite-test", icon: "/icon-192.png", data: { url: "/people/me" } });
  });
});

describe("the service worker", () => {
  it("always shows a notification, even for an empty or broken payload", async () => {
    const w = loadWorker();
    await dispatch(w, "push", { data: null });
    await dispatch(w, "push", { data: { json: () => { throw new Error("not json"); } } });
    expect(w.shown.map(([t]) => t)).toEqual(["Metorite", "Metorite"]);
    expect(w.shown.every(([, o]) => o.data?.url === "/")).toBe(true);
  });

  it("a url off this origin becomes /", async () => {
    const w = loadWorker();
    for (const url of ["https://evil.test/x", "//evil.test/x", "javascript:alert(1)"]) {
      await dispatch(w, "push", { data: { json: () => ({ title: "T", url }) } });
    }
    expect(w.shown.map(([, o]) => o.data?.url)).toEqual(["/", "/", "/"]);
  });

  it("a click focuses a Metorite window and navigates it to the url", async () => {
    const w = loadWorker();
    const focus = vi.fn(async () => undefined);
    const navigate = vi.fn(async (u: string) => ({ url: u }));
    w.windows.push({ url: "https://other.test/", focus: vi.fn(), navigate: vi.fn() });
    w.windows.push({ url: "https://app.metorite.test/projects", focus, navigate });
    const close = vi.fn();
    await dispatch(w, "notificationclick", { notification: { close, data: { url: "/chat?reminder=r1" } } });
    expect(close).toHaveBeenCalled();
    expect(focus).toHaveBeenCalled();
    expect(navigate).toHaveBeenCalledWith("https://app.metorite.test/chat?reminder=r1");
    expect(w.openWindow).not.toHaveBeenCalled();
  });

  it("a click with no Metorite window opens one, at / when the url is missing", async () => {
    const w = loadWorker();
    await dispatch(w, "notificationclick", { notification: { close: vi.fn(), data: {} } });
    expect(w.openWindow).toHaveBeenCalledWith("https://app.metorite.test/");
  });

  it("a window that cannot navigate gets a new window instead", async () => {
    const w = loadWorker();
    w.windows.push({
      url: "https://app.metorite.test/",
      focus: vi.fn(async () => undefined),
      navigate: vi.fn(async () => {
        throw new TypeError("not controlled");
      }),
    });
    await dispatch(w, "notificationclick", { notification: { close: vi.fn(), data: { url: "/people/me" } } });
    expect(w.openWindow).toHaveBeenCalledWith("https://app.metorite.test/people/me");
  });

  it("has no fetch handler", () => {
    const w = loadWorker();
    expect(Object.keys(w.listeners).sort()).toEqual(["activate", "install", "notificationclick", "push"]);
  });
});

// ── What this browser can do ────────────────────────────────────────────────

describe("iOS and the other browsers", () => {
  const base: BrowserFacts = {
    userAgent: DESKTOP_UA,
    platform: "Win32",
    maxTouchPoints: 0,
    standalone: undefined,
    displayStandalone: false,
    hasServiceWorker: true,
    hasPushManager: true,
    hasNotification: true,
  };

  it("a Safari tab on iOS gets the Home Screen steps, even where the Push API exists", () => {
    expect(supportOf({ ...base, userAgent: IPHONE_UA, platform: "iPhone", maxTouchPoints: 5 })).toBe("ios-tab");
    // iPadOS asks for the desktop site: a Mac with a touch screen.
    expect(isIos({ userAgent: "Macintosh", platform: "MacIntel", maxTouchPoints: 5 })).toBe(true);
    expect(isIos({ userAgent: "Macintosh", platform: "MacIntel", maxTouchPoints: 0 })).toBe(false);
  });

  it("the Home Screen app on iOS is supported", () => {
    const ios = { ...base, userAgent: IPHONE_UA, platform: "iPhone", maxTouchPoints: 5 };
    expect(supportOf({ ...ios, standalone: true })).toBe("supported");
    expect(supportOf({ ...ios, displayStandalone: true })).toBe("supported");
  });

  it("a browser with no Push API is unsupported", () => {
    expect(supportOf({ ...base, hasPushManager: false })).toBe("unsupported");
    expect(supportOf({ ...base, hasServiceWorker: false })).toBe("unsupported");
  });

  it("the section draws the steps on iOS, a toggle elsewhere, and nothing when dark", () => {
    const key = { kind: "key" as const, key: KEY };
    const v = (over: Partial<Parameters<typeof pushView>[0]>) =>
      pushView({
        flag: true, support: "supported", key, permission: "default",
        subscribed: false, registered: false, ...over,
      });
    expect(v({ flag: false })).toEqual({ kind: "hidden" });
    expect(v({ key: { kind: "off" } })).toEqual({ kind: "hidden" });
    expect(v({ key: null })).toEqual({ kind: "loading" });
    expect(v({ support: "ios-tab" })).toEqual({ kind: "ios-tab" });
    expect(v({ support: "unsupported" })).toEqual({ kind: "unsupported" });
    expect(v({ key: { kind: "not-configured" } })).toEqual({ kind: "not-configured" });
    expect(v({ permission: "denied" })).toEqual({ kind: "blocked" });
    expect(v({})).toEqual({ kind: "ready", on: false });
    expect(v({ permission: "granted", subscribed: true, registered: true })).toEqual({ kind: "ready", on: true });
    // One browser, several accounts: another account subscribed this browser,
    // and I hold no row of it. The toggle reads OFF for me.
    expect(v({ permission: "granted", subscribed: true, registered: false })).toEqual({ kind: "ready", on: false });
  });

  it("the iOS section has no checkbox, only the three steps", () => {
    const section = code(read("app/people/components/NotificationsSection.tsx"));
    const ios = section.slice(section.indexOf('view.kind === "ios-tab"'), section.indexOf('view.kind === "unsupported"'));
    expect(ios).not.toMatch(/Checkbox|onToggle/);
    expect(ios.match(/<li>/g)).toHaveLength(3);
    expect(ios).toContain("Add to Home Screen");
  });
});

// ── Sign-out ────────────────────────────────────────────────────────────────

describe("a sign-out forgets this device first", () => {
  it("deletes this device's row, then unsubscribes", async () => {
    const b = fakeBrowser({ subscribed: true });
    await forgetThisDevice();
    expect(b.order).toEqual(["fetch DELETE /api/push/subscriptions", "unsubscribe"]);
    const del = b.fetch.mock.calls[0];
    expect(JSON.parse(String(del[1]!.body))).toEqual({ endpoint: ENDPOINT });
  });

  it("keeps the subscription when another account stays on this browser", async () => {
    const b = fakeBrowser({ subscribed: true });
    await forgetThisDevice(FORGET_TIMEOUT_MS, { keepSubscription: true });
    expect(b.order).toEqual(["fetch DELETE /api/push/subscriptions"]);
  });

  it("signOutThis deletes MY row before the switch, keeps the subscription, and names it at the remove", async () => {
    // Two accounts on one browser. I leave, and `next` stays and keeps its pushes.
    const b = fakeBrowser({
      subscribed: true,
      fetchImpl: async (url) =>
        url.endsWith("/switch") ? Response.json({ ok: true, email: "b@two.test" }) : Response.json({ ok: true }),
    });
    vi.stubGlobal("localStorage", { removeItem: () => undefined, getItem: () => null, setItem: () => undefined, key: () => null, length: 0 });
    vi.stubGlobal("BroadcastChannel", class { postMessage() {} close() {} addEventListener() {} });
    resetLeaving();
    expect(await signOutThis("a@one.test", { slot: 1 }, vi.fn(), vi.fn(async () => {}))).toBe("one");
    const del = b.order.indexOf("fetch DELETE /api/push/subscriptions");
    expect(del).toBeGreaterThan(-1);
    expect(del).toBeLessThan(b.order.indexOf("fetch POST /api/accounts/switch"));
    expect(b.unsubscribe).not.toHaveBeenCalled();
    const remove = b.fetch.mock.calls.find(([u]) => u === "/api/accounts/remove")!;
    expect(JSON.parse(String(remove[1]!.body))).toEqual({ slot: 1, push_endpoint: ENDPOINT });
  });

  it("Remove from this browser names this device's endpoint, and never unsubscribes", async () => {
    const b = fakeBrowser({ subscribed: true });
    expect(await removeAccount(2)).toBe(true);
    const [url, init] = b.fetch.mock.calls[0];
    expect(url).toBe("/api/accounts/remove");
    expect(JSON.parse(String(init!.body))).toEqual({ slot: 2, push_endpoint: ENDPOINT });
    expect(b.unsubscribe).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
    const none = fakeBrowser({ subscribed: false });
    await removeAccount(2);
    expect(JSON.parse(String(none.fetch.mock.calls[0][1]!.body))).toEqual({ slot: 2 });
  });

  it("makes no request when this browser holds no subscription", async () => {
    const b = fakeBrowser({ registered: true, subscribed: false });
    await forgetThisDevice();
    expect(b.fetch).not.toHaveBeenCalled();
  });

  it("still unsubscribes when the delete fails, and never throws", async () => {
    const b = fakeBrowser({
      subscribed: true,
      fetchImpl: async () => {
        throw new Error("offline");
      },
    });
    await expect(forgetThisDevice()).resolves.toBeUndefined();
    expect(b.unsubscribe).toHaveBeenCalled();
  });

  it("gives up after its timeout, so a sign-out never waits on it", async () => {
    vi.useFakeTimers();
    fakeBrowser({ subscribed: true, fetchImpl: () => new Promise<Response>(() => undefined) });
    const done = forgetThisDevice(50);
    await vi.advanceTimersByTimeAsync(60);
    await expect(done).resolves.toBeUndefined();
  });

  it("signOutAll deletes this device's row before it ends the session", async () => {
    const b = fakeBrowser({
      subscribed: true,
      fetchImpl: async (url) =>
        url.includes("/api/accounts") ? Response.json({ emails: ["a@one.test"] }) : Response.json({ removed: true }),
    });
    vi.stubGlobal("localStorage", { removeItem: () => undefined, getItem: () => null, setItem: () => undefined, key: () => null, length: 0 });
    vi.mocked(signOut).mockImplementation(async () => {
      b.order.push("signOut");
      return undefined as never;
    });
    await signOutAll();
    const del = b.order.indexOf("fetch DELETE /api/push/subscriptions");
    expect(del).toBeGreaterThan(-1);
    expect(del).toBeLessThan(b.order.indexOf("signOut"));
    expect(b.order.indexOf("unsubscribe")).toBeLessThan(b.order.indexOf("signOut"));
  });

  it("every file that calls next-auth's signOut forgets the device first", () => {
    const callers = walk(SRC)
      .map((f) => ({ rel: rel(f), text: code(fs.readFileSync(f, "utf8")) }))
      .filter((f) => /import\s*\{[^}]*\bsignOut\b[^}]*\}\s*from\s*"next-auth\/react"/.test(f.text));
    expect(callers.map((f) => f.rel).sort()).toEqual([
      "app/signup/SignUpForm.tsx",
      "components/Sidebar.tsx",
      "lib/accountSwitch.ts",
    ]);
    for (const f of callers) {
      const calls = [...f.text.matchAll(/\bsignOut\s*\(\s*\{/g)];
      expect(calls.length, f.rel).toBeGreaterThan(0);
      for (const m of calls) {
        const before = f.text.slice(0, m.index!);
        const forget = before.lastIndexOf("forgetThisDevice()");
        expect(forget, `${f.rel}: a signOut with no forgetThisDevice before it`).toBeGreaterThan(-1);
        // The forget is in the same function: no function starts between them.
        expect(before.slice(forget), f.rel).not.toMatch(/\n(export )?(async )?function /);
      }
    }
  });

  it("signOutThis forgets the device before the switch moves the cookie, and keeps the subscription", () => {
    const src = code(read("lib/accountSwitch.ts"));
    const fn = src.slice(src.indexOf("export async function signOutThis("), src.indexOf("export async function signOutAll("));
    const forget = fn.indexOf("await forgetThisDevice(");
    expect(forget).toBeGreaterThan(-1);
    expect(forget).toBeLessThan(fn.indexOf('act("switch"'));
    expect(fn.slice(forget, fn.indexOf(";", forget))).toContain("keepSubscription: true");
  });

  it("the fifth path: every remove carries this device's endpoint, and the server deletes that row", () => {
    // "Remove from this browser" (AccountSwitcher) and signOutThis both call
    // the `remove` action. After it, nothing can act for the removed account.
    const switchSrc = code(read("lib/accountSwitch.ts"));
    const removes = [...switchSrc.matchAll(/act\(\s*"remove"\s*,\s*([^)]*\)?)/g)].map((m) => m[1]);
    expect(removes.length).toBe(2); // removeAccount and the retry helper of signOutThis
    for (const arg of removes) expect(arg).toMatch(/^await removeBody\(/);
    const body = switchSrc.slice(switchSrc.indexOf("async function removeBody("));
    expect(body.slice(0, body.indexOf("\n}\n"))).toContain("thisDeviceEndpoint(");
    // No other file reaches the remove action by itself.
    const direct = walk(SRC)
      .map((f) => ({ rel: rel(f), text: code(fs.readFileSync(f, "utf8")) }))
      .filter((f) => f.rel !== "lib/accountSwitch.ts" && /\/api\/accounts\/remove|act\(\s*"remove"/.test(f.text))
      .map((f) => f.rel);
    expect(direct).toEqual([]);
    // The server half: the slot account's row goes before the slot does.
    const route = code(read("app/api/accounts/[action]/route.ts"));
    const branch = route.slice(route.indexOf('action === "remove"'), route.indexOf('action === "signout-all"'));
    expect(branch.indexOf("forgetSlotDevice(target.account.email")).toBeGreaterThan(-1);
    expect(branch.indexOf("forgetSlotDevice(")).toBeLessThan(branch.indexOf("clearSlot("));
    expect(branch).toContain("!same(target.account.email, s.account.email)");
  });
});

// ── The test push ───────────────────────────────────────────────────────────

describe("the test push", () => {
  it("names what happened", () => {
    expect(testMessage(200, { devices: 2, sent: 2, removed: 0, failed: 0 })).toEqual({ ok: true, message: "Sent to 2 devices." });
    expect(testMessage(200, { devices: 1, sent: 1 })).toEqual({ ok: true, message: "Sent to 1 device." });
    expect(testMessage(200, { devices: 0, sent: 0 }).ok).toBe(false);
    expect(testMessage(200, { devices: 1, sent: 0 }).ok).toBe(false);
    expect(testMessage(429, null).message).toContain("already on its way");
    expect(testMessage(503, null).message).toContain("not set up");
    expect(testMessage(502, null).ok).toBe(false);
  });
});
