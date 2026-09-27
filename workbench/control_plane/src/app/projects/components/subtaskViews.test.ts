/**
 * D-PM-38 (Subtasks S3) — the Projects canvases, rendered.
 *
 * Rendered through `react-dom/server`, as `TimelineView.test.ts` does, so it
 * runs in the node environment. It reads the markup, and so it proves what is
 * DRAWN: which rows, in which order, and which carry the "↳ Parent" crumb.
 *
 * - The list and the table draw the same row set for the same input, in all
 *   three modes.
 * - The board in Separate mode draws each subtask card with its crumb, and in
 *   Hidden mode draws no subtask at all.
 * - A calendar chip for a subtask carries the crumb.
 */

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { StatusRow, TaskRow } from "../lib/api";
import { calendarGrid } from "../lib/calendar";
import { EMPTY_FILTERS, SUBTASK_MODES, type SubtaskMode } from "../lib/grouping";
import { CalendarView } from "./CalendarView";
import { TableView } from "./TableView";
import { TaskBoard } from "./TaskBoard";
import { TaskList } from "./TaskList";

const STATUS: StatusRow = {
  id: "s1",
  project_id: "p1",
  name: "To do",
  color: "#888888",
  position: 1,
  category: "todo",
  is_default: true,
} as StatusRow;

const task = (id: string, parent: string | null = null, over: Partial<TaskRow> = {}): TaskRow => ({
  id,
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: `Title ${id}`,
  parent_task_id: parent,
  parent: parent ? { id: parent, ref: null, title: `Title ${parent}` } : null,
  created_at: `2026-09-0${1 + ["parent", "child-1", "grandchild", "child-2", "solo", "orphan"].indexOf(id)}T00:00:00Z`,
  ...over,
});

/** A parent, two children, a grandchild, a lone task, and an orphan. */
const FAMILY = [
  task("parent"),
  task("child-1", "parent"),
  task("grandchild", "child-1"),
  task("child-2", "parent"),
  task("solo"),
  task("orphan", "elsewhere"),
];

const GROUPS = [{ key: "all", label: "All", tasks: FAMILY }];

/** Task ids in the order their title links are drawn. */
const drawnIds = (html: string): string[] =>
  [...html.matchAll(/href="\/projects\?task=([^"&]+)"/g)].map((m) => m[1]);

/** How many "↳ Parent" crumbs name this parent. */
const crumbsFor = (html: string, parentTitle: string): number =>
  html.split(`title="Subtask of ${parentTitle}"`).length - 1;

const noop = () => {};

function list(mode: SubtaskMode) {
  return renderToStaticMarkup(
    createElement(TaskList, {
      groups: GROUPS,
      groupBy: "none",
      subtasks: mode,
      filters: EMPTY_FILTERS,
      onClearFilters: noop,
      statuses: [STATUS],
      projectId: "p1",
      shownFields: [],
      onCreated: noop,
      onSelect: noop,
    }),
  );
}

function table(mode: SubtaskMode) {
  return renderToStaticMarkup(
    createElement(TableView, {
      groups: GROUPS,
      groupBy: "none",
      subtasks: mode,
      statuses: [STATUS],
      fields: [],
      shownFields: [],
      sort: null,
      onSort: noop,
      projectId: "p1",
      onCreated: noop,
      onSaved: noop,
      onSelect: noop,
    }),
  );
}

function board(mode: SubtaskMode) {
  return renderToStaticMarkup(
    createElement(TaskBoard, {
      groups: [{ key: "s1", label: "To do", tasks: FAMILY }],
      groupBy: "status",
      subtasks: mode,
      filters: EMPTY_FILTERS,
      onClearFilters: noop,
      lanes: { subGroupBy: "none", collapsedLanes: [], showEmptyLanes: false },
      onToggleLane: noop,
      onShowEmptyLanes: noop,
      statuses: [STATUS],
      projectId: "p1",
      shownFields: [],
      onCreated: noop,
      onSelect: noop,
      onDrop: noop,
    }),
  );
}

describe("the list and the table draw one row set", () => {
  it.each(SUBTASK_MODES)("in %s mode", (mode) => {
    const fromList = drawnIds(list(mode));
    const fromTable = drawnIds(table(mode));
    expect(fromList.length).toBeGreaterThan(0);
    expect(fromList).toEqual(fromTable);
  });

  it("Nested draws every level, and only the orphan carries a crumb", () => {
    const html = list("nested");
    expect(drawnIds(html)).toEqual([
      "parent", "child-1", "grandchild", "child-2", "solo", "orphan",
    ]);
    expect(crumbsFor(html, "Title elsewhere")).toBe(1);
    expect(crumbsFor(html, "Title parent")).toBe(0);
    expect(crumbsFor(table("nested"), "Title elsewhere")).toBe(1);
  });

  it("Separate draws flat rows, and each subtask names its parent", () => {
    for (const html of [list("separate"), table("separate")]) {
      expect(crumbsFor(html, "Title parent")).toBe(2);
      expect(crumbsFor(html, "Title child-1")).toBe(1);
      expect(crumbsFor(html, "Title elsewhere")).toBe(1);
    }
  });

  it("Hidden draws top-level rows only", () => {
    expect(drawnIds(list("hidden"))).toEqual(["parent", "solo"]);
  });

  it("the table's group heading counts the group, not the rows drawn (B7)", () => {
    const html = renderToStaticMarkup(
      createElement(TableView, {
        groups: [{ key: "s1", label: "To do", tasks: FAMILY }],
        groupBy: "status",
        subtasks: "nested",
        statuses: [STATUS],
        fields: [],
        shownFields: [],
        sort: null,
        onSort: noop,
        projectId: "p1",
        onCreated: noop,
        onSaved: noop,
        onSelect: noop,
      }),
    );
    expect(html).toContain('title="6 tasks in To do"');
  });
});

describe("the board", () => {
  it("Separate draws each subtask card with its crumb", () => {
    const html = board("separate");
    for (const id of ["child-1", "grandchild", "child-2", "orphan"]) {
      expect(html).toContain(`Title ${id}`);
    }
    expect(crumbsFor(html, "Title parent")).toBe(2);
    expect(crumbsFor(html, "Title child-1")).toBe(1);
    expect(crumbsFor(html, "Title elsewhere")).toBe(1);
    // The column counts the cards drawn, and says how many are subtasks.
    expect(html).toContain('title="6 tasks (4 subtasks) in To do"');
  });

  it("Hidden draws no subtask card and no crumb", () => {
    const html = board("hidden");
    expect(html).toContain("Title parent");
    expect(html).toContain("Title solo");
    for (const id of ["child-1", "grandchild", "child-2", "orphan"]) {
      expect(html).not.toContain(`Title ${id}`);
    }
    expect(html).not.toContain("Subtask of");
    expect(html).toContain('title="2 tasks in To do"');
  });
});

describe("the calendar", () => {
  const due = "2026-09-15T12:00:00Z";
  const draw = (mode: SubtaskMode) =>
    renderToStaticMarkup(
      createElement(CalendarView, {
        grid: calendarGrid("month", new Date(2026, 8, 15)),
        tasks: [task("parent", null, { due_at: due }), task("child-1", "parent", { due_at: due })],
        subtasks: mode,
        undated: 0,
        truncated: false,
        shownFields: [],
        projectId: "p1",
        onCreated: noop,
        onSelect: noop,
        onMove: noop,
        onStep: noop,
        onToday: noop,
        onLayout: noop,
        onRefuse: noop,
      }),
    );

  it("Separate draws the subtask chip with its crumb", () => {
    const html = draw("separate");
    expect(html).toContain("Title child-1");
    expect(crumbsFor(html, "Title parent")).toBe(1);
  });

  it("Hidden hides the subtask chip", () => {
    const html = draw("hidden");
    expect(html).toContain("Title parent");
    expect(html).not.toContain("Title child-1");
  });
});
