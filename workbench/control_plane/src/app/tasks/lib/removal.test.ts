/**
 * S6g, P0 — My Tasks never hard-deletes a task that is not in my personal
 * tree (`removal.ts`, my_tasks_cutover.md §5 S6g).
 *
 * Before the fix: Next Actions → delete → `deleteItem` → the Undo window
 * closes → `dismissUndo` → `apiPurgeItem` → `DELETE /projects/tasks/{id}`.
 * A board task a colleague assigned was gone for the whole team.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiDeleteItem: vi.fn(),
    apiPurgeItem: vi.fn(),
    apiBulkDispose: vi.fn(),
    apiPatchItem: vi.fn(),
    apiRestoreItem: vi.fn(),
    fetchUntriaged: vi.fn(),
    fetchItems: vi.fn(),
  };
});

import {
  apiBulkDispose,
  apiDeleteItem,
  apiPatchItem,
  apiPurgeItem,
  apiRestoreItem,
  fetchItems,
  fetchUntriaged,
} from "./api";
import {
  REMOVE_LABEL,
  UNDO_WINDOW_SECONDS,
  canPurge,
  purgeable,
  removalCopy,
  removalLabel,
  removalLabelFor,
} from "./removal";
import { itemsForView, useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const ROOT = "root-1";
const AREA = "area-1";
const scope = { personalRootId: ROOT, areaIds: [AREA] };

const task = (id: string, over: Partial<MyTask> = {}): MyTask => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "NEXT",
  isMine: true,
  isTriaged: true,
  createdAt: "2026-09-20T09:00:00Z",
  updatedAt: "2026-09-20T09:00:00Z",
  projectId: ROOT,
  ...over,
});

const read = (rel: string) =>
  readFileSync(resolve(__dirname, "..", rel), "utf-8").replace(/\r\n/g, "\n");

describe("the rule", () => {
  it("only my root and my Areas may be purged", () => {
    expect(canPurge(task("a"), scope)).toBe(true);
    expect(canPurge(task("a", { projectId: AREA }), scope)).toBe(true);
    expect(canPurge(task("a", { projectId: "board-1" }), scope)).toBe(false);
    // A null root cannot prove a task with a project is mine: fail closed.
    expect(canPurge(task("a", { projectId: "board-1" }), { personalRootId: null, areaIds: [] })).toBe(false);
  });

  it("refuses a purge for a board row, and for an id with no row", () => {
    const rows = [task("m"), task("b", { projectId: "board-1" })];
    expect(purgeable(["m", "b", "gone"], rows, scope)).toEqual({
      allowed: ["m"],
      refused: ["b", "gone"],
    });
  });

  it("labels the gesture honestly", () => {
    expect(removalLabel(task("b", { projectId: "board-1" }), scope)).toBe(REMOVE_LABEL);
    expect(REMOVE_LABEL).toBe("Remove from my lists");
    expect(removalLabel(task("m"), scope)).toBe("Delete");
    expect(removalLabelFor([task("m"), task("b", { projectId: "board-1" })], scope)).toBe(
      "Delete or remove",
    );
  });
});

describe("the confirmation says what really happens", () => {
  const board = (id: string) => task(id, { projectId: "board-1" });

  it("my own task: undo for the toast's window, then deleted for good", () => {
    const copy = removalCopy([task("m")], scope);
    expect(copy.title).toBe("Delete this task?");
    expect(copy.confirmLabel).toBe("Delete");
    expect(copy.body).toBe(
      `You can undo this for ${UNDO_WINDOW_SECONDS} seconds. After that, your task is deleted for good.`,
    );
    expect(copy.note).toBeNull();
  });

  it("the promised window IS the toast's timer", () => {
    // The dialog quotes a number. If the toast used its own, the promise
    // could drift from what Undo really allows.
    const toast = read("components/UndoToast.tsx");
    expect(toast).toMatch(/setTimeout\(\(\) => dismissUndo\(\), UNDO_WINDOW_SECONDS \* 1000\)/);
  });

  it("a board task: removed from my lists, the board keeps it, never 'delete'", () => {
    const copy = removalCopy([board("b")], scope);
    expect(copy.title).toBe("Remove this task from your lists?");
    expect(copy.confirmLabel).toBe(REMOVE_LABEL);
    expect(copy.body).toMatch(/The team board keeps it, and nothing is deleted\./);
    expect(`${copy.title} ${copy.body} ${copy.confirmLabel}`).not.toMatch(/\bdelete\b(?!d)/i);
    expect(copy.icon).toBe("UserX");
  });

  it("a mixed set names both acts, and which tasks each one reaches", () => {
    const copy = removalCopy([task("m"), board("b"), board("c")], scope);
    expect(copy.title).toBe("Delete or remove 3 tasks?");
    expect(copy.confirmLabel).toBe("Delete or remove");
    expect(copy.body).toMatch(/your task is deleted for good\./);
    expect(copy.note).toBe(
      "2 of these are on a team board. Those leave your lists, and the board keeps them.",
    );
  });
});

describe("delete in Next Actions", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    for (const fn of [apiDeleteItem, apiPurgeItem, apiBulkDispose, apiPatchItem, apiRestoreItem]) {
      vi.mocked(fn).mockReset();
    }
    vi.mocked(apiDeleteItem).mockResolvedValue(undefined);
    vi.mocked(apiPurgeItem).mockResolvedValue(undefined);
    vi.mocked(apiPatchItem).mockImplementation(async (id) => task(String(id)));
    vi.mocked(fetchUntriaged).mockResolvedValue([]);
    vi.mocked(fetchItems).mockResolvedValue([]);
    useTaskStore.setState({
      backend: "live",
      personalRootId: ROOT,
      areas: [{ id: AREA, name: "Home", archived: false, openTasks: 0 }],
      items: [
        task("board-task", { projectId: "board-1", projectName: "Printer v3" }),
        task("my-task"),
      ],
      fromProjectIds: new Set(),
      undoSnapshot: null,
      pendingDeleteIds: null,
    });
  });
  afterEach(() => {
    vi.useRealTimers();
    useTaskStore.setState({ backend: "demo", undoSnapshot: null });
  });

  it("a board task writes the overlay TRASH and is never purged, even after the Undo window", async () => {
    vi.mocked(apiBulkDispose).mockResolvedValue([task("board-task", { disposition: "TRASH" })]);
    const s = useTaskStore.getState();
    expect(itemsForView(s.items, "next", null).map((i) => i.id)).toContain("board-task");
    s.requestDelete(["board-task"]);
    useTaskStore.getState().confirmPendingDelete();
    await vi.runAllTimersAsync();

    expect(apiBulkDispose).toHaveBeenCalledWith(["board-task"], "TRASH");
    expect(apiDeleteItem).not.toHaveBeenCalled();
    expect(useTaskStore.getState().undoSnapshot?.softDeletedIds).toBeUndefined();
    expect(useTaskStore.getState().undoSnapshot?.label).toBe("Removed it from your lists");

    // The Undo window closes.
    useTaskStore.getState().dismissUndo();
    await vi.runAllTimersAsync();
    expect(apiPurgeItem).not.toHaveBeenCalled();
  });

  it("a personal task still purges when the Undo window closes", async () => {
    useTaskStore.getState().requestDelete(["my-task"]);
    useTaskStore.getState().confirmPendingDelete();
    expect(apiDeleteItem).toHaveBeenCalledWith("my-task");
    useTaskStore.getState().dismissUndo();
    await vi.runAllTimersAsync();
    expect(apiPurgeItem).toHaveBeenCalledWith("my-task");
    expect(apiBulkDispose).not.toHaveBeenCalled();
  });

  it("a mixed selection splits: purge the mine, overlay-TRASH the board", async () => {
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    useTaskStore.getState().deleteItems(["board-task", "my-task"]);
    useTaskStore.getState().dismissUndo();
    await vi.runAllTimersAsync();
    expect(apiBulkDispose).toHaveBeenCalledWith(["board-task"], "TRASH");
    expect(apiPurgeItem).toHaveBeenCalledTimes(1);
    expect(apiPurgeItem).toHaveBeenCalledWith("my-task");
  });

  it("the purge refuses a board row before any request, whatever put it there", async () => {
    // A path that broke the rule and put a board id in `softDeletedIds`.
    const s = useTaskStore.getState();
    useTaskStore.setState({
      undoSnapshot: {
        items: s.items,
        projects: [],
        processed: 0,
        selectedItemId: null,
        label: "Deleted",
        softDeletedIds: ["board-task", "my-task"],
      },
    });
    useTaskStore.getState().dismissUndo();
    await vi.runAllTimersAsync();
    expect(apiPurgeItem).toHaveBeenCalledTimes(1);
    expect(apiPurgeItem).toHaveBeenCalledWith("my-task");
  });

  it("Undo on my own deleted task restores the disposition it had, not INBOX", async () => {
    vi.mocked(apiRestoreItem).mockResolvedValue(task("my-task"));
    useTaskStore.setState({
      items: [task("my-task", { disposition: "WAITING" }), task("fresh", { isTriaged: false })],
    });
    useTaskStore.getState().deleteItems(["my-task", "fresh"]);
    useTaskStore.getState().undoLastChange();
    await vi.runAllTimersAsync();
    expect(apiRestoreItem).toHaveBeenCalledWith("my-task", "WAITING");
    // An untriaged row is cleared, never given a triage it did not have.
    expect(apiRestoreItem).toHaveBeenCalledWith("fresh", null);
  });

  it("Undo on a removed board task writes back what my overlay said, and never restores as INBOX", async () => {
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    useTaskStore.getState().deleteItems(["board-task"]);
    useTaskStore.getState().undoLastChange();
    await vi.runAllTimersAsync();
    expect(apiRestoreItem).not.toHaveBeenCalled();
    expect(apiPatchItem).toHaveBeenCalledWith("board-task", { disposition: "NEXT" });
    expect(useTaskStore.getState().items.map((i) => i.id)).toContain("board-task");
  });

  it("Undo on an untriaged board row CLEARS the disposition, stating no triage", async () => {
    useTaskStore.setState({
      items: [task("board-task", { projectId: "board-1", isTriaged: false })],
    });
    vi.mocked(apiBulkDispose).mockResolvedValue([]);
    useTaskStore.getState().deleteItems(["board-task"]);
    useTaskStore.getState().undoLastChange();
    await vi.runAllTimersAsync();
    expect(apiPatchItem).toHaveBeenCalledWith("board-task", { disposition: null });
  });
});

describe("the fences around it", () => {
  it("the purge goes to the personal-only route, never the board's delete", () => {
    const lens = readFileSync(resolve(__dirname, "lens.ts"), "utf-8");
    expect(lens).toMatch(/purge: "my\/tasks\/\{task_id\}"/);
    expect(lens).toMatch(/projectsCall<Raw>\(at\(MY_ROUTES\.purge, id\), \{ method: "DELETE" \}\)/);
    expect(lens).not.toMatch(/projectsCall<Raw>\(`tasks\/\$\{id\}`, \{ method: "DELETE" \}\)/);
  });

  it("every delete surface labels a board task honestly", () => {
    for (const rel of [
      "components/ItemDetail.tsx",
      "components/ItemList.tsx",
      "components/EliminatePopup.tsx",
      "components/DeleteConfirmModal.tsx",
      "../calendar/components/TimeGrid.tsx",
      "../calendar/components/UnscheduledRail.tsx",
    ]) {
      expect(read(rel), rel).toMatch(/useRemoval\(\)/);
    }
    // The dialog is the shared ConfirmDialog, worded by `removalCopy`.
    const modal = read("components/DeleteConfirmModal.tsx");
    expect(modal).toMatch(/import ConfirmDialog from "@\/components\/ui\/ConfirmDialog";/);
    expect(modal).toMatch(/const copy = removalCopy\(targets, scope\);/);
    expect(modal).not.toMatch(/fixed inset-0/);
  });
});
