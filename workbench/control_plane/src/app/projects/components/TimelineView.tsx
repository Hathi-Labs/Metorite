"use client";

/**
 * Projects · the timeline (WS-27t).
 *
 * A sticky task column beside a scrolling chart of bars, with `blocks`
 * dependencies drawn as arrows between them. The geometry, the depth grouping,
 * the conflict rule and every patch a drag produces are in `lib/timeline.ts`
 * and tested there; this draws them and turns mouse events into days.
 *
 * **The layout is Paca's** (`roadmap-view.tsx`, Apache-2.0): sticky left
 * column, fixed pixels-per-day, month header cells, a today line, and an
 * undated task listed on the left with no bar.
 *
 * **The interaction is Plane's** (`gantt-chart/`, AGPL-3.0 — read at
 * `effd0c57`, not copied): a bar you can move by dragging its body and extend
 * by dragging either edge, dedicated wide hit-zones at the edges rather than
 * the bar's own border, a live date pill while you drag, snapping to whole
 * days, a minimum width of one day, three fixed zooms rather than a slider, and
 * a two-tier header. What is NOT Plane's is how the drag reaches the screen —
 * see "A drag is state" below — and the dependency arrows, which Plane draws
 * from a mobx store and we draw from the same rows the bars come from.
 *
 * ── A drag is STATE, not a style mutation ─────────────────────────────────
 *
 * Plane's `use-gantt-resizable` writes `div.style.width` and `.marginLeft` on
 * every mousemove and reconciles afterwards. That is fast, and it is why their
 * dependency lines lag the bar being dragged: the DOM has moved and the store
 * has not. Here the drag is one state object and the bar is rendered from it,
 * so the arrows, the conflict tone and the date pill all follow the bar for
 * free, because they are all reading the same number.
 *
 * The cost React would normally charge for that is a render per mousemove. It
 * is not charged, because the state only changes when the SNAPPED DAY changes —
 * at most once per `pxPerDay` pixels of travel, and the handler returns without
 * touching state in between. A drag across a whole quarter is a few dozen
 * renders, not a few thousand.
 *
 * ── Nothing is written until the mouse comes up ───────────────────────────
 *
 * One `PATCH` per gesture, through the page's existing `moveTask` — the same
 * seam the calendar's drag uses. A per-mousemove write would produce a hundred
 * activity rows for one drag.
 *
 * **An arrow warns; it never reschedules (D-PM-12).** A dependency whose
 * blocker finishes after the blocked task starts is drawn in the danger tone
 * and says so on hover. Dragging a blocker onto its dependent turns the arrow
 * red and moves nothing else — the alternative, dragging dependents forward as
 * Jira does, contradicts WS-27p's "derived and shown, never enforced" and turns
 * one drag into a cascade of writes nobody asked for.
 */

import Icon from "@/components/Icon";
import { TaskMeta } from "@/components/TaskMeta";
import Button from "@/components/ui/Button";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { StatusRow, TagRow, TaskRow } from "../lib/api";
import { accentForGroup } from "../lib/accent";
import { tagColours, visibleChips } from "../lib/card";
import type { GroupBy, TaskGroup } from "../lib/grouping";
import { anchorDay, dayKey, rescheduleTo, shiftDay } from "../lib/calendar";
import {
  BAR_H,
  type Bar,
  type Edge,
  LABEL_INSIDE_PX,
  labelSide,
  ROW_H,
  type TimelineBand,
  type TimelineRow,
  type TimelineWindow,
  type TimelineZoom,
  ZOOMS,
  ZOOM_ORDER,
  bar,
  barForSpan,
  canLink,
  conflictLabel,
  conflicts,
  dayAtPx,
  dayCells,
  dayPx,
  dayStep,
  dragRefusal,
  CONTROL_SHIELD_PX,
  AIM_KEEP_PX,
  EDGE_GRACE_MS,
  EDGE_HIT_PX,
  edgeHitOrder,
  edgeMidpoint,
  edgePoints,
  interval,
  monthCells,
  resizeEnd,
  resizeStart,
  roundedPath,
  spanFor,
  timelineBands,
  timelineRange,
  timelineRows,
  weekCells,
} from "../lib/timeline";

const LEFT_COL = 340;

/**
 * ── The meta column (WS-27t S4) ───────────────────────────────────────────
 *
 * The two facts the timeline's rail reserves a slot for, in this order.
 *
 * **Why they left the bar.** The bar carried the full chip strip, and a chip
 * strip is VARIABLE width inside a box whose width is the task's duration —
 * so a short task with a priority, a due date and a tag simply ran out of bar
 * and clipped mid-chip. No threshold fixes that: the content and the container
 * are sized by unrelated things. Plane reaches the same conclusion from the
 * other direction (`issue-layouts/gantt/blocks.tsx`) — their bar renders
 * `issueDetails.name` and nothing else, and the sidebar carries the metadata.
 *
 * **Two FIXED slots, not the strip moved sideways.** A rail that renders
 * whatever chips a task earned would overflow exactly as the bar did, one
 * column to the left. Reserving two named slots is what makes the width
 * knowable.
 *
 * These are still `visibleChips` output, so the field picker, the overdue rule
 * and the priority vocabulary are the same ones every other surface reads —
 * this is a filter on the shared seam, not a second chip vocabulary.
 */
const RAIL_CHIPS: readonly string[] = ["importance", "due"];
/** Two header tiers, each ROW_H/2-ish. Kept as one number the chart and the
 *  task column both read, so the two cannot start at different heights. */
const HEAD_H = 52;
/**
 * One drawn line of the chart — a band heading or a task.
 *
 * ⚠️ **One list, one index space.** A row's position here IS its y, and the
 * dependency arrows are placed from that index. Keeping headings in the same
 * list rather than nesting rows inside band objects is what makes an arrow
 * between two bands land on the right rows without a second traversal to work
 * out how many headings it crossed.
 */
type DrawnRow =
  | { kind: "band"; band: TimelineBand; collapsed: boolean }
  | {
      kind: "task";
      task: TaskRow;
      children: TaskRow[];
      depth: number;
      hasKids: boolean;
    };

type DragMode = "move" | "start" | "end" | "create";

interface DragState {
  taskId: string;
  mode: DragMode;
  /** clientX at mousedown — a drag is a delta from here. */
  originX: number;
  /** Whole days travelled, snapped. */
  steps: number;
  /** The day under the cursor, for the `create` gesture and the date pills. */
  hoverDay: string | null;
  /** Where a `create` drag started. */
  anchorDay: string | null;
}

interface LinkState {
  fromId: string;
  /** Cursor position in chart coordinates, for the rubber-band line. */
  x: number;
  y: number;
  overId: string | null;
}

interface Props {
  tasks: TaskRow[];
  links: Edge[];
  undated: number;
  truncated: boolean;
  /** WS-27x — the view's shown fields; chips a hidden field earned are not drawn. */
  shownFields: readonly string[];
  /** S6 — the project's tag registry, so a tag chip is the colour its owner
   *  chose here too. One tag, one colour, on every surface of the project. */
  tags?: readonly TagRow[];
  today?: string;
  /**
   * S3 — the zoom and the span of dates the page FETCHED at it.
   *
   * Both live on the page rather than here, because the zoom decides the query
   * and not only the layout. Held locally, the timeline drew a quarter of
   * calendar over one month of data.
   */
  zoom?: TimelineZoom;
  window?: TimelineWindow;
  onZoom?: (zoom: TimelineZoom) => void;
  /**
   * S5 — the grouping axis and the groups it produced.
   *
   * The SAME pair the board and list read. A grouped timeline is bands of
   * rows, which is the list's headed sections drawn against a time axis, so
   * inventing a timeline-only grouping would be a second vocabulary for one
   * idea. `commands.honoursGroupBy` is what decides the control is offered.
   */
  groupBy?: GroupBy;
  groups?: TaskGroup[];
  /** For a band's accent — one group, one colour, on every canvas. */
  statuses?: StatusRow[];
  onSelect: (task: TaskRow) => void;
  /** S2 — a committed drag. The page's existing task-date write path. */
  onMove: (task: TaskRow, patch: Record<string, string | null>) => void;
  onLink: (blockerId: string, blockedId: string) => void;
  /**
   * Remove a dependency, from the arrow itself.
   *
   * ⚠️ **This affordance is MOUSE-ONLY, and deliberately so.** The arrow layer
   * is `aria-hidden` decoration — it draws relationships that
   * `TaskPanel` states in words, and a screen reader that met both would hear
   * every dependency twice. So the keyboard and assistive path is the panel's
   * own remove control, not this one. Optional, so a timeline rendered
   * read-only simply passes nothing and no edge becomes hoverable.
   */
  onUnlink?: (blockerId: string, linkId: string) => void;
  onRefuse: (reason: string) => void;
}

export function TimelineView({
  tasks,
  links,
  undated,
  truncated,
  today,
  shownFields,
  tags,
  zoom = "month",
  window: fetched,
  onZoom,
  groupBy = "none",
  groups = [],
  statuses = [],
  onSelect,
  onMove,
  onLink,
  onUnlink,
  onRefuse,
}: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  /** Collapsed bands, by group key — local, like the list's folded sections. */
  const [folded, setFolded] = useState<ReadonlySet<string>>(new Set());
  const [drag, setDrag] = useState<DragState | null>(null);
  const [link, setLink] = useState<LinkState | null>(null);
  const [hoverRow, setHoverRow] = useState<string | null>(null);
  /**
   * The edge under the cursor, by link id.
   *
   * Separate from `hoverRow`, which lights every edge a ROW touches. This one
   * is the single arrow being pointed at, and only it earns a remove control —
   * lighting a row and offering to delete four dependencies at once is not the
   * same gesture.
   */
  const [hoverEdge, setHoverEdge] = useState<string | null>(null);

  /**
   * ── The aim LATCH, and why a plain mouseleave will not do ─────────────────
   *
   * The remove control sits half way ALONG the arrow, which is a dogleg. So the
   * straight line a hand takes from the arrow to the control leaves the path —
   * and the path is all there is: a 12px stroke with nothing either side of it.
   * `onMouseLeave` fired part-way, `hoverEdge` cleared, and the control
   * unmounted from under a cursor that was travelling toward it. Where a second
   * arrow overlapped, the pointer landed on THAT stroke instead and the control
   * reappeared on the wrong arrow, which is how the owner reported it on
   * 2026-09-05.
   *
   * Measured before this landed: walking the pointer from the arrow to its own
   * control in 16 steps, the control was absent for 10 of them.
   *
   * So leaving the stroke does not drop the aim immediately — it schedules the
   * drop, and anything that means "still going there" cancels it: the stroke
   * again, the shield around the control, or the control itself. Deliberately
   * moving to a different arrow still works, because that arrow's own enter
   * cancels the timer and claims the aim.
   */
  const releaseRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aimEdge = useCallback((id: string) => {
    if (releaseRef.current) clearTimeout(releaseRef.current);
    releaseRef.current = null;
    setHoverEdge(id);
  }, []);
  const releaseEdge = useCallback((id: string) => {
    if (releaseRef.current) clearTimeout(releaseRef.current);
    releaseRef.current = setTimeout(() => {
      releaseRef.current = null;
      setHoverEdge((current) => (current === id ? null : current));
    }, EDGE_GRACE_MS);
  }, []);
  // A pending release must not outlive the view and set state on a dead tree.
  useEffect(
    () => () => {
      if (releaseRef.current) clearTimeout(releaseRef.current);
    },
    [],
  );

  const todayKey = today ?? dayKey(new Date());

  // The live drag, for listeners that were created once and must not close over
  // a stale render's copy.
  const dragRef = useRef<DragState | null>(null);
  const linkRef = useRef<LinkState | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  /** The scroll container's box, measured at mousedown rather than per move. */
  const boxRef = useRef<DOMRect | null>(null);
  /**
   * Did the pointer actually travel during the gesture that just ended?
   *
   * `preventDefault()` on mousedown stops selection and focus but NOT the click
   * that follows mouseup — so without this, every completed drag also opens the
   * task panel over the chart you were arranging. Cleared at mousedown, set the
   * moment a drag crosses a day boundary, read by the bar's own click.
   */
  const travelledRef = useRef(false);

  // Once per registry, not once per bar.
  const tagHues = useMemo(() => tagColours(tags ?? []), [tags]);
  const rows = useMemo(() => timelineRows(tasks), [tasks]);
  const range = useMemo(
    () => timelineRange(rows, todayKey, zoom, fetched),
    [rows, todayKey, zoom, fetched]
  );

  // One flat list of drawn lines — parents, then their children when expanded —
  // so a row's index IS its y. Arrows are positioned from that index, and a
  // second traversal to find it would be a second chance to disagree.
  /**
   * The bands, when a grouping axis is on (WS-27t S5).
   *
   * Built from the SAME `groups` the board and list read, so a view grouped by
   * project opens grouped by project on every canvas that can draw it.
   */
  const bands = useMemo(
    () => (groupBy === "none" ? [] : timelineBands(groups)),
    [groupBy, groups]
  );
  const grouped = groupBy !== "none";

  const drawn = useMemo(() => {
    const out: DrawnRow[] = [];
    /** One band's task rows, honouring the subtask fold. */
    const pushRows = (source: readonly TimelineRow[]) => {
      for (const row of source) {
        out.push({
          kind: "task",
          task: row.task,
          children: row.children,
          depth: 0,
          hasKids: row.children.length > 0,
        });
        if (expanded.has(row.task.id)) {
          for (const kid of row.children) {
            out.push({
              kind: "task", task: kid, children: [], depth: 1, hasKids: false,
            });
          }
        }
      }
    };

    if (!grouped) {
      pushRows(rows);
      return out;
    }
    for (const band of bands) {
      const isFolded = folded.has(band.key);
      out.push({ kind: "band", band, collapsed: isFolded });
      // A folded band's rows are off-screen, so they take no index — the same
      // rule the list applies to a folded section, and what keeps a row's
      // position in this list equal to its y on the chart.
      if (!isFolded) pushRows(band.rows);
    }
    return out;
  }, [grouped, bands, rows, expanded, folded]);

  const indexById = useMemo(() => {
    const map = new Map<string, number>();
    drawn.forEach((row, i) => {
      if (row.kind === "task") map.set(row.task.id, i);
    });
    return map;
  }, [drawn]);
  const taskById = useMemo(
    () => new Map(tasks.map((t) => [t.id, t])),
    [tasks],
  );

  /**
   * Every bar, INCLUDING the one being dragged — which is drawn where the
   * cursor has put it rather than where the server still thinks it is.
   *
   * Computed in one place so the arrows, the conflict tone and the bar itself
   * all read the same geometry during a drag. That is the whole reason the drag
   * is state: three consumers, one number.
   */
  const barById = useMemo(() => {
    const out = new Map<string, Bar | null>();
    for (const row of drawn) {
      if (row.kind !== "task") continue;
      const settled = bar(row.task, row.children, range);
      if (!drag || drag.taskId !== row.task.id) {
        out.set(row.task.id, settled);
        continue;
      }
      out.set(row.task.id, previewBar(row.task, settled, drag, range));
    }
    return out;
  }, [drawn, range, drag]);

  const months = monthCells(range);
  const days = useMemo(() => dayCells(range, todayKey), [range, todayKey]);
  const weeks = useMemo(() => weekCells(range, todayKey), [range, todayKey]);
  const todayPx = todayKey >= range.from && todayKey <= range.to
    ? dayPx(todayKey, range) : null;
  const height = Math.max(drawn.length * ROW_H, ROW_H);

  /** Cursor x in CHART coordinates — past the sticky column, past the scroll. */
  const chartX = useCallback((clientX: number): number => {
    const box = boxRef.current;
    const el = scrollRef.current;
    if (!box || !el) return 0;
    return clientX - box.left - LEFT_COL + el.scrollLeft;
  }, []);

  /** Centre the chart on a day. The only imperative scroll in here. */
  const scrollToDay = useCallback((day: string, smooth = true) => {
    const el = scrollRef.current;
    if (!el) return;
    const target = dayPx(day, range) - (el.clientWidth - LEFT_COL) / 2;
    el.scrollTo({
      left: Math.max(0, target),
      behavior: smooth ? "smooth" : "auto",
    });
  }, [range]);

  /**
   * What is on screen, for the off-screen markers below.
   *
   * A state write per scroll frame, throttled to one per animation frame —
   * which is what any virtualised list does, and the only way to know whether a
   * bar is visible. Nothing else in this component reads it, so a scroll
   * re-renders the markers and not the chart's geometry.
   */
  const [viewport, setViewport] = useState({ left: 0, width: 0 });
  const frameRef = useRef(0);
  /** The day in the middle of the viewport — a REF, so scrolling is free. */
  const centredRef = useRef<string | null>(null);

  const readViewport = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const width = el.clientWidth - LEFT_COL;
    centredRef.current = dayAtPx(el.scrollLeft + width / 2, range);
    setViewport((current) =>
      current.left === el.scrollLeft && current.width === width
        ? current
        : { left: el.scrollLeft, width }
    );
  }, [range]);

  function onScroll() {
    if (frameRef.current) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = 0;
      readViewport();
    });
  }

  // Open on today rather than on the range's left edge — a chart that opens
  // four months in the past is one whose first interaction is always a scroll.
  // A ZOOM change instead holds the day you were looking at: changing zoom to
  // see more context should not also teleport you out of the quarter you were
  // reading.
  const openedAt = useRef<string | null>(null);
  const lastZoom = useRef<TimelineZoom>(zoom);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (lastZoom.current !== zoom) {
      lastZoom.current = zoom;
      const held = centredRef.current;
      if (held) {
        scrollToDay(held, false);
        readViewport();
        return;
      }
    }
    if (openedAt.current === range.from) return;
    openedAt.current = range.from;
    if (todayPx !== null) scrollToDay(todayKey, false);
    readViewport();
  }, [zoom, range.from, todayPx, todayKey, scrollToDay, readViewport]);

  // ── Dragging a bar ──────────────────────────────────────────────────────

  function beginDrag(
    event: React.MouseEvent,
    taskId: string,
    mode: DragMode,
    anchor?: string,
  ) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const el = scrollRef.current;
    if (!el) return;
    boxRef.current = el.getBoundingClientRect();
    travelledRef.current = false;

    const next: DragState = {
      taskId,
      mode,
      originX: event.clientX,
      steps: 0,
      hoverDay: anchor ?? null,
      anchorDay: anchor ?? null,
    };
    dragRef.current = next;
    setDrag(next);

    const onMouseMove = (moved: MouseEvent) => {
      const current = dragRef.current;
      if (!current) return;
      const steps = dayStep(moved.clientX - current.originX, range);
      const hoverDay = dayAtPx(chartX(moved.clientX), range);
      // The guard that makes state-per-drag affordable: between day boundaries
      // nothing has changed, so nothing re-renders.
      if (steps === current.steps && hoverDay === current.hoverDay) return;
      if (steps !== 0 || current.mode === "create") travelledRef.current = true;
      const updated = { ...current, steps, hoverDay };
      dragRef.current = updated;
      setDrag(updated);
    };

    const onMouseUp = () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      const finished = dragRef.current;
      dragRef.current = null;
      setDrag(null);
      if (finished) commit(finished);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }

  /** One gesture → at most one PATCH. A drag that changed nothing writes nothing. */
  function commit(finished: DragState) {
    const task = taskById.get(finished.taskId);
    if (!task) return;

    if (finished.mode === "create") {
      if (!finished.anchorDay || !finished.hoverDay) return;
      onMove(task, spanFor(finished.anchorDay, finished.hoverDay));
      return;
    }

    if (finished.steps === 0) return;
    const span = interval(task);
    if (!span) return;

    if (finished.mode === "move") {
      const from = anchorDay(task);
      if (!from) return;
      const patch = rescheduleTo(task, shiftDay(from, finished.steps));
      if (patch) onMove(task, patch as Record<string, string | null>);
      return;
    }
    if (finished.mode === "start") {
      const patch = resizeStart(task, shiftDay(span.from, finished.steps));
      if (patch) onMove(task, patch);
      return;
    }
    const patch = resizeEnd(task, shiftDay(span.to, finished.steps));
    if (patch) onMove(task, patch);
  }

  /** Refuse a drag the geometry cannot express, and say why. */
  function tryDrag(
    event: React.MouseEvent,
    taskId: string,
    mode: DragMode,
    drawnBar: Bar | null,
  ) {
    const refusal = dragRefusal(drawnBar);
    if (refusal) {
      event.preventDefault();
      onRefuse(refusal);
      return;
    }
    beginDrag(event, taskId, mode);
  }

  // ── Dragging a dependency ───────────────────────────────────────────────

  function beginLink(event: React.MouseEvent, fromId: string) {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const el = scrollRef.current;
    if (!el) return;
    boxRef.current = el.getBoundingClientRect();
    const box = boxRef.current;

    const point = (moved: { clientX: number; clientY: number }) => ({
      x: chartX(moved.clientX),
      y: moved.clientY - box.top - HEAD_H + el.scrollTop,
    });

    const next: LinkState = { fromId, ...point(event), overId: null };
    linkRef.current = next;
    setLink(next);

    const onMouseMove = (moved: MouseEvent) => {
      const current = linkRef.current;
      if (!current) return;
      const updated = { ...current, ...point(moved) };
      linkRef.current = updated;
      setLink(updated);
    };

    const onMouseUp = () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
      const finished = linkRef.current;
      linkRef.current = null;
      setLink(null);
      if (finished?.overId) applyLink(finished.fromId, finished.overId);
    };

    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  }

  function applyLink(blockerId: string, blockedId: string) {
    const verdict = canLink(blockerId, blockedId, links);
    if (!verdict.ok) {
      onRefuse(verdict.reason);
      return;
    }
    onLink(blockerId, blockedId);
  }

  /** The row the link drag is currently over, tracked from the rows themselves. */
  function hoverLink(taskId: string | null) {
    const current = linkRef.current;
    if (!current || current.overId === taskId) return;
    const updated = { ...current, overId: taskId };
    linkRef.current = updated;
    setLink(updated);
  }

  if (drawn.length === 0) {
    return (
      <p className="p-6 text-sm text-muted-foreground">
        No tasks to display.
        {undated > 0 ? ` ${undated} have no start or due date.` : ""}
      </p>
    );
  }

  const busy = drag !== null || link !== null;

  return (
    <div className="flex flex-col gap-2 p-3">
      <header className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">Timeline</span>

        {/* Zoom. A segmented control rather than Plane's dropdown: three fixed
            options whose whole job is to be swapped between while you look at
            the chart, so hiding two of them behind a click is the wrong trade. */}
        <div
          role="group"
          aria-label="Zoom"
          className="ml-1 flex items-center gap-0.5 rounded-lg border border-border p-0.5"
        >
          {ZOOM_ORDER.map((key) => (
            <Button
              key={key}
              variant={zoom === key ? "primary" : "ghost"}
              size="sm"
              aria-pressed={zoom === key}
              onClick={() => onZoom?.(key)}
            >
              {ZOOMS[key].label}
            </Button>
          ))}
        </div>

        {todayPx !== null ? (
          <Button
            variant="secondary"
            size="sm"
            icon="Crosshair"
            onClick={() => scrollToDay(todayKey)}
          >
            Today
          </Button>
        ) : null}

        <span className="ml-auto text-xs text-muted-foreground">
          Drag a bar to move it, an edge to change one date, or the dot on its
          right onto another bar to say it blocks that one.
        </span>
      </header>

      {undated > 0 || truncated ? (
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {undated > 0 ? (
            <span className="text-muted-foreground">
              {undated} unscheduled — drag across an empty row to give one dates.
            </span>
          ) : null}
          {truncated ? (
            <span className="font-medium text-destructive">
              Too many tasks to show them all — narrow the filters.
            </span>
          ) : null}
        </div>
      ) : null}

      {/* ⚠️ `isolate` is load-bearing, not decoration.
          A Gantt needs real stacking INSIDE itself — a sticky task column over
          a sticky header over bars over arrows — and those z-indexes climb to
          30. Without a stacking context of its own they all land in the ROOT
          one, where they outrank every dropdown in the app: the Fields menu
          (`FilterBar`, z-20) rendered *underneath* the chart's header, which is
          what the owner saw. `isolation: isolate` confines the whole ladder, so
          the chart stacks correctly against itself and as a single flat layer
          against everything else. */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className={`isolate overflow-auto rounded-lg border border-border ${
          busy ? "select-none" : ""
        }`}
        style={{ maxHeight: "68vh" }}
      >
        {/* `w-full` with a minWidth, not a bare minWidth: below it the row
            stretches to the card and the header, shading and borders reach the
            right edge. Without it the chart stopped wherever the data did and
            left the rest of the card blank. */}
        <div
          className="flex w-full"
          style={{ minWidth: LEFT_COL + range.widthPx }}
        >
          {/* Task column — sticky, so the names stay while the chart scrolls. */}
          <div
            className="sticky left-0 z-30 shrink-0 border-r border-border bg-card"
            style={{ width: LEFT_COL }}
          >
            <div
              className="sticky top-0 z-10 flex items-end border-b border-border bg-muted px-3 pb-1.5 text-xs font-medium text-muted-foreground"
              style={{ height: HEAD_H }}
            >
              Task
            </div>
            {drawn.map((row, index) =>
              row.kind === "band" ? (
                <BandHeading
                  key={`band-${row.band.key}`}
                  band={row.band}
                  collapsed={row.collapsed}
                  accent={accentForGroup(
                    groupBy,
                    row.band.key,
                    bands.findIndex((b) => b.key === row.band.key),
                    bands.length,
                    statuses
                  )}
                  onToggle={() =>
                    setFolded((current) => {
                      const next = new Set(current);
                      if (!next.delete(row.band.key)) next.add(row.band.key);
                      return next;
                    })
                  }
                />
              ) : (
              <div
                key={row.task.id}
                onMouseEnter={() => setHoverRow(row.task.id)}
                onMouseLeave={() => setHoverRow((c) => (c === row.task.id ? null : c))}
                className={`flex items-center gap-1 border-b border-border/50 px-2 ${
                  hoverRow === row.task.id ? "bg-muted/50" : ""
                }`}
                style={{
                  height: ROW_H,
                  // Indented under its band heading, so the rail reads as a
                  // hierarchy rather than a flat list with dividers in it.
                  paddingLeft: 8 + row.depth * 14 + (grouped ? 12 : 0),
                }}
              >
                {row.hasKids ? (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    icon={expanded.has(row.task.id) ? "ChevronDown" : "ChevronRight"}
                    aria-label={
                      expanded.has(row.task.id)
                        ? `Collapse ${row.task.title}`
                        : `Expand ${row.task.title}`
                    }
                    onClick={() =>
                      setExpanded((current) => {
                        const next = new Set(current);
                        if (next.has(row.task.id)) next.delete(row.task.id);
                        else next.add(row.task.id);
                        return next;
                      })
                    }
                  />
                ) : (
                  <span className="w-5 shrink-0" />
                )}
                <button
                  type="button"
                  onClick={() => onSelect(row.task)}
                  className="min-w-0 flex-1 truncate text-left text-xs text-foreground hover:underline"
                >
                  {row.task.title}
                </button>
                {/* The meta column. `shrink-0` against the title's `flex-1`,
                    so the NAME gives way first — a truncated title is still
                    recognisable, a truncated date is a lie. */}
                <div className="shrink-0">
                  <TaskMeta
                    chips={visibleChips(
                      row.task,
                      shownFields,
                      undefined,
                      tagHues
                    ).filter((chip) => RAIL_CHIPS.includes(chip.key))}
                  />
                </div>
              </div>
              )
            )}
          </div>

          {/* Chart */}
          <div className="relative flex-1" style={{ minWidth: range.widthPx }}>
            {/* Two tiers: months, then days or weeks depending on the zoom.
                One tier could not say both "which month" and "which day", and a
                Gantt you cannot date by eye is a picture of some bars. */}
            <div
              className="sticky top-0 z-20 border-b border-border bg-muted"
              style={{ height: HEAD_H }}
            >
              <div className="relative" style={{ height: HEAD_H / 2 }}>
                {months.map((cell) => (
                  <div
                    key={cell.key}
                    className="absolute top-0 flex h-full items-center border-r border-border/60 px-2 text-xs font-medium text-foreground"
                    style={{ left: cell.px, width: cell.widthPx }}
                  >
                    {cell.widthPx > 56 ? cell.label : ""}
                  </div>
                ))}
              </div>
              <div
                className="relative border-t border-border/40"
                style={{ height: HEAD_H / 2 }}
              >
                {ZOOMS[zoom].tier === "day"
                  ? days.map((cell) => (
                      <div
                        key={cell.day}
                        className={`absolute top-0 flex h-full flex-col items-center justify-center border-r border-border/40 text-[10px] leading-none ${
                          cell.today
                            ? "font-semibold text-primary"
                            : cell.weekend
                              ? "text-muted-foreground/60"
                              : "text-muted-foreground"
                        }`}
                        style={{ left: cell.px, width: cell.widthPx }}
                      >
                        <span>{cell.label}</span>
                        <span className="mt-0.5">{cell.date}</span>
                      </div>
                    ))
                  : weeks.map((cell) => (
                      <div
                        key={cell.key}
                        className={`absolute top-0 flex h-full items-center justify-center border-r border-border/40 text-[10px] ${
                          cell.today ? "font-semibold text-primary" : "text-muted-foreground"
                        }`}
                        style={{ left: cell.px, width: cell.widthPx }}
                      >
                        {cell.widthPx > 18 ? cell.label : ""}
                      </div>
                    ))}
              </div>
            </div>

            <div className="relative" style={{ height }}>
              {/* Column shading, under everything. Weekends only where a
                  column is wide enough to read as a column rather than a
                  stripe — at quarter zoom a 7px band is visual noise. */}
              {range.pxPerDay >= 12
                ? days
                    .filter((cell) => cell.weekend)
                    .map((cell) => (
                      <div
                        key={cell.day}
                        className="absolute top-0 bg-muted/40"
                        style={{ left: cell.px, width: cell.widthPx, height }}
                      />
                    ))
                : null}
              {/* Week rules give the eye something to measure against when the
                  day columns are too narrow to draw. */}
              {range.pxPerDay < 12
                ? days
                    .filter((cell) => cell.weekStart)
                    .map((cell) => (
                      <div
                        key={cell.day}
                        className="absolute top-0 w-px bg-border/40"
                        style={{ left: cell.px, height }}
                      />
                    ))
                : null}

              {todayPx !== null ? (
                <div
                  className="absolute top-0 z-0 bg-primary/50"
                  style={{ left: todayPx, width: Math.max(2, range.pxPerDay), height }}
                  title="Today"
                />
              ) : null}

              {/* Arrows, under the bars so a bar is never un-clickable. */}
              <svg
                className="pointer-events-none absolute inset-0 z-10"
                width={range.widthPx}
                height={height}
                aria-hidden
              >
                <defs>
                  <marker
                    id="pm-arrow"
                    markerWidth="6"
                    markerHeight="6"
                    refX="5"
                    refY="3"
                    orient="auto"
                  >
                    <path d="M0,0 L6,3 L0,6 z" className="fill-muted-foreground" />
                  </marker>
                  <marker
                    id="pm-arrow-bad"
                    markerWidth="6"
                    markerHeight="6"
                    refX="5"
                    refY="3"
                    orient="auto"
                  >
                    <path d="M0,0 L6,3 L0,6 z" className="fill-destructive" />
                  </marker>
                  {/* A marker cannot inherit the stroke of the path that uses
                      it, so a lit edge needs its own head or the line brightens
                      and its arrow stays grey. */}
                  <marker
                    id="pm-arrow-lit"
                    markerWidth="6"
                    markerHeight="6"
                    refX="5"
                    refY="3"
                    orient="auto"
                  >
                    <path d="M0,0 L6,3 L0,6 z" className="fill-primary" />
                  </marker>
                </defs>
                {/* ── The aim corridor, and why it is its OWN layer ──────────
                    Wide enough to travel ALONG the aimed arrow, not merely to
                    point at it: the remove control sits half way along a
                    dogleg, so the route to it cuts corners and a 12px stroke
                    loses the pointer part-way.

                    ⚠️ It is drawn BEFORE every edge group, so it sits UNDER
                    all the narrow hit strokes. That layering is the whole
                    trick. Rendered inside the aimed edge's own group it was on
                    top — `edgeHitOrder` puts that group last — and a 44px
                    invisible stroke then buried the neighbouring arrow, which
                    could no longer be aimed at all (measured, 2026-09-05).
                    Underneath, precision still wins: land on any arrow's real
                    stroke and that arrow takes the aim, and the corridor only
                    catches the pointer where no stroke is. */}
                {(() => {
                  if (!onUnlink || !hoverEdge) return null;
                  const edge = links.find((e) => e.id === hoverEdge);
                  if (!edge) return null;
                  const from = indexById.get(edge.blocker_id);
                  const to = indexById.get(edge.blocked_id);
                  if (from === undefined || to === undefined) return null;
                  const pts = edgePoints(
                    { bar: barById.get(edge.blocker_id) ?? null, row: from },
                    { bar: barById.get(edge.blocked_id) ?? null, row: to },
                  );
                  if (!pts) return null;
                  return (
                    <path
                      d={roundedPath(pts)}
                      fill="none"
                      stroke="transparent"
                      strokeWidth={AIM_KEEP_PX}
                      style={{ pointerEvents: "stroke", cursor: "pointer" }}
                      onMouseEnter={() => aimEdge(edge.id)}
                      onMouseLeave={() => releaseEdge(edge.id)}
                    />
                  );
                })()}
                {/* ⚠️ Ordered, not raw. The aimed edge must paint LAST or it
                    loses the pointer to whichever overlapping edge happens to
                    sit later in `links` — see `edgeHitOrder`. */}
                {edgeHitOrder(links, hoverEdge).map((edge) => {
                  const from = indexById.get(edge.blocker_id);
                  const to = indexById.get(edge.blocked_id);
                  if (from === undefined || to === undefined) return null;
                  const points = edgePoints(
                    { bar: barById.get(edge.blocker_id) ?? null, row: from },
                    { bar: barById.get(edge.blocked_id) ?? null, row: to },
                  );
                  if (!points) return null;
                  const d = roundedPath(points);
                  const mid = edgeMidpoint(points);
                  const blocker = taskById.get(edge.blocker_id);
                  const blocked = taskById.get(edge.blocked_id);
                  const bad =
                    !!blocker && !!blocked && conflicts(blocker, blocked);
                  /**
                   * Hovering a row lifts ITS edges and fades the rest.
                   *
                   * Rounded corners make one arrow followable; they do nothing
                   * for six crossing arrows, where the problem is not the
                   * corner but that every line looks equally important. Hover
                   * answers the question actually being asked — "what does
                   * THIS depend on" — without a mode, a click or a legend.
                   */
                  const aimed = hoverEdge === edge.id;
                  const lit =
                    aimed ||
                    hoverRow === edge.blocker_id ||
                    hoverRow === edge.blocked_id;
                  const dimmed = hoverRow !== null && !lit;
                  return (
                    <g key={edge.id}>
                      {/*
                        ⚠️ THE HIT TARGET, and the reason this feature works at
                        all. The visible line below is 1.5px wide, which is not
                        something a person can reliably point at. This invisible
                        twin carries the same path at EDGE_HIT_PX and takes the
                        pointer instead.

                        The parent <svg> is `pointer-events-none` so a bar is
                        never un-clickable through the arrow layer. This child
                        opts back in, and only on its STROKE — `fill` is none,
                        but "auto" would still hand it the whole bounding box,
                        which for a dogleg is a rectangle covering both rows.
                      */}
                      {onUnlink ? (
                        <path
                          d={d}
                          fill="none"
                          stroke="transparent"
                          strokeWidth={EDGE_HIT_PX}
                          style={{ pointerEvents: "stroke", cursor: "pointer" }}
                          onMouseEnter={() => aimEdge(edge.id)}
                          onMouseLeave={() => releaseEdge(edge.id)}
                        />
                      ) : null}
                      <path
                        d={d}
                        fill="none"
                        strokeWidth={bad || lit ? 2 : 1.5}
                        // Rounded joins as well as rounded corners: the arrowhead
                        // sits on a butt end otherwise and reads as a notch.
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        opacity={dimmed ? 0.25 : 1}
                        pointerEvents="none"
                        className={`transition-opacity ${
                          bad ? "stroke-destructive" : lit ? "stroke-primary" : "stroke-muted-foreground"
                        }`}
                        markerEnd={`url(#${
                          bad ? "pm-arrow-bad" : lit ? "pm-arrow-lit" : "pm-arrow"
                        })`}
                      />
                      {/* The approach corridor.

                          `edgeHitOrder` keeps the aimed edge on top along the
                          path, which covers the journey. This covers the
                          ARRIVAL: the control sits ON the line, so the last few
                          pixels are exactly where a second arrow's stroke is
                          most likely to be, and stepping off the aimed stroke
                          there would hand the pointer away one pixel before the
                          target.

                          Hover only — deliberately NOT inside the group that
                          carries `onClick`. A 22px halo that deletes a
                          dependency is a mis-click waiting to happen; this one
                          only keeps the edge aimed, and the control below stays
                          the only thing that removes anything. */}
                      {onUnlink && aimed && mid ? (
                        <circle
                          cx={mid.x}
                          cy={mid.y}
                          r={CONTROL_SHIELD_PX}
                          fill="transparent"
                          style={{ pointerEvents: "all" }}
                          onMouseEnter={() => aimEdge(edge.id)}
                          onMouseLeave={() => releaseEdge(edge.id)}
                        />
                      ) : null}
                      {onUnlink && aimed && mid ? (
                        <g
                          transform={`translate(${mid.x} ${mid.y})`}
                          style={{ pointerEvents: "auto", cursor: "pointer" }}
                          /*
                            It keeps its OWN hover alive. Moving onto the control
                            leaves the stroke, and without this the control
                            unmounts from under the cursor on the way to it.
                          */
                          onMouseEnter={() => aimEdge(edge.id)}
                          onMouseLeave={() => releaseEdge(edge.id)}
                          onClick={(event) => {
                            event.stopPropagation();
                            onUnlink(edge.blocker_id, edge.id);
                          }}
                        >
                          <title>Remove this dependency</title>
                          <circle
                            r={8}
                            className="fill-background stroke-destructive"
                            strokeWidth={1.5}
                          />
                          <path
                            d="M-3 -3 L3 3 M3 -3 L-3 3"
                            className="stroke-destructive"
                            strokeWidth={1.5}
                            strokeLinecap="round"
                          />
                        </g>
                      ) : null}
                    </g>
                  );
                })}
                {/* The rubber band. Drawn to the cursor, so a link you are
                    halfway through making looks like the thing it will become. */}
                {link ? (
                  <LinkPreview link={link} barById={barById} indexById={indexById} />
                ) : null}
              </svg>

              {drawn.map((row, index) => {
                if (row.kind === "band") {
                  return (
                    <BandLane
                      key={`band-${row.band.key}`}
                      band={row.band}
                      collapsed={row.collapsed}
                      top={index * ROW_H}
                      range={range}
                    />
                  );
                }
                const drawnBar = barById.get(row.task.id) ?? null;
                const blocker = links.find((l) => l.blocked_id === row.task.id);
                const blockerTask = blocker
                  ? taskById.get(blocker.blocker_id)
                  : undefined;
                const bad = !!blockerTask && conflicts(blockerTask, row.task);
                const isDragging = drag?.taskId === row.task.id;
                const isLinkTarget = link?.overId === row.task.id;
                return (
                  <div
                    key={row.task.id}
                    className={`absolute inset-x-0 border-b border-border/50 ${
                      isLinkTarget
                        ? "bg-primary/10 ring-1 ring-inset ring-primary/40"
                        : hoverRow === row.task.id
                          ? "bg-muted/40"
                          : ""
                    }`}
                    style={{ top: index * ROW_H, height: ROW_H }}
                    onMouseEnter={() => {
                      setHoverRow(row.task.id);
                      hoverLink(row.task.id);
                    }}
                    onMouseLeave={() => {
                      setHoverRow((c) => (c === row.task.id ? null : c));
                      hoverLink(null);
                    }}
                    // An unscheduled row is a drawing surface: drag across it
                    // to give the task the dates it never had.
                    onMouseDown={(event) => {
                      if (drawnBar) return;
                      const day = dayAtPx(chartX(event.clientX), range);
                      beginDrag(event, row.task.id, "create", day);
                    }}
                  >
                    {/* Plane's `block-row.tsx` marker: a bar scrolled out of
                        sight leaves an arrow at the edge it went past, and
                        clicking it brings the bar back. Without it a row whose
                        bar is off screen is indistinguishable from a row with
                        no bar at all — which is exactly what "the task
                        disappeared" feels like. Placed at the viewport's own
                        left edge in chart coordinates, since `scrollLeft` is
                        already known. */}
                    {drawnBar && viewport.width > 0 ? (
                      <OffscreenMarker
                        drawnBar={drawnBar}
                        viewport={viewport}
                        onGo={() => {
                          const span = interval(row.task);
                          if (span) scrollToDay(span.from);
                        }}
                      />
                    ) : null}

                    {drawnBar ? (
                      <TimelineBar
                        task={row.task}
                        drawnBar={drawnBar}
                        // The chart's own width, so a narrow bar's label can be
                        // kept inside it. Without this the label ran off the
                        // end — see `labelSide`.
                        canvasPx={range.widthPx}
                        bad={bad}
                        blockerTitle={blockerTask?.title}
                        dragging={isDragging ? drag : null}
                        hovered={hoverRow === row.task.id}
                        // A drag that travelled must not ALSO open the panel
                        // over the chart you were just arranging.
                        onOpen={() => {
                          if (!travelledRef.current) onSelect(row.task);
                        }}
                        onGrab={(event, mode) =>
                          tryDrag(event, row.task.id, mode, drawnBar)
                        }
                        onLinkFrom={(event) => beginLink(event, row.task.id)}
                      />
                    ) : drag?.taskId === row.task.id && drag.mode === "create" ? (
                      <GhostBar drag={drag} range={range} />
                    ) : (
                      <span
                        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground"
                      >
                        Drag here to schedule
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        A red arrow means a task starts before the thing blocking it is due to
        finish. Nothing is rescheduled automatically — the dates stay yours.
      </p>
    </div>
  );
}

/**
 * Where a bar sits mid-drag.
 *
 * The clamps live in `resizeStart`/`resizeEnd`, and this has to agree with them
 * or the bar snaps on release to somewhere the cursor never was. So it applies
 * the same two rules: an edge stops at the other edge, and a bar keeps its span
 * when it moves.
 */
function previewBar(
  task: TaskRow,
  settled: Bar | null,
  drag: DragState,
  range: ReturnType<typeof timelineRange>,
): Bar | null {
  const span = interval(task);
  if (!span || !settled || settled.derived) return settled;
  if (drag.mode === "create") return settled;

  if (drag.mode === "move") {
    return barForSpan(
      shiftDay(span.from, drag.steps),
      shiftDay(span.to, drag.steps),
      range,
    );
  }
  if (drag.mode === "start") {
    const moved = shiftDay(span.from, drag.steps);
    return barForSpan(moved > span.to ? span.to : moved, span.to, range);
  }
  const moved = shiftDay(span.to, drag.steps);
  return barForSpan(span.from, moved < span.from ? span.from : moved, range);
}

/**
 * A band's heading, in the rail (WS-27t S5).
 *
 * The /tasks and list grammar, so a grouped timeline and a grouped list read
 * identically: chevron, accent dot, label, count as a pill. The accent comes
 * from `accentForGroup` — the same call the list makes — because a grouped
 * list and a grouped timeline are two drawings of one grouping and must not
 * disagree about what colour "Done" is.
 */
function BandHeading({
  band,
  collapsed,
  accent,
  onToggle,
}: {
  band: TimelineBand;
  collapsed: boolean;
  accent: { soft: string; bar: string; text: string; dot: string };
  onToggle: () => void;
}) {
  return (
    <div
      className={`flex items-center border-b border-border/50 ${accent.soft}`}
      style={{ height: ROW_H }}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        className={`flex min-w-0 flex-1 items-center gap-2 border-l-2 px-2 text-left text-xs font-medium ${accent.bar} ${accent.text}`}
        style={{ height: ROW_H }}
      >
        <Icon
          name="ChevronRight"
          className={`h-3.5 w-3.5 shrink-0 transition-transform ${
            collapsed ? "" : "rotate-90"
          }`}
        />
        <span className={`h-2 w-2 shrink-0 rounded-full ${accent.dot}`} />
        <span className="truncate">{band.label}</span>
        <span className="shrink-0 rounded-full bg-background/60 px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
          {band.count}
        </span>
      </button>
    </div>
  );
}

/**
 * A band's row on the CHART.
 *
 * Open, it is a tinted spacer that lines the heading up with the grid.
 *
 * Collapsed, it draws the band's whole span as one bar — MS Project's summary
 * task. Collapsing is otherwise pure loss: the rows go and nothing takes their
 * place. A roll-up makes it the summarising gesture it should be, so twelve
 * rows become one line that still says when the group runs.
 *
 * The bar is deliberately inert. It is a reading of other tasks' dates, not a
 * thing with dates of its own — the same argument that stops a `derived`
 * parent bar being dragged, one level up.
 */
function BandLane({
  band,
  collapsed,
  top,
  range,
}: {
  band: TimelineBand;
  collapsed: boolean;
  top: number;
  range: ReturnType<typeof timelineRange>;
}) {
  const rolled =
    collapsed && band.span
      ? barForSpan(band.span.from, band.span.to, range)
      : null;
  return (
    <div
      className="absolute inset-x-0 border-b border-border/50 bg-muted/30"
      style={{ top, height: ROW_H }}
    >
      {rolled ? (
        <div
          className="pointer-events-none absolute rounded-md border border-primary/50 bg-primary/15"
          style={{
            left: rolled.leftPx,
            width: rolled.widthPx,
            height: 8,
            top: (ROW_H - 8) / 2,
          }}
          title={`${band.label}: ${band.span?.from} to ${band.span?.to}`}
        />
      ) : null}
    </div>
  );
}

/**
 * "Your bar is that way" — and clicking it goes there.
 *
 * Renders nothing while the bar is even partly visible, so it costs a
 * comparison per row per scroll frame and no DOM.
 */
function OffscreenMarker({
  drawnBar,
  viewport,
  onGo,
}: {
  drawnBar: Bar;
  viewport: { left: number; width: number };
  onGo: () => void;
}) {
  const right = drawnBar.leftPx + drawnBar.widthPx;
  const offLeft = right < viewport.left;
  const offRight = drawnBar.leftPx > viewport.left + viewport.width;
  if (!offLeft && !offRight) return null;
  return (
    <button
      type="button"
      onClick={onGo}
      title="Scroll to this task's dates"
      className="absolute top-1/2 z-20 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-md border border-border bg-card text-muted-foreground hover:text-foreground"
      style={{
        left: offLeft
          ? viewport.left + 8
          : viewport.left + viewport.width - 32,
      }}
    >
      <Icon name={offLeft ? "ArrowLeft" : "ArrowRight"} size={13} />
    </button>
  );
}

/** The span a `create` drag has swept out so far. */
function GhostBar({
  drag,
  range,
}: {
  drag: DragState;
  range: ReturnType<typeof timelineRange>;
}) {
  if (!drag.anchorDay || !drag.hoverDay) return null;
  const [from, to] =
    drag.anchorDay <= drag.hoverDay
      ? [drag.anchorDay, drag.hoverDay]
      : [drag.hoverDay, drag.anchorDay];
  const ghost = barForSpan(from, to, range);
  return (
    <div
      className="pointer-events-none absolute rounded-md border border-dashed border-primary bg-primary/20"
      style={{
        left: ghost.leftPx,
        width: ghost.widthPx,
        height: BAR_H,
        top: (ROW_H - BAR_H) / 2,
      }}
    />
  );
}

/** The rubber-band line from a bar's right edge to the cursor. */
function LinkPreview({
  link,
  barById,
  indexById,
}: {
  link: LinkState;
  barById: Map<string, Bar | null>;
  indexById: Map<string, number>;
}) {
  const from = barById.get(link.fromId);
  const row = indexById.get(link.fromId);
  if (!from || row === undefined) return null;
  const x = from.leftPx + from.widthPx;
  const y = row * ROW_H + ROW_H / 2;
  return (
    <path
      d={`M ${x} ${y} L ${link.x} ${link.y}`}
      fill="none"
      strokeWidth={1.5}
      strokeDasharray="4 3"
      className="stroke-primary"
      markerEnd="url(#pm-arrow)"
    />
  );
}

/**
 * One bar: a body you can move, an edge at each end you can pull, and the
 * handle a dependency starts from.
 *
 * **The edges are wider than they look.** A 1px border is not a drag target,
 * so each handle is a 10px column straddling the edge with a 3px grip drawn
 * inside it — Plane's proportions, and the reason its resize feels reliable
 * while a hairline-bordered Gantt feels broken.
 *
 * **A narrow bar puts its title OUTSIDE.** A one-day task at month zoom is 24
 * pixels wide; a title truncated into that is an ellipsis, which is how the
 * first version of this view ended up as a row of unreadable coloured squares.
 * Below `LABEL_INSIDE_PX` the label moves to the right of the bar, where there
 * is always room.
 */
function TimelineBar({
  task,
  drawnBar,
  canvasPx,
  bad,
  blockerTitle,
  dragging,
  hovered,
  onOpen,
  onGrab,
  onLinkFrom,
}: {
  task: TaskRow;
  drawnBar: Bar;
  /** The chart's full width, which bounds where a floating label may sit. */
  canvasPx: number;
  bad: boolean;
  blockerTitle?: string;
  dragging: DragState | null;
  hovered: boolean;
  onOpen: () => void;
  onGrab: (event: React.MouseEvent, mode: DragMode) => void;
  onLinkFrom: (event: React.MouseEvent) => void;
}) {
  const inside = drawnBar.widthPx >= LABEL_INSIDE_PX;
  const title = bad && blockerTitle ? conflictLabel(blockerTitle) : task.title;
  const span = interval(task);
  const place = labelSide(drawnBar, canvasPx);

  const tone = drawnBar.derived
    ? "border border-dashed border-border bg-muted text-muted-foreground"
    : bad
      ? "border border-destructive bg-destructive/20 text-foreground"
      : "bg-primary/25 text-foreground hover:bg-primary/35";

  return (
    <>
      <div
        className={`group absolute z-10 ${dragging ? "z-20" : ""}`}
        style={{
          left: drawnBar.leftPx,
          width: drawnBar.widthPx,
          height: BAR_H,
          top: (ROW_H - BAR_H) / 2,
        }}
      >
        {/* The date being dragged to, beside the edge that is moving. Plane
            shows this and it is the difference between "I moved it about a
            week" and knowing the date you landed on. */}
        {dragging && span ? (
          <DatePill
            drag={dragging}
            span={span}
            side={dragging.mode === "start" ? "left" : "right"}
          />
        ) : null}

        {/* Left edge. */}
        {drawnBar.derived ? null : (
          <span
            role="presentation"
            onMouseDown={(event) => onGrab(event, "start")}
            title="Drag to change the start date"
            className="absolute -left-1.5 top-0 z-20 h-full w-3 cursor-col-resize"
          >
            <span
              className={`absolute left-1 top-1/2 h-4 w-1 -translate-y-1/2 rounded-full bg-foreground/50 transition-opacity ${
                hovered || dragging ? "opacity-100" : "opacity-0"
              }`}
            />
          </span>
        )}

        <button
          type="button"
          onMouseDown={(event) => {
            // Both gestures start here. A press that never travels is a click
            // and opens the task; one that does is a move and does not —
            // `onOpen` is the parent's guard on exactly that.
            if (!drawnBar.derived) onGrab(event, "move");
          }}
          onClick={onOpen}
          title={title}
          className={`flex h-full w-full items-center gap-1 overflow-hidden rounded-md px-2 text-left text-[11px] ${
            drawnBar.derived ? "cursor-pointer" : "cursor-grab"
          } ${dragging ? "cursor-grabbing ring-2 ring-ring" : ""} ${tone}`}
        >
          {bad ? (
            <Icon
              name="AlertTriangle"
              className="h-3 w-3 shrink-0 text-destructive"
            />
          ) : null}
          {/* The title, and nothing else. Metadata lives in the rail — see
              RAIL_CHIPS for why a chip strip and a duration-sized box cannot
              share a width. */}
          {inside ? <span className="truncate">{task.title}</span> : null}
        </button>

        {/* Right edge. */}
        {drawnBar.derived ? null : (
          <span
            role="presentation"
            onMouseDown={(event) => onGrab(event, "end")}
            title="Drag to change the due date"
            className="absolute -right-1.5 top-0 z-20 h-full w-3 cursor-col-resize"
          >
            <span
              className={`absolute right-1 top-1/2 h-4 w-1 -translate-y-1/2 rounded-full bg-foreground/50 transition-opacity ${
                hovered || dragging ? "opacity-100" : "opacity-0"
              }`}
            />
          </span>
        )}

        {/* The dependency handle. Only on a real bar: a derived one has no
            dates of its own, so a dependency drawn from it would be about days
            nobody typed. Sits outside the right edge, past the resize column,
            so the two gestures cannot be confused for one another. */}
        {drawnBar.derived ? null : (
          <span
            role="presentation"
            onMouseDown={onLinkFrom}
            title={`Drag onto another bar: "${task.title}" blocks it`}
            className={`absolute -right-4 top-1/2 z-20 h-2.5 w-2.5 -translate-y-1/2 cursor-crosshair rounded-full border border-primary bg-card transition-opacity ${
              hovered ? "opacity-100" : "opacity-0"
            }`}
          />
        )}
      </div>

      {/* The label for a bar too narrow to hold one.

          ⚠️ The plate (`bg-card`) is not decoration. An outgoing dependency
          leaves the bar's right edge at the row's mid-height — exactly where
          this label sits — so the line runs between the letters and reads as a
          STRIKETHROUGH on the task's own name. An opaque backing occludes it.
          Routing the arrow around the label instead would mean the geometry
          layer knowing how wide a piece of rendered text is.

          ⚠️ **It does NOT always go on the right.** This header used to say the
          right side is "where there is always room", and that is false at the
          end of the canvas. Measured at 1440 on 2026-09-05: "Landing page A/B:
          price framing" rendered 57px past the scroller's right edge and was
          clipped, because the label took `left: leftPx + widthPx + 8` with a
          `max-w` and no bound against the chart. A bar in the last stretch of
          the canvas is exactly the bar whose name you cannot otherwise read,
          so losing that one is the worst case, not the harmless one.

          So the side is chosen, and the width is capped by the room actually
          there. No text is measured — `labelSide` picks a side from the bar's
          own geometry, which is the arithmetic the comment above declines to
          do on rendered glyphs. */}
      {inside ? null : (
        <button
          type="button"
          onClick={onOpen}
          title={title}
          className={`absolute top-1/2 -translate-y-1/2 truncate rounded bg-card px-1 py-0.5 text-[11px] text-foreground hover:underline ${
            place.side === "right" ? "text-left" : "text-right"
          }`}
          style={
            place.side === "right"
              ? { left: place.offsetPx, maxWidth: place.maxWidthPx }
              : { right: place.offsetPx, maxWidth: place.maxWidthPx }
          }
        >
          {task.title}
        </button>
      )}
    </>
  );
}

const PILL_MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function prettyDay(key: string): string {
  const [, month, date] = key.split("-").map(Number);
  return `${PILL_MONTHS[(month ?? 1) - 1]} ${date}`;
}

/** The date the moving edge is currently on. */
function DatePill({
  drag,
  span,
  side,
}: {
  drag: DragState;
  span: { from: string; to: string };
  side: "left" | "right";
}) {
  const day =
    drag.mode === "start"
      ? shiftDay(span.from, drag.steps)
      : drag.mode === "end"
        ? shiftDay(span.to, drag.steps)
        : shiftDay(side === "left" ? span.from : span.to, drag.steps);
  return (
    <span
      className={`pointer-events-none absolute top-1/2 z-30 -translate-y-1/2 whitespace-nowrap rounded-md bg-foreground px-1.5 py-0.5 text-[10px] font-medium text-background ${
        side === "left" ? "right-full mr-2" : "left-full ml-2"
      }`}
    >
      {prettyDay(day)}
    </span>
  );
}
