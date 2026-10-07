/**
 * The prefetch of the HTML of the visible rows (WS-17 EM-S2, §14.4.2 item 5,
 * §14.6.2 items 3, 5 and 6).
 *
 * The provider holds the HTML of an old message (`htmlRemote`). When the list
 * stays still for {@link PREFETCH_STILL_MS}, the list asks the gateway for the
 * HTML of the visible rows, so a click paints the HTML at once.
 *
 * The bounds, and each one is a fence in `htmlPrefetch.test.ts`:
 *
 * - At most {@link PREFETCH_MAX_ROWS} rows in one run.
 * - At most {@link PREFETCH_PARALLEL} prefetch requests in flight, for ALL
 *   runs together. A new run fills only the free slots, and its rows replace
 *   the rows that an older run still waits to ask for.
 * - One id is never asked twice while a request for it is in flight. The pane
 *   joins a request in flight through {@link openMessageHtml}, so an open
 *   during a prefetch makes one request.
 * - Only a row with `htmlRemote: true`. With `EMAIL_HTML_FROM_PROVIDER` off
 *   the gateway sends no such row, so the prefetch asks for nothing.
 * - A row that the cache holds is skipped. A row that failed is not asked
 *   again while the list stays the same.
 * - The first 503 stops the prefetch until its `Retry-After` ends, on any
 *   list. After that, the next run may prefetch again. EM-S1 turns a provider
 *   429 into that 503, so a 429 stops it too. The first 401 stops it until the
 *   list changes.
 *
 * "The list changes" means another list key: another mailbox, folder, label or
 * search ({@link prefetchListKey}). A soft refresh of the same list gives a new
 * array of rows, and it clears nothing.
 *
 * ⚠️ The state lives at module scope ({@link sharedHtmlPrefetcher}), not in a
 * component. On a phone each open of a message unmounts the list, and a stop
 * held in component state was lost on each remount. The state belongs to the
 * signed-in member, so `dataCache.clearAll` (and so `bindIdentity`) drops it.
 */
import { coalesce } from "@/lib/useCachedResource";
import { identity, onClear, peek, put } from "@/lib/dataCache";

import { fetchMessageHtml, messageHtmlKey } from "./api";
import { filterKey, type SearchFilter } from "./searchFilters";

/** The most rows that one run asks for. */
export const PREFETCH_MAX_ROWS = 6;
/** The most prefetch requests in flight at one time, for all runs together. */
export const PREFETCH_PARALLEL = 2;
/** How long the list stays still before a run starts. */
export const PREFETCH_STILL_MS = 500;
/** The wait after a 503 that sent no usable `Retry-After`, in seconds. */
export const PREFETCH_DEFAULT_WAIT_S = 30;
/**
 * How long the open of the pane trusts a held HTML, in milliseconds. The HTML
 * of a message does not change, and the gateway caches it for 1 hour too.
 */
export const HTML_HOLD_MS = 60 * 60 * 1000;

/** The part of a row that the prefetch reads. */
export interface PrefetchRow {
  id: string;
  htmlRemote?: boolean;
}

/** The effects of a run. Production wires them in {@link defaultPrefetchDeps}. */
export interface PrefetchDeps {
  /** Ask the gateway with `prefetch=1`. Null is a plain-text message. */
  fetchHtml: (id: string) => Promise<string | null>;
  /** True when the cache already holds an answer for the row. */
  held: (id: string) => boolean;
  /** Keep an answer, so the open of the row paints it at once. */
  keep: (id: string, html: string | null) => void;
  /** The wall clock, in milliseconds. */
  now: () => number;
}

/** Why the prefetch stopped. */
export type PrefetchStop = "busy" | "auth";

/** What one run did. */
export interface PrefetchOutcome {
  /** The ids that the run asked for, in the order the requests started. */
  asked: string[];
  /** Set when a 503 or a 401 stopped the prefetch, or a stop held it back. */
  stopped: PrefetchStop | null;
}

/** One run, while its requests settle. */
interface RunTracker {
  generation: number;
  asked: string[];
  pending: number;
  done: boolean;
  resolve: (out: PrefetchOutcome) => void;
}

/**
 * The memory of the prefetch of one member. Every run of every prefetcher
 * that shares it shares its slots, its in-flight ids, its stop and its wait.
 */
export interface PrefetchState {
  /** The member it belongs to, from `dataCache.identity()`. */
  owner: string | null;
  /** True after a clear. A late answer of a retired state keeps nothing. */
  retired: boolean;
  /** The list that the stop and the failed ids belong to. */
  listKey: string | null;
  stop: PrefetchStop | null;
  /** No request before this time, in milliseconds. A list change never clears it. */
  waitUntil: number;
  /** The rows of this list that failed for a reason other than 503 or 401. */
  failed: Set<string>;
  /** Each id with a request in flight, prefetch or open, and its answer. */
  inFlight: Map<string, Promise<string | null>>;
  /** The prefetch requests in flight. The bound is {@link PREFETCH_PARALLEL}. */
  active: number;
  /** The rows that the newest run still waits to ask for. */
  queue: string[];
  generation: number;
  current: RunTracker | null;
}

/** A new, empty state for `owner`. */
export function newPrefetchState(owner: string | null = null): PrefetchState {
  return {
    owner,
    retired: false,
    listKey: null,
    stop: null,
    waitUntil: 0,
    failed: new Set(),
    inFlight: new Map(),
    active: 0,
    queue: [],
    generation: 0,
    current: null,
  };
}

/** The status and the `Retry-After` of a failed fetch, as `api.ts` sets them. */
function failure(err: unknown): { status?: number; retryAfter?: number } {
  if (err && typeof err === "object") {
    const e = err as { status?: unknown; retryAfter?: unknown };
    return {
      status: typeof e.status === "number" ? e.status : undefined,
      retryAfter: typeof e.retryAfter === "number" ? e.retryAfter : undefined,
    };
  }
  return {};
}

/**
 * The rows that one run asks for: the rows with `htmlRemote: true` that
 * `skip` does not refuse, in the order of the list, and at most
 * {@link PREFETCH_MAX_ROWS} of them.
 */
export function prefetchTargets(
  rows: readonly PrefetchRow[],
  skip: (id: string) => boolean
): string[] {
  const out: string[] = [];
  for (const row of rows) {
    if (out.length >= PREFETCH_MAX_ROWS) break;
    if (row.htmlRemote !== true) continue;
    if (!row.id || out.includes(row.id) || skip(row.id)) continue;
    out.push(row.id);
  }
  return out;
}

/** The box of one row on screen, from `getBoundingClientRect`. */
export interface RowBox {
  id: string;
  top: number;
  bottom: number;
}

/**
 * The ids of the rows that show in the scroll box from `viewTop` to
 * `viewBottom`, in the order of the list. A row that shows only in part counts.
 */
export function visibleRowIds(
  boxes: readonly RowBox[],
  viewTop: number,
  viewBottom: number
): string[] {
  return boxes.filter((b) => b.bottom > viewTop && b.top < viewBottom).map((b) => b.id);
}

/**
 * What makes one list: the mailbox or All inboxes, the folder, the label, and
 * the search. The search is its text, its scope and its pills, as
 * `searchViewKey` in `emailStore.ts` reads them.
 */
export interface ListIdentity {
  viewAll: boolean;
  accountId: string | null;
  folder: string;
  label: string | null;
  query: string;
  /** `searchScope` of the store. Null means the open folder. */
  scope: string | null;
  /** `searchFilters` of the store, the pills. */
  filters: readonly SearchFilter[];
}

/** The key of one list. A soft refresh of the same list keeps the same key. */
export function prefetchListKey(list: ListIdentity): string {
  const box = list.viewAll ? "all" : list.accountId ?? "";
  return JSON.stringify([
    box,
    list.folder,
    list.label ?? "",
    list.query.trim(),
    list.scope ?? "",
    list.filters.map(filterKey),
  ]);
}

/** The prefetcher, over one {@link PrefetchState}. */
export interface HtmlPrefetcher {
  /**
   * The list on screen. The same key is a soft refresh and clears nothing.
   * Another key clears the stop and the failed ids, but never the wait of
   * `Retry-After`.
   */
  listLoaded: (listKey: string) => void;
  /** Ask for the HTML of `rows`. Its rows replace the rows an older run still waits to ask for. */
  run: (rows: readonly PrefetchRow[]) => Promise<PrefetchOutcome>;
}

/** Make a prefetcher over `state`. Two prefetchers over one state share every bound. */
export function createHtmlPrefetcher(
  deps: PrefetchDeps,
  state: PrefetchState = newPrefetchState()
): HtmlPrefetcher {
  const settleIfDone = (run: RunTracker) => {
    if (run.done) return;
    const superseded = run.generation !== state.generation;
    const drained = superseded || state.queue.length === 0 || state.stop !== null;
    if (!drained || run.pending > 0) return;
    run.done = true;
    if (state.current === run) state.current = null;
    run.resolve({ asked: run.asked, stopped: state.stop });
  };

  const skip = (id: string) => deps.held(id) || state.inFlight.has(id) || state.failed.has(id);

  const start = (id: string, run: RunTracker) => {
    state.active += 1;
    run.pending += 1;
    run.asked.push(id);
    let request: Promise<string | null>;
    try {
      request = deps.fetchHtml(id);
    } catch (err) {
      request = Promise.reject(err);
    }
    state.inFlight.set(id, request);
    request
      .then(
        (html) => {
          if (state.retired) return;
          try {
            deps.keep(id, html);
          } catch {
            // A row that the cache cannot keep counts as failed for this list.
            state.failed.add(id);
          }
        },
        (err: unknown) => {
          const { status, retryAfter } = failure(err);
          if (status === 503) {
            state.stop = "busy";
            const wait = retryAfter && retryAfter > 0 ? retryAfter : PREFETCH_DEFAULT_WAIT_S;
            state.waitUntil = Math.max(state.waitUntil, deps.now() + wait * 1000);
            state.queue = [];
          } else if (status === 401) {
            state.stop = "auth";
            state.queue = [];
          } else {
            // Each other failure skips the row for this list. The pane keeps its text.
            state.failed.add(id);
          }
        }
      )
      .finally(() => {
        state.active -= 1;
        run.pending -= 1;
        if (state.inFlight.get(id) === request) state.inFlight.delete(id);
        pump();
        settleIfDone(run);
      });
  };

  /** Fill the free slots from the queue of the newest run. */
  const pump = () => {
    const run = state.current;
    if (!run) return;
    // A retired state (after a sign-out or a change of member) starts nothing.
    // Its requests in flight settle, and its runs resolve.
    if (state.retired) {
      state.queue = [];
      settleIfDone(run);
      return;
    }
    while (state.active < PREFETCH_PARALLEL && state.queue.length > 0) {
      if (state.stop || deps.now() < state.waitUntil) {
        state.queue = [];
        break;
      }
      const id = state.queue.shift() as string;
      if (skip(id)) continue;
      start(id, run);
    }
    settleIfDone(run);
  };

  return {
    listLoaded(listKey) {
      if (listKey === state.listKey) return;
      state.listKey = listKey;
      state.stop = null;
      state.failed.clear();
      state.queue = [];
      if (state.current) settleIfDone(state.current);
    },

    run(rows) {
      // A 503 means the provider is busy for `Retry-After` seconds, not "stop
      // for good". Once the wait ends, the same list may prefetch again. A 401
      // stays until the list changes.
      if (state.stop === "busy" && deps.now() >= state.waitUntil) state.stop = null;
      if (state.stop) return Promise.resolve({ asked: [], stopped: state.stop });
      if (deps.now() < state.waitUntil) {
        return Promise.resolve({ asked: [], stopped: "busy" as const });
      }
      state.generation += 1;
      state.queue = prefetchTargets(rows, skip);
      return new Promise<PrefetchOutcome>((resolve) => {
        const run: RunTracker = {
          generation: state.generation, asked: [], pending: 0, done: false, resolve,
        };
        // The older run asks for nothing more. Its requests in flight settle
        // and keep their answers, and its promise resolves when they do.
        const older = state.current;
        state.current = run;
        if (older) settleIfDone(older);
        pump();
      });
    },
  };
}

/** The production effects: the one fetch helper, and the one read cache. */
export function defaultPrefetchDeps(): PrefetchDeps {
  return {
    fetchHtml: (id) => fetchMessageHtml(id, { prefetch: true }).then((r) => r.bodyHtml),
    held: (id) => peek(messageHtmlKey(id)) !== undefined,
    keep: (id, html) => put(messageHtmlKey(id), html),
    now: () => Date.now(),
  };
}

// ── The one state of the signed-in member ────────────────────────────────

let shared: PrefetchState | null = null;

/** Retire a state: it starts no request and keeps no answer. */
function retire(state: PrefetchState): void {
  state.retired = true;
  state.queue = [];
}

onClear(() => {
  if (shared) retire(shared);
  shared = null;
});

/**
 * The prefetch state of the signed-in member. It survives a remount of the
 * list. A change of member, or a `clearAll`, gives a new one.
 */
export function sharedPrefetchState(): PrefetchState {
  const who = identity();
  if (!shared || shared.owner !== who) {
    if (shared) retire(shared);
    shared = newPrefetchState(who);
  }
  return shared;
}

/** The prefetcher of the list. Get it when you use it. Never hold it across renders. */
export function sharedHtmlPrefetcher(): HtmlPrefetcher {
  return createHtmlPrefetcher(defaultPrefetchDeps(), sharedPrefetchState());
}

/**
 * The HTML for the pane, which `MessageContent` reads through `dataCache`.
 * It joins a request for the same id that is in flight, so an open during a
 * prefetch makes one request. A joined prefetch that fails, a 503 under load
 * for instance, gives way to an open of its own, because the gateway never
 * refuses an open.
 */
export function openMessageHtml(
  id: string,
  state: PrefetchState = sharedPrefetchState()
): Promise<string | null> {
  const own = () => {
    const request = fetchMessageHtml(id).then((r) => r.bodyHtml);
    state.inFlight.set(id, request);
    const forget = () => {
      if (state.inFlight.get(id) === request) state.inFlight.delete(id);
    };
    request.then(forget, forget);
    return request;
  };
  const running = state.inFlight.get(id);
  return running ? running.catch(own) : own();
}

/**
 * The wait of a still list: many schedules in {@link PREFETCH_STILL_MS} run
 * once, after the last one, and the run of the last schedule is the one that
 * runs. A schedule with no remote row cancels the wait and starts none, so
 * with the flag off no timer runs.
 */
export function createPrefetchSchedule(): {
  schedule: (anyRemote: boolean, run: () => void) => void;
  cancel: () => void;
} {
  let latest: (() => void) | null = null;
  const still = coalesce(() => latest?.(), PREFETCH_STILL_MS);
  return {
    schedule(anyRemote, run) {
      still.cancel();
      latest = run;
      if (anyRemote) still.trigger();
    },
    cancel: () => still.cancel(),
  };
}

/**
 * The id whose HTML the pane must get from the provider, or null. Each render
 * path of the pane passes it to `MessageContent` as `remoteId`. It is null for
 * each message with `htmlRemote` false, so with the flag off the pane makes no
 * request and draws as before.
 */
export function remoteHtmlId(email: { id: string; htmlRemote?: boolean } | null | undefined): string | null {
  return email && email.htmlRemote === true && email.id ? email.id : null;
}
