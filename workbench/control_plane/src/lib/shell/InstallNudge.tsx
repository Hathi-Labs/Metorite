"use client";

/**
 * The one invitation to put Metorite on a phone's home screen.
 *
 * **Android (Chrome).** The browser fires `beforeinstallprompt` once the
 * manifest qualifies (`app/manifest.ts`). We hold the event, show ONE toast
 * with an Install button, and run the browser's own prompt on the tap. The
 * browser's mini-infobar is suppressed so the member never sees two nudges.
 * The Chrome menu's "Install app" stays as the fallback.
 *
 * **iPhone and iPad.** Safari has no install event. The toast says where the
 * Share button is. It shows once, then stays quiet for 30 days.
 *
 * It says nothing in an installed app, in an in-app browser, or on a desktop.
 * Mounted once in `app/layout.tsx` beside `UpdateNotice`, outside `Providers`,
 * so it also reaches a person on the sign-in page. Rules: `installPrompt.ts`.
 */

import { useEffect, useRef } from "react";

import { useToast } from "@/components/ui/Toast";

import {
  COPY,
  detectPlatform,
  isStandalone,
  rememberDismissal,
  wasDismissedRecently,
} from "./installPrompt";

const KEY = "install-nudge";
const SHOW_AFTER_MS = 4_000;
const WINDOW_MS = 45_000;

/** The one event Chrome fires. It is not in the DOM typings. */
interface InstallEvent extends Event {
  prompt(): Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

function storage(): Storage | undefined {
  try {
    return window.localStorage;
  } catch {
    return undefined;
  }
}

export default function InstallNudge() {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  useEffect(() => {
    if (process.env.NEXT_PUBLIC_INSTALL_NUDGE === "off") return;
    const platform = detectPlatform(navigator.userAgent, navigator.maxTouchPoints);
    if (platform !== "android" && platform !== "ios") return;
    if (isStandalone(window as never)) return;
    if (wasDismissedRecently(storage(), Date.now())) return;

    let timer: ReturnType<typeof setTimeout> | undefined;
    const dismissed = () => rememberDismissal(storage(), Date.now());

    if (platform === "ios") {
      timer = setTimeout(() => {
        toastRef.current.show({
          key: KEY,
          variant: "success",
          ...COPY.ios,
          timeout: WINDOW_MS,
          onClose: dismissed,
        });
      }, SHOW_AFTER_MS);
      return () => clearTimeout(timer);
    }

    let deferred: InstallEvent | undefined;
    const onPrompt = (e: Event) => {
      e.preventDefault(); // we show our own nudge, not the mini-infobar
      deferred = e as InstallEvent;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        toastRef.current.show({
          key: KEY,
          variant: "success",
          title: COPY.android.title,
          description: COPY.android.description,
          action: {
            label: COPY.android.action,
            onClick: () => {
              const ev = deferred;
              deferred = undefined; // the event works once
              if (!ev) return;
              ev.prompt()
                .then(() => ev.userChoice)
                .then((c) => {
                  // A "no" in the browser's own dialog is a dismissal too.
                  if (c.outcome === "dismissed") dismissed();
                })
                .catch(() => {});
            },
          },
          timeout: WINDOW_MS,
          // Closing the toast any other way (X, timeout) is a dismissal.
          onClose: () => {
            if (deferred) dismissed();
          },
        });
      }, SHOW_AFTER_MS);
    };
    const onInstalled = () => {
      deferred = undefined;
      if (timer) clearTimeout(timer);
      toastRef.current.dismiss(KEY);
    };

    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      if (timer) clearTimeout(timer);
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  return null;
}
