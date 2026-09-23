/**
 * D73.9 — Next Actions groups by the Projects status CATEGORY, and a drag
 * between groups resolves to a lane of the task's OWN project.
 *
 * Owner request, 2026-09-23: "if we do not have any ClickUp connection, then
 * the status mapping also needs to be removed from the settings of My Tasks.
 * Make sure we are properly mapping stages with the Projects app."
 *
 * Three claims, and the fixtures are two projects that name their lanes
 * differently, because that is the case the old stage list got wrong:
 *
 *   1. The group is the lane's category, never its name.
 *   2. A category resolves to the FIRST lane by position with that category,
 *      in whichever project the task lives in.
 *   3. The store PATCHes that lane's id, completes through `/complete` for
 *      Done, and moves nothing when the project has no such lane.
 *
 * Plus the source fence on the settings modal: no ClickUp, no stage editor.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, fetchMyTaskLanes: vi.fn() };
});
vi.mock("./lens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lens")>();
  return {
    ...actual,
    lensSetStatusId: vi.fn(async () => undefined),
    lensGetItem: vi.fn(),
  };
});

import { fetchMyTaskLanes } from "./api";
import { type LensLane, lensGetItem, lensSetStatusId } from "./lens";
import {
  laneForCategory,
  nextCategoryOf,
  noLaneMessage,
} from "./statusCategory";
import { useTaskStore } from "./taskStore";
import type { GtdItem } from "./types";

const lane = (
  id: string,
  project: string,
  name: string,
  category: string,
  position: number,
): LensLane => ({ id, project_id: project, name, color: "", position, category });

/** A workshop board: "Queued", then two in-progress lanes, then "Shipped". */
const WORKSHOP = [
  // Out of position order on purpose: the route's row order must not decide.
  lane("w-done", "workshop", "Shipped", "done", 40),
  lane("w-test", "workshop", "Testing", "in_progress", 30),
  lane("w-queue", "workshop", "Queued", "todo", 10),
  lane("w-build", "workshop", "Building", "in_progress", 20),
];
/** An office board with the default names, and no in-progress lane at all. */
const OFFICE = [
  lane("o-todo", "office", "To do", "todo", 10),
  lane("o-done", "office", "Done", "done", 20),
  lane("o-back", "office", "Backlog", "backlog", 5),
];

describe("nextCategoryOf — the group is the category, not the lane name", () => {
  it("puts two differently named in-progress lanes in one group", () => {
    expect(nextCategoryOf({ statusCategory: "in_progress", disposition: "NEXT" })).toBe(
      "in_progress",
    );
    // "Building" and "In progress" differ only in name.
    const building = { statusCategory: "in_progress", workflowStage: "Building" };
    const plain = { statusCategory: "in_progress", workflowStage: "In progress" };
    expect(nextCategoryOf({ ...building, disposition: "NEXT" })).toBe(
      nextCategoryOf({ ...plain, disposition: "NEXT" }),
    );
  });

  it("keeps backlog, triage and cancelled out of Next", () => {
    for (const c of ["backlog", "triage", "cancelled"]) {
      expect(nextCategoryOf({ statusCategory: c, disposition: "NEXT" }), c).toBeNull();
    }
  });

  it("says Done for a task I completed, whatever its lane says", () => {
    expect(nextCategoryOf({ statusCategory: "todo", disposition: "DONE" })).toBe("done");
  });

  it("files a row with no category (the demo backend) under To do", () => {
    expect(nextCategoryOf({ statusCategory: undefined, disposition: "NEXT" })).toBe("todo");
  });
});

describe("laneForCategory — the first lane by position in the task's project", () => {
  it("picks the lowest position, not the first row the route sent", () => {
    expect(laneForCategory(WORKSHOP, "in_progress")?.name).toBe("Building");
    expect(laneForCategory(WORKSHOP, "todo")?.id).toBe("w-queue");
  });

  it("answers the same category with each project's own lane", () => {
    expect(laneForCategory(WORKSHOP, "done")?.name).toBe("Shipped");
    expect(laneForCategory(OFFICE, "done")?.name).toBe("Done");
  });

  it("answers undefined when the project has no lane of that category", () => {
    expect(laneForCategory(OFFICE, "in_progress")).toBeUndefined();
    expect(noLaneMessage("in_progress", "Office")).toContain('"Office" has no In progress lane');
  });
});

// ── The store action ────────────────────────────────────────────────────────

const task = (over: Partial<GtdItem>): GtdItem => ({
  id: "t1",
  source: "LOCAL",
  title: "Wire the jig",
  disposition: "NEXT",
  isTwoMinute: false,
  isMine: true,
  createdAt: "2026-09-23T00:00:00Z",
  updatedAt: "2026-09-23T00:00:00Z",
  statusCategory: "todo",
  workflowStage: "Queued",
  projectId: "workshop",
  projectName: "Workshop",
  ...over,
});

describe("setCategory — resolve, then PATCH the lane id", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({ items: [], syncFailure: null });
  });

  it("moves a workshop task to Building, the first in-progress lane", async () => {
    const t = task({});
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    vi.mocked(lensGetItem).mockResolvedValue({
      ...t, statusCategory: "in_progress", workflowStage: "Building",
    });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setCategory("t1", "in_progress");
    expect(fetchMyTaskLanes).toHaveBeenCalledWith("t1");
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-build");
    const after = useTaskStore.getState().items[0];
    expect(after.workflowStage).toBe("Building");
    expect(nextCategoryOf(after)).toBe("in_progress");
  });

  it("moves nothing and says why when the project has no such lane", async () => {
    const t = task({ projectId: "office", projectName: "Office", workflowStage: "To do" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(OFFICE);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setCategory("t1", "in_progress");
    expect(lensSetStatusId).not.toHaveBeenCalled();
    expect(useTaskStore.getState().items[0]).toEqual(t);
    expect(useTaskStore.getState().syncFailure?.message).toContain(
      '"Office" has no In progress lane',
    );
  });

  it("completes a task dropped on Done, and asks for no lane", async () => {
    const quickDispose = vi.fn();
    const t = task({});
    useTaskStore.setState({ backend: "live", items: [t], quickDispose });
    await useTaskStore.getState().setCategory("t1", "done");
    expect(quickDispose).toHaveBeenCalledWith("t1", "DONE");
    expect(fetchMyTaskLanes).not.toHaveBeenCalled();
    expect(lensSetStatusId).not.toHaveBeenCalled();
  });
});

// ── The settings modal: the mapping and the stage editor are gone ──────────

describe("My Tasks settings (D73.9)", () => {
  const modal = readFileSync(
    fileURLToPath(new URL("../components/TaskSettingsModal.tsx", import.meta.url)),
    "utf-8",
  );

  it("names no ClickUp anywhere in the source", () => {
    expect(modal).not.toMatch(/clickup/i);
  });

  it("carries no stage editor and no status mapping", () => {
    for (const gone of [
      "StageEditor",
      "StatusMappingEditor",
      "workflowStages",
      "statusStageMap",
      "fetchStatusCatalog",
      "Kanban stages",
    ]) {
      expect(modal, gone).not.toContain(gone);
    }
  });

  it("says where the stages come from", () => {
    expect(modal).toContain("each project&rsquo;s lanes in Projects");
  });
});
