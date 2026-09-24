/**
 * WS-39 S6f — one set of fields across My Tasks and Projects (D77).
 *
 * Spec: `project-docs/specs/my_tasks_cutover.md` §4.10. The owner directive:
 * My Tasks derives every work fact from the Projects field, and keeps its
 * own overlay only for how one member holds the work.
 *
 * This file pins the client half of the derivations that are pure:
 *
 *   1. the member's Important stays theirs (D76): the shared Priority only
 *      seeds it, and the switch writes the overlay, never the Priority;
 *   2. the list keeps the shared Priority column beside the member's own
 *      matrix column, and the cells keep D76's labels;
 *   3. the shared start date tickles a task like my own defer does;
 *   4. the card and the list draw the shared Priority and tags with the
 *      Projects card's own chips.
 *
 * The lens split (`TASK_KEYS` / `OVERLAY_KEYS`) is fenced in `lens.test.ts`,
 * and the one-label-per-panel rule in `itemDetail.test.ts`.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { gtdMetaChips } from "./cardMeta";
import { COLUMNS, DEFAULT_VISIBLE } from "./columns";
import { splitPatch } from "./lens";
import { CELL_META, priorityCell, seededImportant } from "./priority";
import type { GtdItem } from "./types";
import { isTickled, resurfacesAt } from "./utils";

const BASE: GtdItem = {
  id: "t",
  source: "LOCAL",
  title: "A task",
  disposition: "NEXT",
  isMine: true,
  createdAt: "2026-09-01T00:00:00Z",
  updatedAt: "2026-09-01T00:00:00Z",
};

describe("the member's Important stays theirs (D76, unchanged by D77)", () => {
  it("is seeded by High or Highest while unstated, and nothing else", () => {
    expect(seededImportant({ orgPriority: 3 })).toBe(true);
    expect(seededImportant({ orgPriority: 2 })).toBe(true);
    expect(seededImportant({ orgPriority: 1 })).toBe(false);
    expect(seededImportant({ orgPriority: 3, important: false })).toBe(false);
    expect(seededImportant({ orgPriority: 3, important: true })).toBe(false);
  });

  it("lets the member's own answer win over the shared Priority", () => {
    // "Not important to me" sticks on a Highest task: the cell is Low Priority.
    expect(priorityCell({ ...BASE, orgPriority: 3, important: false })).toBe("low-priority");
    expect(priorityCell({ ...BASE, orgPriority: 3 })).not.toBe("low-priority");
  });

  it("sends the Important switch to the overlay, never to the shared Priority", () => {
    const split = splitPatch({ important: true });
    expect(split.personal).toEqual({ important: true });
    expect(split.task).toEqual({});
  });
});

describe("the list shows the shared Priority beside the member's own cell (D77)", () => {
  it("keeps D76's cell labels", () => {
    expect(CELL_META["low-priority"].label).toBe("Low Priority");
  });

  it("names the shared column Priority and the member's cell Your focus", () => {
    // Two columns under one header would give two answers to one question.
    // "Your focus" is the name the Projects panel gives the private row (D76).
    const byKey = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label]));
    expect(byKey.priority).toBe("Priority");
    expect(byKey.focus).toBe("Your focus");
    expect(byKey.tags).toBe("Tags");
    expect(DEFAULT_VISIBLE.priority).toBe(true);
    expect(DEFAULT_VISIBLE.tags).toBe(false);
    // The Priority track is paid for by Energy, or Due date falls off at 1440.
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

describe("the card draws the shared facts with the Projects chips (D77)", () => {
  it("adds the Priority chip first, in D76's words, and the tags", () => {
    const chips = gtdMetaChips({ ...BASE, orgPriority: 3, tags: ["ops"] });
    expect(chips[0]).toMatchObject({ key: "importance", label: "Highest" });
    expect(chips.some((c) => c.key === "tags:ops")).toBe(true);
  });

  it("draws no Priority chip for an unset Priority, but does for Low", () => {
    expect(gtdMetaChips(BASE).some((c) => c.key === "importance")).toBe(false);
    expect(gtdMetaChips({ ...BASE, orgPriority: 0 })[0]).toMatchObject({
      key: "importance",
      label: "Low",
    });
  });
});
