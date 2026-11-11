import { describe, expect, it } from "vitest";

import { CHECK_RESULT_LOOK, checkSummary, runDay } from "./taskCheck";

const check = {
  text: "gh pr view 11 --json state",
  last_run_at: null,
  last_result: null,
  last_evidence: null,
  due: true,
};

describe("the Check of a task (SW-23)", () => {
  it("says that a Check with no run has not run yet", () => {
    expect(checkSummary(check)).toEqual({
      result: null,
      when: "Not run yet. An agent runs it next.",
    });
  });

  it("names the last result and the day of the last run", () => {
    const at = new Date(2026, 10, 12, 9, 30).toISOString();
    const summary = checkSummary({ ...check, last_run_at: at, last_result: "open", due: false });
    expect(summary.result).toEqual(CHECK_RESULT_LOOK.open);
    expect(summary.when).toBe("Last run 12 Nov 2026.");
  });

  it("says when a new run is due", () => {
    const at = new Date(2026, 9, 1, 12).toISOString();
    const summary = checkSummary({ ...check, last_run_at: at, last_result: "cannot_run" });
    expect(summary.result?.label).toBe("Could not run");
    expect(summary.when).toBe("Last run 1 Oct 2026. A new run is due.");
  });

  it("gives each result one label and a Badge tone", () => {
    expect(Object.keys(CHECK_RESULT_LOOK).sort()).toEqual(["cannot_run", "open", "passed"]);
    expect(CHECK_RESULT_LOOK.passed).toMatchObject({ label: "Passed", tone: "success" });
  });

  it("reads a bad instant as no day", () => {
    expect(runDay("not a date")).toBeNull();
    expect(runDay(null)).toBeNull();
  });
});
