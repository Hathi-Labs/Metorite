/**
 * The timeline, rendered.
 *
 * `AGENTS.md` says it plainly: nothing in this tree tests layout, and the
 * conformance suite checks eight regexes. So the drag affordances this view
 * exists for — an edge you can pull, a handle a dependency starts from, a bar
 * whose title is readable — are exactly the kind of thing that can disappear in
 * a refactor with every other test still green.
 *
 * Rendered through `react-dom/server`, which needs no DOM and so runs in the
 * node environment the suite already uses. That buys the structure and nothing
 * else: **this cannot test the drag itself**, which is mouse events against a
 * scroll container. The arithmetic behind every gesture is pure and lives in
 * `lib/timeline.test.ts`; what is left untested in between is the wiring from
 * one to the other, and that is a click-through, not a test.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { describe, expect, it } from "vitest";

import type { TaskRow } from "../lib/api";
import { fromDayKey } from "../lib/calendar";
import { LABEL_INSIDE_PX, PX_PER_DAY, windowFor } from "../lib/timeline";
import { TimelineView } from "./TimelineView";

const at = (key: string) => {
  const d = fromDayKey(key);
  d.setHours(12, 0, 0, 0);
  return d.toISOString();
};

const task = (over: Partial<TaskRow> = {}): TaskRow => ({
  id: "t1",
  project_id: "p1",
  root_project_id: "p1",
  status_id: "s1",
  title: "Landing page A/B",
  ...over,
});

const LONG = task({
  id: "long",
  title: "Press kit and spec sheet",
  start_date: "2026-08-10",
  due_at: at("2026-08-28"),
});
const SHORT = task({
  id: "short",
  title: "Launch video shot list",
  start_date: "2026-08-12",
  due_at: at("2026-08-12"),
});
const UNDATED = task({ id: "none", title: "Nothing scheduled" });

function draw(over: Record<string, unknown> = {}) {
  return renderToStaticMarkup(
    createElement(TimelineView, {
      tasks: [LONG, SHORT, UNDATED],
      links: [{ id: "e1", blocker_id: "long", blocked_id: "short" }],
      undated: 1,
      truncated: false,
      today: "2026-08-15",
      shownFields: [],
      onSelect: () => {},
      onMove: () => {},
      onLink: () => {},
      onRefuse: () => {},
      ...over,
    } as never)
  );
}

describe("TimelineView", () => {
  it("renders without throwing, with a row per task", () => {
    const svg = draw();
    for (const t of [LONG, SHORT, UNDATED]) expect(svg).toContain(t.title);
  });

  it("offers every zoom, with one of them pressed", () => {
    const html = draw();
    for (const label of ["Week", "Month", "Quarter"]) expect(html).toContain(label);
    // Exactly one — two pressed buttons is a segmented control that has lost
    // track of which option is live.
    expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
  });

  it("gives a datable bar BOTH resize edges and a dependency handle", () => {
    // The three affordances the view exists for. A refactor that drops one
    // leaves a timeline that still draws correctly and can no longer be used.
    const html = draw();
    expect(html).toContain("Drag to change the start date");
    expect(html).toContain("Drag to change the due date");
    expect(html).toContain("blocks it");
    expect(html).toContain("cursor-col-resize");
  });

  it("puts a NARROW bar's title beside it rather than inside", () => {
    // The defect this view was rebuilt to fix: a one-day task at month zoom is
    // 24px wide, and a title truncated into that is an ellipsis. The whole
    // chart then reads as a row of unlabelled coloured squares.
    expect(PX_PER_DAY).toBeLessThan(LABEL_INSIDE_PX);
    const html = draw({ tasks: [SHORT], links: [], undated: 0 });
    // Drawn outside the bar's own box, carrying its own offset.
    //
    // ⚠️ This pinned `style="left:\d+px"` with the quote immediately after,
    // which is the exact style string rather than the behaviour. Two things
    // changed under it on 2026-09-05, both deliberate: the label now also
    // carries a `max-width` capped to the room actually available, and it
    // takes a `right` instead of a `left` when it would otherwise run off the
    // end of the canvas (measured: 57px past, and clipped). The rule the test
    // states is still the right rule, so the assertion moved to the rule.
    expect(html).toMatch(/style="(left|right):\d+px[^"]*"[^>]*>Launch video shot list/);
    // And the cap is present, because "beside the bar" is only true while the
    // label is still on the chart.
    expect(html).toMatch(/max-width:\d+px[^"]*"[^>]*>Launch video shot list/);
  });

  it("keeps a WIDE bar's title inside it", () => {
    const html = draw({ tasks: [LONG], links: [], undated: 0 });
    expect(html).toContain("truncate");
    expect(html).not.toMatch(/style="left:\d+px"[^>]*>Press kit/);
  });

  it("invites a drag on an unscheduled row instead of naming the problem", () => {
    // "unscheduled" was the old text: it stated the fact and offered nothing.
    const html = draw();
    expect(html).toContain("Drag here to schedule");
  });

  it("refuses the drag handles on a DERIVED bar", () => {
    // A parent borrowing its children's span has no dates of its own, so there
    // is no field a drag could write. It still draws — it just cannot be pulled.
    const parent = task({ id: "p", title: "Launch week" });
    const kid = task({
      id: "k",
      title: "Kid",
      parent_task_id: "p",
      start_date: "2026-08-10",
      due_at: at("2026-08-20"),
    });
    const html = draw({ tasks: [parent, kid], links: [], undated: 0 });
    // The bar IS drawn, in the derived tone — asserted first, because
    // "carries no resize edge" passes for free on a row with no bar at all,
    // which is the vacuous version of this test.
    expect(html).toContain("border-dashed");
    expect(html).not.toContain("Drag here to schedule");
    // And it carries neither resize edge nor a dependency handle.
    expect(html).not.toContain("Drag to change the start date");
    expect(html).not.toContain("Drag to change the due date");
    expect(html).not.toContain("blocks it");
  });

  it("draws the dependency arrow between two bars", () => {
    const html = draw();
    expect(html).toContain("marker-end");
    expect(html).toContain("pm-arrow");
  });

  it("says the arrow warns and does not reschedule (D-PM-12)", () => {
    // The promise the whole drag feature had to keep. If this line goes, check
    // that the behaviour did not go with it.
    expect(draw()).toContain("Nothing is rescheduled automatically");
  });

  it("draws the whole FETCHED window, not just the dated span", () => {
    // The regression this guards: the timeline borrowed the calendar's
    // one-month window, so there was nowhere to drag a bar TO — past the last
    // dated day it left the scrollable area and vanished under the cursor —
    // and moving the earliest task slid every other bar sideways.
    const window = windowFor("month", "2026-08-15");
    const narrow = draw({ tasks: [SHORT], links: [], undated: 0 });
    const wide = draw({ tasks: [SHORT], links: [], undated: 0, window });

    const widthOf = (html: string) =>
      Number(/min-width:(\d+)px/.exec(html)?.[1] ?? 0);
    expect(widthOf(wide)).toBeGreaterThan(widthOf(narrow));
    // Wide enough to hold the window at the current zoom, so a drag has room.
    expect(widthOf(wide)).toBeGreaterThanOrEqual(240 * PX_PER_DAY);
  });

  it("draws a band heading per group, with its count", () => {
    const html = draw({
      groupBy: "status",
      groups: [
        { key: "s1", label: "In progress", tasks: [LONG, SHORT] },
        { key: "s2", label: "Done", tasks: [UNDATED] },
      ],
    });
    expect(html).toContain("In progress");
    expect(html).toContain("Done");
    // Counts come from the GROUP, so they agree with the board's for the same
    // group even when a subtask folds into its parent and draws no row.
    expect(html).toMatch(/In progress[\s\S]{0,200}>2</);
  });

  it("drops an empty band rather than heading nothing", () => {
    const html = draw({
      groupBy: "status",
      groups: [
        { key: "s1", label: "In progress", tasks: [LONG] },
        { key: "s2", label: "Nothing here", tasks: [] },
      ],
    });
    expect(html).toContain("In progress");
    expect(html).not.toContain("Nothing here");
  });

  it("draws no band at all when the axis is none", () => {
    // Grouping is a control the canvas HONOURS, not one it forces: the
    // ungrouped timeline must be exactly what it was before bands existed.
    const html = draw({ groupBy: "none", groups: [] });
    expect(html).toContain(LONG.title);
    expect(html).not.toContain("aria-expanded");
  });

  it("keeps arrows on the right rows once a heading takes one", () => {
    // The regression a band introduces: headings occupy a row index, so an
    // arrow placed from a task's index lands a row high for every heading
    // above it. Both bars still carry their dependency after grouping.
    const html = draw({
      groupBy: "status",
      groups: [{ key: "s1", label: "Everything", tasks: [LONG, SHORT] }],
    });
    expect(html).toContain("pm-arrow");
    // Heading at index 0 pushes the first task's bar to row 1, so no bar may
    // sit at the very top of the chart body.
    expect(html).not.toMatch(/class="absolute inset-x-0[^"]*"style="top:0px/);
  });

  it("reports a truncated window", () => {
    expect(draw({ truncated: true })).toContain("narrow the filters");
  });

  it("falls back to a plain message with no tasks at all", () => {
    expect(draw({ tasks: [], links: [], undated: 0 })).toContain("No tasks to display");
  });
});
