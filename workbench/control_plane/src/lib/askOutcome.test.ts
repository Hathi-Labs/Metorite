/**
 * The one line an answered card folds to (owner report, 2026-10-19).
 *
 * Mutations: return "" for a plan -> "a sent plan says how many tasks went"
 * fails; drop the decline branch -> "a decline says Not answered" fails.
 */
import { describe, expect, it } from "vitest";

import { askOutcome, OUTCOME_MAX } from "@/lib/askOutcome";

const template = (name: string, data: Record<string, unknown> = {}) => ({
  type: "template", props: { name, data }, request_id: "a".repeat(32),
});
const TASKS = [
  { key: "t1", title: "Build it", owner: "", effort_mins: 60, due: "2026-11-01" },
  { key: "t2", title: "Ship it", owner: "", effort_mins: 60, due: "2026-11-02" },
];

describe("askOutcome", () => {
  it("is empty with no answer, so a waiting card folds to nothing", () => {
    expect(askOutcome(template("planCard", { tasks: TASKS }), undefined)).toBe("");
    expect(askOutcome(template("optionPicker"), "")).toBe("");
  });

  it("a sent plan says how many tasks went, and a cancel says so", () => {
    const plan = template("planCard", { tasks: TASKS });
    const sent = `Review plan — ${JSON.stringify({ project: { name: "P" }, tasks: [TASKS[0]] })}`;
    expect(askOutcome(plan, sent)).toBe("1 task sent for approval");
    expect(askOutcome(plan, "Cancel")).toBe("cancelled, nothing created");
    // #1152: the plan's Cancel sends the decline, and it is a cancel there.
    expect(askOutcome(plan, "__declined__")).toBe("cancelled, nothing created");
  });

  it("a picker names the pick, a form says Sent, and a decline says Not answered", () => {
    expect(askOutcome(template("optionPicker"), "Selected: Weekly, Monthly")).toBe("Sent: Weekly, Monthly");
    expect(askOutcome(template("formCard"), 'Save — {"a":1}')).toBe("Sent");
    expect(askOutcome(template("optionPicker"), "__declined__")).toBe("Not answered");
    expect(askOutcome({ type: "card", children: [] }, "yes")).toBe("Answered");
  });

  it("cuts a long pick to one line, and strips the model's «marks»", () => {
    const long = askOutcome(template("optionPicker"), `Selected: ${"x".repeat(200)}`);
    expect(long.length).toBe(OUTCOME_MAX);
    expect(long.endsWith("…")).toBe(true);
    expect(askOutcome(template("optionPicker"), "Selected: «Ops»")).toBe("Sent: Ops");
  });
});
