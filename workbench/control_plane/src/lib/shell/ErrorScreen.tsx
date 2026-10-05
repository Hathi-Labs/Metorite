"use client";

/**
 * What a member sees when a page throws, instead of React's raw
 * "Application error: a client-side exception has occurred".
 *
 * Two cases, because they need opposite words:
 *
 * 1. **A stale tab after a deploy** (`isChunkLoadError`). Nothing is broken.
 *    The page reloads itself once to fetch the new version. If the guard says
 *    it already reloaded in the last minute, it offers the button instead.
 * 2. **Anything else.** Something on this page failed. The member can try
 *    again, which re-renders the page, or reload it. The words say what to do
 *    and do not apologise, and nothing claims the work is safe when the page
 *    cannot know that.
 *
 * Used by `app/error.tsx` (inside the shell) and `app/global-error.tsx` (when
 * the root layout itself fails). Spec: `navigation_shell.md` §7.3.
 */

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import Button from "@/components/ui/Button";

import { claimAutoReload, isChunkLoadError, sessionStore } from "./chunkReload";

export const ERROR_COPY = {
  updated: {
    title: "Metorite was just updated",
    reloading: "Loading the new version…",
    manual: "Reload this page to get the new version.",
  },
  failed: {
    title: "This page did not load",
    body: "Try again. If it keeps happening, reload the page or tell your admin.",
  },
} as const;

export default function ErrorScreen({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset?: () => void;
}) {
  const stale = isChunkLoadError(error);
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    if (stale && claimAutoReload(sessionStore(), Date.now())) {
      setReloading(true);
      window.location.reload();
    }
  }, [stale]);

  const reload = () => window.location.reload();

  return (
    <div className="flex h-full min-h-[50vh] items-center justify-center p-8">
      <div
        role={stale ? "status" : "alert"}
        className="max-w-md rounded-xl border border-border bg-card p-8 text-center"
      >
        <div className="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
          <Icon
            name={stale ? "RefreshCw" : "AlertCircle"}
            size={20}
            className={
              stale && reloading
                ? "text-muted-foreground motion-safe:animate-spin"
                : "text-muted-foreground"
            }
          />
        </div>
        <h1 className="text-base font-semibold text-foreground">
          {stale ? ERROR_COPY.updated.title : ERROR_COPY.failed.title}
        </h1>
        <p className="mt-2 text-sm text-muted-foreground">
          {stale
            ? reloading
              ? ERROR_COPY.updated.reloading
              : ERROR_COPY.updated.manual
            : ERROR_COPY.failed.body}
        </p>
        {!reloading && (
          <div className="mt-6 flex justify-center gap-2">
            {!stale && reset && (
              <Button variant="primary" size="sm" onClick={reset}>
                Try again
              </Button>
            )}
            <Button variant={stale ? "primary" : "secondary"} size="sm" onClick={reload}>
              Reload page
            </Button>
          </div>
        )}
        {error.digest && !stale && (
          <p className="mt-4 text-[11px] text-muted-foreground">Reference: {error.digest}</p>
        )}
      </div>
    </div>
  );
}
