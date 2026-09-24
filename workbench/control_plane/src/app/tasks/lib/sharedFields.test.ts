/**
 * WS-39 S6f — one set of fields across My Tasks and Projects (D77).
 *
 * Spec: `project-docs/specs/my_tasks_cutover.md` §4.10. The owner directive:
 * My Tasks derives every work fact from the Projects field, and keeps its
 * own overlay only for how one member holds the work.
 *
 * This file pins the client half of the derivations that are pure:
 *
 *   1. Important and Leveraged are one shared answer on the task (D78). The
 *      switch writes the task, never the overlay.
 *   2. The list has ONE Priority column, the matrix level (D78).
 *   3. The shared start date tickles a task like my own defer does.
 *   4. The card draws the shared tags with the Projects card's own chips.
 *      It draws no priority chip, because PriorityBadge draws the level.
 *
 * The lens split (`TASK_KEYS` / `OVERLAY_KEYS`) is fenced in `lens.test.ts`,
 * and the one-label-per-panel rule in `itemDetail.test.ts`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { taskMetaChips } from "./cardMeta";
import { COLUMNS, DEFAULT_VISIBLE } from "./columns";
import { splitPatch } from "./lens";
import { CELL_META, importantFromImportance, priorityCell } from "./priority";
import type { MyTask } from "./types";
import { isTickled, localDate, resurfacesAt } from "./utils";

const BASE: MyTask = {
  id: "t",
  source: "LOCAL",
  title: "A task",
  disposition: "NEXT",
  isMine: true,
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
};

describe("Important and Leveraged are shared facts on the task (D78)", () => {
  it("reads Important from the shared importance, with no seed", () => {
    const at = (importance: number | null) => ({
      ...BASE,
      important: importantFromImportance(importance),
    });
    expect(priorityCell(at(3))).toBe("important");
    expect(priorityCell(at(2))).toBe("important");
    expect(priorityCell(at(1))).toBe("low-priority");
    expect(priorityCell(at(null))).toBe("low-priority");
  });

  it("sends the Important switch to the task, never to the overlay", () => {
    const split = splitPatch({ important: true });
    expect(split.task).toEqual({ importance: 2 });
    expect(split.personal).toEqual({});
  });

  it("sends the Leveraged switch to the task, never to the overlay", () => {
    const split = splitPatch({ leveraged: true });
    expect(split.task).toEqual({ leveraged: true });
    expect(split.personal).toEqual({});
  });

  it("keeps Deep work personal", () => {
    const split = splitPatch({ deep_work: true });
    expect(split.personal).toEqual({ deep_work: true });
    expect(split.task).toEqual({});
  });
});

describe("the list has ONE Priority column, the matrix level (D78)", () => {
  it("keeps the matrix's cell labels", () => {
    expect(CELL_META["low-priority"].label).toBe("Low Priority");
  });

  it("names one column Priority and has no Your focus column", () => {
    // Two columns under two headers gave two answers to one question.
    const byKey = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label]));
    expect(COLUMNS.filter((c) => c.label === "Priority").map((c) => c.key)).toEqual([
      "priority",
    ]);
    expect(byKey).not.toHaveProperty("focus");
    expect(COLUMNS.some((c) => c.label === "Your focus")).toBe(false);
    expect(byKey.tags).toBe("Tags");
    expect(DEFAULT_VISIBLE.priority).toBe(true);
    expect(DEFAULT_VISIBLE.tags).toBe(false);
    expect(DEFAULT_VISIBLE.energy).toBe(false);
  });
});

describe("the shared start date tickles a task (D77)", () => {
  const now = new Date(2026, 8, 23, 12).getTime();

  it("hides it until the start day, local", () => {
    expect(isTickled({ startDate: "2026-09-24" }, now)).toBe(true);
    expect(isTickled({ startDate: "2026-09-23" }, now)).toBe(false);
    expect(isTickled({ startDate: "2026-09-01" }, now)).toBe(false);
  });

  it("waits for the LATER of my defer and the start date", () => {
    const later = new Date(2026, 8, 30).toISOString();
    expect(isTickled({ startDate: "2026-09-01", deferUntil: later }, now)).toBe(true);
    expect(isTickled({ startDate: "2026-10-05", deferUntil: later }, now)).toBe(true);
    expect(resurfacesAt({ startDate: "2026-10-05", deferUntil: later })).toBe(
      new Date(2026, 9, 5).toISOString(),
    );
    expect(resurfacesAt({})).toBeUndefined();
  });
});

/**
 * F4 — the client's "not yet" rule against the gateway's, through ONE table.
 * `tests/fixtures/deferred_parity.json` is also read by the pytest side
 * (`personal.not_yet`) and the live check (`DEFERRED_CLAUSE` on Postgres).
 */
interface ParityCase {
  name: string;
  defer_days: number | null;
  start_days: number | null;
  hidden: boolean;
}
const PARITY = JSON.parse(
  readFileSync(
    fileURLToPath(new URL("../../../../../../tests/fixtures/deferred_parity.json", import.meta.url)),
    "utf8",
  ),
) as { cases: ParityCase[] };

interface ExplicitCase {
  name: string;
  at: string;
  timezone: string;
  today: string;
  start_date: string | null;
  defer_until: string | null;
  hidden: boolean;
}

describe("isTickled reads the member's own date (F5, the shared fixture)", () => {
  const cases = (PARITY as unknown as { explicit_today_cases: ExplicitCase[] })
    .explicit_today_cases;

  it("reads every case", () => {
    expect(cases.length).toBeGreaterThanOrEqual(3);
  });

  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const now = Date.parse(c.at);
    expect(localDate(now, c.timezone)).toBe(c.today);
    const item = {
      startDate: c.start_date ?? undefined,
      deferUntil: c.defer_until ?? undefined,
    };
    expect(isTickled(item, now, c.timezone)).toBe(c.hidden);
  });
});

describe("isTickled holds the gateway's rule (the shared fixture)", () => {
  const now = new Date(2026, 8, 23, 12);
  const day = 24 * 60 * 60 * 1000;
  const localDate = (offset: number) => {
    const d = new Date(now.getTime() + offset * day);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  };

  it("reads every case", () => {
    expect(PARITY.cases.length).toBeGreaterThanOrEqual(10);
  });

  it.each(PARITY.cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    const item = {
      deferUntil:
        c.defer_days === null
          ? undefined
          : new Date(now.getTime() + c.defer_days * day).toISOString(),
      startDate: c.start_days === null ? undefined : localDate(c.start_days),
    };
    expect(isTickled(item, now.getTime())).toBe(c.hidden);
    // When it is hidden, it says when it comes back; when it is not, the
    // date it names is not in the future.
    const back = resurfacesAt(item);
    if (c.hidden) expect(new Date(back!).getTime()).toBeGreaterThan(now.getTime());
    else if (back) expect(new Date(back).getTime()).toBeLessThanOrEqual(now.getTime());
  });
});

describe("the card draws the shared facts with the Projects chips (D77, D78)", () => {
  it("draws the tags with the Projects pills", () => {
    const chips = taskMetaChips({ ...BASE, tags: ["ops"] });
    expect(chips.some((c) => c.key === "tags:ops")).toBe(true);
  });

  it("draws no priority chip, because PriorityBadge draws the level", () => {
    // D78. A chip here would be a second drawing of the one level.
    const soon = new Date(Date.now() + 3_600_000).toISOString();
    for (const item of [
      BASE,
      { ...BASE, important: true },
      { ...BASE, important: true, leveraged: true, dueAt: soon },
    ]) {
      expect(taskMetaChips(item).some((c) => c.key === "importance")).toBe(false);
    }
  });
});
