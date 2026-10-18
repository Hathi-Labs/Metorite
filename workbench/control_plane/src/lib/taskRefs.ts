"use client";

/**
 * Task references (`#171`) in any text: find them, look them up, draw them.
 *
 * Owner report, 2026-10-13: "in some cases, the # reference number does not
 * reference a clickable link". The chat drew a task pill only for the fenced
 * form `#172 «one surface»`. A bare `#171` in the same paragraph stayed text.
 *
 * ONE module for the concept, so every surface that names a task by number
 * reads the same rules (AGENTS.md: one implementation per concept). The chat
 * imports it through `remarkEntityPills.ts` and `ChatTaskRef.tsx`. The task
 * card's title, description and comments import it too (WS-57 TR-5, through
 * `taskRefResolverFor` and `TaskRefText`). Nothing chat-only lives here.
 *
 * The gateway has ONE copy of part (a), `find_task_refs` in
 * `gateway/routes/projects/text_links.py`, for the links it saves from the
 * text. `tests/fixtures/task_ref_cases.json` holds the cases, and both
 * `taskRefs.test.ts` and `test_task_refs_text_links.py` run them. Change a
 * rule here and change it there. Three parts:
 *
 * - **(a) `findTaskRefs`**: the pure parser. No React, no fetch.
 * - **(b) `resolveTaskRefs`**, `useTaskRef` and the store under them: the
 *   batched, cached, tenant-scoped lookup (`GET /projects/search/refs`,
 *   through the Projects proxy, so the gateway scopes it to the member).
 * - **(c) `TaskRefPill` and `TaskPill`**: the draw, through the existing
 *   `EntityPill`. There is no second pill.
 *
 * Fences: `taskRefs.test.ts` (a and c), `taskRefResolver.test.ts` (b).
 */

import { createElement, Fragment, useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";

import EntityPill from "@/components/ui/EntityPill";
import { nameKey, taskHref } from "@/lib/entityIndex";
import { statusAccent } from "@/lib/statusAccent";

// ═══ (a) The parser ═════════════════════════════════════════════════════════
//
// It finds CANDIDATES. A candidate links only when the lookup finds exactly
// one visible task with that number, so these guards keep the lookup small
// and the obvious non-references out. Six rules:
//
// 1. A reference is `#` and a number that does not start with 0, 1 to 9
//    digits. `#0` and `#012` are not task numbers.
// 2. No letter, digit, `_` or URL character before the `#`. That rules out
//    `abc#12`, `page/#12`, `?a=#12` and a second `##12`.
// 3. No HTML entity: `&#123;` is a character, not a task. The `&` before the
//    `#` rules it out.
// 4. No letter or `_` after the number, and no decimal part. That rules out
//    `#12a`, `#12_x` and `#1.5`. A hashtag word like `#launch` has no digit,
//    so it never matches.
// 5. A colour is not a reference. A hex run of 6 or 8 characters (`#123456`,
//    `#11223344`) is a colour, even with digits only. A run with a hex letter
//    (`#fff`, `#a1b2c3`, `#1a2b3c`) fails rule 1 or rule 4 already.
// 6. A URL is not a reference. A word that holds `://` or starts with `www.`
//    keeps its `#12`.
// 7. Another kind of number is not a reference. `PR #120`, `Order #45`,
//    `Sprint #3` and `Invoice #4521` name another thing (`OTHER_KINDS`), and
//    a rank (`#1 priority`, `#2 spot`) is no task (`RANKS`). Review round 1.
//
// Code, inline code and a heading's own `#` marker are the CALLER's to skip:
// a Markdown caller hands this function text nodes only (`remarkEntityPills`).

/** One reference: `text.slice(start, end)` is `#171`, and `number` is 171. */
export interface TaskRefMatch {
  start: number;
  end: number;
  number: number;
}

/** Rules 1 to 4. The lookbehind is rules 2 and 3, the lookahead rule 4. */
const CANDIDATE = /(?<![\p{L}\p{N}_/\\#&=?@])#([1-9][0-9]{0,8})(?![\p{L}\p{N}_]|[.,][0-9])/gu;

/** Rule 5: the hex run that starts at `#`. */
const HEX_RUN = /^#[0-9a-fA-F]+/;

function isColourAt(rest: string): boolean {
  const run = rest.match(HEX_RUN)?.[0] ?? "";
  const digits = run.length - 1;
  return digits === 6 || digits === 8;
}

/** Rule 6: the white-space-free word around `index` is a URL. */
function isInsideUrl(text: string, index: number): boolean {
  let start = index;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  let end = index;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const word = text.slice(start, end);
  return word.includes("://") || /^\(?www\./i.test(word);
}

/** Rule 7: the word before the `#` that names another kind of number. */
//    Words that can also name a task ("issue", "bug", "ticket", "item") are
//    left out on purpose: the lookup decides those.
const OTHER_KINDS = new Set([
  "pr", "prs", "pull", "order", "orders", "invoice", "invoices", "po", "receipt", "account",
  "sprint", "build", "release", "version", "v", "step", "phase", "batch", "room", "case",
  "rank", "ranked", "episode", "chapter", "section", "slide", "figure", "channel",
]);

/** Rule 7: the word after the number that makes it a rank. */
const RANKS = /^\s+(?:priority|priorities|place|spot|rank|choice|pick|seed|position|most|best|top)\b/i;

function isOtherKind(text: string, start: number, end: number): boolean {
  const before = text.slice(Math.max(0, start - 24), start).match(/([\p{L}]+)\.?\s*$/u);
  if (before && OTHER_KINDS.has(before[1].toLowerCase())) return true;
  return RANKS.test(text.slice(end, end + 24));
}

/** Every task reference in one run of plain text, in order. Pure. */
export function findTaskRefs(text: string): TaskRefMatch[] {
  if (!text.includes("#")) return [];
  const out: TaskRefMatch[] = [];
  for (const m of text.matchAll(CANDIDATE)) {
    const start = m.index ?? 0;
    if (isColourAt(text.slice(start))) continue;
    if (isInsideUrl(text, start)) continue;
    if (isOtherKind(text, start, start + m[0].length)) continue;
    out.push({ start, end: start + m[0].length, number: Number(m[1]) });
  }
  return out;
}

/** One part of a text run: plain words, or a reference. */
export type TaskRefPart =
  | { kind: "text"; text: string }
  | { kind: "ref"; ref: string; number: number };

/** Split one run of plain text into words and references. Pure. */
export function splitTaskRefs(text: string): TaskRefPart[] {
  const refs = findTaskRefs(text);
  if (refs.length === 0) return text ? [{ kind: "text", text }] : [];
  const out: TaskRefPart[] = [];
  let last = 0;
  for (const r of refs) {
    if (r.start > last) out.push({ kind: "text", text: text.slice(last, r.start) });
    out.push({ kind: "ref", ref: text.slice(r.start, r.end), number: r.number });
    last = r.end;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

// ═══ (b) The lookup ═════════════════════════════════════════════════════════
//
// The store asks the gateway (`GET /projects/search/refs`), which answers
// with the tasks the member can see that carry those numbers. Four rules:
//
// 1. One call per batch. Every number asked for in one tick goes in ONE
//    request: the pills of one answer mount in one commit, so one answer is
//    one call. A number asked for once is not asked for again in a scope.
// 2. Never block the text. Until the answer comes, a reference draws as the
//    text it is. A failed lookup draws as text too, and the store tries again
//    on a short ladder (`RETRY_MS`), so the pill appears when the gateway
//    answers.
// 3. A link only on exactly one match. A task number is unique per root
//    project, not per organization (`pm_tasks UNIQUE (root_project_id,
//    task_number)`), so two spaces can each hold a #5. `pickTask` links only
//    when one task fits. A wrong link is worse than no link.
// 4. The cache is per scope. The chat passes its session id. Any other
//    caller gets the page's scope, `""`.
// 5. A miss does not outlive a write (owner report, 2026-10-19). A chat turn
//    can create #234 and then name it, so a number asked for before the
//    write must be asked again after it. `revalidate` asks every answered
//    number again and keeps the old answer until the new one comes, so a
//    pill never blinks back to text. The chat calls it when a tool of a turn
//    finishes (`useRevalidateOnTools`). A miss also expires by itself after
//    `MISS_TTL_MS`, for a write that no chat turn made.

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

/** How long a miss (no visible task) is kept before a new mount asks again. */
export const MISS_TTL_MS = 30_000;

/**
 * The one task a reference names, or null.
 *
 * `title`, when given, is the title the answer wrote (`#5 «Title»`): then the
 * hit must carry that title too. Without it, an open task wins over an
 * archived one, and two open tasks with one number link to neither.
 *
 * `echoed`, when given, breaks a tie. It is true for a title that the text
 * wrote right after the number (`#234 Spec tool-gap detection`). A number is
 * unique per root project only, so an org with two spaces can hold two
 * #234s. When exactly one of them carries the title the text wrote, that is
 * the task the text names (owner report, 2026-10-19).
 */
export function pickTask(
  hits: readonly TaskRefHit[],
  title?: string,
  echoed?: (title: string) => boolean,
): TaskRefHit | null {
  let fit = [...hits];
  if (title !== undefined) {
    const key = nameKey(title);
    fit = fit.filter((h) => nameKey(h.title) === key);
  }
  const one = (rows: TaskRefHit[]) => (new Set(rows.map((h) => h.id)).size === 1 ? rows[0] : null);
  const only = one(fit);
  if (only) return only;
  if (echoed) {
    const said = fit.filter((h) => echoed(h.title));
    const pick = one(said) ?? one(said.filter((h) => !h.archived));
    if (pick) return pick;
  }
  return one(fit.filter((h) => !h.archived));
}

export interface TaskRefResolver {
  get(scope: string, number: number): TaskRefEntry | undefined;
  request(scope: string, number: number): void;
  /**
   * Ask every answered number again, in one batch per scope (rule 5). The
   * old answer stays until the new one comes. No scope: every scope.
   */
  revalidate(scope?: string): void;
  subscribe(listener: () => void): () => void;
  /** Send what is queued now. Tests call it. The timer calls it otherwise. */
  flush(): Promise<void>;
}

export function createTaskRefResolver(
  fetcher: TaskRefFetcher,
  retryMs: readonly number[] = RETRY_MS,
  now: () => number = Date.now,
  missTtlMs: number = MISS_TTL_MS,
): TaskRefResolver {
  const cache = new Map<string, Map<number, TaskRefEntry>>();
  const queue = new Map<string, Set<number>>();
  // When each number was last answered, keyed `scope\nnumber` (rule 5).
  const answeredAt = new Map<string, number>();
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
        // A revalidation that fails keeps the answer it had (rule 5).
        if (prev?.state === "done") continue;
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
      answeredAt.set(`${scope}\n${n}`, now());
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
      // An old miss is asked again, and keeps its answer meanwhile (rule 5).
      const key = `${scope}\n${number}`;
      if (known?.state === "done" && known.hits.length === 0 && now() - (answeredAt.get(key) ?? 0) >= missTtlMs) {
        answeredAt.set(key, now());
        enqueue(scope, number);
        return;
      }
      // Asked once is enough: a pending, answered or retrying number is not
      // asked again. A failed one asks again only on its own retry timer.
      if (known) return;
      m.set(number, { state: "pending" });
      enqueue(scope, number);
    },
    revalidate(scope) {
      for (const [key, m] of cache) {
        if (scope !== undefined && key !== scope) continue;
        for (const [n, entry] of m) if (entry.state === "done") enqueue(key, n);
      }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    flush,
  };
}

/** The gateway read, through the Projects proxy. `root` prefers that root's
 *  task for each number (WS-57 TR-5). Exported for its test. */
export function refsUrl(numbers: readonly number[], root?: string): string {
  const base = `/api/projects/search/refs?numbers=${numbers.join(",")}`;
  return root ? `${base}&root=${encodeURIComponent(root)}` : base;
}

function fetcherFor(root?: string): TaskRefFetcher {
  return async (numbers) => {
    const res = await fetch(refsUrl(numbers, root));
    if (!res.ok) throw new Error(`task refs: ${res.status}`);
    const body = (await res.json()) as { rows?: TaskRefHit[] };
    return Array.isArray(body?.rows) ? body.rows : [];
  };
}

/** The app's one store. The chat uses it: no root. */
export const taskRefResolver: TaskRefResolver = createTaskRefResolver(fetcherFor());

const rootResolvers = new Map<string, TaskRefResolver>();

/**
 * The store for a text that belongs to a task (WS-57 TR-5): the task card's
 * title, description and comments. A number resolves in the task's own root
 * and nowhere else. A number that root cannot resolve stays text, because a
 * moved, deleted or hidden #12 must not borrow another root's #12 (review
 * round 1). The gateway's links from text read the same rule
 * (`text_links.py`), so a pill and a saved link name one task.
 *
 * One store per root, kept for the page's life: a root's answers stay right
 * for every task in it. No root: the app's store.
 */
export function taskRefResolverFor(root?: string | null): TaskRefResolver {
  if (!root) return taskRefResolver;
  let resolver = rootResolvers.get(root);
  if (!resolver) {
    resolver = createTaskRefResolver(fetcherFor(root));
    rootResolvers.set(root, resolver);
  }
  return resolver;
}

/** One resolved task reference. */
export interface TaskRef {
  id: string;
  number: number;
  title: string;
  status?: string;
  category?: string;
  archived?: boolean;
}

function toTaskRef(hit: TaskRefHit): TaskRef {
  return {
    id: hit.id,
    number: Number(hit.task_number),
    title: hit.title,
    status: hit.status_name ?? undefined,
    category: hit.category ?? undefined,
    archived: hit.archived,
  };
}

/** The task an entry names, or null: still pending, failed, none or two.
 *  `echoed` breaks a tie, as in `pickTask`. */
export function taskOf(
  entry: TaskRefEntry | undefined,
  title?: string,
  echoed?: (title: string) => boolean,
): TaskRef | null {
  if (entry?.state !== "done") return null;
  const hit = pickTask(entry.hits, title, echoed);
  return hit ? toTaskRef(hit) : null;
}

/**
 * The tasks these numbers name, for a caller outside React. One batched
 * request for the numbers the cache does not hold yet.
 *
 * Never rejects. A number with no visible task, with two, or whose lookup
 * failed maps to `null`: draw it as plain text. A failed number is asked
 * again on the retry ladder, so a later call can find it.
 */
export async function resolveTaskRefs(
  numbers: readonly number[],
  scope = "",
  resolver: TaskRefResolver = taskRefResolver,
): Promise<Map<number, TaskRef | null>> {
  const wanted = [...new Set(numbers.filter((n) => Number.isInteger(n) && n > 0))];
  for (const n of wanted) resolver.request(scope, n);
  const settled = () => wanted.every((n) => resolver.get(scope, n)?.state !== "pending");
  if (!settled()) {
    await new Promise<void>((resolve) => {
      const off = resolver.subscribe(() => {
        if (settled()) {
          off();
          resolve();
        }
      });
    });
  }
  return new Map(wanted.map((n) => [n, taskOf(resolver.get(scope, n))]));
}

/**
 * What the store knows about `number`, and ask for it when `enabled`. For a
 * React caller. `enabled` is false while a chat answer streams: a number
 * still being typed (`#1`, then `#17`, then `#171`) must not cost a lookup
 * each.
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

/**
 * Ask the app's store again when a tool of a chat turn finishes (rule 5).
 * `finished` is the count of the turn's tools that are done, and `streaming`
 * is true while the turn runs. A turn that ran a tool asks once more when it
 * ends, because its answer names what the tools wrote. A turn read from
 * history asks nothing: no tool finishes while it is on the screen.
 */
export function useRevalidateOnTools(
  finished: number,
  streaming: boolean,
  scope?: string,
  resolver: TaskRefResolver = taskRefResolver,
): void {
  const seen = useRef<ToolWatch | null>(null);
  useEffect(() => {
    const step = revalidateStep(seen.current, finished, streaming);
    seen.current = step.next;
    if (step.revalidate) revalidateChat(resolver, scope);
  }, [finished, streaming, scope, resolver]);
}

/**
 * Ask again the numbers ONE chat can show: its answers (`scope`, the session
 * id) and the cards (`""`). Never every scope: one finished tool must not
 * ask again the numbers of every other chat (review of PR #1156).
 */
export function revalidateChat(resolver: TaskRefResolver, scope?: string): void {
  resolver.revalidate("");
  if (scope) resolver.revalidate(scope);
}

/** What `useRevalidateOnTools` remembers between two renders of one turn. */
export interface ToolWatch {
  /** The count of finished tools at the last render. */
  count: number;
  /** A tool finished while this turn streamed. */
  ran: boolean;
}

/**
 * One render of `useRevalidateOnTools`, pure, so the node-env test holds it.
 * `prev` is null on the first render.
 */
export function revalidateStep(
  prev: ToolWatch | null,
  finished: number,
  streaming: boolean,
): { next: ToolWatch; revalidate: boolean } {
  const grew = finished > (prev?.count ?? 0);
  if (streaming) {
    // A tool finished during the run. The first render of a running turn
    // counts too: a remount in the middle of a run must not lose a write.
    return { next: { count: finished, ran: (prev?.ran ?? false) || grew }, revalidate: grew };
  }
  // A turn from history: nothing finished while it was on the screen.
  if (prev === null) return { next: { count: finished, ran: false }, revalidate: false };
  // The turn ended: its answer names what its tools wrote.
  return { next: { count: finished, ran: false }, revalidate: prev.ran || grew };
}

// ═══ (c) The draw ═══════════════════════════════════════════════════════════

/** A task that is already known, drawn as the task pill: `EntityPill`, the
 *  `#n`, the title, the status dot and the `?task=` link. `label` replaces
 *  the title when the text wrote its own. */
export function TaskPill({ task, label, fit }: { task: TaskRef; label?: string; fit?: boolean }) {
  return createElement(EntityPill, {
    fit,
    kind: "task",
    label: label ?? task.title,
    number: `#${task.number}`,
    href: taskHref(task.id),
    dot:
      task.status || task.category
        ? statusAccent({ category: task.category, name: task.status })
        : undefined,
    statusName: task.status,
  });
}

/**
 * A `#n` that is looked up, then drawn. Until the lookup answers, and when it
 * finds no single task, it draws `fallback`, or the plain `#n`. It never
 * draws a pill that does not open.
 *
 * `title`: the title the text wrote beside the number (`#5 «Title»`). Then
 * the task must carry that title too.
 */
export function TaskRefPill({
  number,
  title,
  scope = "",
  live = true,
  fallback,
  label,
  resolver = taskRefResolver,
  echoed,
  accept,
  fit,
}: {
  number: number;
  title?: string;
  /** Breaks a tie between two tasks with this number (`pickTask`). */
  echoed?: (title: string) => boolean;
  /** False for a task the caller will not link: the fallback draws. */
  accept?: (task: TaskRef) => boolean;
  /** Fill a card's value column, as `EntityPill`'s `fit` does. */
  fit?: boolean;
  /** The words the pill shows. Absent: the task's title, or `title`. */
  label?: (task: TaskRef) => string;
  /** The cache's key. The chat passes its session id. */
  scope?: string;
  /** False: draw the fallback and ask nothing (a streaming answer). */
  live?: boolean;
  fallback?: ReactNode;
  resolver?: TaskRefResolver;
}) {
  const entry = useTaskRef(scope, number, live, resolver);
  const found = live ? taskOf(entry, title, echoed) : null;
  const task = found && (!accept || accept(found)) ? found : null;
  if (!task) return createElement(Fragment, null, fallback ?? `#${number}`);
  return createElement(TaskPill, { task, label: label ? label(task) : title, fit });
}

/**
 * A run of plain text, not Markdown, with each `#n` drawn through
 * `TaskRefPill` (WS-57 TR-5). The task card's title uses it. The words stay
 * as written, and a number that names no single task stays text.
 */
export function TaskRefText({
  text,
  resolver = taskRefResolver,
  refClassName,
}: {
  text: string;
  resolver?: TaskRefResolver;
  /** A class on the span around each reference. A heading passes the body
   *  size, so its pill is the one look of every other pill. */
  refClassName?: string;
}) {
  return createElement(
    Fragment,
    null,
    ...splitTaskRefs(text).map((part, k) =>
      part.kind === "text"
        ? createElement(Fragment, { key: k }, part.text)
        : createElement(RefPart, { key: k, number: part.number, resolver, className: refClassName }),
    ),
  );
}

/** One reference of `TaskRefText`. The class wraps a pill only: a number
 *  that names no single task stays text in the words' own size. */
function RefPart({
  number,
  resolver,
  className,
}: {
  number: number;
  resolver: TaskRefResolver;
  className?: string;
}) {
  const task = taskOf(useTaskRef("", number, true, resolver));
  if (!task) return createElement(Fragment, null, `#${number}`);
  const pill = createElement(TaskPill, { task });
  return className ? createElement("span", { className }, pill) : pill;
}
