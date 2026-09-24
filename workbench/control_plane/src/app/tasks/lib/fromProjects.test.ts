/**
 * WS-39 S6e — the "From Projects" group's client half (my_tasks_cutover.md
 * §4.8 point 2), on the store with the api mocked.
 *
 * The claim worth pinning is ORDER: `markTriaged` drops the id at once so
 * the group answers the gesture, and the re-read that makes the set the
 * server's answer runs AFTER the write resolves — never before. The first
 * version fired the re-read as the PATCH left, so the server answered from
 * its pre-write state and put the id straight back into the group. A refused
 * write restores the id before that re-read.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiBulkDispose: vi.fn(),
    apiOrganize: vi.fn(),
    fetchUntriaged: vi.fn(),
  };
});

import { apiBulkDispose, apiOrganize, fetchUntriaged } from "./api";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
};

const T1: MyTask = {
  id: "t1",
  source: "LOCAL",
  title: "Draft the quote",
  disposition: "NEXT",
  isMine: true,
  isTriaged: false,
  projectId: "p-sales",
  projectName: "Sales",
  assignedBy: "pm@example.com",
  createdAt: "2026-09-20T08:00:00+00:00",
  updatedAt: "2026-09-20T08:00:00+00:00",
};

describe("From Projects — triage drops the id, and the re-read waits for the write", () => {
  beforeEach(() => {
    vi.mocked(apiBulkDispose).mockReset();
    vi.mocked(apiOrganize).mockReset();
    vi.mocked(fetchUntriaged).mockReset();
    useTaskStore.setState({
      backend: "live",
      items: [T1],
      fromProjectIds: new Set(["t1"]),
      undoSnapshot: null,
      syncFailure: null,
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("a quick dispose drops the id at once and re-reads only after the PATCH resolves", async () => {
    let release: (v: MyTask[]) => void = () => {};
    vi.mocked(apiBulkDispose).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    // The server's answer AFTER the write: the row is triaged, the set is empty.
    vi.mocked(fetchUntriaged).mockResolvedValue([]);

    useTaskStore.getState().quickDispose("t1", "SOMEDAY");
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(false);
    await flush();
    // Nothing re-read while the write is in flight — that read would see
    // the pre-write state and put the id back.
    expect(fetchUntriaged).not.toHaveBeenCalled();

    release([{ ...T1, disposition: "SOMEDAY", isTriaged: true }]);
    await flush();
    expect(fetchUntriaged).toHaveBeenCalledTimes(1);
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(false);
  });

  it("a refused dispose restores the id, then re-reads the truth", async () => {
    vi.mocked(apiBulkDispose).mockRejectedValue(new Error("nope"));
    vi.mocked(fetchUntriaged).mockResolvedValue([T1]);

    useTaskStore.getState().quickDispose("t1", "SOMEDAY");
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(false);
    await flush();
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(true);
    expect(fetchUntriaged).toHaveBeenCalledTimes(1);
  });

  it("a clarify decision is a triage, and its re-read follows organize", async () => {
    let release: (v: MyTask) => void = () => {};
    vi.mocked(apiOrganize).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    vi.mocked(fetchUntriaged).mockResolvedValue([]);

    useTaskStore.getState().clarify("t1", { kind: "someday" } as never);
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(false);
    await flush();
    expect(fetchUntriaged).not.toHaveBeenCalled();
    release({ ...T1, disposition: "SOMEDAY", isTriaged: true });
    await flush();
    expect(fetchUntriaged).toHaveBeenCalledTimes(1);
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(false);
  });

  it("a context or a defer is not a triage — the id stays", () => {
    useTaskStore.getState().updateItem("t1", { context: "@calls" });
    useTaskStore.getState().deferItem("t1", "2026-10-01T00:00:00.000Z");
    expect(useTaskStore.getState().fromProjectIds.has("t1")).toBe(true);
  });
});
