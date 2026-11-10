/**
 * Install-to-home-screen, the rules without the screen.
 *
 * Android Chrome fires `beforeinstallprompt` when the manifest qualifies. iPhone
 * Safari has no such event and no install API, so the only help is words: Share,
 * then Add to Home Screen. `InstallNudge.tsx` shows both. This file decides
 * WHEN, and it touches no React, so `installPrompt.test.ts` can read every rule.
 */

export type InstallPlatform = "android" | "ios" | "ios-webview" | "other";

/** A member who dismisses the hint is not asked again for this long. */
export const DISMISS_MS = 30 * 24 * 60 * 60 * 1000;

export const DISMISS_KEY = "metorite:install-nudge-dismissed-at";

/**
 * Which platform the page runs on. An iPad asks for the desktop site and says
 * "Macintosh", so a Mac that has a touch screen is an iPad. An in-app browser
 * (Facebook, Instagram, LinkedIn…) has no Share → Add to Home Screen, so it
 * gets no hint at all.
 */
export function detectPlatform(ua: string, maxTouchPoints = 0): InstallPlatform {
  const iosUa = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && maxTouchPoints > 1);
  if (iosUa) {
    return /FBAN|FBAV|Instagram|Line\/|LinkedInApp|Twitter|MicroMessenger|GSA\//.test(ua)
      ? "ios-webview"
      : "ios";
  }
  if (/Android/.test(ua)) {
    // An Android WebView ("; wv)") cannot install a site either.
    return /; wv\)/.test(ua) ? "other" : "android";
  }
  return "other";
}

/** True when the app already runs from the home screen. */
export function isStandalone(win: {
  matchMedia?: (q: string) => { matches: boolean };
  navigator?: { standalone?: boolean };
}): boolean {
  if (win.navigator?.standalone === true) return true; // iOS only
  try {
    return win.matchMedia?.("(display-mode: standalone)").matches === true;
  } catch {
    return false;
  }
}

/** Storage can throw (private mode, blocked cookies). Then nothing is remembered. */
export function wasDismissedRecently(
  store: Pick<Storage, "getItem"> | undefined,
  now: number,
): boolean {
  try {
    const raw = store?.getItem(DISMISS_KEY);
    const at = raw ? Number(raw) : NaN;
    return Number.isFinite(at) && now - at < DISMISS_MS && now >= at;
  } catch {
    return false;
  }
}

export function rememberDismissal(store: Pick<Storage, "setItem"> | undefined, now: number): void {
  try {
    store?.setItem(DISMISS_KEY, String(now));
  } catch {
    /* nothing to remember it in */
  }
}

/** The words, in one place, so a test can read them. */
export const COPY = {
  android: {
    title: "Install Metorite",
    description: "Open it from your home screen, full screen, with no browser bar.",
    action: "Install",
  },
  ios: {
    title: "Add Metorite to your Home Screen",
    description: "Tap the Share button, then Add to Home Screen.",
  },
} as const;
