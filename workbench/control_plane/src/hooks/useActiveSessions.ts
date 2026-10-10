"use client";

/**
 * useActiveSessions — React hook that returns the set of session IDs
 * that currently have an active (streaming) agent run.
 *
 * Two sources of truth, merged:
 *   1. Local chatStore (isLoading flag) — instant, reactive via
 *      useSyncExternalStore.  Works for sessions in the current
 *      browser tab.
 *   2. The server's live runs (`cc:<org>:liveruns`), read by ONE shared
 *      poller for the whole app (`lib/liveRuns.ts`, WS-51 S1): every 5 s
 *      while the tab is visible, every 30 s while it is hidden. Catches
 *      sessions running in background after a browser refresh or on
 *      another device.
 *
 * The returned set is the UNION of both sources.
 *
 * `useRunActivity` is the same union, counted per app pane for the nav's run
 * badge (`lib/runActivity.ts`).
 */

import { useSyncExternalStore, useCallback, useMemo, useRef } from "react";
import { subscribeAllSessions, getActiveSessionIdsStable, getSessionAgent } from "@/lib/chatStore";
import { getLiveRuns, getServerLiveRuns, subscribeLiveRuns } from "@/lib/liveRuns";
import { countRunsByApp, mergeRuns } from "@/lib/runActivity";

const EMPTY = new Set<string>();

function useLocalActive(): Set<string> {
  const subscribe = useCallback(
    (listener: () => void) => subscribeAllSessions(listener),
    [],
  );
  const getSnapshot = useCallback(() => getActiveSessionIdsStable(), []);
  // Server snapshot must be stable and not cause hydration mismatches.
  const getServerSnapshot = useCallback(() => EMPTY, []);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

const NO_RUNS = () => () => {};

/** `enabled: false` reads nothing and starts no poll: no workspace yet. */
function useServerRuns(enabled = true) {
  return useSyncExternalStore(
    enabled ? subscribeLiveRuns : NO_RUNS,
    enabled ? getLiveRuns : getServerLiveRuns,
    getServerLiveRuns,
  );
}

export function useActiveSessions(): Set<string> {
  const localActive = useLocalActive();
  const serverRuns = useServerRuns();
  // `getLiveRuns` hands back the same array while the list is unchanged, so
  // this set keeps its reference too.
  const serverActiveIds = useMemo(
    () => (serverRuns.length ? new Set(serverRuns.map((r) => r.threadId)) : EMPTY),
    [serverRuns],
  );
  // Stable reference for the merged union (see the merge step below).
  const mergedRef = useRef<Set<string>>(EMPTY);
  const mergedKeyRef = useRef<string>("");

  // Merge local + server active IDs (union).  The early-return paths below all
  // hand back already-stable references (EMPTY constant, the memoised server
  // set, or the cached localActive snapshot).
  if (localActive.size === 0 && serverActiveIds.size === 0) return EMPTY;
  if (localActive.size === 0) return serverActiveIds;
  if (serverActiveIds.size === 0) return localActive;

  // Both non-empty — compute the union.  CRITICAL: return a STABLE reference
  // when the union's contents are unchanged.  Consumers (AppShell, the chat
  // list, the email chat) put this Set in effect/memo dependency arrays, so a
  // fresh Set on every render re-triggers those effects → setState →
  // re-render → … → "Maximum update depth exceeded" (React #185).  This is the
  // exact case that fires while an agent run is active in BOTH the local store
  // (isLoading) AND the server poll (cc:<org>:liveruns) — e.g. running the
  // email assistant in the email app.
  const merged = new Set<string>();
  for (const id of localActive) merged.add(id);
  for (const id of serverActiveIds) merged.add(id);
  const mergedKey = [...merged].sort().join(",");
  if (mergedKey === mergedKeyRef.current) return mergedRef.current;
  mergedKeyRef.current = mergedKey;
  mergedRef.current = merged;
  return merged;
}

export type RunActivity = {
  /** Live runs across every app. */
  total: number;
  /** Live runs per app pane href, e.g. `{ "/chat": 2, "/projects": 1 }`. */
  byApp: Record<string, number>;
  /** The runs that wait on the member's answer (WS-51 S2), across every app. */
  needsTotal: number;
  /** The same, per app pane href. A pane with any wears the amber badge. */
  needsByApp: Record<string, number>;
};

/**
 * The live runs, counted per app pane. `visibleHrefs` is the set of panes the
 * member's nav shows: a run on a pane they cannot see counts on Chat. Pass a
 * memoised set, or the counts recompute on every render. `enabled: false`
 * starts no poll, for a member with no workspace (`shouldPollWorkspace`).
 */
export function useRunActivity(
  visibleHrefs: ReadonlySet<string> | null,
  enabled = true,
): RunActivity {
  const localActive = useLocalActive();
  const serverRuns = useServerRuns(enabled);
  return useMemo(() => {
    const runs = mergeRuns(serverRuns, localActive, getSessionAgent);
    const needsByApp = countRunsByApp(runs, visibleHrefs, undefined, true);
    return {
      total: runs.length,
      byApp: countRunsByApp(runs, visibleHrefs),
      needsTotal: Object.values(needsByApp).reduce((a, b) => a + b, 0),
      needsByApp,
    };
  }, [serverRuns, localActive, visibleHrefs]);
}

/**
 * True while the server says *threadId* waits on the member's answer
 * (WS-51 S2). The chat reads it to fetch its parked card again. It reads the
 * one shared poller and starts no poll of its own.
 */
export function useThreadNeedsInput(threadId: string | null | undefined): boolean {
  const serverRuns = useServerRuns(Boolean(threadId));
  return useMemo(
    () => Boolean(threadId) && serverRuns.some((r) => r.threadId === threadId && r.state === "needs_input"),
    [serverRuns, threadId],
  );
}
