// WS-50 slice 3 — where a customer is, and what comes next.

import { describe, expect, it } from "vitest";

import { lifecycleSteps, nextStep, tabFrom } from "./lifecycle";

describe("lifecycleSteps", () => {
  it("marks a trial customer at the start", () => {
    expect(lifecycleSteps("trial").map((s) => s.state)).toEqual(["current", "todo", "todo", "todo"]);
  });

  it("puts a suspended customer on Paid, and says suspended", () => {
    const steps = lifecycleSteps("suspended");
    expect(steps[1]).toEqual({ key: "active", label: "Paid (suspended)", state: "current" });
    expect(steps[0].state).toBe("done");
  });

  it("marks every earlier step done for a deleted customer", () => {
    expect(lifecycleSteps("deleted").map((s) => s.state)).toEqual(["done", "done", "done", "current"]);
  });
});

describe("nextStep", () => {
  it("sends a trial customer to Billing to start the paid plan", () => {
    expect(nextStep("trial", null)).toMatchObject({ tab: "billing" });
  });

  it("catches a paid plan under a trial account, the old two-activate trap", () => {
    expect(nextStep("trial", "active")).toMatchObject({ tab: "access" });
    expect(nextStep("trial", "active")?.text).toContain("End the trial");
  });

  it("says nothing for a healthy paying customer", () => {
    expect(nextStep("active", "active")).toBeNull();
  });
});

describe("tabFrom", () => {
  it("accepts a known tab and falls back to the overview", () => {
    expect(tabFrom("billing")).toBe("billing");
    expect(tabFrom(["people"])).toBe("people");
    expect(tabFrom("nonsense")).toBe("overview");
    expect(tabFrom(undefined)).toBe("overview");
  });
});
