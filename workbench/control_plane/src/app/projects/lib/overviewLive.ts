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
  overviewState,
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
 * When the last row would hold one panel alone, that panel takes the row.
 * So each row holds two panels or one wide panel. `keys` is in draw order.
 */
export function gridSpans(keys: readonly string[]): Set<string> {
  const wide = new Set<string>();
  let col = 0;
  let lastNarrow: string | null = null;
  for (const key of keys) {
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
