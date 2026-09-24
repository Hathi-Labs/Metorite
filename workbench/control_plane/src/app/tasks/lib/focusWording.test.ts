/**
 * D77 (F1) — one word, one meaning in My Tasks.
 *
 * "Priority" is ONLY the task's shared `importance`, in D76's vocabulary
 * (read here as `orgPriority`). Every control over the member's private
 * matrix says "Your focus", the name D76 gives the private row in Projects.
 *
 * This file pins both halves:
 *   1. the toolbar's sort, group and filter, and the matrix view, never call
 *      a matrix option "Priority";
 *   2. the "Priority" sort, group and filter order and slice by `orgPriority`,
 *      so a member can rank their list by the company's priority.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  DEFAULT_SORT,
  NO_ORG_PRIORITY_FACET,
  SORT_LABEL,
  applyFilters,
  applySort,
  groupItems,
  DEFAULT_FILTERS,
} from "./ordering";
import type { GtdItem } from "./types";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");

const TOOLBAR = read("../components/TaskToolbar.tsx");
const ITEM_LIST = read("../components/ItemList.tsx");

function task(id: string, orgPriority?: number, extra: Partial<GtdItem> = {}): GtdItem {
  return {
    id,
    source: "LOCAL",
    title: id,
    disposition: "NEXT",
    isMine: true,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
    orgPriority,
    ...extra,
  };
}

describe("no My Tasks control calls the matrix 'Priority' (D77 F1)", () => {
  it("labels the matrix sort 'Your focus' and the shared sort 'Priority'", () => {
    expect(SORT_LABEL.priority).toBe("Your focus");
    expect(SORT_LABEL.orgPriority).toBe("Priority");
    // The default stays the matrix rank.
    expect(DEFAULT_SORT.field).toBe("priority");
  });

  it("labels the matrix group-by 'Your focus' and the shared one 'Priority'", () => {
    const group = TOOLBAR.slice(TOOLBAR.indexOf("const GROUP_LABEL"));
    expect(group).toMatch(/\n\s*priority: "Your focus",/);
    expect(group).toMatch(/\n\s*orgPriority: "Priority",/);
    expect(TOOLBAR).toMatch(/"priority", "orgPriority"/);
  });

  it("labels the matrix filter 'Your focus' and the shared one 'Priority'", () => {
    expect(TOOLBAR).toMatch(/key: "priorities", label: "Your focus"/);
    expect(TOOLBAR).toMatch(/key: "orgPriorities", label: "Priority"/);
  });

  it("names the matrix view 'Your focus'", () => {
    expect(ITEM_LIST).toMatch(/priority: \{ title: "Your focus"/);
  });

  it("names the clarify card's matrix section 'Your focus'", () => {
    const clarify = read("../components/ClarifyPanel.tsx");
    expect(clarify).toContain('<SubField label="Your focus" inline>');
    expect(clarify).not.toContain('<SubField label="Priority"');
  });

  it("never pairs a matrix key with the word Priority anywhere in the toolbar", () => {
    // Every `priority` / `priorities` key in the toolbar is the matrix.
    expect(TOOLBAR).not.toMatch(/\bpriority: "Priority"/);
    expect(TOOLBAR).not.toMatch(/key: "priorities", label: "Priority"/);
  });
});

describe("the 'Priority' controls read the shared orgPriority (D77 F1)", () => {
  const items = [
    task("normal", 1),
    task("unset"),
    task("highest", 3),
    task("low", 0),
    task("high", 2),
  ];

  it("sorts Highest first, unset last, and reverses on desc", () => {
    const asc = applySort(items, { field: "orgPriority", dir: "asc" }).map((i) => i.id);
    expect(asc).toEqual(["highest", "high", "normal", "low", "unset"]);
    const desc = applySort(items, { field: "orgPriority", dir: "desc" }).map((i) => i.id);
    expect(desc).toEqual(["low", "normal", "high", "highest", "unset"]);
  });

  it("ignores the member's own flags when sorting by Priority", () => {
    // My "important" on a Low task does not lift it in the company's order.
    const mine = [task("low-but-mine", 0, { important: true }), task("high", 2)];
    const asc = applySort(mine, { field: "orgPriority", dir: "asc" }).map((i) => i.id);
    expect(asc).toEqual(["high", "low-but-mine"]);
  });

  it("groups in D76's words, Highest first", () => {
    const groups = groupItems(items, "orgPriority");
    expect(groups.map((g) => g.label)).toEqual([
      "Highest", "High", "Normal", "Low", "No priority",
    ]);
  });

  it("filters by the shared level, and by 'no priority'", () => {
    const high = applyFilters(items, { ...DEFAULT_FILTERS, orgPriorities: ["3", "2"] });
    expect(high.map((i) => i.id).sort()).toEqual(["high", "highest"]);
    const none = applyFilters(items, {
      ...DEFAULT_FILTERS,
      orgPriorities: [NO_ORG_PRIORITY_FACET],
    });
    expect(none.map((i) => i.id)).toEqual(["unset"]);
  });

  it("reads a filter state saved before the facet existed as 'any'", () => {
    const legacy = { ...DEFAULT_FILTERS };
    delete legacy.orgPriorities;
    expect(applyFilters(items, legacy)).toHaveLength(items.length);
  });
});
