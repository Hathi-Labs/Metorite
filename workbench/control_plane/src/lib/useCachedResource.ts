/**
 * ── The React face of `dataCache` ───────────────────────────────────────────
 *
 * `dataCache` holds the photograph. This hook is how a component looks at it:
 * paint whatever is already known, immediately, and let a revalidation land
 * underneath.
 *
 * THE STATE THIS EXPRESSES, and why there are two "busy" flags:
 *
 *   loading     nothing to show yet — the ONLY state that earns a skeleton
 *   refreshing  something is shown AND a newer answer is on its way
 *
 * Collapsing those two into one boolean is what produced the wait we are
 * fixing. `loading` was true on every mount, so a page that already had the
 * answer still blanked itself and said "Loading…". A revisit must never blank.
 *
 * ⚠️ AN ERROR DOES NOT CLEAR THE DATA. A failed revalidation over good rows
 * leaves the rows on screen and raises the error beside them. Blanking on
 * failure hides the last thing that worked, at the moment it is most useful.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { type CacheKey, peek, read, subscribe } from "./dataCache";

export interface CachedResource<T> {
  data: T | undefined;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
  /** Force a read past the fresh window. */
  refresh: () => void;
}

/** The part of the state the pure helpers own, so the fences can drive it. */
export interface ResourceState<T> {
  data: T | undefined;
  loading: boolean;
  refreshing: boolean;
  error: string | null;
}

/**
 * How long a revalidation triggered by a WRITE waits for the next write.
 *
 * ⚠️ Without this, a drag is a request storm. Re-ordering a board patches one
 * task per drop and every patch invalidates the family, so ten drops meant ten
 * forced re-reads of the same list — each one a ~124 ms round trip, each one
 * answering a question the next patch was about to change again. A trailing
 * window collapses a burst into ONE read, after the burst.
 *
 * Trailing, not leading: the value we want is the one that exists after the
 * last write, and a leading edge reads before the writes have finished.
 */
export const WRITE_SETTLE_MS = 150;

/**
 * A trailing coalescer: many triggers in a window run the work ONCE, after.
 *
 * Extracted from the effect below so it can be fenced — this suite runs
 * without a DOM, and a debounce written inline in a hook is a debounce nobody
 * can test.
 */
export function coalesce(
  run: () => void,
  ms = WRITE_SETTLE_MS
): { trigger: () => void; cancel: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return {
    trigger() {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, ms);
    },
    cancel() {
      if (timer) clearTimeout(timer);
      timer = undefined;
    },
  };
}

/**
 * The state a component starts in, given what the cache already holds.
 *
 * A hit means the very first paint is real content, and `loading` is false
 * from the start — no skeleton, no flash. That is the whole point.
 */
export function initialState<T>(hit: { data: T } | undefined): ResourceState<T> {
  return hit
    ? { data: hit.data, loading: false, refreshing: true, error: null }
    : { data: undefined, loading: true, refreshing: false, error: null };
}

/**
 * A read succeeded.
 *
 * The previous state is deliberately not consulted — a good answer replaces
 * everything, including a stale error from the read before it.
 */
export function applyData<T>(data: T): ResourceState<T> {
  return { data, loading: false, refreshing: false, error: null };
}

/**
 * A read failed.
 *
 * ⚠️ `prev.data` is CARRIED, deliberately. See the header note — an error over
 * good rows must not blank the surface. `loading` also goes false: there is
 * nothing more coming, so a skeleton would spin forever.
 */
export function applyError<T>(prev: ResourceState<T>, message: string): ResourceState<T> {
  return { data: prev.data, loading: false, refreshing: false, error: message };
}

/** Entering a revalidation over data already on screen. */
export function applyRefreshing<T>(prev: ResourceState<T>): ResourceState<T> {
  return prev.data === undefined
    ? { ...prev, loading: true, refreshing: false }
    : { ...prev, refreshing: true };
}

/**
 * Read `key` through the cache.
 *
 * `key` may be `null` when the inputs are not ready yet (no selected project,
 * for instance). The hook then holds still — it does not fetch, and it does
 * not claim to be loading something it has not asked for.
 */
export function useCachedResource<T>(
  key: CacheKey | null,
  fetcher: () => Promise<T>,
  opts?: { ttl?: number; revalidateOnFocus?: boolean }
): CachedResource<T> {
  const [state, setState] = useState<ResourceState<T>>(() =>
    initialState<T>(key ? peek<T>(key) : undefined)
  );

  /**
   * ── Re-seat DURING RENDER when the key changes ─────────────────────────
   *
   * React's own "adjusting state when a prop changes" pattern, and it is the
   * right one here for a reason that matters: doing this in an effect means
   * one painted frame carrying the PREVIOUS key's rows — the old filter's
   * tasks under the new filter's heading — before the correction lands. Done
   * in render, React discards this pass and re-runs before anything reaches
   * the screen. There is no wrong frame to see.
   *
   * It is also what makes a cache HIT paint instantly on a key change rather
   * than one frame later.
   */
  const [seatedKey, setSeatedKey] = useState<CacheKey | null>(key);
  if (key !== seatedKey) {
    setSeatedKey(key);
    setState(initialState<T>(key ? peek<T>(key) : undefined));
  }

  // The fetcher is almost always an inline arrow, so its identity changes on
  // every render. Holding it in a ref keeps it OUT of the effect deps — in
  // them, the effect would re-run forever and this hook would be a request
  // loop rather than a cache.
  const fetcherRef = useRef(fetcher);
  // Mirrors `key` for the race guard inside `load`. A ref, because the `.then`
  // closes over the render that created it and must compare against the
  // CURRENT key, not the one that was current when the request went out.
  const keyRef = useRef(key);
  // Both assigned in an effect, not in render: a ref written during render is
  // invisible to React's own bookkeeping, and this effect is declared FIRST so
  // it runs before the read effect below on every commit.
  useEffect(() => {
    fetcherRef.current = fetcher;
    keyRef.current = key;
  });

  const ttl = opts?.ttl;

  const load = useCallback(
    /**
     * `markBusy` is false for the read that runs on mount.
     *
     * Not a lint dodge — the flag is genuinely redundant there. The render-time
     * seat above has ALREADY put this hook in the right state: `refreshing`
     * over a cache hit, `loading` over a miss. Setting it again synchronously
     * inside the effect only asks React for a second render pass to reach the
     * state it is already in.
     *
     * A revalidation from an EVENT — a write, a window focus, an explicit
     * refresh — is the opposite case. Nothing has re-seated, so those do mark.
     */
    (force: boolean, markBusy = true) => {
      if (!key) return;
      // The key AT REQUEST TIME. A response that arrives after the key moved
      // on belongs to a question nobody is asking any more, and writing it
      // would show the previous filter's rows under the current filter.
      const asked = key;
      if (markBusy) setState((prev) => applyRefreshing(prev));
      read<T>(asked, () => fetcherRef.current(), { force, ttl })
        .then((data) => {
          setState((prev) => (asked === keyRef.current ? applyData(data) : prev));
        })
        .catch((err: unknown) => {
          const message = err instanceof Error ? err.message : String(err);
          setState((prev) => (asked === keyRef.current ? applyError(prev, message) : prev));
        });
    },
    [key, ttl]
  );

  useEffect(() => {
    // A null key means the inputs are not ready (no project selected yet). The
    // hook holds still — it does not fetch, and the render-time re-seat above
    // has already put it in the empty state.
    if (!key) return;
    /**
     * `set-state-in-effect` is suppressed rather than worked around, and the
     * argument is the rule's OWN second bullet: this effect is the subscribe
     * half of an external system. The cache is that system.
     *
     * The rule traces `load` statically and sees a `setState` inside it. At
     * runtime `markBusy: false` skips the only SYNCHRONOUS one — that is what
     * the second argument is for — and the two that remain are in `.then` and
     * `.catch`, which is exactly the shape the rule asks for. Working around it
     * would mean inlining `load`'s body here, and two copies of the race guard
     * is a worse defect than this line.
     */
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load(false, false);
    // Any `invalidate()` for this key — a write anywhere in the app — wakes
    // us, and a burst of writes wakes us many times. Coalesce them: see
    // WRITE_SETTLE_MS.
    const settle = coalesce(() => load(true));
    const off = subscribe(key, settle.trigger);
    return () => {
      settle.cancel();
      off();
    };
  }, [key, load]);

  const wantsFocus = opts?.revalidateOnFocus ?? true;
  useEffect(() => {
    if (!key || !wantsFocus) return;
    // Coming back to the tab is the moment the data is most likely to be stale
    // and the moment the user is most likely to look at it. `read` still
    // honours the fresh window, so an alt-tab flurry costs nothing.
    const onFocus = () => load(false);
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [key, wantsFocus, load]);

  const refresh = useCallback(() => load(true), [load]);

  return { ...state, refresh };
}
