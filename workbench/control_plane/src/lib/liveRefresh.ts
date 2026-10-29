/**
 * Live refresh for every app (WS-64 #128, task #124).
 *
 * The assistant's tool writes raise `ASSISTANT_WROTE_EVENT`
 * (`lib/assistantWrites.ts`), and a surface that names its `app` reads at once.
 * That is the one path for all apps.
 *
 * A write made by another process (an MCP client, an agent run on the
 * server) never raises `PROJECTS_CHANGED_EVENT`. That event comes only from
 * a fresh chat receipt in the same tab. So a surface also reads again when
 * the tab returns to sight or the window gets focus, at most once in each
 * `LIVE_REFRESH_MS`. This is a stopgap. A push or delta feed is a later slice.
 *
 * `wireLiveRefresh` is plain DOM code so a test can drive it. `useLiveRefresh`
 * is its React face. The Projects page and `MyWorkHost` both call it, so the
 * two surfaces share one rule.
 */
import { useEffect, useRef } from "react";

import { PROJECTS_CHANGED_EVENT } from "@/components/projects/ProjectToolCards";
import { ASSISTANT_WROTE_EVENT, eventNamesApp, type LiveApp } from "@/lib/assistantWrites";

/** The least gap between two reads that a wake (focus, visible) can cause. */
export const LIVE_REFRESH_MS = 15_000;

/**
 * How long a burst of assistant writes waits before ONE read (task #124).
 * A run that makes twenty tasks raises twenty events in a few seconds. Each
 * one moves the read on, so the app reads once, just after the last write,
 * and not twenty times.
 */
export const WRITE_SETTLE_MS = 400;

interface Timers {
  set: (fn: () => void, ms: number) => unknown;
  clear: (handle: unknown) => void;
}

interface Doc {
  visibilityState: string;
  addEventListener: (type: string, fn: () => void) => void;
  removeEventListener: (type: string, fn: () => void) => void;
}
interface Win {
  addEventListener: (type: string, fn: (e?: unknown) => void) => void;
  removeEventListener: (type: string, fn: (e?: unknown) => void) => void;
}

/**
 * Run `run` on a chat write at once, and on a wake at most once per window.
 * A chat write is a known change, so it is never throttled. Any run, from
 * either cause, restarts the window. Returns the cleanup.
 */
export function wireLiveRefresh(
  run: () => void,
  opts: {
    doc?: Doc; win?: Win; everyMs?: number; clock?: () => number; app?: LiveApp;
    settleMs?: number; timers?: Timers;
  } = {},
): () => void {
  const doc = opts.doc ?? (typeof document !== "undefined" ? (document as Doc) : undefined);
  const win = opts.win ?? (typeof window !== "undefined" ? (window as Win) : undefined);
  const everyMs = opts.everyMs ?? LIVE_REFRESH_MS;
  const clock = opts.clock ?? Date.now;
  if (!doc || !win) return () => {};
  let last = clock();
  const onChanged = () => {
    last = clock();
    run();
  };
  const onWake = () => {
    if (doc.visibilityState !== "visible") return;
    if (clock() - last < everyMs) return;
    last = clock();
    run();
  };
  // The assistant wrote through a tool (task #124). It names the apps it
  // changed, and this surface reads only when its own app is among them. A
  // burst of writes folds into one read, after `settleMs` with no new write.
  const settleMs = opts.settleMs ?? WRITE_SETTLE_MS;
  const timers: Timers = opts.timers ?? {
    set: (fn, ms) => setTimeout(fn, ms),
    clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  };
  let settle: unknown = null;
  const onWrote = (e?: unknown) => {
    if (!opts.app) return;
    if (!eventNamesApp((e as { detail?: unknown } | undefined)?.detail, opts.app)) return;
    if (settle !== null) timers.clear(settle);
    settle = timers.set(() => {
      settle = null;
      onChanged();
    }, settleMs);
  };
  win.addEventListener(PROJECTS_CHANGED_EVENT, onChanged);
  win.addEventListener(ASSISTANT_WROTE_EVENT, onWrote);
  win.addEventListener("focus", onWake);
  doc.addEventListener("visibilitychange", onWake);
  return () => {
    if (settle !== null) timers.clear(settle);
    win.removeEventListener(PROJECTS_CHANGED_EVENT, onChanged);
    win.removeEventListener(ASSISTANT_WROTE_EVENT, onWrote);
    win.removeEventListener("focus", onWake);
    doc.removeEventListener("visibilitychange", onWake);
  };
}

/**
 * Call the latest `run` on a chat write or a throttled wake. `app` names the
 * surface. Pass it and the surface also reads when the assistant wrote to that
 * app through a tool, with no receipt card on screen (task #124).
 */
export function useLiveRefresh(run: () => void, app?: LiveApp): void {
  const ref = useRef(run);
  useEffect(() => {
    ref.current = run;
  });
  useEffect(() => wireLiveRefresh(() => ref.current(), { app }), [app]);
}
