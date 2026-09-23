/**
 * WS-39 S6f — one set of fields across My Tasks and Projects (D76).
 *
 * Spec: `project-docs/specs/my_tasks_cutover.md` §4.10. The owner directive:
 * My Tasks derives every work fact from the Projects field, and keeps its
 * own overlay only for how one member holds the work.
 *
 * This file pins the client half of the derivations that are pure:
 *
 *   1. Important is the shared Priority read at High or above, and the
 *      Important switch turns into a Priority write without demoting Urgent;
 *   2. no Focus matrix cell is called "…Priority" — that word is the field;
 *   3. the shared start date tickles a task like my own defer does;
 *   4. the card and the list draw the shared Priority and tags with the
 *      Projects card's own chips.
 *
 * The lens split (`TASK_KEYS` / `OVERLAY_KEYS`) is fenced in `lens.test.ts`,
 * and the one-label-per-panel rule in `itemDetail.test.ts`.
 */

import { describe, expect, it } from "vitest";

import { gtdMetaChips } from "./cardMeta";
import { COLUMNS, DEFAULT_VISIBLE } from "./columns";
import {
  CELL_META,
  IMPORTANT_AT,
  importanceForImportant,
  isImportant,
  isUntagged,
  priorityCell,
} from "./priority";
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

describe("Important is the shared Priority (D76)", () => {
  it("is High or Urgent, and one number with the gateway", () => {
    expect(IMPORTANT_AT).toBe(2);
    expect(isImportant({ importance: 3 })).toBe(true);
    expect(isImportant({ importance: 2 })).toBe(true);
    expect(isImportant({ importance: 1 })).toBe(false);
    expect(isImportant({ importance: 0 })).toBe(false);
  });

  it("wins over a stale stored flag whenever the task carries a Priority", () => {
    expect(isImportant({ importance: 0, important: true })).toBe(false);
    expect(isImportant({ importance: 3, important: false })).toBe(true);
    // The demo backend's rows have no Priority field at all.
    expect(isImportant({ important: true })).toBe(true);
  });

  it("puts an Urgent task in an important cell — never 'Low value'", () => {
    // The measured defect: Projects said Urgent, My Tasks said
    // "Low Priority · Eliminate?".
    const urgent = { ...BASE, importance: 3 };
    expect(priorityCell(urgent)).not.toBe("low-priority");
    expect(isUntagged(urgent)).toBe(false);
  });

  it("turns the Important switch into a Priority write", () => {
    expect(importanceForImportant(undefined, true)).toBe(2);
    expect(importanceForImportant(0, true)).toBe(2);
    expect(importanceForImportant(1, true)).toBe(2);
    expect(importanceForImportant(3, false)).toBe(1);
    expect(importanceForImportant(2, false)).toBe(1);
  });

  it("never demotes Urgent by switching Important on, and never writes a no-op", () => {
    expect(importanceForImportant(3, true)).toBeUndefined();
    expect(importanceForImportant(2, true)).toBeUndefined();
    expect(importanceForImportant(1, false)).toBeUndefined();
    expect(importanceForImportant(undefined, false)).toBeUndefined();
  });
});

describe("no Focus cell borrows the word Priority (D76)", () => {
  it("labels every cell without it", () => {
    for (const meta of Object.values(CELL_META)) {
      expect(meta.label, meta.cell).not.toMatch(/priority/i);
    }
    expect(CELL_META["low-priority"].label).toBe("Low value");
  });

  it("keeps the Priority column for the shared field and names the matrix Focus", () => {
    const byKey = Object.fromEntries(COLUMNS.map((c) => [c.key, c.label]));
    expect(byKey.priority).toBe("Priority");
    expect(byKey.focus).toBe("Focus");
    expect(byKey.tags).toBe("Tags");
    expect(DEFAULT_VISIBLE.priority).toBe(true);
    expect(DEFAULT_VISIBLE.tags).toBe(false);
  });
});

describe("the shared start date tickles a task (D76)", () => {
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

describe("the card draws the shared facts with the Projects chips (D76)", () => {
  it("adds the Priority chip first, and the tags", () => {
    const chips = gtdMetaChips({ ...BASE, importance: 3, tags: ["ops"] });
    expect(chips[0]).toMatchObject({ key: "importance", label: "Urgent" });
    expect(chips.some((c) => c.key === "tags:ops")).toBe(true);
  });

  it("draws no Priority chip for an unset Priority, but does for Low", () => {
    expect(gtdMetaChips(BASE).some((c) => c.key === "importance")).toBe(false);
    expect(gtdMetaChips({ ...BASE, importance: 0 })[0]).toMatchObject({
      key: "importance",
      label: "Low",
    });
  });
});
