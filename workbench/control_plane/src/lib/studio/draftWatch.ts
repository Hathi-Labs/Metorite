/**
 * How the Studio IDE learns that the draft changed (`ai_studio.md` §6.3, slice G3a).
 *
 * No event says that the agent saved a draft. So the page asks: every 5 s while the page is
 * visible, when the page becomes visible, and when a chat run ends. The page redraws only when
 * the hash of the manifest changed, so a quiet draft never flickers. A failed read keeps the
 * old draft on screen and sets a status line.
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
    && prev.name === next.name && prev.liveVersion === next.liveVersion;
  return same ? prev : next;
}

/** True when a chat run was live and no run is live now. */
export function wentIdle(wasActive: boolean, isActive: boolean): boolean {
  return wasActive && !isActive;
}

export interface DraftWatch {
  draft: PackageDraft | null;
  /** The sentence for a failed refresh, or null. */
  stale: string | null;
  /** True when the gateway answered 404: the switch is off or the app is not the member's. */
  off: boolean;
  loading: boolean;
  refresh: () => void;
}

export function useDraftWatch(pkg: string | null): DraftWatch {
  const [draft, setDraft] = useState<PackageDraft | null>(null);
  const [stale, setStale] = useState<string | null>(null);
  const [off, setOff] = useState(false);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);

  const refresh = useCallback(() => {
    if (!pkg) return;
    const mine = ++seq.current;
    getPackageDraft(pkg).then(
      (next) => {
        if (mine !== seq.current) return;
        setDraft((prev) => nextDraft(prev, next));
        setStale(null);
        setOff(false);
        setLoading(false);
      },
      (e: unknown) => {
        if (mine !== seq.current) return;
        setLoading(false);
        if (e instanceof Error && e.message === STUDIO_OFF) { setOff(true); return; }
        setStale(STALE_LINE);
      },
    );
  }, [pkg]);

  useEffect(() => {
    refresh();
    const tick = setInterval(() => { if (document.visibilityState === "visible") refresh(); }, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    let active = getActiveSessionIds().size > 0;
    const unsub = subscribeAllSessions(() => {
      const now = getActiveSessionIds().size > 0;
      if (wentIdle(active, now)) refresh();
      active = now;
    });
    return () => {
      seq.current++;
      clearInterval(tick);
      document.removeEventListener("visibilitychange", onVisible);
      unsub();
    };
  }, [refresh]);

  return { draft, stale, off, loading, refresh };
}
