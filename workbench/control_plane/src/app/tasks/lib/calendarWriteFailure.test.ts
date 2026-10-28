/**
 * UXD-1 — a Calendar write that fails is never silent, on the REAL store with
 * a mocked gateway.
 *
 * R7 fences named here:
 *   * `calendar-write-failure-rolls-back`: a refused move reads the list back,
 *     so the block returns to the slot the server holds, and the failure toast
 *     gets the act's name and a Retry.
 *   * `calendar-undo-after-confirm`: the Undo snapshot stays `pending` until
 *     the server answers, and a refused write leaves no Undo.
 *   * `task-sync-never-silent`: any other fire-and-forget write that fails
 *     reads the list back and reports on `syncFailure`.
 *   * `calendar-mounts-failure-toast`: /calendar mounts `SyncFailureToast`.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, apiPatchItem: vi.fn(), fetchItems: vi.fn() };
});

import { apiPatchItem, fetchItems } from "./api";
import { SYNC_FAILURE_TEXT, useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const NINE = "2099-06-15T09:00:00.000Z";
const TEN = "2099-06-15T10:00:00.000Z";
const ELEVEN = "2099-06-15T11:00:00.000Z";

const block = (start: string, end: string): MyTask =>
  ({ id: "t1", title: "Write the brief", disposition: "NEXT", scheduledStart: start, scheduledEnd: end }) as MyTask;

const move = [{ id: "t1", patch: { scheduledStart: TEN, scheduledEnd: ELEVEN } }];

/** Let the write, its settle and the read-back run. */
const settle = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  vi.mocked(apiPatchItem).mockReset();
  vi.mocked(fetchItems).mockReset();
  useTaskStore.setState({
    backend: "live",
    items: [block(NINE, TEN)],
    undoSnapshot: null,
    syncFailure: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("calendar-write-failure-rolls-back", () => {
  it("returns a refused move to the server's slot and offers Retry", async () => {
    vi.mocked(apiPatchItem).mockRejectedValue(new Error("409"));
    vi.mocked(fetchItems).mockResolvedValue([block(NINE, TEN)]);

    useTaskStore.getState().applySchedule("Moved block", move);
    // Optimistic first: the block moves at once.
    expect(useTaskStore.getState().items[0].scheduledStart).toBe(TEN);
    expect(useTaskStore.getState().undoSnapshot?.pending).toBe(true);

    await settle();
    await settle();

    const s = useTaskStore.getState();
    expect(s.items[0].scheduledStart).toBe(NINE);
    expect(s.undoSnapshot).toBeNull();
    expect(s.syncFailure?.message).toMatch(/^Moved block: this did not save/);
    expect(typeof s.syncFailure?.retry).toBe("function");

    // Retry runs the same act again.
    vi.mocked(apiPatchItem).mockResolvedValue(block(TEN, ELEVEN));
    s.syncFailure!.retry!();
    expect(useTaskStore.getState().items[0].scheduledStart).toBe(TEN);
    await settle();
    expect(useTaskStore.getState().undoSnapshot?.pending).toBe(false);
  });
});

describe("calendar-undo-after-confirm", () => {
  it("offers Undo only after the server confirms", async () => {
    let answer!: (row: MyTask) => void;
    vi.mocked(apiPatchItem).mockReturnValue(new Promise((r) => (answer = r)));

    useTaskStore.getState().applySchedule("Moved block", move);
    await settle();
    expect(useTaskStore.getState().undoSnapshot?.pending).toBe(true);

    answer(block(TEN, ELEVEN));
    await settle();
    const s = useTaskStore.getState();
    expect(s.undoSnapshot?.pending).toBe(false);
    expect(s.undoSnapshot?.label).toBe("Moved block");
    expect(s.syncFailure).toBeNull();
    expect(fetchItems).not.toHaveBeenCalled();
  });

  it("the Undo toast hides a pending change", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/UndoToast.tsx", import.meta.url)),
      "utf-8",
    );
    expect(src).toMatch(/snap\?\.pending \? null : snap/);
    expect(src).toMatch(/useTaskStore\(\(s\) => shownSnapshot\(s\.undoSnapshot\)\)/);
  });
});

describe("task-sync-never-silent", () => {
  it("a refused rename reads the list back and reports", async () => {
    vi.mocked(apiPatchItem).mockRejectedValue(new Error("500"));
    vi.mocked(fetchItems).mockResolvedValue([block(NINE, TEN)]);

    useTaskStore.getState().renameItem("t1", "A new name");
    expect(useTaskStore.getState().items[0].title).toBe("A new name");
    await settle();
    await settle();

    const s = useTaskStore.getState();
    expect(s.items[0].title).toBe("Write the brief");
    expect(s.syncFailure?.message).toBe(SYNC_FAILURE_TEXT);
  });
});

describe("calendar-mounts-failure-toast", () => {
  it("mounts SyncFailureToast beside UndoToast", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../../calendar/page.tsx", import.meta.url)),
      "utf-8",
    );
    expect(src).toContain("<SyncFailureToast />");
  });
});
