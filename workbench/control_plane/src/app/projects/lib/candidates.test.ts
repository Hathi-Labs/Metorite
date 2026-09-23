import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { NO_ACCESS } from "@/lib/access";

import {
  type CandidatesResponse,
  type FitCandidate,
  describeCandidate,
  shouldAskForFit,
  suggestedRows,
} from "./candidates";

describe("shouldAskForFit", () => {
  const hr = { ...NO_ACCESS, capabilities: ["admin:members:read"] };
  it("asks for a task when the member holds the grant", () => {
    expect(shouldAskForFit("t1", hr, false)).toBe(true);
  });
  it("skips the request once access says the member lacks the grant", () => {
    expect(shouldAskForFit("t1", NO_ACCESS, false)).toBe(false);
  });
  it("asks while access is still loading, and the server decides", () => {
    expect(shouldAskForFit("t1", NO_ACCESS, true)).toBe(true);
  });
  it("never asks without a task", () => {
    expect(shouldAskForFit(undefined, hr, false)).toBe(false);
  });
});

function person(over: Partial<FitCandidate> = {}): FitCandidate {
  return {
    person_id: "p1",
    name: "Cara Diaz",
    email: "cara@x.io",
    skill_points: 2,
    matched_skills: ["CAD"],
    spare_hours: 12.5,
    away: null,
    rank: 25,
    warnings: [],
    ...over,
  };
}

function answer(over: Partial<CandidatesResponse> = {}): CandidatesResponse {
  return {
    hr_visible: true,
    due_on: "2026-09-30",
    window: { starts_on: "2026-09-23", ends_on: "2026-09-30", days: 7, basis: "due_date" },
    candidates: [
      person(),
      person({ person_id: "p2", name: "Dan Ek", email: "dan@x.io", rank: 10 }),
    ],
    hours_basis: true,
    pool_size: 9,
    ...over,
  };
}

describe("suggestedRows", () => {
  it("keeps the server's order and ranks nothing itself", () => {
    const res = answer({
      candidates: [
        person({ email: "low@x.io", rank: 1 }),
        person({ email: "high@x.io", rank: 99 }),
      ],
    });
    expect(suggestedRows(res).map((c) => c.email)).toEqual(["low@x.io", "high@x.io"]);
  });

  it("shows nothing without the HR grant (§13.4 rule 1)", () => {
    expect(suggestedRows(answer({ hr_visible: false }))).toEqual([]);
    const hidden: CandidatesResponse = {
      hr_visible: false,
      due_on: null,
      window: { starts_on: "2026-09-23", ends_on: "2026-10-07", days: 14, basis: "default" },
    };
    expect(suggestedRows(hidden)).toEqual([]);
  });

  it("survives no answer and a malformed one", () => {
    expect(suggestedRows(null)).toEqual([]);
    const broken = { ...answer(), candidates: "nope" } as unknown as CandidatesResponse;
    expect(suggestedRows(broken)).toEqual([]);
  });

  it("drops somebody already on the task, whatever the case", () => {
    const rows = suggestedRows(answer(), { assigned: ["CARA@x.io"] });
    expect(rows.map((c) => c.email)).toEqual(["dan@x.io"]);
  });

  it("narrows to the typed text, by name or address", () => {
    expect(suggestedRows(answer(), { query: "dan" }).map((c) => c.email)).toEqual(["dan@x.io"]);
    expect(suggestedRows(answer(), { query: "CARA@" }).map((c) => c.email)).toEqual([
      "cara@x.io",
    ]);
    expect(suggestedRows(answer(), { query: "zed" })).toEqual([]);
  });
});

describe("describeCandidate", () => {
  it("says what matched, the spare hours and every warning", () => {
    const line = describeCandidate(
      person({
        away: { kind: "leave", until: "2026-09-26" },
        warnings: ["3 tasks in progress, over the limit of 2"],
      }),
    );
    expect(line).toBe(
      "Knows CAD · 12.5h spare · away (leave) until 2026-09-26 · 3 tasks in progress, over the limit of 2",
    );
  });

  it("says an absence once when the due-date warning already names it", () => {
    const line = describeCandidate(
      person({
        away: { kind: "leave", until: "2026-10-01" },
        warnings: ["Away (leave) on the due date 2026-09-30, until 2026-10-01"],
      }),
    );
    expect(line).toBe("Knows CAD · 12.5h spare · Away (leave) on the due date 2026-09-30, until 2026-10-01");
  });

  it("prints no spare figure when the server left it out (§13.4 rule 2)", () => {
    const rest = person();
    delete rest.spare_hours;
    expect(describeCandidate(rest)).toBe("Knows CAD");
  });
});

describe("where Suggested renders", () => {
  const read = (rel: string) =>
    readFileSync(resolve(__dirname, "..", rel), "utf-8");

  it("only the task panel asks for it", () => {
    // `taskId` is what turns "Suggested" on. The bulk bar and the move
    // dialog hold many tasks or none, so they must not pass it.
    expect(read("components/TaskBody.tsx")).toMatch(/<AssigneePicker[\s\S]*?taskId=/);
    for (const rel of ["components/BulkBar.tsx", "components/MoveTasksDialog.tsx"]) {
      const source = read(rel);
      const pickers = source.match(/<AssigneePicker[\s\S]*?\/>/g) ?? [];
      expect(pickers.length, rel).toBeGreaterThan(0);
      for (const picker of pickers) expect(picker, rel).not.toMatch(/taskId=/);
    }
  });

  it("a pick goes through the picker's onPick, the assignees PUT", () => {
    const picker = read("components/AssigneePicker.tsx");
    expect(picker).toMatch(/onPick\(c\.email\)/);
    expect(read("components/TaskBody.tsx")).toMatch(/saveAssignees\(next\)/);
  });
});
