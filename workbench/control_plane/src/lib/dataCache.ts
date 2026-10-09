/**
 * ── THE ONE READ CACHE (stale-while-revalidate) ────────────────────────────
 *
 * Every app in this product reads through `/api/**`, and until this module
 * existed every read was cold. Navigate away from Projects and back, and the
 * page threw away what it knew, painted "Loading projects…", and re-ran the
 * whole waterfall. The data had not changed. The user waited anyway.
 *
 * WHY THAT COSTS SO MUCH HERE, specifically. Measured 2026-08-31: the VPS is
 * in Mumbai and the tenant database is Supabase `ap-northeast-1`, in Tokyo.
 * The pooler answers in ~124 ms median. So every SQL query the gateway runs
 * pays a WAN round trip that no amount of front-end work can remove. What the
 * front end CAN do is stop paying it before the first paint.
 *
 * THE CONTRACT, and it is the whole design:
 *
 *   1. A cached value is served IMMEDIATELY, however old it is.
 *   2. A revalidation ALWAYS runs behind it.
 *
 * Both halves, always. Rule 1 alone is a cache that lies. Rule 2 alone is what
 * we had. Together the reader sees something true-as-of-recently at once, and
 * something true a moment later.
 *
 * ⚠️ MEMORY ONLY. NEVER localStorage, sessionStorage or IndexedDB.
 * This holds one signed-in member's tenant data. Persisting it would leave
 * that data on the device for whoever opens the browser next — on a shared
 * machine, that is one member reading another's rows, and no amount of
 * server-side row-level security can reach a value the browser already wrote
 * to disk. `dataCache.test.ts` asserts this module touches no storage API.
 *
 * WHAT THIS IS NOT. It is not a store, and it does not own truth. The server
 * owns truth. This holds a photograph of it, timestamped, and always chases a
 * newer one.
 */

/** A cache key. Build it from the URL and every parameter that changes the answer. */
export type CacheKey = string;

export interface CacheEntry<T> {
  data: T;
  /** When this value was stored. Milliseconds since the epoch. */
  storedAt: number;
}

/**
 * How long a value is treated as FRESH — inside this window a revalidation is
 * skipped entirely.
 *
 * Deliberately short. This is not "how long we will show stale data" (that is
 * unbounded — rule 1), it is "how long we will not even bother asking". It
 * exists to collapse the burst of duplicate reads that a mounting page fires
 * within a few hundred milliseconds of itself, not to hold data back.
 */
export const FRESH_MS = 5_000;

/**
 * The most entries kept. A long session moving through many filter
 * combinations mints a new key each time, and without a ceiling the map is a
 * slow leak. Eviction is least-recently-READ, not least-recently-written: the
 * value you keep coming back to is the one worth keeping.
 */
export const MAX_ENTRIES = 200;

const store = new Map<CacheKey, CacheEntry<unknown>>();
/** In-flight reads, so N callers asking at once make ONE request. */
const inflight = new Map<CacheKey, Promise<unknown>>();
const watchers = new Map<CacheKey, Set<() => void>>();
/**
 * A write counter per key, bumped by `invalidate` and `clearAll` while a read
 * of that key is IN FLIGHT. A read compares it before and after its fetch.
 * If it moved, a write landed during the read, the answer predates it, and
 * the read neither stores it nor hands it back as current (see `read`).
 *
 * Never deleted, on purpose: a counter that went back to zero could make an
 * old read's number match again. One small integer per key ever invalidated
 * in flight is the whole cost.
 */
const generation = new Map<CacheKey, number>();
const genOf = (key: CacheKey): number => generation.get(key) ?? 0;
const bump = (key: CacheKey): void => {
  generation.set(key, genOf(key) + 1);
};
/** Re-reads one read makes when writes keep landing during it. */
const MAX_REREADS = 3;

/** Injectable for the tests. Production reads the wall clock. */
let clock: () => number = () => Date.now();

/** Test seam. `restoreClock()` puts the real one back. */
export function setClock(fn: () => number): void {
  clock = fn;
}
export function restoreClock(): void {
  clock = () => Date.now();
}

/**
 * Build a key from a path and its parameters.
 *
 * ⚠️ The parameters are SORTED. `?a=1&b=2` and `?b=2&a=1` are the same read,
 * and two keys for one answer means a cache that misses on the way back — the
 * exact thing this module exists to stop.
 */
export function cacheKey(path: string, params?: Record<string, unknown>): CacheKey {
  if (!params) return path;
  const pairs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}=${String(v)}`)
    .sort();
  return pairs.length ? `${path}?${pairs.join("&")}` : path;
}

/** Whether an entry is inside the no-ask window. Pure, so the tests can drive it. */
export function isFresh(entry: CacheEntry<unknown>, now: number, ttl = FRESH_MS): boolean {
  return now - entry.storedAt < ttl;
}

/**
 * The stored value, at any age, or `undefined`.
 *
 * Reading MOVES the key to the end of the map, which is what makes eviction
 * least-recently-read. `Map` iterates in insertion order, so delete+set is the
 * documented way to say "this one is the newest".
 */
export function peek<T>(key: CacheKey): CacheEntry<T> | undefined {
  const hit = store.get(key) as CacheEntry<T> | undefined;
  if (hit) {
    store.delete(key);
    store.set(key, hit);
  }
  return hit;
}

/** Store a value and wake everybody watching that key. */
export function put<T>(key: CacheKey, data: T): void {
  store.delete(key);
  store.set(key, { data, storedAt: clock() });
  while (store.size > MAX_ENTRIES) {
    const oldest = store.keys().next();
    if (oldest.done) break;
    store.delete(oldest.value);
  }
  notify(key);
}

function notify(key: CacheKey): void {
  const set = watchers.get(key);
  if (!set) return;
  // Copied before iterating: a watcher is allowed to unsubscribe itself while
  // being called, and mutating the live set mid-iteration drops the next one.
  for (const cb of [...set]) cb();
}

/** Watch one key. Returns the unsubscribe. */
export function subscribe(key: CacheKey, cb: () => void): () => void {
  let set = watchers.get(key);
  if (!set) {
    set = new Set();
    watchers.set(key, set);
  }
  set.add(cb);
  return () => {
    const live = watchers.get(key);
    if (!live) return;
    live.delete(cb);
    if (live.size === 0) watchers.delete(key);
  };
}

/**
 * Read through the cache.
 *
 * Returns the FRESH promise — the caller still awaits real data. What the
 * cache adds is `peek`, which the caller reads first to paint immediately.
 *
 * ⚠️ A FAILED REVALIDATION MUST NOT EVICT GOOD DATA. When the network drops or
 * the gateway 500s, the last known good value stays in the map and the error
 * travels to the caller instead. Throwing the data away on failure is the
 * classic version of this bug: the screen goes blank at the exact moment the
 * user most needs to see something.
 */
export async function read<T>(
  key: CacheKey,
  fetcher: () => Promise<T>,
  opts?: { force?: boolean; ttl?: number }
): Promise<T> {
  const hit = peek<T>(key);
  if (!opts?.force && hit && isFresh(hit, clock(), opts?.ttl)) {
    return hit.data;
  }

  const running = inflight.get(key) as Promise<T> | undefined;
  if (running) return running;

  /**
   * 🔴 **A WRITE THAT LANDS WHILE THIS READ IS IN FLIGHT MAKES ITS ANSWER
   * STALE** (NS-3 review, 2026-10-10). The order that broke: a write drops
   * the key, and its watcher starts a re-read. A second write (an Undo)
   * lands while that re-read is in flight. The key is not in the store, so
   * the second `invalidate` wakes nobody. The re-read then answers with the
   * state from BEFORE the second write, `put` stores it as fresh, and the
   * screen shows the change the member just undid.
   *
   * So the read notes the key's write counter before it fetches. If the
   * counter moved, it does not store that answer. A newer read already in
   * flight answers instead, or one that already stored, or this read asks
   * again.
   */
  // The body reads its own promise, which exists only once it has started.
  const self: { task?: Promise<T> } = {};
  const task: Promise<T> = (self.task = (async () => {
    try {
      for (let attempt = 0; ; attempt += 1) {
        const gen = genOf(key);
        const data = await fetcher();
        if (genOf(key) === gen) {
          put(key, data);
          return data;
        }
        const newer = inflight.get(key) as Promise<T> | undefined;
        if (newer && newer !== self.task) return newer;
        // `invalidate` deletes the entry, so an entry here now was stored
        // AFTER the write: a newer read already answered.
        const since = store.get(key) as CacheEntry<T> | undefined;
        if (since) return since.data;
        // Writes that never stop landing: answer, but store nothing stale.
        if (attempt >= MAX_REREADS) return data;
        inflight.set(key, self.task!);
      }
    } finally {
      if (inflight.get(key) === self.task) inflight.delete(key);
    }
  })());
  inflight.set(key, task);
  return task;
}

/**
 * Drop cached reads after a write.
 *
 * Takes a PREFIX, because one write invalidates a family: saving a task
 * changes the board, the table, the calendar roll-up and the counts, and they
 * are all keyed under the same path root. A predicate is accepted for the
 * cases a prefix cannot express.
 *
 * ⚠️ Invalidating also NOTIFIES. A view watching a dropped key re-reads at
 * once, which is what makes an edit in one lens appear in the others without
 * a refresh — the one-task-store promise (D52/D53/D54) kept on the client.
 */
export function invalidate(match: string | ((key: CacheKey) => boolean)): number {
  const hit = typeof match === "string"
    ? (key: CacheKey) => key.startsWith(match)
    : match;
  const dropped: CacheKey[] = [];
  for (const key of store.keys()) if (hit(key)) dropped.push(key);
  for (const key of dropped) store.delete(key);
  // In-flight reads for a dropped key answer with data that predates the
  // write. Forget them too, so the next read starts a new request rather than
  // joining one already carrying a stale answer. And mark them stale, so the
  // forgotten read does not STORE its answer when it lands (see `read`).
  for (const key of [...inflight.keys()]) {
    if (!hit(key)) continue;
    bump(key);
    inflight.delete(key);
  }
  for (const key of dropped) notify(key);
  return dropped.length;
}

/**
 * Empty everything.
 *
 * ⚠️ CALL THIS ON SIGN-OUT AND ON ANY IDENTITY CHANGE. The cache is keyed by
 * request path, and a path says nothing about who asked. Two members using one
 * browser share these keys, so a cache that survives the switch hands the
 * second member the first one's rows.
 */
export function clearAll(): void {
  store.clear();
  // A read in flight belongs to the member who asked. Marked stale, it stores
  // nothing for the next one when it lands.
  for (const key of inflight.keys()) bump(key);
  inflight.clear();
  // Memory BESIDE the cache that belongs to the member (the email HTML
  // prefetch state, WS-17 EM-S2) empties first, so a watcher that re-reads
  // below meets a clean prefetcher.
  for (const cb of [...clearWatchers]) cb();
  // Watchers deliberately survive: a mounted component still wants to hear
  // about its key. It is told now, so it re-reads as itself.
  for (const key of [...watchers.keys()]) notify(key);
}

const clearWatchers = new Set<() => void>();

/**
 * Run `cb` on each `clearAll`, so on each sign-out and each change of member
 * too, because `bindIdentity` calls `clearAll`. A module that keeps memory of
 * one member beside this cache uses it to forget that memory at the same
 * moment. Returns the unsubscribe.
 */
export function onClear(cb: () => void): () => void {
  clearWatchers.add(cb);
  return () => {
    clearWatchers.delete(cb);
  };
}

/** Entry count. For the tests and for a debug read-out. */
export function size(): number {
  return store.size;
}

/** Who the cached rows belong to. `null` before anyone has signed in. */
let boundIdentity: string | null = null;

/**
 * ⚠️ THE TENANCY GUARD. Bind the cache to the signed-in member.
 *
 * The keys are request paths, and a path says nothing about WHO asked. So two
 * members using one browser share every key. Sign out, sign in as somebody
 * else, and without this the second member's first paint is the first
 * member's rows — served from memory, below the gateway, where no amount of
 * FORCE ROW LEVEL SECURITY can reach it.
 *
 * Bound from `AppShell`, which wraps every app and already holds the session,
 * rather than beside each sign-out button. There are two of those buttons
 * today. The number of places a session can end is not fixed, and the guard
 * has to hold for the ones nobody has written yet.
 *
 * A full page load clears module memory on its own. That is a side effect of
 * how the browser works, not a guarantee this code may rest on — one
 * `signOut({ redirect: false })` anywhere turns it into a leak.
 *
 * Returns whether the identity actually changed, so a caller can log it.
 */
export function bindIdentity(id: string | null): boolean {
  if (id === boundIdentity) return false;
  boundIdentity = id;
  clearAll();
  return true;
}

/** Who the cache currently believes it is holding rows for. For the fence. */
export function identity(): string | null {
  return boundIdentity;
}
