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
 *   2. A stage resolves through the ONE shared resolver
 *      (`@/lib/statusCategory`), to the FIRST lane by position, in whichever
 *      project the task lives in (D79).
 *   3. The store PATCHes that lane's id, completes through `/complete` for
 *      the first Done status, moves nothing when the project has no such
 *      lane, and ASKS when the stage holds two or more (D79).
 *
 * Plus two source fences: the settings modal (no ClickUp, no stage editor),
 * and the Tasks library (no first-by-position resolver of its own).
 */
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchMyTaskLanes: vi.fn(),
    apiCapture: vi.fn(),
    apiPatchItem: vi.fn(async () => ({})),
    apiBulkDispose: vi.fn(async () => []),
  };
});
vi.mock("./lens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lens")>();
  return {
    ...actual,
    lensSetStatusId: vi.fn(async () => undefined),
    lensGetItem: vi.fn(),
  };
});

import { apiBulkDispose, apiCapture, apiPatchItem, fetchMyTaskLanes } from "./api";
import { type LensLane, lensGetItem, lensSetStatusId } from "./lens";
import { landingLane } from "@/lib/statusCategory";

import { nextCategoryOf, noLaneMessage } from "./statusCategory";
import { itemsForView, useTaskStore, viewCounts } from "./taskStore";
import type { MyTask } from "./types";

/** Let every queued promise settle. */
const flush = async () => {
  for (let i = 0; i < 8; i += 1) await new Promise((r) => setTimeout(r, 0));
};

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

  it("puts a stated NEXT in a backlog or triage lane under To do (§4.9 point 5)", () => {
    // "backlog is Someday" is the derivation for an UNSTATED disposition.
    // Once the member states NEXT, the lane must not hide the row.
    for (const c of ["backlog", "triage"]) {
      expect(nextCategoryOf({ statusCategory: c, disposition: "NEXT" }), c).toBe("todo");
    }
  });

  it("keeps a cancelled task out of Next, though it reads DONE (D79)", () => {
    // The row the gateway really sends: `effective_disposition` turns every
    // closing lane, Cancelled too, into DONE. This test once used a NEXT
    // disposition, a row that cannot exist, and so passed while every
    // cancelled task drew under Done.
    expect(nextCategoryOf({ statusCategory: "cancelled", disposition: "DONE" })).toBeNull();
  });

  it("says Done for a task I completed, whatever its lane says", () => {
    expect(nextCategoryOf({ statusCategory: "todo", disposition: "DONE" })).toBe("done");
  });

  it("files a row with no category (the demo backend) under To do", () => {
    expect(nextCategoryOf({ statusCategory: undefined, disposition: "NEXT" })).toBe("todo");
  });
});

describe("landingLane — the first lane by position in the task's project", () => {
  it("picks the lowest position, not the first row the route sent", () => {
    expect(landingLane(WORKSHOP, "in_progress")?.name).toBe("Building");
    expect(landingLane(WORKSHOP, "todo")?.id).toBe("w-queue");
  });

  it("answers the same category with each project's own lane", () => {
    expect(landingLane(WORKSHOP, "done")?.name).toBe("Shipped");
    expect(landingLane(OFFICE, "done")?.name).toBe("Done");
  });

  it("answers null when the project has no lane of that category", () => {
    expect(landingLane(OFFICE, "in_progress")).toBeNull();
    expect(noLaneMessage("in_progress", "Office")).toContain('"Office" has no In progress lane');
  });
});

describe("one client resolver (D79)", () => {
  // The Tasks library held its own first-by-position copy
  // (`laneForCategory`) beside Projects' `landingLane`. Two copies of one
  // rule drift, so any sort on `.position` in the Tasks library fails here.
  const dir = fileURLToPath(new URL(".", import.meta.url));
  const sources = readdirSync(dir)
    .filter((f) => /\.(ts|tsx)$/.test(f) && !/\.test\.ts$/.test(f))
    .map((f) => [f, readFileSync(`${dir}/${f}`, "utf-8")] as const);

  it("reads the library at all", () => {
    expect(sources.map(([f]) => f)).toContain("taskStore.ts");
  });

  it("defines no first-by-position resolver in the Tasks library", () => {
    for (const [file, text] of sources) {
      expect(text, file).not.toMatch(/\.position\s*-\s*\w+\.position/);
      expect(text, file).not.toMatch(/function\s+laneForCategory\b/);
    }
  });
});

// ── The store action ────────────────────────────────────────────────────────

const task = (over: Partial<MyTask>): MyTask => ({
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

describe("setStage — resolve, then PATCH the lane id, or ask (D79)", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({ items: [], syncFailure: null, stagePrompt: null, undoSnapshot: null });
  });

  it("asks when the stage holds two statuses, and writes nothing yet", async () => {
    const t = task({});
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    const out = await useTaskStore.getState().setStage("t1", "in_progress");
    expect(out).toBe("asked");
    expect(lensSetStatusId).not.toHaveBeenCalled();
    expect(useTaskStore.getState().stagePrompt).toMatchObject({
      taskId: "t1",
      stage: "in_progress",
    });
  });

  it("writes the status the member picks, and names it with the project", async () => {
    const t = task({ statusId: "w-queue" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    vi.mocked(lensGetItem).mockResolvedValue({
      ...t, statusId: "w-test", statusCategory: "in_progress", workflowStage: "Testing",
    });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    const landed = vi.fn();
    await useTaskStore.getState().setStage("t1", "in_progress", { onLanded: landed });
    useTaskStore.getState().confirmStagePrompt("w-test");
    await flush();
    expect(landed).toHaveBeenCalledOnce();
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-test");
    expect(useTaskStore.getState().stagePrompt).toBeNull();
    expect(useTaskStore.getState().undoSnapshot?.label).toBe("Moved to Testing · Workshop");
  });

  it("writes nothing and runs no rank when the member backs out", async () => {
    const t = task({});
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    const landed = vi.fn();
    await useTaskStore.getState().setStage("t1", "in_progress", { onLanded: landed });
    useTaskStore.getState().cancelStagePrompt();
    await flush();
    expect(landed).not.toHaveBeenCalled();
    expect(lensSetStatusId).not.toHaveBeenCalled();
    expect(useTaskStore.getState().items[0]).toEqual(t);
  });

  it("writes at once when the stage holds one status", async () => {
    const t = task({ statusId: "w-build", statusCategory: "in_progress" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    vi.mocked(lensGetItem).mockResolvedValue({ ...t, statusId: "w-queue", statusCategory: "todo" });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    expect(await useTaskStore.getState().setStage("t1", "todo")).toBe("written");
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-queue");
  });

  it("quick-add takes the first status with no question (noAsk)", async () => {
    const t = task({});
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    vi.mocked(lensGetItem).mockResolvedValue({ ...t, statusCategory: "in_progress" });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStage("t1", "in_progress", { noAsk: true });
    expect(useTaskStore.getState().stagePrompt).toBeNull();
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-build");
  });

  it("moves nothing and says why when the project has no such lane", async () => {
    const t = task({ projectId: "office", projectName: "Office", workflowStage: "To do" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(OFFICE);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    expect(await useTaskStore.getState().setStage("t1", "in_progress")).toBe("none");
    expect(lensSetStatusId).not.toHaveBeenCalled();
    expect(useTaskStore.getState().items[0]).toEqual(t);
    expect(useTaskStore.getState().syncFailure?.message).toContain(
      '"Office" has no In progress lane',
    );
  });

  it("the first Done status is Mark done: it completes, and asks for no lane write", async () => {
    const real = useTaskStore.getState().quickDispose;
    const quickDispose = vi.fn();
    const t = task({ projectId: "office", projectName: "Office" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(OFFICE);
    useTaskStore.setState({ backend: "live", items: [t], quickDispose });
    try {
      await useTaskStore.getState().setStage("t1", "done");
      expect(quickDispose).toHaveBeenCalledWith("t1", "DONE");
      expect(lensSetStatusId).not.toHaveBeenCalled();
    } finally {
      useTaskStore.setState({ quickDispose: real });
    }
  });
});

describe("setStatus — one exact status, inside the same stage too (D79)", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({ items: [], syncFailure: null, undoSnapshot: null });
  });

  it("moves Building to Testing, two statuses of one stage", async () => {
    const t = task({ statusId: "w-build", statusCategory: "in_progress", workflowStage: "Building" });
    vi.mocked(lensGetItem).mockResolvedValue({ ...t, statusId: "w-test", workflowStage: "Testing" });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStatus("t1", "w-test", { lanes: WORKSHOP });
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-test");
  });

  it("does nothing for the status the task is already in", async () => {
    const t = task({ statusId: "w-build" });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStatus("t1", "w-build", { lanes: WORKSHOP });
    expect(lensSetStatusId).not.toHaveBeenCalled();
  });

  it("names only the status for a task in my own tree", async () => {
    const t = task({ statusId: "w-queue", projectId: "root-1", projectName: "My Tasks" });
    vi.mocked(lensGetItem).mockResolvedValue(t);
    useTaskStore.setState({
      backend: "live", items: [t], syncFailure: null, personalRootId: "root-1",
    });
    await useTaskStore.getState().setStatus("t1", "w-build", { lanes: WORKSHOP });
    expect(useTaskStore.getState().undoSnapshot?.label).toBe("Moved to Building");
  });
});

// ── Undo restores the exact prior status (D79) ─────────────────────────────

describe("undo puts the exact status back, then the overlay", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({
      items: [], syncFailure: null, undoSnapshot: null, fromProjectIds: new Set(),
    });
  });

  it("undo of Mark done sends an In review task back to In review", async () => {
    const t = task({ statusId: "w-test", statusCategory: "in_progress", workflowStage: "Testing" });
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    useTaskStore.getState().quickDispose("t1", "DONE");
    await flush();
    // Hold the status write open: the overlay write must WAIT for it, or a
    // NEXT on the still-closed task reopens it into the first To do status.
    let release: () => void = () => {};
    vi.mocked(lensSetStatusId).mockImplementationOnce(
      () => new Promise<void>((resolve) => { release = resolve; }),
    );
    useTaskStore.getState().undoLastChange();
    await flush();
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-test");
    expect(apiPatchItem).not.toHaveBeenCalled();
    release();
    await flush();
    expect(apiPatchItem).toHaveBeenCalledWith("t1", { disposition: "NEXT" });
  });

  it("undo of a quick move on an untriaged board task CLEARS my overlay", async () => {
    const t = task({
      id: "b1", statusId: "w-test", isTriaged: false, disposition: "NEXT",
      projectId: "workshop", projectName: "Workshop",
    });
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    useTaskStore.setState({
      backend: "live", items: [t], syncFailure: null, fromProjectIds: new Set(["b1"]),
    });
    useTaskStore.getState().quickDispose("b1", "SOMEDAY");
    await flush();
    useTaskStore.getState().undoLastChange();
    await flush();
    expect(apiPatchItem).toHaveBeenCalledWith("b1", { disposition: null });
    expect(apiPatchItem).not.toHaveBeenCalledWith("b1", { disposition: "NEXT" });
    expect(useTaskStore.getState().fromProjectIds.has("b1")).toBe(true);
  });

  it("Mark done names the Done status it chose when there are two", async () => {
    const t = task({ statusId: "w-queue" });
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    vi.mocked(fetchMyTaskLanes).mockResolvedValue([
      ...WORKSHOP, lane("w-done2", "workshop", "Delivered", "done", 50),
    ]);
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    useTaskStore.getState().quickDispose("t1", "DONE");
    await flush();
    expect(useTaskStore.getState().undoSnapshot?.label).toBe("Done · Shipped in Workshop");
  });
});

// ── The backfilled rows (§4.9 point 5) ─────────────────────────────────────

describe("a backfilled NEXT task in the Inbox lane", () => {
  // Migration 189 moved every task into its root's Inbox lane, whose
  // category is `backlog`. The overlay kept the member's NEXT.
  const backfilled = task({
    id: "b1", statusCategory: "backlog", workflowStage: "Inbox",
    projectId: "root-1", projectName: "My Tasks",
  });
  const rows = [
    backfilled,
    task({ id: "b2", statusCategory: "in_progress", workflowStage: "Building" }),
    task({ id: "b3", statusCategory: "triage", workflowStage: "Triage" }),
  ];

  it("is drawn under To do", () => {
    expect(nextCategoryOf(backfilled)).toBe("todo");
  });

  it("is counted by the sidebar exactly as often as it is drawn", () => {
    const drawn = itemsForView(rows, "next", null).filter(
      (i) => nextCategoryOf(i) !== null,
    );
    expect(drawn.map((i) => i.id).sort()).toEqual(["b1", "b2", "b3"]);
    expect(viewCounts(rows).next).toBe(drawn.length);
  });
});

describe("moving a backlog-lane task", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({ items: [], syncFailure: null });
  });

  it("to To do resolves the first todo lane, though it already sits under To do", async () => {
    const t = task({ statusCategory: "backlog", workflowStage: "Backlog" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue([
      ...WORKSHOP, lane("w-back", "workshop", "Backlog", "backlog", 5),
    ]);
    vi.mocked(lensGetItem).mockResolvedValue({
      ...t, statusCategory: "todo", workflowStage: "Queued",
    });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStage("t1", "todo");
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-queue");
  });

  it("to In progress resolves the lane as for any other task", async () => {
    const t = task({ statusCategory: "backlog", workflowStage: "Backlog" });
    vi.mocked(fetchMyTaskLanes).mockResolvedValue(WORKSHOP);
    vi.mocked(lensGetItem).mockResolvedValue({ ...t, statusCategory: "in_progress" });
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStage("t1", "in_progress", { noAsk: true });
    expect(lensSetStatusId).toHaveBeenCalledWith("t1", "w-build");
  });

  it("does nothing for a task already in a todo lane", async () => {
    const t = task({});
    useTaskStore.setState({ backend: "live", items: [t], syncFailure: null });
    await useTaskStore.getState().setStage("t1", "todo");
    expect(fetchMyTaskLanes).not.toHaveBeenCalled();
  });
});

describe("a quick-add in the To do group", () => {
  afterEach(() => {
    vi.clearAllMocks();
    useTaskStore.setState({ items: [], syncFailure: null });
  });

  it("stays visible after the server row replaces the optimistic one", async () => {
    // The capture lands in my root's default lane, category `backlog`. The
    // clarify PATCH states NEXT. The row must stay under To do.
    const server = task({
      id: "srv-1", title: "Order the bearings", statusCategory: "backlog",
      workflowStage: "Inbox", projectId: "root-1", projectName: "My Tasks",
    });
    vi.mocked(apiCapture).mockResolvedValue({ ...server, disposition: "INBOX" });
    vi.mocked(apiPatchItem).mockResolvedValue(server);
    useTaskStore.setState({ backend: "live", items: [], syncFailure: null });
    const tmp = useTaskStore.getState().quickAddNext("Order the bearings", {
      statusCategory: "todo",
    });
    expect(tmp).toBeTruthy();
    for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
    const items = useTaskStore.getState().items;
    expect(items.map((i) => i.id)).toEqual(["srv-1"]);
    const drawn = itemsForView(items, "next", null).filter(
      (i) => nextCategoryOf(i) === "todo",
    );
    expect(drawn.map((i) => i.id)).toEqual(["srv-1"]);
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
