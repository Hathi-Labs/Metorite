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
 * - At most {@link PREFETCH_MAX_ROWS} rows in one run, and at most
 *   {@link PREFETCH_PARALLEL} requests at one time.
 * - Only a row with `htmlRemote: true`. With `EMAIL_HTML_FROM_PROVIDER` off
 *   the gateway sends no such row, so the prefetch asks for nothing.
 * - A row that the cache already holds is skipped.
 * - The first 503 stops the prefetch until the next list load, and no run
 *   starts before its `Retry-After` ends. EM-S1 turns a provider 429 into
 *   that 503, so a 429 stops it too.
 * - The first 401 stops the prefetch until the next list load. A dead token
 *   answers 401 for each row, so asking again only adds load.
 * - Any other failure skips its row. The pane keeps the text of that row and
 *   shows no error.
 *
 * The decisions are pure and take their effects as {@link PrefetchDeps}, so
 * the fence drives them with no DOM and no network. `EmailList` holds one
 * prefetcher and wires it to `fetchMessageHtml` and `dataCache`.
 */
import { peek, put } from "@/lib/dataCache";

import { fetchMessageHtml, messageHtmlKey } from "./api";

/** The most rows that one run asks for. */
export const PREFETCH_MAX_ROWS = 6;
/** The most requests in flight at one time. */
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
  /** Set when a 503 or a 401 stopped the run, or a stop held it back. */
  stopped: PrefetchStop | null;
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
 * The rows that one run asks for: the rows with `htmlRemote: true` that the
 * cache does not hold, in the order of the list, and at most
 * {@link PREFETCH_MAX_ROWS} of them.
 */
export function prefetchTargets(
  rows: readonly PrefetchRow[],
  held: (id: string) => boolean
): string[] {
  const out: string[] = [];
  for (const row of rows) {
    if (out.length >= PREFETCH_MAX_ROWS) break;
    if (row.htmlRemote !== true) continue;
    if (!row.id || out.includes(row.id) || held(row.id)) continue;
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

/** The prefetcher of one list. */
export interface HtmlPrefetcher {
  /** A new list load. It clears a stop, but never a `Retry-After` wait. */
  listLoaded: () => void;
  /** Ask for the HTML of `rows`. A newer run makes an older run stop. */
  run: (rows: readonly PrefetchRow[]) => Promise<PrefetchOutcome>;
}

/** Make the prefetcher of one list. */
export function createHtmlPrefetcher(deps: PrefetchDeps): HtmlPrefetcher {
  let stopped: PrefetchStop | null = null;
  let waitUntil = 0;
  let generation = 0;

  return {
    listLoaded() {
      stopped = null;
    },

    async run(rows) {
      if (stopped) return { asked: [], stopped };
      if (deps.now() < waitUntil) return { asked: [], stopped: "busy" };
      generation += 1;
      const mine = generation;
      const queue = prefetchTargets(rows, deps.held);
      const asked: string[] = [];

      const worker = async () => {
        while (queue.length > 0) {
          // A stop, or a newer run, ends this run. A request in flight
          // settles, and no new one starts.
          if (stopped || mine !== generation) return;
          const id = queue.shift() as string;
          if (deps.held(id)) continue;
          asked.push(id);
          try {
            deps.keep(id, await deps.fetchHtml(id));
          } catch (err) {
            const { status, retryAfter } = failure(err);
            if (status === 503) {
              stopped = "busy";
              const wait = retryAfter && retryAfter > 0 ? retryAfter : PREFETCH_DEFAULT_WAIT_S;
              waitUntil = Math.max(waitUntil, deps.now() + wait * 1000);
            } else if (status === 401) {
              stopped = "auth";
            }
            // Each other failure skips the row. The pane keeps its text.
          }
        }
      };

      await Promise.all(Array.from({ length: PREFETCH_PARALLEL }, () => worker()));
      return { asked, stopped };
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

/**
 * The id whose HTML the pane must get from the provider, or null. Each render
 * path of the pane passes it to `MessageContent` as `remoteId`. It is null for
 * each message with `htmlRemote` false, so with the flag off the pane makes no
 * request and draws as before.
 */
export function remoteHtmlId(email: { id: string; htmlRemote?: boolean } | null | undefined): string | null {
  return email && email.htmlRemote === true && email.id ? email.id : null;
}
