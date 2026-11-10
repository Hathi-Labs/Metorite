import { describe, expect, it } from "vitest";

import {
  DISMISS_KEY,
  DISMISS_MS,
  detectPlatform,
  isStandalone,
  rememberDismissal,
  wasDismissedRecently,
} from "./installPrompt";

const ANDROID =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36";
const ANDROID_WV =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8; wv) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36";
const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1";
const IPAD_DESKTOP =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15";

describe("detectPlatform", () => {
  it("reads Android Chrome, iPhone and iPad", () => {
    expect(detectPlatform(ANDROID)).toBe("android");
    expect(detectPlatform(IPHONE)).toBe("ios");
    expect(detectPlatform(IPAD_DESKTOP, 5)).toBe("ios");
  });
  it("leaves a real Mac, a WebView and an in-app browser alone", () => {
    expect(detectPlatform(IPAD_DESKTOP, 0)).toBe("other");
    expect(detectPlatform(ANDROID_WV)).toBe("other");
    expect(detectPlatform(IPHONE + " Instagram 300.0")).toBe("ios-webview");
  });
});

describe("isStandalone", () => {
  it("is true for the iOS flag and for the display-mode query", () => {
    expect(isStandalone({ navigator: { standalone: true } })).toBe(true);
    expect(isStandalone({ matchMedia: () => ({ matches: true }) })).toBe(true);
    expect(isStandalone({ matchMedia: () => ({ matches: false }) })).toBe(false);
    expect(isStandalone({})).toBe(false);
  });
});

describe("dismissal", () => {
  it("holds for 30 days, then lapses", () => {
    const data: Record<string, string> = {};
    const store = {
      getItem: (k: string) => data[k] ?? null,
      setItem: (k: string, v: string) => void (data[k] = v),
    };
    expect(wasDismissedRecently(store, 1000)).toBe(false);
    rememberDismissal(store, 1000);
    expect(data[DISMISS_KEY]).toBe("1000");
    expect(wasDismissedRecently(store, 1000 + DISMISS_MS - 1)).toBe(true);
    expect(wasDismissedRecently(store, 1000 + DISMISS_MS)).toBe(false);
  });
  it("survives storage that throws, and junk values", () => {
    const boom = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(wasDismissedRecently(boom, 1)).toBe(false);
    expect(() => rememberDismissal(boom, 1)).not.toThrow();
    expect(wasDismissedRecently({ getItem: () => "abc" }, 1)).toBe(false);
  });
});
