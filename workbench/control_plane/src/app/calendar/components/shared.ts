// Shared calendar primitives — constants, geometry helpers, formatters and the
// plan-request builder used across the calendar surface (CalendarView + its
// subcomponents in this directory, and FocusMode). Extracted from the former
// CalendarView monolith so each piece has one home and the day/week/month grid,
// the rail, the planner panels and Focus Mode all agree on the same math.

import { useEffect, useState } from "react";
import type { EnergyWindow, DayTemplate } from "@/app/tasks/lib/api";
import { startOfDay, addDays, sameDay, type Block } from "@/app/tasks/lib/scheduling";
import type { MyTask } from "@/app/tasks/lib/types";

// ── layout constants ─────────────────────────────────────────────────────────
export const DAY_START_HOUR = 7; // default grid window (overridden by prefs)
export const DAY_END_HOUR = 22;
export const HOUR_PX = 46; // vertical pixels per hour on the time grid
export const SOFT_CAPACITY_MINS = 6 * 60; // default "booked > 6h" flag
export const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** The planner's built-in philosophy, used when the user hasn't set their own.
 *  MIRROR of DEFAULT_PLANNING_PROMPT in the gateway (routes/tasks/calendar.py) —
 *  shown in Settings so "leave blank" is not a mystery. Keep the two in sync. */
export const DEFAULT_PLANNING_PROMPT =
  "Plan my day like a thoughtful human, not a task-cramming machine. Leave " +
  "breathing room — do NOT try to fill every free minute; a calmer, well-paced " +
  "day that actually gets done beats an over-packed one. Front-load the " +
  "hardest / deepest work into my peak-energy windows, and batch similar tasks " +
  "together (calls with calls, admin with admin) to avoid context-switching. " +
  "Protect focus for the most important thing. Respect any constraints or mood " +
  "I give for today.";

/** "7" → "7 AM", "0" → "12 AM", "12" → "12 PM", "24" → "12 AM" (end-of-day). */
export const hourLabel = (h: number): string => {
  const x = ((h % 24) + 24) % 24;
  if (x === 0) return "12 AM";
  if (x === 12) return "12 PM";
  return x < 12 ? `${x} AM` : `${x - 12} PM`;
};

// ── drag-and-drop / resize ───────────────────────────────────────────────────
export const SNAP_MINS = 15; // blocks snap to a quarter-hour
export const DRAG_TYPE = "application/x-cc-cal";
export type DragPayload = {
  id: string;
  durationMins: number;
  grabOffsetMins: number;
};
export const snap = (mins: number) =>
  Math.round(mins / SNAP_MINS) * SNAP_MINS;

export type Mode = "day" | "week" | "month";
/** Truncated project-outcome lookup for the outcome ribbon on blocks. */
export type OutcomeById = Map<string, string>;

// ── date helpers (plain Date math; no date lib in the bundle) ────────────────
export const addMonths = (d: Date, n: number) => {
  const x = startOfDay(d);
  x.setMonth(x.getMonth() + n, 1);
  return x;
};
/** Monday-start week. */
export const startOfWeek = (d: Date) => {
  const x = startOfDay(d);
  const dow = (x.getDay() + 6) % 7; // Mon=0 … Sun=6
  return addDays(x, -dow);
};
export const startOfMonthGrid = (d: Date) =>
  startOfWeek(new Date(d.getFullYear(), d.getMonth(), 1));
export const minutesInto = (d: Date) => d.getHours() * 60 + d.getMinutes();

// ── formatters ───────────────────────────────────────────────────────────────
/** "2:05 PM". Re-exported, not redefined — it moved to `@/app/tasks/lib/utils`
 *  on 2026-08-24 so `FocusMode` (mounted globally by `AppShell`) could stop
 *  importing across the app boundary. The five call sites in this directory
 *  keep their import path; a second definition here would be the CLAUDE.md §5
 *  defect. */
export { fmtClock } from "@/app/tasks/lib/utils";

/** "23m" / "1h 5m" — compact human duration for countdowns. */
export function fmtLeft(mins: number): string {
  if (mins < 1) return "<1m";
  if (mins < 60) return `${mins}m`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

// ── a block's lines ──────────────────────────────────────────────────────────

/**
 * Which optional lines a block on the grid draws, for its length (D-PM-38
 * S4). The title and the clock line always draw. A subtask's block also
 * draws its "↳ Parent" crumb, which a member cannot hide.
 *
 * - From 90 minutes the crumb has its own line, and the outcome ribbon draws.
 * - Under 90 minutes the crumb shares the clock line, and the ribbon gives
 *   way. A one-hour block then draws two lines, which fit.
 *
 * A plain block keeps the ribbon from 45 minutes, as before S4. The first S4
 * build drew four lines on a one-hour step block, and the clock line fell
 * below the block edge.
 */
export function blockLines(
  mins: number,
  isStep: boolean,
): { crumb: "own" | "inline" | null; outcome: boolean } {
  if (!isStep) return { crumb: null, outcome: mins >= 45 };
  return mins >= 90
    ? { crumb: "own", outcome: true }
    : { crumb: "inline", outcome: false };
}

// ── a block's card (the task block cards redesign, task 1124) ────────────────

/** How tall a card is, by its length. The grid draws `mins / 60 * HOUR_PX - 2`
 *  pixels, so 30 minutes is 21 px, 45 is 32 px, 60 is 44 px and 90 is 67 px. */
export type BlockRows = "one" | "two" | "three";

/** The always-visible Focus button of the current card. */
export type FocusForm = "label" | "icon" | false;

export type BlockLayout = {
  /** one: title and range on a single row. two: a title row and a meta row.
   *  three: the same, plus the optional lines of `blockLines`. */
  rows: BlockRows;
  /** The title may wrap to two lines (a card of 2 hours or more). */
  wrapTitle: boolean;
  /** The time range on the title row (a one-row card only). */
  rangeOnTitle: "full" | "compact" | false;
  /** The Focus button of the current card: a label, an icon, or none. */
  focus: FocusForm;
  /** The "Now" and "Overlaps" chips draw. */
  chips: boolean;
  /** The check circle draws. A card under 90 px gives its room to the title. */
  check: boolean;
  /** The range on the meta row (a card of two or three rows). */
  metaRange: "full" | "compact";
  /** The "· 1h" length after the range. */
  duration: boolean;
  energy: boolean;
  crumb: "own" | "inline" | null;
  /** The "→ outcome" text: on its own line, or at the end of the meta row. */
  outcome: false | "inline" | "own";
};

// Widths in px of what a one-row card can hold, at 11 px text. The title keeps
// at least TITLE_MIN_PX, or 40% of the card, whichever is more.
const TITLE_MIN_PX = 48;
const TITLE_MIN_SHARE = 0.4;
const ROW_BASE_PX = 34; // left bar and padding, the check circle and its gap
const ROW_BASE_NO_CHECK_PX = 12; // the same, with no check circle
const CHECK_MIN_PX = 90;
const GAP_PX = 6;
const W_FOCUS_LABEL = 56;
const W_FOCUS_ICON = 20;
const W_RANGE_FULL = 96;
const W_RANGE_COMPACT = 62;
const W_NOW_CHIP = 88;
const W_OVERLAP_CHIP = 62;

/**
 * What a card on the grid draws, from its length and its REAL width in px.
 *
 * - Under 45 minutes: ONE row. A 30-minute card is 21 px tall.
 * - 45 to 89 minutes: a title row and a meta row. 45 minutes is 30 px inside
 *   its border, which two 11 px lines just fill. A plain block keeps its
 *   "→ outcome" from 45 minutes, as `blockLines` has it, and a step keeps its
 *   crumb on the meta row. A third line does not fit at 11 px, so the outcome
 *   ends the meta row here, where main drew it on its own line.
 * - 90 minutes or more: the lines of `blockLines` have their own rows. From
 *   120 minutes the title may wrap to two lines.
 *
 * The title keeps at least 48 px or 40% of the card. As the card narrows, a
 * one-row card drops, in this order: the Focus label (the icon stays), the
 * range (full, then compact, then none), the chips, and last the Focus icon.
 * The Focus of the hover toolbar still reaches a card that lost its own.
 */
export function blockLayout(
  mins: number,
  widthPx: number,
  isStep: boolean,
  opts: { isNow?: boolean; overlaps?: boolean } = {},
): BlockLayout {
  const title = Math.max(TITLE_MIN_PX, widthPx * TITLE_MIN_SHARE);
  const lines = blockLines(mins, isStep);
  const check = widthPx >= CHECK_MIN_PX;
  const base = check ? ROW_BASE_PX : ROW_BASE_NO_CHECK_PX;
  const meta = {
    metaRange: widthPx >= 170 ? ("full" as const) : ("compact" as const),
    duration: widthPx >= 150,
    energy: widthPx >= 240,
  };

  if (mins < 45) {
    let focus: FocusForm = opts.isNow ? "label" : false;
    let range: "full" | "compact" | false = "full";
    let chips = Boolean(opts.isNow || opts.overlaps);
    const cost = () =>
      base +
      title +
      (focus === "label" ? W_FOCUS_LABEL + GAP_PX : 0) +
      (focus === "icon" ? W_FOCUS_ICON + GAP_PX : 0) +
      (range === "full" ? W_RANGE_FULL + GAP_PX : 0) +
      (range === "compact" ? W_RANGE_COMPACT + GAP_PX : 0) +
      (chips
        ? (opts.isNow ? W_NOW_CHIP + GAP_PX : 0) +
          (opts.overlaps ? W_OVERLAP_CHIP + GAP_PX : 0)
        : 0);
    while (cost() > widthPx) {
      if (focus === "label") focus = "icon";
      else if (range === "full") range = "compact";
      else if (range === "compact") range = false;
      else if (chips) chips = false;
      else if (focus === "icon") focus = false;
      else break;
    }
    return {
      rows: "one",
      wrapTitle: false,
      rangeOnTitle: range,
      focus,
      chips,
      check,
      ...meta,
      crumb: null,
      outcome: false,
    };
  }

  // Two or three rows: the title row holds the Focus button alone.
  const room = widthPx - base - title;
  const focus: FocusForm = !opts.isNow
    ? false
    : room >= W_FOCUS_LABEL + GAP_PX
      ? "label"
      : room >= W_FOCUS_ICON + GAP_PX
        ? "icon"
        : false;
  const chips = widthPx >= 150;
  if (mins < 90) {
    return {
      rows: "two",
      wrapTitle: false,
      rangeOnTitle: false,
      focus,
      chips,
      check,
      ...meta,
      crumb: lines.crumb,
      outcome: lines.outcome && widthPx >= 220 ? "inline" : false,
    };
  }
  return {
    rows: "three",
    wrapTitle: mins >= 120 && widthPx >= 140,
    rangeOnTitle: false,
    focus,
    chips,
    check,
    ...meta,
    crumb: lines.crumb,
    outcome: lines.outcome ? "own" : false,
  };
}

/**
 * "4:26 – 4:56" or "4:26 – 5:26 AM". The start drops its AM or PM when the
 * end shares it. `compact` also drops the period and the spaces, for a narrow
 * card: "4:26–4:56". `locale` is for tests.
 */
export function fmtRange(
  start: Date,
  end: Date,
  opts: { compact?: boolean; locale?: string } = {},
): string {
  const f = new Intl.DateTimeFormat(opts.locale, {
    hour: "numeric",
    minute: "2-digit",
  });
  const parts = (d: Date) => f.formatToParts(d);
  const text = (ps: Intl.DateTimeFormatPart[], period: boolean) =>
    ps
      .filter((p) => period || p.type !== "dayPeriod")
      .map((p) => p.value)
      .join("")
      .replace(/\s/g, " ") // ICU puts a narrow no-break space before AM
      .trim();
  const a = parts(start);
  const b = parts(end);
  const periodOf = (ps: Intl.DateTimeFormatPart[]) =>
    ps.find((p) => p.type === "dayPeriod")?.value ?? "";
  const samePeriod = periodOf(a) === periodOf(b);
  if (opts.compact) return `${text(a, false)}–${text(b, false)}`;
  return `${text(a, !samePeriod)} – ${text(b, true)}`;
}

/** "4:39", the clock without AM or PM: the pill of the hour gutter. The hour
 *  labels beside it carry the period. `locale` is for tests. */
export function fmtClockShort(d: Date, locale?: string): string {
  return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" })
    .formatToParts(d)
    .filter((p) => p.type !== "dayPeriod")
    .map((p) => p.value)
    .join("")
    .trim();
}

/** How far through a block "now" is, as a whole percent from 0 to 100. */
export function elapsedPct(start: Date, end: Date, now: Date): number {
  const span = end.getTime() - start.getTime();
  if (span <= 0) return 0;
  const pct = ((now.getTime() - start.getTime()) / span) * 100;
  return Math.round(Math.min(100, Math.max(0, pct)));
}

/** "12m left", "1h 5m left", "<1m left" for a block that holds "now". */
export function leftText(end: Date, now: Date): string {
  const mins = Math.ceil((end.getTime() - now.getTime()) / 60000);
  return `${fmtLeft(Math.max(0, mins))} left`;
}

// ── the unscheduled rail ─────────────────────────────────────────────────────

/** A step of mine that I can schedule: it is itself NEXT, and not filed. The
 *  disposition is the effective one the gateway sends (D77). */
const isNextStepOfMine = (i: MyTask): boolean =>
  Boolean(i.parentItemId) &&
  Boolean(i.isMine) &&
  !i.archivedAt &&
  i.disposition === "NEXT";

/**
 * The tasks the unscheduled rail offers, and the planner may pack (D-PM-38,
 * Subtasks S4): mine, NEXT, not archived, with no block yet, and NOT a parent
 * that has a NEXT step of mine.
 *
 * The Calendar app always shows subtasks. The NEXT steps are the units I
 * schedule, so packing their parent too would book the same work twice. A
 * step in INBOX, SOMEDAY or WAITING cannot be scheduled, so it leaves its
 * parent a candidate. So does a step that is done, archived, trashed or
 * somebody else's.
 *
 * The gateway's copy is `planning._LensSource.candidates`. Both read one table,
 * `tests/fixtures/subtask_planner_parity.json`: `shared.test.ts` here, and
 * `tests/live/live_ws39_subtask_planner.py` against Postgres.
 */
export function plannerCandidates(items: readonly MyTask[]): MyTask[] {
  const withOpenSteps = new Set<string>();
  for (const i of items) {
    if (isNextStepOfMine(i)) withOpenSteps.add(i.parentItemId!);
  }
  return items.filter(
    (i) =>
      i.disposition === "NEXT" &&
      Boolean(i.isMine) &&
      !i.scheduledStart &&
      !i.archivedAt &&
      !withOpenSteps.has(i.id),
  );
}

// ── grid helpers ─────────────────────────────────────────────────────────────
/** Deadline items (hard date, not timeboxed) due on `day` — the all-day lane. */
export function deadlinesForDay(items: MyTask[], day: Date): MyTask[] {
  return items.filter(
    (i) =>
      i.isHardDate &&
      i.dueAt &&
      !i.scheduledStart &&
      i.disposition !== "DONE" &&
      sameDay(new Date(i.dueAt), day),
  );
}

/** A reserved zone on the grid — protected lunch, or a recurring Block/Focus
 *  window — shown as a labeled band so "this time is spoken for" is visible. */
export interface ReservedBand {
  startHour: number;
  endHour: number;
  label: string;
  /** "block" = no tasks (lunch, gym, family); "focus" = reserved for a kind
   *  of work (the planner biases matching tasks in, but it's not off-limits). */
  kind: "block" | "focus";
}

/** The reserved windows that apply to `day`: the protected lunch plus any
 *  recurring windows whose day-of-week matches (empty `days` = every day).
 *  MIRRORS the gateway's _expand_templates / _lunch_interval so the grid shows
 *  exactly what the AI planner treats as busy (blocks) or themed (focus).
 *  Weekday is 0=Sun … 6=Sat — matching the settings day-picker. */
export function reservedWindowsForDay(
  day: Date,
  lunchStartHour: number | null | undefined,
  lunchEndHour: number | null | undefined,
  dayTemplates: DayTemplate[] | undefined,
): ReservedBand[] {
  const out: ReservedBand[] = [];
  const dow = day.getDay(); // 0=Sun … 6=Sat
  if (
    lunchStartHour != null &&
    lunchEndHour != null &&
    lunchEndHour > lunchStartHour
  ) {
    out.push({
      startHour: lunchStartHour,
      endHour: lunchEndHour,
      label: "Lunch",
      kind: "block",
    });
  }
  for (const t of dayTemplates ?? []) {
    const days = t.days ?? [];
    if (days.length && !days.includes(dow)) continue;
    const sh = Number(t.start_hour);
    const eh = Number(t.end_hour);
    if (!Number.isFinite(sh) || !Number.isFinite(eh) || eh <= sh) continue;
    const kind = t.kind === "focus" ? "focus" : "block";
    const label = (
      t.label ||
      t.theme ||
      (kind === "block" ? "Blocked" : "Focus")
    ).trim();
    out.push({ startHour: sh, endHour: eh, label, kind });
  }
  return out;
}

/** Assign overlapping blocks to side-by-side lanes (Google-Calendar style) so a
 *  double-booking is visible, not hidden. Returns each block with its lane index
 *  and the number of lanes in its overlap group (>1 ⇒ a conflict). */
export function layoutBlocks(
  blocks: Block[],
): { block: Block; lane: number; lanes: number }[] {
  const sorted = [...blocks].sort(
    (a, b) =>
      a.start.getTime() - b.start.getTime() || a.end.getTime() - b.end.getTime(),
  );
  const out: { block: Block; lane: number; lanes: number }[] = [];
  let i = 0;
  while (i < sorted.length) {
    // Maximal group of transitively-overlapping blocks.
    let groupEnd = sorted[i].end.getTime();
    let j = i + 1;
    while (j < sorted.length && sorted[j].start.getTime() < groupEnd) {
      groupEnd = Math.max(groupEnd, sorted[j].end.getTime());
      j++;
    }
    const group = sorted.slice(i, j);
    const laneEnds: number[] = [];
    const laneOf = new Map<Block, number>();
    for (const b of group) {
      let lane = laneEnds.findIndex((end) => end <= b.start.getTime());
      if (lane === -1) {
        lane = laneEnds.length;
        laneEnds.push(b.end.getTime());
      } else {
        laneEnds[lane] = b.end.getTime();
      }
      laneOf.set(b, lane);
    }
    const lanes = laneEnds.length;
    for (const b of group)
      out.push({ block: b, lane: laneOf.get(b) ?? 0, lanes });
    i = j;
  }
  return out;
}

/** A live clock that ticks so the calendar reflects the passage of time — the
 *  now-line, the current-block highlight, and the Now/Next countdowns all read
 *  from this instead of calling Date.now() in render (which the purity lint
 *  forbids, and which wouldn't re-render on its own anyway). */
export function useNow(intervalMs = 30000): Date {
  const [now, setNow] = useState<Date>(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

// ── planner request ──────────────────────────────────────────────────────────
/** Map the user's energy windows to absolute ISO intervals on `day` — the exact
 *  payload the planner/rollover endpoints expect. One home for what used to be
 *  copy-pasted into rollOver() and the Plan-day panel. */
export function energyWindowsPayload(
  windows: EnergyWindow[],
  day: Date,
): { start: string; end: string; energy: string }[] {
  return windows.map((w) => {
    const ws = new Date(day);
    ws.setHours(w.start_hour, 0, 0, 0);
    const we = new Date(day);
    we.setHours(w.end_hour, 0, 0, 0);
    return { start: ws.toISOString(), end: we.toISOString(), energy: w.energy };
  });
}
