/**
 * Which task a `#171` in a chat answer is: one batched lookup, cached.
 *
 * Owner report, 2026-10-13. `remarkEntityPills` marks each bare `#171`, and
 * `ChatTaskRef` asks this store what it is. The store asks the gateway
 * (`GET /projects/search/refs`), which answers with the tasks the member can
 * see that carry those numbers. Four rules:
 *
 * 1. **One call per message.** Every reference that a render asks for in one
 *    tick goes in ONE request. The pills of one answer mount in one commit,
 *    so one answer costs one call, and a number asked for once is never asked
 *    for again in that chat session.
 * 2. **Never block the text.** Until the answer comes, a reference draws as
 *    the text it is. A failed lookup draws as text too, and the store tries
 *    again on a short ladder (`RETRY_MS`), so the pill appears when the
 *    gateway answers.
 * 3. **A link only on exactly one match.** A task number is unique per root
 *    project, not per organization (`pm_tasks UNIQUE (root_project_id,
 *    task_number)`), so two spaces can each hold a #5. `pickTask` links only
 *    when one task fits. A wrong link is worse than no link, as in
 *    `entityIndex.ts`.
 * 4. **The cache is per chat session**, keyed by the session id. A new
 *    session asks again, so a task that the member lost access to stops
 *    linking there.
 *
 * Framework-free except for the hook at the end, so the node-env vitest can
 * drive the store with a fake fetcher and fake timers.
 * Fence: `src/lib/taskRefResolver.test.ts`.
 */

import { useEffect, useSyncExternalStore } from "react";

import { nameKey } from "@/lib/entityIndex";

/** One task the lookup found, as `GET /projects/search/refs` returns it. */
export interface TaskRefHit {
  id: string;
  task_number: number;
  title: string;
  status_name?: string | null;
  category?: string | null;
  archived?: boolean;
}

/** What the store knows about one number. */
export type TaskRefEntry =
  | { state: "pending" }
  | { state: "done"; hits: TaskRefHit[] }
  | { state: "failed"; attempts: number };

export type TaskRefFetcher = (numbers: number[]) => Promise<TaskRefHit[]>;

/** The most numbers in one request. The gateway caps at the same figure. */
export const MAX_REFS_PER_CALL = 50;

/** The waits before each retry of a failed lookup. Then it stays text. */
export const RETRY_MS: readonly number[] = [2_000, 8_000, 30_000];

/**
 * The one task a reference names, or null.
 *
 * `title`, when given, is the title the answer wrote (`#5 «Title»`): then the
 * hit must carry that title too. Without it, an open task wins over an
 * archived one, and two open tasks with one number link to neither.
 */
export function pickTask(hits: readonly TaskRefHit[], title?: string): TaskRefHit | null {
  let fit = [...hits];
  if (title !== undefined) {
    const key = nameKey(title);
    fit = fit.filter((h) => nameKey(h.title) === key);
  }
  const ids = new Set(fit.map((h) => h.id));
  if (ids.size === 1) return fit[0];
  const open = fit.filter((h) => !h.archived);
  return new Set(open.map((h) => h.id)).size === 1 ? open[0] : null;
}

export interface TaskRefResolver {
  get(scope: string, number: number): TaskRefEntry | undefined;
  request(scope: string, number: number): void;
  subscribe(listener: () => void): () => void;
  /** Send what is queued now. Tests call it. The timer calls it otherwise. */
  flush(): Promise<void>;
}

export function createTaskRefResolver(
  fetcher: TaskRefFetcher,
  retryMs: readonly number[] = RETRY_MS,
): TaskRefResolver {
  const cache = new Map<string, Map<number, TaskRefEntry>>();
  const queue = new Map<string, Set<number>>();
  const listeners = new Set<() => void>();
  let timer: ReturnType<typeof setTimeout> | null = null;

  const entries = (scope: string) => {
    let m = cache.get(scope);
    if (!m) {
      m = new Map();
      cache.set(scope, m);
    }
    return m;
  };
  const notify = () => listeners.forEach((l) => l());

  const schedule = () => {
    if (timer !== null) return;
    // A macrotask, not a microtask: every pill of one commit runs its effect
    // before this fires, so one answer is one request.
    timer = setTimeout(() => {
      timer = null;
      void flush();
    }, 0);
  };

  const enqueue = (scope: string, number: number) => {
    let q = queue.get(scope);
    if (!q) {
      q = new Set();
      queue.set(scope, q);
    }
    q.add(number);
    schedule();
  };

  async function send(scope: string, numbers: number[]): Promise<void> {
    const m = entries(scope);
    let hits: TaskRefHit[];
    try {
      hits = await fetcher(numbers);
    } catch {
      for (const n of numbers) {
        const prev = m.get(n);
        const attempts = (prev?.state === "failed" ? prev.attempts : 0) + 1;
        m.set(n, { state: "failed", attempts });
        const wait = retryMs[attempts - 1];
        if (wait !== undefined) {
          setTimeout(() => {
            if (m.get(n)?.state === "failed") enqueue(scope, n);
          }, wait);
        }
      }
      notify();
      return;
    }
    for (const n of numbers) {
      m.set(n, { state: "done", hits: hits.filter((h) => Number(h.task_number) === n) });
    }
    notify();
  }

  async function flush(): Promise<void> {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    const batches: Promise<void>[] = [];
    for (const [scope, set] of queue) {
      const numbers = [...set].sort((a, b) => a - b);
      for (let k = 0; k < numbers.length; k += MAX_REFS_PER_CALL) {
        batches.push(send(scope, numbers.slice(k, k + MAX_REFS_PER_CALL)));
      }
    }
    queue.clear();
    await Promise.all(batches);
  }

  return {
    get: (scope, number) => cache.get(scope)?.get(number),
    request(scope, number) {
      const m = entries(scope);
      const known = m.get(number);
      // Asked once is enough: a pending, answered or retrying number is not
      // asked again. A failed one asks again only on its own retry timer.
      if (known) return;
      m.set(number, { state: "pending" });
      enqueue(scope, number);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    flush,
  };
}

/** The gateway read, through the Projects proxy. */
async function fetchRefs(numbers: number[]): Promise<TaskRefHit[]> {
  const res = await fetch(`/api/projects/search/refs?numbers=${numbers.join(",")}`);
  if (!res.ok) throw new Error(`task refs: ${res.status}`);
  const body = (await res.json()) as { rows?: TaskRefHit[] };
  return Array.isArray(body?.rows) ? body.rows : [];
}

/** The app's one store. */
export const taskRefResolver: TaskRefResolver = createTaskRefResolver(fetchRefs);

/**
 * What the store knows about `number`, and ask for it when `enabled`.
 * `enabled` is false while the answer streams: a number still being typed
 * (`#1`, then `#17`, then `#171`) must not cost a lookup each.
 */
export function useTaskRef(
  scope: string,
  number: number | null,
  enabled: boolean,
  resolver: TaskRefResolver = taskRefResolver,
): TaskRefEntry | undefined {
  const read = () => (number === null ? undefined : resolver.get(scope, number));
  // The server snapshot reads the same store. On a server the store is
  // always empty, because only the effect below fills it, and an effect never
  // runs there. So a server render draws text, as the client's first does.
  const entry = useSyncExternalStore(resolver.subscribe, read, read);
  useEffect(() => {
    if (enabled && number !== null) resolver.request(scope, number);
  }, [enabled, number, scope, resolver]);
  return entry;
}
