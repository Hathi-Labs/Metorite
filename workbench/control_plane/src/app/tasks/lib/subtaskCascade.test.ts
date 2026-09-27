/**
 * D-PM-38 decisions 2 and 4 (Subtasks S5) — My Tasks' store.
 *
 * - Mark done on a parent with open subtasks ASKS, and writes nothing until
 *   the member answers. "Only this task" still completes the parent.
 * - "Complete all" is ONE request, and Undo puts back the parent's status and
 *   each subtask's EXACT prior status, keeping a teammate's newer move.
 * - Archive of a task (or a selection) with subtasks asks, with the box
 *   ticked, and Undo restores exactly the ids the gateway shelved.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchMyTaskLanes: vi.fn(async () => []),
    apiPatchItem: vi.fn(async () => ({})),
    apiBulkDispose: vi.fn(async () => []),
    apiCompleteCascade: vi.fn(),
    apiArchiveItem: vi.fn(async () => ({})),
    apiArchiveCascade: vi.fn(async () => []),
    apiBulkArchive: vi.fn(async () => []),
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

import { ProjectsApiError } from "@/app/projects/lib/api";

import {
  apiArchiveCascade,
  apiBulkArchive,
  apiBulkDispose,
  apiCompleteCascade,
} from "./api";
import { lensGetItem, lensSetStatusId } from "./lens";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const flush = async () => {
  for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
};

const task = (over: Partial<MyTask>): MyTask => ({
  id: "p",
  source: "LOCAL",
  title: "Ship the jig",
  disposition: "NEXT",
  isTwoMinute: false,
  isMine: true,
  createdAt: "2026-09-23T00:00:00Z",
  updatedAt: "2026-09-23T00:00:00Z",
  statusCategory: "todo",
  statusId: "todo",
  workflowStage: "To do",
  projectId: "workshop",
  projectName: "Workshop",
  ...over,
});

/** The server: each task's status and row version. */
const server: Record<string, { statusId: string; v: number }> = {};
const serverRow = (id: string): MyTask =>
  task({
    id,
    statusId: server[id].statusId,
    updatedAt: `v${server[id].v}`,
    statusCategory: server[id].statusId.startsWith("done") ? "done" : "todo",
    disposition: server[id].statusId.startsWith("done") ? "DONE" : "NEXT",
  });
const move = (id: string, statusId: string) => {
  server[id] = { statusId, v: server[id].v + 1 };
};

beforeEach(() => {
  Object.assign(server, {
    p: { statusId: "todo", v: 1 },
    k1: { statusId: "review", v: 1 },
    k2: { statusId: "todo-b", v: 1 },
  });
  vi.mocked(lensGetItem).mockImplementation(async (id: string) => serverRow(id));
  vi.mocked(lensSetStatusId).mockImplementation(async (id, sid, opts) => {
    if (opts?.ifMatch && opts.ifMatch !== `v${server[id].v}`) {
      throw new ProjectsApiError("This row changed since you loaded it.", 412);
    }
    move(id, sid);
  });
  vi.mocked(apiCompleteCascade).mockImplementation(async (id: string) => {
    move("p", "done");
    move("k1", "done");
    move("k2", "done-b");
    return {
      item: serverRow(id),
      cascaded: 2,
      changes: [
        { task_id: "k1", from_status_id: "review", to_status_id: "done" },
        { task_id: "k2", from_status_id: "todo-b", to_status_id: "done-b" },
      ],
    };
  });
});

afterEach(() => {
  vi.clearAllMocks();
  useTaskStore.setState({
    items: [], syncFailure: null, undoSnapshot: null, subtaskPrompt: null,
    fromProjectIds: new Set(),
  });
});

const PARENT = task({ id: "p", subtaskCount: 3, subtaskDone: 1 });

describe("Mark done asks about open subtasks (decision 2)", () => {
  it("asks, and writes nothing yet", async () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().quickDispose("p", "DONE");
    await flush();
    expect(useTaskStore.getState().subtaskPrompt).toEqual({
      kind: "complete", ids: ["p"], count: 2, title: "Ship the jig",
    });
    expect(apiBulkDispose).not.toHaveBeenCalled();
    expect(apiCompleteCascade).not.toHaveBeenCalled();
  });

  it("does not ask for a task with no open subtask", async () => {
    useTaskStore.setState({
      backend: "live", items: [task({ subtaskCount: 2, subtaskDone: 2 })],
    });
    useTaskStore.getState().quickDispose("p", "DONE");
    await flush();
    expect(useTaskStore.getState().subtaskPrompt).toBeNull();
    expect(apiBulkDispose).toHaveBeenCalledWith(["p"], "DONE");
  });

  it("Only this task (and Escape) still completes the parent, alone", async () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().quickDispose("p", "DONE");
    useTaskStore.getState().answerSubtaskPrompt(false);
    await flush();
    expect(useTaskStore.getState().subtaskPrompt).toBeNull();
    expect(apiBulkDispose).toHaveBeenCalledWith(["p"], "DONE");
    expect(apiCompleteCascade).not.toHaveBeenCalled();
  });

  it("Complete all is one request, and the receipt counts the subtasks", async () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().quickDispose("p", "DONE");
    useTaskStore.getState().answerSubtaskPrompt(true);
    await flush();
    expect(apiCompleteCascade).toHaveBeenCalledWith("p");
    expect(apiBulkDispose).not.toHaveBeenCalled();
    expect(useTaskStore.getState().undoSnapshot?.label).toBe("Completed · and 2 subtasks");
  });
});

describe("Undo covers the cascade (D79)", () => {
  it("puts back the parent's status, then each subtask's exact prior status", async () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().quickDispose("p", "DONE", { includeSubtasks: true });
    await flush();
    useTaskStore.getState().undoLastChange();
    await flush();
    expect(server.p.statusId).toBe("todo");
    expect(server.k1.statusId).toBe("review");
    expect(server.k2.statusId).toBe("todo-b");
    // Each with the row version it read, so a newer move answers 412.
    expect(lensSetStatusId).toHaveBeenCalledWith("k1", "review", { ifMatch: "v2" });
    expect(lensSetStatusId).toHaveBeenCalledWith("k2", "todo-b", { ifMatch: "v2" });
  });

  it("keeps a teammate's move on a subtask, and says so", async () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().quickDispose("p", "DONE", { includeSubtasks: true });
    await flush();
    move("k2", "wontfix"); // somebody moved it on after the complete
    useTaskStore.getState().undoLastChange();
    await flush();
    expect(server.k1.statusId).toBe("review");
    expect(server.k2.statusId).toBe("wontfix");
    expect(useTaskStore.getState().syncFailure?.message).toMatch(/changed by someone since/);
  });
});

describe("Archive takes the subtasks along, box ticked (decision 4)", () => {
  it("asks for a task that has subtasks, and archives nothing yet", () => {
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().archiveItem("p", true);
    expect(useTaskStore.getState().subtaskPrompt).toMatchObject({
      kind: "archive", ids: ["p"], count: 3,
    });
    expect(apiArchiveCascade).not.toHaveBeenCalled();
  });

  it("with the box ticked: one request, and Undo restores exactly what it shelved", async () => {
    vi.mocked(apiArchiveCascade).mockResolvedValue(["k1", "k2"]);
    useTaskStore.setState({ backend: "live", items: [PARENT] });
    useTaskStore.getState().archiveItem("p", true);
    useTaskStore.getState().answerSubtaskPrompt(true);
    await flush();
    expect(apiArchiveCascade).toHaveBeenCalledWith(["p"]);
    const snap = useTaskStore.getState().undoSnapshot;
    expect(snap?.label).toBe("Archived · and 2 subtasks");
    useTaskStore.getState().undoLastChange();
    await flush();
    expect(apiBulkArchive).toHaveBeenCalledWith(["p", "k1", "k2"], false);
  });

  it("the bulk bar asks ONCE for the whole selection", async () => {
    const other = task({ id: "q", subtaskCount: 1, subtaskDone: 0 });
    useTaskStore.setState({ backend: "live", items: [PARENT, other] });
    useTaskStore.getState().bulkArchive(["p", "q"], true);
    expect(useTaskStore.getState().subtaskPrompt).toMatchObject({
      kind: "archive", ids: ["p", "q"], count: 4, bulk: true,
    });
    useTaskStore.getState().answerSubtaskPrompt(false);
    await flush();
    expect(apiBulkArchive).toHaveBeenCalledWith(["p", "q"], true);
    expect(apiArchiveCascade).not.toHaveBeenCalled();
  });
});
