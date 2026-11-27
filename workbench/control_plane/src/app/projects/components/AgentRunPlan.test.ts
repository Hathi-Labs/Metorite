/**
 * SW-32 (task 1049). The checklist of a run plan, as markup. The vitest
 * environment has no DOM, so the test renders with `renderToStaticMarkup`.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  type AgentPlanStep,
  planProgressText,
  planSummary,
} from "../lib/agentRuns";
import AgentRunPlan from "./AgentRunPlan";

const PLAN: AgentPlanStep[] = [
  { content: "Read the spec", status: "completed" },
  { content: "Drop <b>tables</b>", status: "inProgress" },
  { content: "Run the tests", status: "pending" },
  { content: "Old idea", status: "canceled" },
];

const render = (plan?: AgentPlanStep[] | null) =>
  renderToStaticMarkup(createElement(AgentRunPlan, { plan }));

describe("planProgressText and planSummary", () => {
  it("counts the closed steps and names the step in progress", () => {
    expect(planSummary(PLAN)).toEqual({ done: 2, total: 4, step: "Drop <b>tables</b>" });
    expect(planProgressText(planSummary(PLAN))).toBe("2 of 4");
  });

  it("reads no plan as null", () => {
    expect(planSummary(null)).toBeNull();
    expect(planSummary([])).toBeNull();
    expect(planProgressText(null)).toBeNull();
    expect(planProgressText({ done: 0, total: 0 })).toBeNull();
  });

  it("keeps the count inside the total", () => {
    expect(planProgressText({ done: 9, total: 7 })).toBe("7 of 7");
    expect(planProgressText({ done: -1, total: 7 })).toBe("0 of 7");
  });
});

describe("AgentRunPlan", () => {
  it("draws nothing for no plan", () => {
    expect(render(null)).toBe("");
    expect(render([])).toBe("");
    expect(render(undefined)).toBe("");
  });

  it("draws the count and one row for each step with its status", () => {
    const html = render(PLAN);
    expect(html).toContain("Plan · 2 of 4");
    for (const status of ["completed", "inProgress", "pending", "canceled"]) {
      expect(html).toContain(`data-plan-step="${status}"`);
    }
    expect((html.match(/<li /g) ?? []).length).toBe(4);
    // Each status has a word for a screen reader, so colour is not the only mark.
    for (const word of ["Done: ", "In progress: ", "Not started: ", "Canceled: "]) {
      expect(html).toContain(word);
    }
  });

  it("draws the step text as text: markup in it is escaped", () => {
    const html = render(PLAN);
    expect(html).not.toContain("<b>tables</b>");
    expect(html).toContain("Drop &lt;b&gt;tables&lt;/b&gt;");
  });

  it("leaves out a status that this client does not know", () => {
    const html = render([
      ...PLAN,
      { content: "future", status: "paused" as unknown as AgentPlanStep["status"] },
    ]);
    expect(html).not.toContain("future");
    expect((html.match(/<li /g) ?? []).length).toBe(4);
  });
});
