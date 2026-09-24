/**
 * D78 — one priority system, one word, in My Tasks.
 *
 * D77 had two answers to one question. "Priority" was the shared 0-3 field
 * (`orgPriority`), and "Your focus" was the member's private matrix. D78
 * (owner, 2026-09-24) retires the 0-3 field. "Priority" is now the matrix
 * level, which is the task's shared level in both apps.
 *
 * This file pins both halves:
 *   1. no My Tasks source names `orgPriority`, and only the private "Your
 *      focus" card in ItemDetail uses those words.
 *   2. the matrix sort, group, filter, view and clarify section say
 *      "Priority", and they order and slice by the matrix level.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { COLUMNS } from "./columns";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  SORT_LABEL,
  applyFilters,
  applySort,
  groupItems,
} from "./ordering";
import type { GtdItem } from "./types";

const TASKS = fileURLToPath(new URL("..", import.meta.url));

const raw = (path: string) => readFileSync(path, { encoding: "utf-8" });

/** Code only. A comment may tell the history of the old names. */
const code = (path: string) =>
  raw(path)
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...sources(path));
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.ts$/.test(name)) out.push(path);
  }
  return out;
}

const rel = (path: string) => relative(TASKS, path).replace(/\\/g, "/");
const read = (relPath: string) => code(join(TASKS, relPath));

const TOOLBAR = read("components/TaskToolbar.tsx");
const ITEM_LIST = read("components/ItemList.tsx");

function task(id: string, extra: Partial<GtdItem> = {}): GtdItem {
  return {
    id,
    source: "LOCAL",
    title: id,
    disposition: "NEXT",
    isMine: true,
    createdAt: "2026-09-01T00:00:00Z",
    updatedAt: "2026-09-01T00:00:00Z",
    ...extra,
  };
}

describe("the retired names are gone from My Tasks (D78)", () => {
  it("has no source that names orgPriority", () => {
    const offenders = sources(TASKS).filter((f) => /orgPriority/.test(raw(f)));
    expect(offenders.map(rel)).toEqual([]);
  });

  it("says 'Your focus' only on the private ItemDetail card", () => {
    // The card holds what is MINE about a task (Deep work). It is not the
    // matrix, so it keeps its own name.
    const users = sources(TASKS).filter((f) => /Your focus/.test(code(f)));
    expect(users.map(rel)).toEqual(["components/ItemDetail.tsx"]);
  });
});

describe("the matrix controls say 'Priority' (D78)", () => {
  it("labels the matrix sort 'Priority', and it stays the default", () => {
    expect(SORT_LABEL.priority).toBe("Priority");
    expect(DEFAULT_SORT.field).toBe("priority");
  });

  it("labels the matrix group-by 'Priority'", () => {
    const group = TOOLBAR.slice(TOOLBAR.indexOf("const GROUP_LABEL"));
    expect(group).toMatch(/\n\s*priority: "Priority",/);
  });

  it("labels the matrix filter 'Priority'", () => {
    expect(TOOLBAR).toMatch(/key: "priorities", label: "Priority"/);
  });

  it("names the matrix view 'Priority'", () => {
    expect(ITEM_LIST).toMatch(/priority: \{ title: "Priority"/);
  });

  it("names the clarify card's matrix section 'Priority'", () => {
    const clarify = read("components/ClarifyPanel.tsx");
    expect(clarify).toContain('<SubField label="Priority" inline>');
    expect(clarify).not.toContain('<SubField label="Your focus"');
  });

  it("has exactly one 'Priority' list column", () => {
    expect(COLUMNS.filter((c) => c.label === "Priority").map((c) => c.key)).toEqual([
      "priority",
    ]);
    expect(COLUMNS.some((c) => (c.key as string) === "focus")).toBe(false);
  });
});

describe("the 'Priority' controls read the matrix level (D78)", () => {
  // No due dates, so no level depends on the clock.
  const items = [
    task("low"),
    task("bet", { leveraged: true }),
    task("important", { important: true }),
    task("high-leverage", { important: true, leveraged: true }),
  ];

  it("sorts by matrix rank, and reverses on desc", () => {
    const asc = applySort(items, { field: "priority", dir: "asc" }).map((i) => i.id);
    expect(asc).toEqual(["high-leverage", "important", "bet", "low"]);
    const desc = applySort(items, { field: "priority", dir: "desc" }).map((i) => i.id);
    expect(desc).toEqual(["low", "bet", "important", "high-leverage"]);
  });

  it("groups by level, in rank order", () => {
    const groups = groupItems(items, "priority");
    expect(groups.map((g) => g.label)).toEqual([
      "High-Leverage",
      "Important",
      "Speculative Bet",
      "Low Priority",
    ]);
  });

  it("filters by level", () => {
    const hit = applyFilters(items, {
      ...DEFAULT_FILTERS,
      priorities: ["important", "high-leverage"],
    });
    expect(hit.map((i) => i.id).sort()).toEqual(["high-leverage", "important"]);
    const low = applyFilters(items, { ...DEFAULT_FILTERS, priorities: ["low-priority"] });
    expect(low.map((i) => i.id)).toEqual(["low"]);
  });
});
