import { describe, expect, it } from "vitest";

import { lanesAfter, planShowView } from "./chatView";
import { NO_LANES } from "./grouping";

const P = "0f8fad5b-d9cb-469f-a165-70867728950e";
const V = "7c9e6679-7425-40de-944b-e07fc1f90ae7";

describe("planShowView", () => {
  it("refuses an id that is not one, a mode and a grouping that are not one", () => {
    expect(planShowView({ project_id: "x" })).toBe("not a project id");
    expect(planShowView({ project_id: P, mode: "gantt" })).toBe("not a mode");
    expect(planShowView({ project_id: P, group_by: "colour" })).toBe("not a grouping");
    expect(planShowView({ project_id: P, filters: [1] })).toBe("filters is an object");
  });

  it("reads filters through the saved-view reader, so the two agree", () => {
    const plan = planShowView({
      project_id: P,
      mode: "board",
      filters: { overdue: true, assignee: "priya@x.in", tags: "bug,ops", archived_only: true },
      group_by: "assignee",
    });
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.mode).toBe("board");
    expect(plan.filters).toMatchObject({
      overdue: true,
      assignee: "priya@x.in",
      tags: ["bug", "ops"],
      archived: true,
      q: "",
    });
    expect(plan.groupBy).toBe("assignee");
    expect(plan.view).toBeNull();
  });

  it("an empty filters object clears every filter", () => {
    const plan = planShowView({ project_id: P, filters: {} });
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.filters).toMatchObject({ q: "", overdue: false, tags: [], assignee: "" });
  });

  it("keeps what it was not told about", () => {
    const plan = planShowView({ project_id: P, mode: "timeline" });
    if (typeof plan === "string") throw new Error(plan);
    expect(plan).toMatchObject({ filters: null, groupBy: null, subGroupBy: null, view: null });
  });

  it("opens a saved view with its own config", () => {
    const plan = planShowView({
      project_id: P,
      view_id: V,
      view_config: { filters: { overdue: true }, group_by: "tag", sub_group_by: "assignee" },
    });
    if (typeof plan === "string") throw new Error(plan);
    expect(plan.view?.id).toBe(V);
    expect(plan.view?.groupBy).toBe("tag");
    expect(plan.view?.lanes.subGroupBy).toBe("assignee");
    expect(plan.view?.filters.overdue).toBe(true);
  });
});

describe("lanesAfter", () => {
  it("lanes by a second axis, and goes flat when it equals the main one", () => {
    expect(lanesAfter(NO_LANES, "status", "assignee").subGroupBy).toBe("assignee");
    expect(lanesAfter(NO_LANES, "assignee", "assignee").subGroupBy).toBe("none");
    const laned = { ...NO_LANES, subGroupBy: "tag" as const };
    expect(lanesAfter(laned, "tag", null).subGroupBy).toBe("none");
    expect(lanesAfter(laned, "status", null)).toBe(laned);
  });
});
