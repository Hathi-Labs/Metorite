/**
 * WS-27bc — the fences for `pagedPicker`.
 *
 * Four behaviours and one structural rule. The four are the cases a
 * reimplementation gets wrong: the threshold boundary (off-by-one at exactly
 * 48px), a row that appears on two consecutive pages (the list is ordered by
 * `updated_at`, which moves between requests), a short page ending the paging,
 * and a response that arrives after a newer query.
 *
 * The fifth is the reason this module was worth writing carefully: a source
 * scan asserting `MIN_QUERY` and `isCurrent` are **imported** from `./search`
 * rather than redefined here. That is CLAUDE.md §5's "do not invent a second
 * way to do an existing thing" made executable — a behavioural test cannot see
 * the difference between a shared constant and a second copy that happens to
 * hold the same number today.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  SCROLL_THRESHOLD_PX,
  appendPage,
  emptyState,
  isSearchable,
  receivePage,
  shouldLoadMore,
  type PagedState,
} from "./pagedPicker";
import { MIN_QUERY } from "./search";

/** A row, as small as the module's own constraint allows. */
interface Row {
  id: string;
  title: string;
}

const row = (id: string, title = id): Row => ({ id, title });
const state = (rows: Row[], done = false): PagedState<Row> => ({ rows, done });

describe("shouldLoadMore — the threshold boundary", () => {
  // One scroller: 1000px of content in a 400px viewport, so the furthest
  // `scrollTop` is 600 and `remaining` is `600 - scrollTop`.
  const remainingAt = (scrollTop: number) => shouldLoadMore(scrollTop, 400, 1000);

  it("does not load one pixel above the threshold", () => {
    // remaining = 49
    expect(remainingAt(551)).toBe(false);
  });

  it("loads EXACTLY at the threshold", () => {
    // remaining = 48. Asserted by name because this is the whole boundary: a
    // `<` here leaves a scroller that comes to rest on the line waiting for a
    // pixel of scroll that may never arrive.
    expect(remainingAt(552)).toBe(true);
    expect(SCROLL_THRESHOLD_PX).toBe(48);
  });

  it("loads one pixel below the threshold", () => {
    // remaining = 47
    expect(remainingAt(553)).toBe(true);
  });

  it("loads at the very bottom, and past it", () => {
    expect(remainingAt(600)).toBe(true);
    // iOS rubber-banding scrolls past the end; a negative remaining distance
    // is still the bottom.
    expect(remainingAt(640)).toBe(true);
  });

  it("loads when the content does not fill the viewport at all", () => {
    // The first page came back shorter than the container, so NO scroll event
    // will ever fire. Returning false here is how a picker stops at page 1
    // with no way for the reader to ask for page 2.
    expect(shouldLoadMore(0, 400, 120)).toBe(true);
  });

  it("honours an explicit threshold instead of the default", () => {
    expect(shouldLoadMore(552, 400, 1000, 0)).toBe(false);
    expect(shouldLoadMore(600, 400, 1000, 0)).toBe(true);
  });

  it("asks for nothing when a measurement is not a number", () => {
    expect(shouldLoadMore(NaN, 400, 1000)).toBe(false);
    expect(shouldLoadMore(0, NaN, 1000)).toBe(false);
    expect(shouldLoadMore(0, 400, NaN)).toBe(false);
  });
});

describe("appendPage — accumulation and dedupe by id", () => {
  it("appends a page in order", () => {
    const next = appendPage(state([row("a"), row("b")]), [row("c"), row("d")], 2);
    expect(next.rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("does NOT duplicate a row that appears on two consecutive pages", () => {
    // The real hazard: `/projects/tasks` is ordered by `updated_at`, so a row
    // touched between the two requests slides across the page boundary and is
    // returned twice. Rendered twice, the second copy is the one clicked.
    const page1 = [row("a"), row("b"), row("c")];
    const page2 = [row("c"), row("d"), row("e")];

    const after1 = appendPage(emptyState<Row>(), page1, 3);
    const after2 = appendPage(after1, page2, 3);

    expect(after2.rows.map((r) => r.id)).toEqual(["a", "b", "c", "d", "e"]);
    expect(after2.rows.filter((r) => r.id === "c")).toHaveLength(1);
  });

  it("keeps the row where the reader already sees it", () => {
    // First position wins. Moving a repeated row to the end would re-sort the
    // list under the cursor — a second defect wearing the fix for the first.
    const after = appendPage(
      state([row("a"), row("b"), row("c")]),
      [row("a"), row("d")],
      2,
    );
    expect(after.rows.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("takes the FRESHER copy of a repeated row", () => {
    // Last payload wins: the later page is a later read of the same table, so
    // rendering the copy from the earlier request shows a title we have
    // already been told is out of date.
    const after = appendPage(
      state([row("a", "old title"), row("b")]),
      [row("a", "renamed")],
      2,
    );
    expect(after.rows.map((r) => r.title)).toEqual(["renamed", "b"]);
  });

  it("dedupes within a single page too", () => {
    const after = appendPage(emptyState<Row>(), [row("a"), row("a", "again")], 2);
    expect(after.rows).toEqual([row("a", "again")]);
  });

  it("does not mutate the state it was handed", () => {
    const before = state([row("a")]);
    appendPage(before, [row("b")], 2);
    expect(before.rows.map((r) => r.id)).toEqual(["a"]);
  });
});

describe("appendPage — the terminal state is derived, not flagged", () => {
  it("keeps paging after a full page", () => {
    expect(appendPage(emptyState<Row>(), [row("a"), row("b")], 2).done).toBe(false);
  });

  it("terminates on a short page", () => {
    expect(appendPage(emptyState<Row>(), [row("a")], 2).done).toBe(true);
  });

  it("terminates on an empty page", () => {
    expect(appendPage(state([row("a"), row("b")]), [], 2).done).toBe(true);
  });

  it("does NOT terminate on a full page whose rows were all already on screen", () => {
    // `done` is computed from the raw page length, never from how many rows
    // the dedupe actually added. Computing it from the growth would call a
    // page of pure duplicates the end of the list and truncate the picker at
    // whatever the reader happened to have seen.
    const after = appendPage(state([row("a"), row("b")]), [row("a"), row("b")], 2);
    expect(after.rows.map((r) => r.id)).toEqual(["a", "b"]);
    expect(after.done).toBe(false);
  });

  it("treats a page size of zero or less as done", () => {
    // Otherwise `page.length < pageSize` is never true and the caller asks
    // forever for a page that cannot contain anything.
    expect(appendPage(emptyState<Row>(), [], 0).done).toBe(true);
    expect(appendPage(emptyState<Row>(), [], -1).done).toBe(true);
  });

  it("re-opens paging if a full page arrives after a short one", () => {
    // Derived means derived: the flag describes the page in hand, so it cannot
    // drift out of agreement with the rows the way a stored `hasMore` does.
    const short = appendPage(emptyState<Row>(), [row("a")], 2);
    expect(short.done).toBe(true);
    expect(appendPage(short, [row("b"), row("c")], 2).done).toBe(false);
  });
});

describe("receivePage — a response that lost its race is discarded", () => {
  it("folds in a page that answers the live query", () => {
    const after = receivePage(
      emptyState<Row>(),
      { query: "parser", rows: [row("a")] },
      "parser",
      2,
    );
    expect(after.rows.map((r) => r.id)).toEqual(["a"]);
  });

  it("discards a response that arrives after a newer query", () => {
    // "par" was typed, then "parser"; nothing orders the two answers. A slow
    // "par" landing last would otherwise append rows for a question the reader
    // has already moved on from.
    const before = receivePage(
      emptyState<Row>(),
      { query: "parser", rows: [row("a")] },
      "parser",
      2,
    );
    const after = receivePage(before, { query: "par", rows: [row("z")] }, "parser", 2);

    expect(after.rows.map((r) => r.id)).toEqual(["a"]);
    // The SAME object, so a caller rendering on identity does not repaint for
    // a page it threw away.
    expect(after).toBe(before);
  });

  it("compares trimmed, the way the box's own value arrives", () => {
    const after = receivePage(
      emptyState<Row>(),
      { query: " parser ", rows: [row("a")] },
      "parser",
      2,
    );
    expect(after.rows.map((r) => r.id)).toEqual(["a"]);
  });

  it("leaves the terminal state alone when it discards a page", () => {
    const before = appendPage(emptyState<Row>(), [row("a"), row("b")], 2);
    const after = receivePage(before, { query: "stale", rows: [] }, "live", 2);
    // An empty stale page must not be read as "no more pages" — that is the
    // discarded response setting the terminal state anyway.
    expect(after.done).toBe(false);
  });
});

describe("isSearchable — the minimum query length", () => {
  it("refuses a query shorter than the minimum", () => {
    expect(isSearchable("")).toBe(false);
    expect(isSearchable("a".repeat(MIN_QUERY - 1))).toBe(false);
  });

  it("accepts a query exactly at the minimum", () => {
    expect(isSearchable("a".repeat(MIN_QUERY))).toBe(true);
  });

  it("accepts a longer query", () => {
    expect(isSearchable("a".repeat(MIN_QUERY + 3))).toBe(true);
  });

  it("counts trimmed characters, not keystrokes", () => {
    // "  a  " is one character of query and a table scan returning half the
    // workspace; whitespace must not buy its way past the gate.
    expect(isSearchable("  ab  ")).toBe(false);
    expect(isSearchable("  abc  ")).toBe(true);
  });

  it("passes a task number at any length (D-PM-31's exception)", () => {
    expect(isSearchable("#7")).toBe(true);
    expect(isSearchable(" 7 ")).toBe(true);
    expect(isSearchable("#")).toBe(false);
    expect(isSearchable("7a")).toBe(false);
  });
});

/**
 * Rooted at `src/` via `import.meta.url`, never the process cwd — the reason
 * `sharedTaskUi.test.ts` and `controlLink.test.ts` both give: a checkout with
 * agent worktrees under `.claude/worktrees/` would otherwise scan itself.
 */
const SRC = fileURLToPath(new URL("../../..", import.meta.url));

/** Source with comments stripped, so prose about a rule cannot pass for it. */
const code = (rel: string) =>
  readFileSync(join(SRC, rel), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(?<![:"'/])\/\/[^\n]*/g, "");

describe("the search seam is imported, not re-derived", () => {
  const MODULE = "app/projects/lib/pagedPicker.ts";

  it("imports isSearchableQuery and isCurrent from ./search", () => {
    const text = code(MODULE);
    const imported = /import\s*\{([^}]*)\}\s*from\s*["']\.\/search["']/.exec(text);
    expect(imported, `${MODULE} no longer imports from ./search.`).not.toBeNull();
    const names = (imported?.[1] ?? "").split(",").map((n) => n.trim());
    expect(names).toContain("isSearchableQuery");
    expect(names).toContain("isCurrent");
  });

  it("declares neither of them itself", () => {
    // The CLAUDE.md §5 defect this module is most likely to author: a second
    // copy of the gateway's minimum, or a second stale-response comparison
    // that drifts from the one the palette already uses. Both would pass every
    // behavioural test above on the day they were written.
    const text = code(MODULE);
    expect(/\b(const|let|var|enum)\s+MIN_QUERY\b/.test(text)).toBe(false);
    expect(/\bfunction\s+isSearchableQuery\b/.test(text)).toBe(false);
    expect(/\.length\s*(<|>=)\s*MIN_QUERY/.test(text)).toBe(false);
    expect(/\bfunction\s+isCurrent\b/.test(text)).toBe(false);
    expect(/\b(const|let|var)\s+isCurrent\b/.test(text)).toBe(false);
  });

  it("actually uses them, rather than importing and shadowing", () => {
    // An import the module never calls is the shape a half-finished revert
    // leaves behind: the assertion above would still pass.
    const body = code(MODULE).replace(
      /import\s*\{[^}]*\}\s*from\s*["']\.\/search["'];?/,
      "",
    );
    expect(/\bisCurrent\s*\(/.test(body)).toBe(true);
    expect(/\bisSearchableQuery\s*\(/.test(body)).toBe(true);
  });

  it("holds the same minimum the gateway does, through one constant", () => {
    // The runtime half of the scan: whatever `./search` says, this module's
    // gate moves with it.
    expect(isSearchable("a".repeat(MIN_QUERY))).toBe(true);
    expect(isSearchable("a".repeat(MIN_QUERY - 1))).toBe(false);
  });
});
