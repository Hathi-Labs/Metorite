"use client";

/**
 * The shell's one message about the product itself being unavailable.
 *
 * It renders nothing of its own. Every message goes through the shared toast
 * (`components/ui/Toast.tsx`), under ONE key, so "updating" turns into "back"
 * in place and never stacks. It is mounted once in `app/layout.tsx`, beside
 * the toast provider and OUTSIDE `Providers`: the moment it must speak is the
 * moment session and access are failing to resolve.
 *
 * It also catches the one error a deploy causes in a tab that stays open: a
 * failed load of an old code file (`chunkReload.ts`). That tab reloads itself
 * once. Spec: `navigation_shell.md` §7.3.
 */

import { useEffect, useRef } from "react";

import { useToast } from "@/components/ui/Toast";

import { claimAutoReload, isChunkLoadError, sessionStore } from "./chunkReload";
import {
  type ChangeInfo,
  type Health,
  createMonitor,
  installFetchObserver,
  probeHealth,
} from "./serviceHealth";

const KEY = "service-health";
const UPDATED_MS = 60_000;

/** The words, in one place, so a test can read them. */
export const COPY = {
  updating: {
    title: "Metorite is updating",
    description:
      "This usually takes under a minute. Keep this page open. It reconnects by itself.",
  },
  offline: {
    title: "You are offline",
    description: "Metorite reconnects by itself when your internet connection is back.",
  },
  back: {
    title: "Metorite is back",
    description: "If something did not save while it was updating, try it again.",
  },
  online: {
    title: "You are back online",
    description: "If something did not save while you were offline, try it again.",
  },
  updated: {
    title: "Metorite was updated",
    description: "Reload to get the new version. Save what you are typing first.",
  },
} as const;

export default function UpdateNotice() {
  const toast = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  useEffect(() => {
    // The kill switch. The notice wraps `window.fetch`, so an operator can
    // turn it off without a revert: set NEXT_PUBLIC_UPDATE_NOTICE=off and
    // rebuild. Caddy's updating page does not depend on it.
    if (process.env.NEXT_PUBLIC_UPDATE_NOTICE === "off") return;
    const reload = () => window.location.reload();

    const show = (state: Health, info: ChangeInfo) => {
      const t = toastRef.current;
      if (state === "updating") {
        t.show({ key: KEY, variant: "loading", ...COPY.updating });
      } else if (state === "offline") {
        // A spinner, not a red mark: it is reconnecting, and it is not broken.
        t.show({ key: KEY, variant: "loading", ...COPY.offline });
      } else if (state === "recovered" && info.buildChanged) {
        // A success, not an error: nothing is wrong. It stays a full minute,
        // the longest the toast allows a success. A tab that misses it still
        // reloads itself on its first failed code load (below).
        t.show({
          key: KEY,
          variant: "success",
          ...COPY.updated,
          action: { label: "Reload", onClick: reload },
          timeout: UPDATED_MS,
        });
      } else if (state === "recovered") {
        t.show({
          key: KEY,
          variant: "success",
          ...(info.from === "offline" ? COPY.online : COPY.back),
          action: { label: "Refresh page", onClick: reload },
          timeout: 8_000,
        });
      }
      // `checking` and `ok` say nothing. A probe that finds the product
      // healthy means one route failed, and that route shows its own error.
    };

    // The probe uses the fetch we are about to wrap, which is safe: the
    // observer never watches the health path itself.
    const nativeFetch = window.fetch.bind(window);
    const monitor = createMonitor({
      probe: () => probeHealth(nativeFetch),
      online: () => navigator.onLine,
      now: () => Date.now(),
      setTimer: (fn, ms) => {
        const id = window.setTimeout(fn, ms);
        return () => window.clearTimeout(id);
      },
      onChange: show,
    });
    void monitor.start();

    const restoreFetch = installFetchObserver(window, () => monitor.suspect());
    const onOnline = () => monitor.setOnline(true);
    const onOffline = () => monitor.setOnline(false);

    // A tab left open across a deploy asks for an old code file.
    const onChunk = (err: unknown) => {
      if (!isChunkLoadError(err)) return;
      if (claimAutoReload(sessionStore(), Date.now())) {
        reload();
      } else {
        toastRef.current.show({
          key: KEY,
          variant: "success",
          ...COPY.updated,
          action: { label: "Reload", onClick: reload },
          timeout: UPDATED_MS,
        });
      }
    };
    const onError = (e: ErrorEvent) => onChunk(e.error ?? { message: e.message });
    const onRejection = (e: PromiseRejectionEvent) => onChunk(e.reason);

    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
    return () => {
      monitor.dispose();
      restoreFetch();
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("error", onError);
      window.removeEventListener("unhandledrejection", onRejection);
    };
  }, []);

  return null;
}
