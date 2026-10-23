// WS-64 OP-1 — the compact chrome of Projects (`one_projects_app.md` §5, §9).
//
// The runner has no DOM. The rules are called directly, and the components are
// rendered to a string. Done-when 2, 3 and 4 are fenced here, and the
// structure half of done-when 1.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ViewRow } from "../lib/api";
import {
  activeChips,
  compactChromeEnabled,
  filtersButtonLabel,
  foldedAgentText,
  loadedOfTotal,
  stepTab,
  swipeDirection,
  touchQuickActions,
  visibleAgentItems,
} from "../lib/compactChrome";
import { CATEGORY_LABEL, EDITABLE_CATEGORIES } from "@/lib/statusCategory";

import { EMPTY_FILTERS, NO_LANES, type Filters } from "../lib/grouping";
import { AgentWorkStrip } from "./AgentWorkStrip";
import { CompactFilterBar, type CompactFilterBarProps } from "./CompactFilterBar";
import { FilterSheetBody } from "./FilterSheet";
import { ViewSheetBody } from "./ViewSheet";

const noop = () => {};
const MODES = [
  { id: "board", icon: "Kanban" },
  { id: "list", icon: "List" },
  { id: "table", icon: "Table" },
  { id: "calendar", icon: "Calendar" },
] as const;

const view = { id: "v1", project_id: "p", name: "Mine", view_type: "board", config: {} } as ViewRow;
const tags = [
  { id: "t1", project_id: null, name: "Feature", color: "blue", task_count: 4 },
  { id: "t2", project_id: null, name: "Bug", color: "red", task_count: 2 },
];

function barProps(over: Partial<CompactFilterBarProps> = {}): CompactFilterBarProps {
  return {
    filters: EMPTY_FILTERS,
    onFilters: noop,
    mode: "board",
    spansProjects: true,
    groupBy: "status",
    onGroupBy: noop,
    lanes: NO_LANES,
    onSubGroupBy: noop,
    subtasks: "separate",
    onSubtasks: noop,
    me: "me@example.com",
    people: ["a@example.com"],
    tags,
    shownFields: [],
    onShownFields: noop,
    fields: [],
    views: [view],
    activeViewId: null,
    onApplyView: noop,
    onSaveView: noop,
    onDeleteView: noop,
    onUpdateView: noop,
    canSave: true,
    onExport: async () => {},
    waitingOnMe: false,
    onWaitingOnMe: noop,
    agentWork: false,
    onAgentWork: noop,
    phone: false,
    modes: MODES,
    onMode: noop,
    resultCount: 219,
    loadedLabel: null,
    agentSummary: null,
    agentFocus: null,
    onAgentFocus: noop,
    ...over,
  } as CompactFilterBarProps;
}

const html = (props: CompactFilterBarProps) =>
  renderToStaticMarkup(createElement(CompactFilterBar, props));

describe("the flag", () => {
  it("is off unless it is set", () => {
    expect(compactChromeEnabled({})).toBe(false);
    expect(compactChromeEnabled({ NEXT_PUBLIC_PROJECTS_COMPACT_CHROME: "0" })).toBe(false);
    expect(compactChromeEnabled({ NEXT_PUBLIC_PROJECTS_COMPACT_CHROME: "1" })).toBe(true);
    expect(compactChromeEnabled({ NEXT_PUBLIC_PROJECTS_COMPACT_CHROME: "on" })).toBe(true);
  });
});

describe("done-when 1: one tool row, the rest in the sheets", () => {
  const out = html(barProps());
  it("draws one tool row with a search field, Filters and View", () => {
    expect(out.match(/data-tool-row/g)).toHaveLength(1);
    expect(out).toContain('aria-label="Search tasks"');
    expect(out).toMatch(/>Filters</);
    expect(out).toMatch(/>View</);
  });
  it("draws no active row while no filter is on", () => {
    expect(out).not.toContain("data-active-row");
  });
  it("draws none of the sheet controls in the row", () => {
    for (const word of ["Overdue", "Watching", "Archived", "Group by", "Subtasks", "Export", "Fields"]) {
      expect(out, word).not.toContain(word);
    }
  });
  it("shows 'N of M' in the tool row only when the list is not whole", () => {
    expect(out).not.toContain("data-loaded-of-total");
    expect(html(barProps({ loadedLabel: loadedOfTotal(100, 219) }))).toContain("100 of 219");
    expect(loadedOfTotal(219, 219)).toBeNull();
  });
});

describe("done-when 2: two filters show as chips, and Filters reads 'Filters 2'", () => {
  const filters: Filters = { ...EMPTY_FILTERS, overdue: true, tags: ["Feature"] };
  const out = html(barProps({ filters }));
  it("draws a removable chip for each", () => {
    expect(out).toContain('aria-label="Remove filter Overdue"');
    expect(out).toContain('aria-label="Remove filter Feature"');
    expect(out).toContain("data-active-row");
  });
  it("names the button with the count", () => {
    expect(out).toMatch(/>Filters 2</);
    expect(filtersButtonLabel(0)).toBe("Filters");
    expect(filtersButtonLabel(2)).toBe("Filters 2");
  });
  it("a chip clears exactly its own filter", () => {
    const chips = activeChips({ filters, waitingOnMe: false, agentWork: false, me: "" });
    expect(chips.map((c) => c.label)).toEqual(["Overdue", "Feature"]);
    expect(chips[0].clear).toEqual({ kind: "filters", patch: { overdue: false } });
    expect(chips[1].clear).toEqual({ kind: "filters", patch: { tags: [] } });
  });
  it("counts the page toggles and the assignee, and never the search text", () => {
    const chips = activeChips({
      filters: { ...EMPTY_FILTERS, q: "abc", assignee: "me@example.com", statusCategory: EDITABLE_CATEGORIES[0] },
      waitingOnMe: true,
      agentWork: true,
      me: "me@example.com",
    });
    expect(chips.map((c) => c.label)).toEqual([CATEGORY_LABEL[EDITABLE_CATEGORIES[0]], "Me", "Waiting on me", "Agent work"]);
  });
});

describe("done-when 3: a count of zero never shows in the agent strip", () => {
  const summary = { queued: 0, working: 2, waiting_on_you: 1, in_review: 0, merged_this_week: 0 };
  it("hides the zero buttons", () => {
    const out = renderToStaticMarkup(
      createElement(AgentWorkStrip, { summary, focus: null, onFocus: noop, hideZero: true })
    );
    expect(out).toContain('data-agent-count="waiting"');
    expect(out).toContain('data-agent-count="working"');
    expect(out).not.toContain('data-agent-count="queued"');
    expect(out).not.toContain('data-agent-count="review"');
    expect(out).not.toContain('data-agent-count="merged"');
  });
  it("keeps a zero button that is the filter now on, so it can be turned off", () => {
    const items = visibleAgentItems(summary, "merged");
    expect(items.map((i) => i.focus)).toContain("merged");
  });
  it("draws nothing when every count is zero", () => {
    const zero = { queued: 0, working: 0, waiting_on_you: 0, in_review: 0, merged_this_week: 0 };
    expect(
      renderToStaticMarkup(
        createElement(AgentWorkStrip, { summary: zero, focus: null, onFocus: noop, hideZero: true })
      )
    ).toBe("");
    expect(foldedAgentText(zero)).toBe("");
  });
  it("folds to one chip with no zero in it, in the phone's active row", () => {
    expect(foldedAgentText(summary)).toBe("1 waiting · 2 working");
    const out = html(barProps({ phone: true, agentSummary: summary }));
    expect(out).toContain("1 waiting · 2 working");
    expect(out).not.toMatch(/\b0 (queued|merged|in review)/);
  });
  it("the desktop row has no folded chip", () => {
    expect(html(barProps({ phone: false, agentSummary: summary }))).not.toContain("1 waiting");
  });
});

describe("done-when 4: every control of the old FilterBar is reachable by role and name", () => {
  const filterSheet = renderToStaticMarkup(
    createElement(FilterSheetBody, {
      ...barProps(),
      archivedCount: 3,
      activeCount: 0,
      onClearAll: noop,
      onDone: noop,
    })
  );
  const viewSheet = renderToStaticMarkup(createElement(ViewSheetBody, { ...barProps() }));
  const row = html(barProps());
  // A saved view that the member has since changed: the dirty-view row appears.
  const dirty = renderToStaticMarkup(
    createElement(ViewSheetBody, {
      ...barProps({ filters: { ...EMPTY_FILTERS, overdue: true }, activeViewId: "v1" }),
    })
  );

  it("the filter controls are in the Filters sheet", () => {
    // [role, name] — a SelectButton is a button named by its label, a toggle is a
    // button with aria-pressed, and a tag is a button named by its tag.
    for (const name of ["Stage", "Assignee"]) expect(filterSheet, name).toContain(name);
    for (const name of ["Overdue", "Watching", "Waiting on me", "Agent work"]) {
      expect(filterSheet, name).toMatch(new RegExp(`aria-pressed="(true|false)"[^>]*>(<[^>]+>)*[^<]*${name}`));
    }
    expect(filterSheet).toContain('aria-label="Archived, 3 tasks"');
    expect(filterSheet).toContain("Feature");
    expect(filterSheet).toContain("Bug");
    expect(filterSheet).toMatch(/>Clear</);
    expect(filterSheet).toMatch(/>Show 219 tasks</);
  });
  it("the display controls are in the View sheet", () => {
    expect(viewSheet).toContain('aria-label="View mode"');
    for (const name of ["Board", "List", "Table", "Calendar"]) expect(viewSheet, name).toContain(name);
    for (const name of ["Group by", "Sub-group by (swimlanes)", "Subtasks"]) {
      expect(viewSheet, name).toContain(name);
    }
    expect(viewSheet).toMatch(/>Fields</);
    expect(viewSheet).toMatch(/>Export</);
    expect(viewSheet).toMatch(/>Save view</);
    expect(viewSheet).toContain("Mine");
    expect(viewSheet).toContain('aria-label="Delete view Mine"');
    expect(viewSheet).toContain("Lane counts and grouping describe what is loaded.");
  });
  it("the search field is in the tool row", () => {
    expect(row).toContain('aria-label="Search tasks"');
  });
  it("no label of the old bar is lost", () => {
    // Every name the old `FilterBar` gave a control, read from its source, must
    // be in the markup of the tool row or one of the sheets.
    const source = readFileSync(join(__dirname, "FilterBar.tsx"), "utf8");
    const names = new Set<string>();
    for (const m of source.matchAll(/label="([^"]+)"/g)) names.add(m[1]);
    for (const m of source.matchAll(/aria-label="([^"]+)"/g)) names.add(m[1]);
    for (const m of source.matchAll(/>\s*(Overdue|Watching|Waiting on me|Agent work|Archived|Fields|Export|Clear|Save view|Update view|Save as new|Reset)\s*</g)) {
      names.add(m[1]);
    }
    const all = row + filterSheet + viewSheet + dirty;
    // "View name" is the field of the naming form, which Save view opens. It is
    // the same `Input` as before and needs a click to appear.
    const missing = [...names].filter(
      // A value that depends on state ("Archived, N tasks") is read by prefix.
      (n) => n !== "View name" && !all.includes(n) && !all.includes(n.split(",")[0])
    );
    expect(missing).toEqual([]);
  });
});

describe("the phone board and the touch card", () => {
  const tabs = [
    { key: "a", label: "Backlog", count: 41 },
    { key: "b", label: "To do", count: 22 },
    { key: "c", label: "Doing", count: 9 },
  ];
  it("steps between status tabs and stops at the ends", () => {
    expect(stepTab(tabs, "a", 1)).toBe("b");
    expect(stepTab(tabs, "c", 1)).toBe("c");
    expect(stepTab(tabs, "a", -1)).toBe("a");
    expect(stepTab(tabs, "gone", 1)).toBe("b");
    expect(stepTab([], null, 1)).toBeNull();
  });
  it("reads a long flat drag as a swipe, and a scroll as none", () => {
    expect(swipeDirection(-120, 10)).toBe(1);
    expect(swipeDirection(120, 10)).toBe(-1);
    expect(swipeDirection(-120, 120)).toBeNull();
    expect(swipeDirection(-20, 0)).toBeNull();
  });
  it("a touch card keeps only the complete tick", () => {
    const items = [
      { id: "task.markDone" },
      { id: "task.subtask" },
      { id: "task.rename" },
      { id: "task.select" },
    ];
    expect(touchQuickActions(items)).toEqual([{ id: "task.markDone" }]);
  });
});
