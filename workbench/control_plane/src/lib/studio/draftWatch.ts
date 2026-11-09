/**
 * How the Studio IDE learns that the draft changed (`ai_studio.md` §6.3, slice G3a).
 *
 * No event says that the agent saved a draft. So the page asks: every 5 s while the page is
 * visible, when the page becomes visible, and when a chat run ends. The page redraws only when
 * the hash of the manifest changed, so a quiet draft never flickers. A failed read keeps the
 * old draft on screen and sets a status line. A 404 ends the polling.
 *
 * `startWatch` holds the wiring, with the browser parts passed in, so a test can prove that the
 * timer, the session subscription and the listener are all removed on stop.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { getActiveSessionIds, subscribeAllSessions } from "@/lib/chatStore";

import { draftHash, getPackageDraft, type PackageDraft } from "./packageDraft";
import { STUDIO_OFF } from "./realDoor";

export const POLL_MS = 5_000;
export const STALE_LINE = "The draft could not refresh. The page shows the last draft and tries again in a few seconds.";

/** Keep `prev` when `next` holds the same manifest, so nothing redraws. */
export function nextDraft(prev: PackageDraft | null, next: PackageDraft): PackageDraft {
  if (!prev) return next;
  const same = draftHash(prev.manifest) === draftHash(next.manifest)
    && prev.name === next.name && prev.liveVersion === next.liveVersion && prev.manifestHash === next.manifestHash;
  return same ? prev : next;
}

/** True when a chat run was live and no run is live now. */
export function wentIdle(wasActive: boolean, isActive: boolean): boolean {
  return wasActive && !isActive;
}

const inflight = new Map<string, Promise<PackageDraft>>();

/** One GET for the asks that overlap. A strict-mode double mount and a tick make two asks at once. */
export function fetchDraft(pkg: string, fetchImpl?: typeof fetch): Promise<PackageDraft> {
  const held = inflight.get(pkg);
  if (held) return held;
  const run = getPackageDraft(pkg, fetchImpl).finally(() => { inflight.delete(pkg); });
  inflight.set(pkg, run);
  return run;
}

export interface WatchParts {
  refresh: () => void;
  doc: Document;
  setIntervalFn: (fn: () => void, ms: number) => ReturnType<typeof setInterval>;
  clearIntervalFn: (handle: ReturnType<typeof setInterval>) => void;
  /** Subscribe to a change of any chat session. Returns the unsubscribe. */
  subscribe: (listener: () => void) => () => void;
  /** Whether any chat run is live now. */
  isActive: () => boolean;
  /** True after a 404: nothing polls any more. */
  isStopped: () => boolean;
}

/** Start the watch. It reads once at once. The returned function removes every part of it. */
export function startWatch(p: WatchParts): () => void {
  const read = () => { if (!p.isStopped()) p.refresh(); };
  const readIfVisible = () => { if (p.doc.visibilityState === "visible") read(); };
  read();
  const timer = p.setIntervalFn(readIfVisible, POLL_MS);
  p.doc.addEventListener("visibilitychange", readIfVisible);
  let active = p.isActive();
  const unsub = p.subscribe(() => {
    const now = p.isActive();
    if (wentIdle(active, now)) read();
    active = now;
  });
  return () => {
    p.clearIntervalFn(timer);
    p.doc.removeEventListener("visibilitychange", readIfVisible);
    unsub();
  };
}

export interface DraftWatch {
  draft: PackageDraft | null;
  /** The sentence for a failed refresh, or null. */
  stale: string | null;
  /** True when the gateway answered 404: the switch is off or the app is not the member's. */
  off: boolean;
  loading: boolean;
  refresh: () => void;
  /** Take a draft that the page just saved, and drop any read that is still in flight (STU-G3c). */
  adopt: (next: PackageDraft) => void;
}

export function useDraftWatch(pkg: string | null): DraftWatch {
  const [draft, setDraft] = useState<PackageDraft | null>(null);
  const [stale, setStale] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const offRef = useRef(false);

  const refresh = useCallback(() => {
    if (!pkg) return;
    const mine = ++seq.current;
    fetchDraft(pkg).then(
      (next) => {
        if (mine !== seq.current) return;
        setDraft((prev) => nextDraft(prev, next));
        setStale(null);
        setOff(false);
        offRef.current = false;
        setLoading(false);
      },
      (e: unknown) => {
        if (mine !== seq.current) return;
        setLoading(false);
        if (e instanceof Error && e.message === STUDIO_OFF) { offRef.current = true; setOff(true); return; }
        setStale(STALE_LINE);
      },
    );
  }, [pkg]);

  const adopt = useCallback((next: PackageDraft) => {
    seq.current++;
    setDraft((prev) => nextDraft(prev, next));
    setStale(null);
  }, []);

  useEffect(() => {
    const seqs = seq;
    const stop = startWatch({
      refresh, doc: document,
      setIntervalFn: (fn, ms) => setInterval(fn, ms),
      clearIntervalFn: (h) => clearInterval(h),
      subscribe: subscribeAllSessions,
      isActive: () => getActiveSessionIds().size > 0,
      isStopped: () => offRef.current,
    });
    return () => {
      seqs.current++;
      stop();
    };
  }, [refresh]);

  return { draft, stale, off, loading, refresh, adopt };
}
