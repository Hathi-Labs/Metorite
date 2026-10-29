/**
 * The Notifications section on My Profile (WS-66 PN-1).
 *
 * The SERVER decides whether the section draws: the mount asks
 * `GET /api/push/public-key` (`loadPushView`), and a 404 (`WEB_PUSH_ENABLED`
 * off) or any failure draws nothing. There is no build-time flag. These cases
 * chain the real `loadPushView` into the real `NotificationsPanel`, rendered
 * to markup, so no DOM is needed. The no-prompt-without-a-tap fence is in
 * `src/lib/push.test.ts`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next-auth/react", () => ({ signOut: vi.fn(async () => undefined) }));

import { loadHideDetails, saveHideDetails } from "@/lib/hidePushDetails";
import { type PushView, loadPushView } from "@/lib/push";

import { NotificationsPanel, NotificationsSection } from "./NotificationsSection";

const KEY = "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U";
const DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36";

/** A desktop browser with the Push API and no subscription. */
function browser(fetchImpl: (url: string) => Promise<Response>) {
  const requestPermission = vi.fn(async () => "granted");
  const register = vi.fn();
  const notification = { permission: "default", requestPermission };
  vi.stubGlobal("navigator", {
    userAgent: DESKTOP_UA,
    platform: "Win32",
    maxTouchPoints: 0,
    serviceWorker: { getRegistration: vi.fn(async () => undefined), register },
  });
  vi.stubGlobal("window", {
    matchMedia: () => ({ matches: false }),
    PushManager: function PushManager() {},
    Notification: notification,
  });
  vi.stubGlobal("Notification", notification);
  const fetchMock = vi.fn(async (url: string) => fetchImpl(url));
  vi.stubGlobal("fetch", fetchMock);
  return { requestPermission, register, fetch: fetchMock };
}

function panel(view: PushView, extra: { busy?: boolean } = {}): string {
  return renderToStaticMarkup(
    createElement(NotificationsPanel, {
      view,
      busy: extra.busy ?? false,
      note: null,
      onToggle: () => undefined,
      onTest: () => undefined,
    }),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the server decides whether the section draws", () => {
  it("renders NOTHING when the gateway answers 404 (WEB_PUSH_ENABLED off)", async () => {
    const b = browser(async () => Response.json({ detail: "Not found" }, { status: 404 }));
    const view = await loadPushView();
    expect(view).toEqual({ kind: "hidden" });
    expect(panel(view)).toBe("");
    expect(b.requestPermission).not.toHaveBeenCalled();
    expect(b.register).not.toHaveBeenCalled();
  });

  it.each([
    ["a 503", async () => Response.json({}, { status: 503 })],
    ["a network fault", async (): Promise<Response> => {
      throw new TypeError("Failed to fetch");
    }],
    ["a 200 with no key", async () => Response.json({})],
  ])("renders nothing on %s", async (_why, answer) => {
    browser(answer);
    expect(panel(await loadPushView())).toBe("");
  });

  it("renders the toggle when the gateway answers 200 with the key", async () => {
    const b = browser(async () => Response.json({ public_key: KEY }));
    const html = panel(await loadPushView());
    expect(html).toContain('type="checkbox"');
    expect(html).toContain("Show Metorite notifications on this device");
    // The attribute, not the word: the box's own classes say `checked:bg-primary`.
    expect(html).not.toMatch(/\schecked=""/);
    // Off: no test button until this member holds a row.
    expect(html).not.toContain("Send a test");
    expect(b.requestPermission).not.toHaveBeenCalled();
    expect(b.register).not.toHaveBeenCalled();
  });

  it("the section itself draws nothing before the server answers, and asks nothing while it renders", () => {
    const fetchMock = vi.fn(async () => {
      throw new Error("a render must not fetch");
    });
    vi.stubGlobal("fetch", fetchMock);
    expect(renderToStaticMarkup(createElement(NotificationsSection))).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("what the panel draws for each view", () => {
  it("loading draws nothing", () => {
    expect(panel({ kind: "loading" })).toBe("");
  });

  it("on draws a checked box and the test button", () => {
    const html = panel({ kind: "ready", on: true });
    expect(html).toMatch(/<input[^>]*\schecked=""/);
    expect(html).toContain("Send a test");
  });

  it("a Safari tab on iOS draws the three steps and no checkbox", () => {
    const html = panel({ kind: "ios-tab" });
    expect(html.match(/<li>/g)).toHaveLength(3);
    expect(html).toContain("Add to Home Screen");
    expect(html).not.toContain('type="checkbox"');
  });

  it("a blocked permission and an unsupported browser draw words and no checkbox", () => {
    for (const view of [{ kind: "blocked" }, { kind: "unsupported" }] as PushView[]) {
      const html = panel(view);
      expect(html).toContain("Notifications on this device");
      expect(html).not.toContain('type="checkbox"');
    }
  });
});

describe("Hide details on the lock screen (WS-66 RE-9a)", () => {
  const ready: PushView = { kind: "ready", on: false };
  const withHide = (hideDetails: boolean | null) =>
    renderToStaticMarkup(
      createElement(NotificationsPanel, {
        view: ready,
        busy: false,
        note: null,
        onToggle: () => undefined,
        onTest: () => undefined,
        hideDetails,
        onHideDetails: () => undefined,
      }),
    );

  it("draws the toggle, unchecked by default and checked when the key is on", () => {
    const off = withHide(false);
    expect(off).toContain("Hide details on the lock screen");
    expect(off.match(/<input[^>]*type="checkbox"/g)).toHaveLength(2);
    expect(off).not.toMatch(/\schecked=""/);
    expect(withHide(true)).toMatch(/<input[^>]*aria-label="Hide details on the lock screen"[^>]*\schecked=""|<input[^>]*\schecked=""[^>]*aria-label="Hide details on the lock screen"/);
  });

  it("draws no toggle before the read answers", () => {
    expect(withHide(null)).not.toContain("Hide details on the lock screen");
    expect(panel(ready)).not.toContain("Hide details on the lock screen");
  });

  it("saves the key and reads it back through the prefs route", async () => {
    const stored: Record<string, unknown> = {};
    const calls: Array<{ method: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        expect(url).toBe("/api/shell/feed/prefs");
        const method = init?.method ?? "GET";
        calls.push({ method, body: init?.body as string | undefined });
        if (method === "PUT") {
          const { key, value } = JSON.parse(init?.body as string);
          stored[key] = value;
        }
        return Response.json({ ...stored });
      }),
    );
    expect(await loadHideDetails()).toBe(false);
    expect(await saveHideDetails(true)).toBe(true);
    expect(calls[1]).toEqual({
      method: "PUT",
      body: JSON.stringify({ key: "hide_push_details", value: true }),
    });
    expect(await loadHideDetails()).toBe(true);
    expect(await saveHideDetails(false)).toBe(false);
    expect(await loadHideDetails()).toBe(false);
  });

  it("a failed read or save gives null, never a guess", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status: 503 })));
    expect(await loadHideDetails()).toBeNull();
    expect(await saveHideDetails(true)).toBeNull();
    vi.stubGlobal("fetch", vi.fn(async (): Promise<Response> => {
      throw new TypeError("Failed to fetch");
    }));
    expect(await loadHideDetails()).toBeNull();
    expect(await saveHideDetails(true)).toBeNull();
  });
});
