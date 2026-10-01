/**
 * WS-27bn R5g — the live Overview, full page (`projects_reports.md` §6.7).
 *
 * The owner (2026-09-30): "live analytics should be viewable there without
 * extra menus." Overview draws its panels across the page, keeps the choices
 * behind one Filters button, and asks the server again while it is open.
 *
 * This module holds the rules of that screen, as pure functions:
 * - when Overview refreshes, and when a late answer drops;
 * - the summary line and the filter count of the toolbar;
 * - whether Filters is open, in `localStorage`, and safe without it;
 * - which panels take both columns of the grid.
 *
 * ⚠️ **A refresh is a forced fetch of the SAME key.** Home keeps the
 * Overview state and its key (`previewNeeded`, `initialShownKey`). A refresh
 * that changed the key would make a return to Home ask again.
 */
import type { ReportConfig } from "./api";
import {
  type BuilderState,
  OVERVIEW_SECTIONS,
  initialShownKey,
  overviewState,
  previewNeeded,
} from "./reportBuilder";

/** A return to the tab refreshes an answer older than this. */
export const LIVE_STALE_MS = 60_000;

/** While the tab is visible, Overview refreshes this often. */
export const LIVE_INTERVAL_MS = 5 * 60_000;

/**
 * True when a return to the tab must refresh Overview. Only a visible tab
 * with an answer over `LIVE_STALE_MS` old, and no request in flight.
 */
export function refreshOnVisible(parts: {
  visible: boolean;
  busy: boolean;
  updatedAt: number | null;
  now: number;
}): boolean {
  if (!parts.visible || parts.busy || parts.updatedAt === null) return false;
  return parts.now - parts.updatedAt > LIVE_STALE_MS;
}

/** True when the 5-minute timer runs. A hidden tab pauses it. */
export function intervalRuns(visibility: DocumentVisibilityState | string): boolean {
  return visibility === "visible";
}

/**
 * The request that a preview key stands for. The first preview, each change
 * and each refresh read it. A refresh sends the key on screen, so it never
 * changes the key. A blocked key (a prompt on screen) asks for nothing.
 */
export function previewRequest(key: string): {
  key: string;
  project_id: string | null;
  config: ReportConfig;
  blocked: boolean;
} {
  const parsed = JSON.parse(key) as {
    project_id: string | null;
    config: ReportConfig;
    blocked: boolean;
  };
  return { key, project_id: parsed.project_id, config: parsed.config, blocked: parsed.blocked };
}

/**
 * True when an answer may go on screen. It must be the newest request, and
 * for the choices on screen now. So a slow refresh never overwrites a newer
 * answer, and never draws choices the member has left.
 */
export function answerIsCurrent(parts: {
  seq: number;
  latestSeq: number;
  key: string;
  currentKey: string;
}): boolean {
  return parts.seq === parts.latestSeq && parts.key === parts.currentKey;
}

/** "Updated just now", "Updated 2 min ago" or "Updated 1 h ago". */
export function updatedLine(updatedAt: number | null, now: number): string | null {
  if (updatedAt === null) return null;
  const minutes = Math.floor(Math.max(0, now - updatedAt) / 60_000);
  if (minutes < 1) return "Updated just now";
  if (minutes < 60) return `Updated ${minutes} min ago`;
  return `Updated ${Math.floor(minutes / 60)} h ago`;
}

/**
 * The summary line of the toolbar. The parts come from the builder's own text
 * helpers (`subjectLabel`, `scopePhrase`, the period label), so the line says
 * what the Filters say.
 */
export function overviewSummary(parts: {
  subject: string | null;
  scope: string;
  period: string;
  updated: string | null;
}): string {
  return [parts.subject ? `About ${parts.subject}` : null, parts.scope, parts.period, parts.updated]
    .filter(Boolean)
    .join(" · ");
}

/**
 * How many of the four choices differ from the Overview defaults: who, which
 * work, what time, and the sections. The badge on Filters shows it.
 */
export function overviewFilterCount(
  state: Pick<
    BuilderState,
    "subject" | "projectId" | "includeSubtree" | "weeks" | "skipCurrentWeek" | "sections"
  >
): number {
  const base = overviewState();
  const same = (a: readonly string[], b: readonly string[]) =>
    a.length === b.length && [...a].sort().join("|") === [...b].sort().join("|");
  let n = 0;
  if (state.subject !== null) n += 1;
  if (state.projectId !== base.projectId || state.includeSubtree !== base.includeSubtree) n += 1;
  if (state.weeks !== base.weeks || state.skipCurrentWeek !== base.skipCurrentWeek) n += 1;
  if (!same(state.sections, OVERVIEW_SECTIONS)) n += 1;
  return n;
}

/** The `localStorage` key of the Filters state. */
export const FILTERS_OPEN_KEY = "projects.reports.overviewFilters";

/** The part of `Storage` this module uses. */
export interface SmallStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/**
 * The browser's storage, or `null`. ⚠️ A getter, because the read of
 * `window.localStorage` itself throws when the browser blocks storage.
 */
export function browserStorage(): SmallStorage | null {
  return typeof window === "undefined" ? null : window.localStorage;
}

/**
 * True when the member left Filters open. Closed by default, and closed when
 * the storage is absent or throws. The page renders the same without it.
 */
export function readFiltersOpen(storage: () => SmallStorage | null): boolean {
  try {
    return storage()?.getItem(FILTERS_OPEN_KEY) === "1";
  } catch {
    return false;
  }
}

/** Remember the Filters state. A failed write changes nothing on screen. */
export function writeFiltersOpen(storage: () => SmallStorage | null, open: boolean): void {
  try {
    storage()?.setItem(FILTERS_OPEN_KEY, open ? "1" : "0");
  } catch {
    // Private mode or a full quota. The state stays for this visit only.
  }
}

/**
 * The sections in the order `RenderedBody` draws them. It is not the
 * `SECTIONS` order: Overdue comes before Open work (R5f, the second
 * difference). `reportsLiveOverview.test.ts` holds the two in step.
 */
export const BODY_ORDER: readonly string[] = [
  "finished",
  "throughput",
  "outlook",
  "stuck",
  "load",
  "capacity",
  "pulse",
  "hygiene",
  "conflicts",
  "rebalance",
];

/** The panels that read better across both columns, when a row is free. */
const PREFERS_WIDE: ReadonlySet<string> = new Set(["outlook", "throughput"]);

/**
 * The sections that span both columns of the Overview grid.
 *
 * A preferred panel is wide only when it starts a row, so it leaves no hole.
 * A section in `full` is always wide. R5g round 1 puts each clear row there:
 * a one-line row stretched to the height of a chart beside it looks empty.
 * When a full-width section arrives after one narrow panel, that panel takes
 * its row. When the last row would hold one panel alone, that panel takes
 * the row. So each row holds two panels or one wide panel. `keys` is in draw
 * order.
 */
export function gridSpans(
  keys: readonly string[],
  full: ReadonlySet<string> = new Set()
): Set<string> {
  const wide = new Set<string>();
  let col = 0;
  let lastNarrow: string | null = null;
  for (const key of keys) {
    if (full.has(key)) {
      if (col === 1 && lastNarrow !== null) wide.add(lastNarrow);
      col = 0;
      wide.add(key);
      continue;
    }
    if (PREFERS_WIDE.has(key) && col === 0) {
      wide.add(key);
      continue;
    }
    col = 1 - col;
    lastNarrow = key;
  }
  if (col === 1 && lastNarrow !== null) wide.add(lastNarrow);
  return wide;
}

// ── R5g round 1: the live wiring, as code a test can drive ─────────────────

/**
 * What the live wiring needs from the browser. The builder passes
 * `browserTimerEnv()`. A test passes fake timers, a fake clock and a fake
 * visibility, so it can prove each rule without a DOM.
 */
export interface LiveTimerEnv {
  now(): number;
  visibility(): string;
  setInterval(fn: () => void, ms: number): unknown;
  clearInterval(id: unknown): void;
  /** Listen for a change of visibility. It gives back the call that stops it. */
  onVisibilityChange(fn: () => void): () => void;
}

/** The timers, the clock and the visibility of the browser. */
export function browserTimerEnv(): LiveTimerEnv {
  return {
    now: () => Date.now(),
    visibility: () => (typeof document === "undefined" ? "hidden" : document.visibilityState),
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (id) => clearInterval(id as ReturnType<typeof setInterval>),
    onVisibilityChange: (fn) => {
      document.addEventListener("visibilitychange", fn);
      return () => document.removeEventListener("visibilitychange", fn);
    },
  };
}

/**
 * Run `onTick` every `intervalMs` while the tab is visible. A hidden tab
 * stops the timer. A return calls `onShow` and starts it again. `onStart`
 * runs once, at the start, when the tab is visible. The call it gives back
 * clears the timer and the listener: call it on unmount.
 */
export function startTicker(
  env: LiveTimerEnv,
  opts: { intervalMs: number; onTick: () => void; onShow: () => void; onStart?: () => void }
): () => void {
  let timer: unknown = null;
  const start = () => {
    if (timer === null) timer = env.setInterval(opts.onTick, opts.intervalMs);
  };
  const stop = () => {
    if (timer !== null) env.clearInterval(timer);
    timer = null;
  };
  const unlisten = env.onVisibilityChange(() => {
    if (!intervalRuns(env.visibility())) {
      stop();
      return;
    }
    opts.onShow();
    start();
  });
  if (intervalRuns(env.visibility())) {
    opts.onStart?.();
    start();
  }
  return () => {
    stop();
    unlisten();
  };
}

/** What `startLive` needs from the preview controller. */
export interface Refreshable {
  busy(): boolean;
  updatedAt(): number | null;
  refresh(): unknown;
}

/**
 * The live rule of Overview (§6.7 D). Every 5 minutes while the tab is
 * visible, a refresh. On a return to the tab, and on mount, a refresh when
 * the answer is more than 60 seconds old. So a return from a saved report
 * also refreshes an old Overview.
 */
export function startLive(env: LiveTimerEnv, live: Refreshable): () => void {
  const maybe = () => {
    if (
      refreshOnVisible({
        visible: intervalRuns(env.visibility()),
        busy: live.busy(),
        updatedAt: live.updatedAt(),
        now: env.now(),
      })
    )
      live.refresh();
  };
  return startTicker(env, {
    intervalMs: LIVE_INTERVAL_MS,
    onTick: () => live.refresh(),
    onShow: maybe,
    onStart: maybe,
  });
}

/** The ports of the preview controller: the key now, and where changes go. */
export interface PreviewPorts<B> {
  currentKey: () => string;
  onChange: (state: PreviewState<B>) => void;
  /** Each answer that lands, with its key and time, for Home to keep. */
  onAnswer?: (body: B, key: string, at: number) => void;
}

/** What the preview controller holds. The builder draws from it. */
export interface PreviewState<B> {
  /** The body on screen and the key it answers. */
  preview: { key: string; body: B } | null;
  /** The key on screen, for `previewNeeded`. */
  shownKey: string | null;
  error: { key: string; message: string } | null;
  /** A request for new choices is in flight. */
  changing: boolean;
  refreshing: boolean;
  refreshFailed: boolean;
  updatedAt: number | null;
}

/**
 * True when the body on screen is dimmed: it answers other choices than the
 * ones on screen. A refresh keeps the key, so it never dims the body.
 */
export function bodyDimmed<B>(state: PreviewState<B>, key: string): boolean {
  return state.preview !== null && state.preview.key !== key && state.error?.key !== key;
}

/**
 * The one owner of the preview requests (R5g round 1). The first preview, a
 * change of the choices and a refresh all go through it. So one guard,
 * `answerIsCurrent`, protects each answer: a good one and an error.
 *
 * - A refresh sends the key on screen. It keeps the key and the body, and
 *   it never dims the body.
 * - A refresh does nothing while a change is in flight or waits for its
 *   delay, or while another refresh runs. So a timer never drops the answer
 *   to a filter.
 * - A change supersedes a refresh. The late refresh answer drops, and the
 *   refresh state clears when it ends, good or bad.
 */
export function createPreviewController<B>(
  base: {
    initial: { key: string; body: B; at?: number } | null;
    fetch: (req: ReturnType<typeof previewRequest>) => Promise<B>;
    now: () => number;
    errorMessage: (e: unknown) => string;
  } & Partial<PreviewPorts<B>>
) {
  // The ports can arrive later, through `bind`: a component binds them in
  // an effect, because they read its refs.
  const opts: typeof base & PreviewPorts<B> = {
    currentKey: () => "",
    onChange: () => undefined,
    ...base,
  };
  let state: PreviewState<B> = {
    preview: opts.initial ? { key: opts.initial.key, body: opts.initial.body } : null,
    shownKey: initialShownKey(opts.initial),
    error: null,
    changing: false,
    refreshing: false,
    refreshFailed: false,
    updatedAt: opts.initial?.at ?? null,
  };
  let seq = 0;
  let live = true;
  const set = (patch: Partial<PreviewState<B>>) => {
    if (!live) return;
    state = { ...state, ...patch };
    opts.onChange(state);
  };
  const current = (my: number, key: string) =>
    live && answerIsCurrent({ seq: my, latestSeq: seq, key, currentKey: opts.currentKey() });
  const land = (key: string, body: B, patch: Partial<PreviewState<B>>) => {
    const at = opts.now();
    set({ ...patch, preview: { key, body }, shownKey: key, error: null, updatedAt: at, refreshFailed: false });
    opts.onAnswer?.(body, key, at);
  };

  return {
    get state() {
      return state;
    },
    busy: () => state.changing || state.refreshing,
    updatedAt: () => state.updatedAt,
    /** True when the choices need a request (`previewNeeded`). */
    needed: (key: string, blocked: boolean) => previewNeeded(state.shownKey, key, blocked),
    /** Ask for new choices. */
    request(key: string) {
      const req = previewRequest(key);
      if (req.blocked) return;
      const my = ++seq;
      set({ changing: true });
      opts.fetch(req).then(
        (body) => {
          if (!current(my, key)) return;
          land(key, body, { changing: false });
        },
        (e) => {
          if (!current(my, key)) return;
          set({ changing: false, error: { key, message: opts.errorMessage(e) } });
        }
      );
    },
    /** Ask again for the key on screen. It gives back true when it asked. */
    refresh(): boolean {
      const key = opts.currentKey();
      const req = previewRequest(key);
      if (req.blocked || state.changing || state.refreshing) return false;
      // A change waits for its delay: the body on screen is for other choices.
      if (state.preview !== null && state.preview.key !== key) return false;
      const my = ++seq;
      set({ refreshing: true });
      opts.fetch(req).then(
        (body) => {
          if (!current(my, key)) return set({ refreshing: false });
          land(key, body, { refreshing: false });
        },
        () => {
          set({ refreshing: false, refreshFailed: current(my, key) });
        }
      );
      return true;
    },
    /** Connect the ports. Call it before the first request. */
    bind(ports: PreviewPorts<B>) {
      Object.assign(opts, ports);
      // A remount (React's StrictMode runs each effect twice) binds again.
      live = true;
    },
    /** Stop: no answer lands after this. Call it on unmount. */
    dispose() {
      live = false;
    },
  };
}

export type PreviewController<B> = ReturnType<typeof createPreviewController<B>>;
