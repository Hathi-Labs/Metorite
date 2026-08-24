"use client";

/**
 * AccessProvider — the signed-in member's effective access, fetched once and
 * shared by the whole shell.
 *
 * Spec: project-docs/specs/org_access_control.md §5 (seams 4 and 5) ·
 * project-docs/specs/launch_surface.md §8.2 / LS-5 (the failure partition).
 *
 * Three consumers: the Sidebar and the home grid filter panes with it, and
 * AccessGate blocks direct navigation to a route the member cannot reach. None
 * is a security boundary — the gateway re-authorizes every request — but a
 * sidebar full of links that 403 is a worse product than one that shows what
 * you actually have.
 *
 * ## The two states that used to be one
 *
 * `loading` matters: rendering the nav before access resolves would flash the
 * full sidebar and then remove items, which reads as a bug. Consumers hold
 * their layout until it clears.
 *
 * `stale` matters for the opposite reason. This provider re-resolves every 120
 * seconds so a revoked permission disappears from a long-lived tab. The old
 * version mapped **every** failure to NO_ACCESS, so a single 502 from a
 * restarting gateway emptied the sidebar mid-session and the member read it as
 * having been signed out. Now an unauthoritative failure — network, 5xx,
 * unparseable body — **keeps the last good answer** and sets `stale`; only the
 * server actually saying no (401/403) clears it.
 *
 * That asymmetry is deliberate and worth stating: we fail *closed* on an
 * answer and *open* on a silence. Failing closed on a silence is what makes a
 * blip look like a revocation; failing open on an answer would be the security
 * bug, and nothing here does that.
 */

import {
  createContext,
  useContext,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { NO_ACCESS, resolveAccess, type Access } from "@/lib/access";

type AccessCtx = {
  access: Access;
  /** True until the FIRST authoritative answer lands. Never true again after. */
  loading: boolean;
  /**
   * True when the most recent re-resolve could not reach an answer, so
   * `access` is the last known good value rather than a current one. Consumers
   * render it exactly as before — a stale answer is enormously better than an
   * empty one — and may surface a quiet indicator.
   */
  stale: boolean;
  /** Re-fetch after an admin change so the sidebar updates without a reload. */
  refresh: () => Promise<void>;
};

const Ctx = createContext<AccessCtx>({
  access: NO_ACCESS,
  loading: true,
  stale: false,
  refresh: async () => {},
});

export function useAccess(): AccessCtx {
  return useContext(Ctx);
}

export default function AccessProvider({ children }: { children: ReactNode }) {
  const [access, setAccess] = useState<Access>(NO_ACCESS);
  const [loading, setLoading] = useState(true);
  const [stale, setStale] = useState(false);
  // Whether an authoritative answer has EVER landed. An `unavailable` on the
  // very first attempt has no previous value to keep, so it must still clear
  // `loading` — otherwise a gateway that is down at page load leaves the shell
  // showing skeleton rows forever.
  const resolvedOnce = useRef(false);

  const apply = useCallback((result: Awaited<ReturnType<typeof resolveAccess>>) => {
    switch (result.kind) {
      case "aborted":
        // Our own teardown. Say nothing, change nothing — a StrictMode double
        // mount must not read as an outage.
        return;
      case "ok":
        resolvedOnce.current = true;
        setAccess(result.access);
        setStale(false);
        setLoading(false);
        return;
      case "unauthorized":
        // The server said no. This IS an answer, and it is authoritative.
        resolvedOnce.current = true;
        setAccess(NO_ACCESS);
        setStale(false);
        setLoading(false);
        return;
      case "unavailable":
        // Nobody said anything. Keep whatever we last knew.
        setStale(true);
        if (!resolvedOnce.current) setLoading(false);
        return;
    }
  }, []);

  const refresh = useCallback(async () => {
    apply(await resolveAccess());
  }, [apply]);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    (async () => {
      const result = await resolveAccess(controller.signal);
      if (alive) apply(result);
    })();

    // Re-resolve periodically so a revoked permission disappears from a
    // long-lived tab. The gateway caches for 60s, so polling faster than that
    // buys nothing.
    const interval = setInterval(() => {
      void refresh();
    }, 120_000);

    return () => {
      alive = false;
      controller.abort();
      clearInterval(interval);
    };
  }, [apply, refresh]);

  return (
    <Ctx.Provider value={{ access, loading, stale, refresh }}>
      {children}
    </Ctx.Provider>
  );
}
