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

import { type PushView, loadPushView } from "@/lib/push";
import { loadFeedPrefs, saveFeedPref } from "@/lib/shell/feed";

import { readFileSync } from "node:fs";

import {
  HIDE_LOAD_FAILED,
  HIDE_SAVE_FAILED,
  NotificationsPanel,
  NotificationsSection,
} from "./NotificationsSection";

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
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        expect(url).toBe("/api/shell/feed/prefs");
        if (init?.method === "PUT") {
          const { key, value } = JSON.parse(init?.body as string);
          stored[key] = value;
        }
        return Response.json({ ...stored });
      }),
    );
    expect((await loadFeedPrefs())?.hide_push_details).toBeUndefined();
    expect((await saveFeedPref("hide_push_details", true))?.hide_push_details).toBe(true);
    expect((await loadFeedPrefs())?.hide_push_details).toBe(true);
  });

  const hideExtra = (extra: Record<string, unknown>, busy = false) =>
    renderToStaticMarkup(
      createElement(NotificationsPanel, {
        view: { kind: "ready", on: true } as PushView,
        busy,
        note: null,
        onToggle: () => undefined,
        onTest: () => undefined,
        hideDetails: false,
        onHideDetails: () => undefined,
        ...extra,
      }),
    );

  it("a hide save shows its own Saving line, and does not set Send a test busy", () => {
    const html = hideExtra({ hideSaving: true });
    expect(html).toContain("Saving…");
    // The hide box waits. The other box and the test button do not.
    const boxes = html.match(/<input[^>]*type="checkbox"[^>]*>/g) ?? [];
    expect(boxes[0]).not.toMatch(/\sdisabled=""/);
    expect(boxes[1]).toMatch(/\sdisabled=""/);
    const test = html.match(/<button[^>]*>(?:(?!<\/button>)[\s\S])*Send a test/)?.[0] ?? "";
    expect(test).not.toMatch(/\sdisabled=""/);
    // Idle: no Saving line.
    expect(hideExtra({})).not.toContain("Saving…");
    // The handler never touches the shared busy flag.
    const src = readFileSync(new URL("./NotificationsSection.tsx", import.meta.url), "utf8");
    const body = src.slice(src.indexOf("async function onHideDetails"), src.indexOf("async function onToggle"));
    expect(body).not.toContain("setBusy");
  });

  it("a failed save names the setting, draws under the box, and keeps the old value", () => {
    const html = hideExtra({ hideError: true, hideDetails: true });
    expect(html).toContain(HIDE_SAVE_FAILED);
    expect(HIDE_SAVE_FAILED).toBe("Could not save the lock screen setting. Try again.");
    expect(html).toMatch(/role="alert"[^>]*>Could not save the lock screen setting/);
    expect(html.indexOf(HIDE_SAVE_FAILED)).toBeGreaterThan(html.indexOf("Hide details on the lock screen"));
    expect(html.indexOf(HIDE_SAVE_FAILED)).toBeLessThan(html.indexOf("Send a test"));
    expect(html).toMatch(/aria-label="Hide details on the lock screen"[^>]*\schecked=""|\schecked=""[^>]*aria-label="Hide details on the lock screen"/);
    // The save handler sets the stored value only on a good answer.
    const src = readFileSync(new URL("./NotificationsSection.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/if \(stored === null\) setHideError\(true\);\s+else setHideDetails/);
  });

  it("a failed read draws the load line and no box; not read yet draws nothing", () => {
    const html = hideExtra({ hideLoadFailed: true, hideDetails: null });
    expect(html).toContain(HIDE_LOAD_FAILED);
    expect(html).not.toContain("Hide details on the lock screen");
    const pending = hideExtra({ hideDetails: null });
    expect(pending).not.toContain(HIDE_LOAD_FAILED);
    expect(pending).not.toContain("Hide details on the lock screen");
  });

  it("the help line is true to the sent title and is tied to the box", () => {
    const html = hideExtra({});
    expect(html).toContain("Metorite: a reminder");
    expect(html).not.toContain("shows only your organization name");
    expect(html).toMatch(/aria-describedby="hide-details-help"/);
    expect(html).toContain('id="hide-details-help"');
  });

  it("a failed read or save gives null, never a guess", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({}, { status: 503 })));
    expect(await loadFeedPrefs()).toBeNull();
    expect(await saveFeedPref("hide_push_details", true)).toBeNull();
  });
});
