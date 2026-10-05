/**
 * "Metorite is updating", instead of a 502.
 *
 * Owner directive, 2026-10-05: "all the errors relating to the database being
 * updated, the backend being updated, or the app being updated … need to be
 * managed so that the user is not worried." Spec: `navigation_shell.md` §7.3.
 *
 * **What a deploy looks like from a browser** (measured on the box,
 * 2026-10-05 04:05 to 04:08 UTC). The gateway is down for 10 s. Two minutes
 * later the workbench takes 7 s to stop and is ready 140 ms after. Caddy and
 * `gatewayFetch` hold most requests through both windows (H-60, H-194). What
 * still escapes is a request that fails anyway: a stream the restart cuts, a
 * hold that runs out, or a route that answers 502 on its own. Each surface
 * then shows its own error, and nothing tells the member that the whole
 * product is updating and will be back.
 *
 * **What this module does.** It watches the app's own `/api/*` requests. On a
 * 502, 503 or 504, or a request that got no answer, it asks `/api/health`
 * ONCE before it says anything. One broken route is not an update, so a
 * healthy probe ends it with no message. A failed probe moves the monitor to
 * `updating` (or `offline`, when the browser itself has no network). It then
 * polls until the gateway answers, and reports `recovered`. It also reports
 * whether the workbench build changed, which means a new version is live.
 *
 * It never changes a response, never retries a request, and never reloads a
 * page by itself. A reload could lose what the member is typing, so the
 * member decides.
 */

export type Health = "ok" | "checking" | "updating" | "offline" | "recovered";

/** The statuses a restart produces. A 500 is an answer, so it is not here. */
export const UPDATE_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

export const HEALTH_PATH = "/api/health";

/** Poll fast first, because most updates end inside 30 s. */
export const POLL_FAST_MS = 3_000;
export const POLL_SLOW_MS = 10_000;
export const FAST_FOR_MS = 60_000;
/** How long "Metorite is back" stays in `recovered` before `ok`. */
export const RECOVERED_MS = 8_000;

export interface ProbeResult {
  up: boolean;
  build: string | null;
}

export interface ChangeInfo {
  /** True when the workbench now serves a different build than at page load. */
  buildChanged: boolean;
}

export interface MonitorDeps {
  probe: () => Promise<ProbeResult>;
  online: () => boolean;
  now: () => number;
  setTimer: (fn: () => void, ms: number) => () => void;
  onChange: (state: Health, info: ChangeInfo) => void;
}

export interface Monitor {
  /** Record the build this page loaded with. Call once, at mount. */
  start(): Promise<void>;
  /** A request looked like an update. Confirm before reporting. */
  suspect(): void;
  /** The browser's `online` and `offline` events. */
  setOnline(online: boolean): void;
  state(): Health;
  dispose(): void;
}

export function createMonitor(deps: MonitorDeps): Monitor {
  let state: Health = "ok";
  let baseline: string | null = null;
  let downSince = 0;
  let cancel: (() => void) | null = null;
  let disposed = false;

  const clear = () => {
    cancel?.();
    cancel = null;
  };

  const set = (next: Health, info: ChangeInfo = { buildChanged: false }) => {
    if (disposed) return;
    state = next;
    deps.onChange(next, info);
  };

  const safeProbe = async (): Promise<ProbeResult> => {
    try {
      return await deps.probe();
    } catch {
      return { up: false, build: null };
    }
  };

  const down = () => {
    if (state !== "updating" && state !== "offline") downSince = deps.now();
    set(deps.online() ? "updating" : "offline");
    schedulePoll();
  };

  const up = (build: string | null) => {
    clear();
    const buildChanged = baseline !== null && build !== null && build !== baseline;
    set("recovered", { buildChanged });
    // A changed build keeps its message until the member acts on it. The
    // monitor itself goes back to watching.
    cancel = deps.setTimer(() => {
      cancel = null;
      if (state === "recovered") set("ok");
    }, RECOVERED_MS);
  };

  const schedulePoll = () => {
    clear();
    const elapsed = deps.now() - downSince;
    const wait = elapsed < FAST_FOR_MS ? POLL_FAST_MS : POLL_SLOW_MS;
    cancel = deps.setTimer(async () => {
      cancel = null;
      if (disposed) return;
      const r = await safeProbe();
      if (r.up) up(r.build);
      else down();
    }, wait);
  };

  return {
    async start() {
      const r = await safeProbe();
      if (r.up) baseline = r.build;
    },
    suspect() {
      if (state !== "ok" && state !== "recovered") return;
      set("checking");
      void safeProbe().then((r) => {
        if (disposed || state !== "checking") return;
        if (r.up) {
          // One route failed, and the product is fine. Say nothing.
          if (baseline === null) baseline = r.build;
          set("ok");
        } else {
          down();
        }
      });
    },
    setOnline(online: boolean) {
      if (!online) {
        down();
      } else if (state === "offline") {
        void safeProbe().then((r) => (r.up ? up(r.build) : down()));
      }
    },
    state: () => state,
    dispose() {
      disposed = true;
      clear();
    },
  };
}

/** Is this request one of the app's own API calls, other than the probe? */
export function isOwnApi(input: RequestInfo | URL, origin: string): boolean {
  let raw: string;
  if (typeof input === "string") raw = input;
  else if (input instanceof URL) raw = input.href;
  else raw = (input as Request).url;
  let url: URL;
  try {
    url = new URL(raw, origin);
  } catch {
    return false;
  }
  if (url.origin !== origin) return false;
  return url.pathname.startsWith("/api/") && url.pathname !== HEALTH_PATH;
}

const INSTALLED = Symbol.for("metorite.serviceHealth.fetch");

type Patched = typeof fetch & { [INSTALLED]?: true };

/**
 * Watch `fetch` for update-shaped failures. Returns a function that restores
 * the original. Installing twice is a no-op, so a hot reload cannot stack
 * wrappers.
 */
export function installFetchObserver(
  win: { fetch: typeof fetch; location: { origin: string } },
  onSuspect: () => void,
): () => void {
  const current = win.fetch as Patched;
  if (current[INSTALLED]) return () => {};
  const original = current;
  const wrapped = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const watched = isOwnApi(input, win.location.origin);
    try {
      const res = await original(input, init);
      if (watched && UPDATE_STATUSES.has(res.status)) onSuspect();
      return res;
    } catch (err) {
      // An abort is the caller's own decision, never an outage.
      if (watched && (err as { name?: string })?.name !== "AbortError") onSuspect();
      throw err;
    }
  }) as Patched;
  wrapped[INSTALLED] = true;
  win.fetch = wrapped;
  return () => {
    if (win.fetch === wrapped) win.fetch = original;
  };
}

/** Read `/api/health`. A non-JSON answer is Caddy's updating page: down. */
export async function probeHealth(
  fetchImpl: typeof fetch,
  timeoutMs = 5_000,
): Promise<ProbeResult> {
  const res = await fetchImpl(HEALTH_PATH, {
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) return { up: false, build: null };
  try {
    const body = (await res.json()) as { gateway?: string; build?: string | null };
    return { up: body.gateway === "up", build: body.build ?? null };
  } catch {
    return { up: false, build: null };
  }
}
