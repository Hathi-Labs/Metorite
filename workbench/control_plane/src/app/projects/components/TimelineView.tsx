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

import type { TagRow, TaskRow } from "../lib/api";
import { tagColours, visibleChips } from "../lib/card";
import { anchorDay, dayKey, rescheduleTo, shiftDay } from "../lib/calendar";
import {
  BAR_H,
  type Bar,
  type Edge,
  LABEL_INSIDE_PX,
  ROW_H,
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
  edgePath,
  interval,
  monthCells,
  resizeEnd,
  resizeStart,
  spanFor,
  timelineRange,
  timelineRows,
  weekCells,
} from "../lib/timeline";

const LEFT_COL = 280;
/** Two header tiers, each ROW_H/2-ish. Kept as one number the chart and the
 *  task column both read, so the two cannot start at different heights. */
const HEAD_H = 52;
/** Chips only earn their space on a bar this wide — below it they are the
 *  reason a one-day bar renders as an unreadable icon. */
const CHIPS_FROM_PX = 168;

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
  onSelect: (task: TaskRow) => void;
  /** S2 — a committed drag. The page's existing task-date write path. */
  onMove: (task: TaskRow, patch: Record<string, string | null>) => void;
  onLink: (blockerId: string, blockedId: string) => void;
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
  onSelect,
  onMove,
  onLink,
  onRefuse,
}: Props) {
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const [zoom, setZoom] = useState<TimelineZoom>("month");
  const [drag, setDrag] = useState<DragState | null>(null);
  const [link, setLink] = useState<LinkState | null>(null);
  const [hoverRow, setHoverRow] = useState<string | null>(null);
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
    () => timelineRange(rows, todayKey, zoom),
    [rows, todayKey, zoom]
  );

  // One flat list of drawn lines — parents, then their children when expanded —
  // so a row's index IS its y. Arrows are positioned from that index, and a
  // second traversal to find it would be a second chance to disagree.
  const drawn = useMemo(() => {
    const out: {
      task: TaskRow;
      children: TaskRow[];
      depth: number;
      hasKids: boolean;
    }[] = [];
    for (const row of rows) {
      out.push({
        task: row.task, children: row.children, depth: 0,
        hasKids: row.children.length > 0,
      });
      if (expanded.has(row.task.id)) {
        for (const kid of row.children) {
          out.push({ task: kid, children: [], depth: 1, hasKids: false });
        }
      }
    }
    return out;
  }, [rows, expanded]);

  const indexById = useMemo(
    () => new Map(drawn.map((row, i) => [row.task.id, i])),
    [drawn],
  );
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
  const scrollToDay = useCallback((day: string) => {
    const el = scrollRef.current;
    if (!el) return;
    const target = dayPx(day, range) - (el.clientWidth - LEFT_COL) / 2;
    el.scrollTo({ left: Math.max(0, target), behavior: "smooth" });
  }, [range]);

  // Open on today rather than on the range's left edge. A chart that opens
  // three months in the past is one whose first interaction is always a scroll.
  // Re-runs on zoom because the same day is at a different pixel at each.
  const openedAt = useRef<string | null>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || todayPx === null) return;
    const stamp = `${zoom}:${range.from}`;
    if (openedAt.current === stamp) return;
    openedAt.current = stamp;
    el.scrollLeft = Math.max(0, todayPx - (el.clientWidth - LEFT_COL) / 2);
  }, [zoom, range.from, todayPx]);

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
              onClick={() => setZoom(key)}
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

      <div
        ref={scrollRef}
        className={`overflow-auto rounded-lg border border-border ${
          busy ? "select-none" : ""
        }`}
        style={{ maxHeight: "68vh" }}
      >
        <div className="flex" style={{ minWidth: LEFT_COL + range.widthPx }}>
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
            {drawn.map((row) => (
              <div
                key={row.task.id}
                onMouseEnter={() => setHoverRow(row.task.id)}
                onMouseLeave={() => setHoverRow((c) => (c === row.task.id ? null : c))}
                className={`flex items-center gap-1 border-b border-border/50 px-2 ${
                  hoverRow === row.task.id ? "bg-muted/50" : ""
                }`}
                style={{ height: ROW_H, paddingLeft: 8 + row.depth * 14 }}
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
              </div>
            ))}
          </div>

          {/* Chart */}
          <div className="relative shrink-0" style={{ width: range.widthPx }}>
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
                </defs>
                {links.map((edge) => {
                  const from = indexById.get(edge.blocker_id);
                  const to = indexById.get(edge.blocked_id);
                  if (from === undefined || to === undefined) return null;
                  const d = edgePath(
                    { bar: barById.get(edge.blocker_id) ?? null, row: from },
                    { bar: barById.get(edge.blocked_id) ?? null, row: to },
                  );
                  if (!d) return null;
                  const blocker = taskById.get(edge.blocker_id);
                  const blocked = taskById.get(edge.blocked_id);
                  const bad =
                    !!blocker && !!blocked && conflicts(blocker, blocked);
                  return (
                    <path
                      key={edge.id}
                      d={d}
                      fill="none"
                      strokeWidth={bad ? 2 : 1.5}
                      className={bad ? "stroke-destructive" : "stroke-muted-foreground"}
                      markerEnd={`url(#${bad ? "pm-arrow-bad" : "pm-arrow"})`}
                    />
                  );
                })}
                {/* The rubber band. Drawn to the cursor, so a link you are
                    halfway through making looks like the thing it will become. */}
                {link ? (
                  <LinkPreview link={link} barById={barById} indexById={indexById} />
                ) : null}
              </svg>

              {drawn.map((row, index) => {
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
                    {drawnBar ? (
                      <TimelineBar
                        task={row.task}
                        drawnBar={drawnBar}
                        bad={bad}
                        blockerTitle={blockerTask?.title}
                        dragging={isDragging ? drag : null}
                        hovered={hoverRow === row.task.id}
                        shownFields={shownFields}
                        tagHues={tagHues}
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
  bad,
  blockerTitle,
  dragging,
  hovered,
  shownFields,
  tagHues,
  onOpen,
  onGrab,
  onLinkFrom,
}: {
  task: TaskRow;
  drawnBar: Bar;
  bad: boolean;
  blockerTitle?: string;
  dragging: DragState | null;
  hovered: boolean;
  shownFields: readonly string[];
  tagHues: ReturnType<typeof tagColours>;
  onOpen: () => void;
  onGrab: (event: React.MouseEvent, mode: DragMode) => void;
  onLinkFrom: (event: React.MouseEvent) => void;
}) {
  const inside = drawnBar.widthPx >= LABEL_INSIDE_PX;
  const chips = drawnBar.widthPx >= CHIPS_FROM_PX;
  const title = bad && blockerTitle ? conflictLabel(blockerTitle) : task.title;
  const span = interval(task);

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
          {inside ? <span className="truncate">{task.title}</span> : null}
          {chips ? (
            <TaskMeta
              chips={visibleChips(task, shownFields, undefined, tagHues)}
            />
          ) : null}
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

      {/* The label for a bar too narrow to hold one. */}
      {inside ? null : (
        <button
          type="button"
          onClick={onOpen}
          title={title}
          className="absolute top-1/2 max-w-[240px] -translate-y-1/2 truncate text-left text-[11px] text-foreground hover:underline"
          style={{ left: drawnBar.leftPx + drawnBar.widthPx + 8 }}
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
