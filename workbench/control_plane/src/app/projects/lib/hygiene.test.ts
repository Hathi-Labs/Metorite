/**
 * WS-27bn R3c — the Hygiene panel's words (`lib/hygiene.ts`).
 *
 * The panel, the table and the email say one set of words for the four
 * kinds. The email formats outside the app (`src/lib/reportEmail.ts`), so
 * it keeps its own list, and this file holds the two equal.
 */
import { describe, expect, it } from "vitest";

import { HYGIENE_WORDS } from "@/lib/reportEmail";

import type { HygieneReport } from "./api";
import {
  HYGIENE_KINDS,
  OVERLAP_NOTE,
  hygieneCount,
  hygieneRows,
  kindTitle,
  moreNote,
} from "./hygiene";

const REPORT: HygieneReport = {
  open_total: 12,
  stale_days: 14,
  by_kind: { no_assignee: 3, no_due_date: 0 },
  rows: [
    { kind: "no_assignee", id: "a", title: "Order steel", task_number: 1,
      project_id: "p", project_name: "Rig", due_at: null, updated_at: null },
    { kind: "stale_in_progress", id: "b", title: "Weld", task_number: 2,
      project_id: "p", project_name: "Rig", due_at: null,
      updated_at: "2026-09-01T00:00:00+00:00" },
  ],
};

describe("the hygiene words", () => {
  it("are the server's four kinds, in its order", () => {
    expect(HYGIENE_KINDS.map((k) => k.kind)).toEqual([
      "no_assignee",
      "no_due_date",
      "no_estimate",
      "stale_in_progress",
    ]);
  });

  it("are the email's words too", () => {
    expect(HYGIENE_WORDS).toEqual(HYGIENE_KINDS.map((k) => [k.kind, k.label]));
  });

  it("say that the counts overlap", () => {
    expect(OVERLAP_NOTE).toContain("do not add up to the open total");
  });

  it("put the server's stale days in the tooltip", () => {
    const stale = HYGIENE_KINDS.find((k) => k.kind === "stale_in_progress")!;
    expect(kindTitle(stale.title, REPORT)).toContain("14 days");
    expect(kindTitle(stale.title, { ...REPORT, stale_days: undefined as never }))
      .not.toContain("{days}");
  });
});

describe("the reads", () => {
  it("take a count only when the server sent a number", () => {
    expect(hygieneCount(REPORT, "no_assignee")).toBe(3);
    expect(hygieneCount(REPORT, "no_due_date")).toBe(0);
    expect(hygieneCount(REPORT, "no_estimate")).toBeUndefined();
    expect(hygieneCount(null, "no_assignee")).toBeUndefined();
  });

  it("take the rows of one kind, in the server's order", () => {
    expect(hygieneRows(REPORT, "no_assignee").map((r) => r.id)).toEqual(["a"]);
    expect(hygieneRows(REPORT, "no_estimate")).toEqual([]);
    expect(hygieneRows({ ...REPORT, rows: "x" as never }, "no_assignee")).toEqual([]);
  });

  it("say how many titles are not on screen, and nothing when none are cut", () => {
    expect(moreNote(23, 5)).toBe("…and 18 more");
    expect(moreNote(5, 5)).toBeNull();
    expect(moreNote(undefined, 5)).toBeNull();
  });
});
