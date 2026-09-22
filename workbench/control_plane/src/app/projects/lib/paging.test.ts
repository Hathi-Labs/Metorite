import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  TASK_PAGE_SIZE,
  appendTasks,
  hasMoreTasks,
  nextBatchSize,
  nextTaskPage,
  truncationNote,
} from "./paging";

const row = (id: string, over: Record<string, unknown> = {}) =>
  ({ id, ...over }) as { id: string } & Record<string, unknown>;

describe("hasMoreTasks", () => {
  it("says yes when the server holds rows we have not asked for", () => {
    expect(hasMoreTasks(100, 150)).toBe(true);
  });

  it("says no when we hold them all", () => {
    expect(hasMoreTasks(150, 150)).toBe(false);
  });

  it("says no on an empty board, so the bar never greets a new project", () => {
    expect(hasMoreTasks(0, 0)).toBe(false);
  });

  it("says no when the total is not known yet", () => {
    // 🔴 The first paint reads from the cache with no `total` in hand. A
    // truthy answer here would flash "Showing 100 of null tasks" on every
    // board open.
    expect(hasMoreTasks(100, null)).toBe(false);
  });

  it("says no when we somehow hold MORE than the total", () => {
    // An optimistic create adds a row before the server counts it, so
    // `loaded` legitimately runs one ahead. "Showing 151 of 150" is worse
    // than saying nothing.
    expect(hasMoreTasks(151, 150)).toBe(false);
  });
});

describe("nextTaskPage", () => {
  it("asks for page 2 once one full page is held", () => {
    expect(nextTaskPage(100)).toBe(2);
  });

  it("asks for page 3 once two are held", () => {
    expect(nextTaskPage(200)).toBe(3);
  });

  it("asks for page 1 when nothing is held", () => {
    expect(nextTaskPage(0)).toBe(1);
  });

  it("does not skip a page after a SHORT page", () => {
    // 🔴 The reason this is derived from the row count rather than a counter.
    // A read that returned 150 rows across two pages means page 2 was short,
    // and a counter would blindly ask for page 3 — stepping over the rows
    // that are actually missing. Floor(150/100)+1 = 2 asks again instead.
    expect(nextTaskPage(150)).toBe(2);
  });

  it("never divides by zero", () => {
    expect(nextTaskPage(100, 0)).toBe(1);
  });
});

describe("truncationNote", () => {
  it("names BOTH numbers, because the question is what is missing", () => {
    expect(truncationNote(100, 150)).toBe("Showing 100 of 150 tasks.");
  });

  it("is null when the board is whole", () => {
    // ⚠️ Null, not an empty string. The bar must not occupy a row on the
    // ordinary board, which is nearly every board.
    expect(truncationNote(150, 150)).toBeNull();
    expect(truncationNote(0, 0)).toBeNull();
    expect(truncationNote(12, null)).toBeNull();
  });

  it("never says a task is missing when none is", () => {
    for (let n = 0; n <= 120; n += 1) {
      expect(truncationNote(n, n)).toBeNull();
    }
  });
});

describe("nextBatchSize", () => {
  it("names the remainder when it is smaller than a page", () => {
    expect(nextBatchSize(100, 150)).toBe(50);
  });

  it("names one page when more than a page remains", () => {
    // A 400-task board closes 100 of the gap per press, not 300.
    expect(nextBatchSize(100, 400)).toBe(100);
  });

  it("is zero when nothing remains, so no button is drawn", () => {
    expect(nextBatchSize(150, 150)).toBe(0);
    expect(nextBatchSize(100, null)).toBe(0);
    expect(nextBatchSize(151, 150)).toBe(0);
  });

  it("never promises more than it can deliver", () => {
    for (let loaded = 0; loaded < 300; loaded += 7) {
      const total = 300;
      expect(nextBatchSize(loaded, total)).toBeLessThanOrEqual(total - loaded);
      expect(nextBatchSize(loaded, total)).toBeLessThanOrEqual(TASK_PAGE_SIZE);
    }
  });
});

describe("appendTasks", () => {
  it("adds the new page after the held rows, in order", () => {
    const got = appendTasks([row("a"), row("b")], [row("c"), row("d")]);
    expect(got.map((r) => r.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("drops a row the shifting offset served twice", () => {
    // 🔴 Offset paging is racy. Somebody adding a task between the two reads
    // shifts every later row down one, so page 1's last row arrives again on
    // page 2. Appended blindly it draws twice and React warns on the key.
    const got = appendTasks([row("a"), row("b")], [row("b"), row("c")]);
    expect(got.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("KEEPS the held copy, so loading more cannot undo an optimistic edit", () => {
    // 🔴 The one that bites. Every write on the board repaints through
    // `setTasks((current) => …)` before the server answers. Taking the
    // incoming copy would silently revert an edit the member is looking at.
    const got = appendTasks(
      [row("a", { title: "edited just now" })],
      [row("a", { title: "what the server last knew" })]
    );
    expect(got).toHaveLength(1);
    expect(got[0].title).toBe("edited just now");
  });

  it("does not mutate what it was handed", () => {
    const current = [row("a")];
    appendTasks(current, [row("b")]);
    expect(current.map((r) => r.id)).toEqual(["a"]);
  });

  it("handles an empty page without changing anything", () => {
    const got = appendTasks([row("a")], []);
    expect(got.map((r) => r.id)).toEqual(["a"]);
  });
});

describe("the client page size agrees with the server's cap", () => {
  it("matches MAX_PAGE_SIZE in the gateway", () => {
    // 🔴 **This fence is the whole reason `TASK_PAGE_SIZE` is a named
    // constant.** A client that asks for more than the server's `le=` allows
    // gets a 422 on EVERY read of the board. That exact defect shipped once
    // already (`page_size=200` against `MAX_PAGE_SIZE=100`) and neither
    // harness could see it: the live harness calls handlers directly, so no
    // query string is ever validated, and the visual rig stubs the API.
    //
    // Read from the gateway's own source rather than restated here, because a
    // number copied into a test is a second place for it to be wrong.
    const core = readFileSync(
      fileURLToPath(
        new URL(
          "../../../../../../apps/services/gateway/gateway/routes/projects/core.py",
          import.meta.url
        )
      ),
      "utf8"
    );
    const declared = core.match(/^MAX_PAGE_SIZE\s*[:=][^=]*?(\d+)\s*$/m);
    expect(declared, "MAX_PAGE_SIZE not found in core.py").not.toBeNull();
    expect(TASK_PAGE_SIZE).toBe(Number(declared![1]));
  });

  it("the board asks with that constant, not a literal", () => {
    // Without this, the constant can be correct while `loadProject` still
    // carries the hardcoded 100 it had before — green, and unchanged.
    const page = readFileSync(
      fileURLToPath(new URL("../page.tsx", import.meta.url)),
      "utf8"
    );
    expect(page).toMatch(/page_size:\s*TASK_PAGE_SIZE/);
    expect(page).not.toMatch(/page_size:\s*\d+\s*,\s*\n\s*\.\.\.toQuery/);
  });
});
