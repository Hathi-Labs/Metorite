/**
 * Projects · live refresh stopgap (WS-64 #128).
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

/** The least gap between two reads that a wake (focus, visible) can cause. */
export const LIVE_REFRESH_MS = 15_000;

interface Doc {
  visibilityState: string;
  addEventListener: (type: string, fn: () => void) => void;
  removeEventListener: (type: string, fn: () => void) => void;
}
interface Win {
  addEventListener: (type: string, fn: () => void) => void;
  removeEventListener: (type: string, fn: () => void) => void;
}

/**
 * Run `run` on a chat write at once, and on a wake at most once per window.
 * A chat write is a known change, so it is never throttled. Any run, from
 * either cause, restarts the window. Returns the cleanup.
 */
export function wireLiveRefresh(
  run: () => void,
  opts: { doc?: Doc; win?: Win; everyMs?: number; clock?: () => number } = {},
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
  win.addEventListener(PROJECTS_CHANGED_EVENT, onChanged);
  win.addEventListener("focus", onWake);
  doc.addEventListener("visibilitychange", onWake);
  return () => {
    win.removeEventListener(PROJECTS_CHANGED_EVENT, onChanged);
    win.removeEventListener("focus", onWake);
    doc.removeEventListener("visibilitychange", onWake);
  };
}

/** Call the latest `run` on a chat write or a throttled wake. */
export function useLiveRefresh(run: () => void): void {
  const ref = useRef(run);
  useEffect(() => {
    ref.current = run;
  });
  useEffect(() => wireLiveRefresh(() => ref.current()), []);
}
