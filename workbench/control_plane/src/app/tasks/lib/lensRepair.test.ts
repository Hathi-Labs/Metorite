/**
 * WS-39 S6a repair round — the three client rules the verifier asked for a
 * fence on (R7). Each is a pure piece, tested where it lives:
 *
 *   1. `lensDelegateBlock` (lib/clarify.ts) — a delegate decision on a task in
 *      the personal tree is refused until a company project is picked, and a
 *      delegate cannot become a private project. `ClarifyPanel` calls it.
 *   2. `mapProject` (lib/api.ts) under the lens reads a `pm_projects` NODE:
 *      `name` → `outcome`, lowercase `status` → `ACTIVE`/`DONE`, `source: "LOCAL"`.
 *   3. The refused-write path in the store: a rejected `apiOrganize` refetches
 *      the list and sets `syncFailure`, which `SyncFailureToast` reports.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { lensDelegateBlock } from "./clarify";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    apiOrganize: vi.fn(),
    fetchItems: vi.fn(),
  };
});

import { apiOrganize, fetchItems, mapProject } from "./api";
import { useTaskStore } from "./taskStore";
import type { MyTask } from "./types";

// ── 1. The delegate rule under the lens ─────────────────────────────────────

describe("lensDelegateBlock", () => {
  const company = ["proj-sales", "proj-ops"];
  const delegating = { lens: true, delegating: true, companyProjectIds: company };

  it("does nothing when the lens is off, or nobody is being delegated to", () => {
    expect(
      lensDelegateBlock({ ...delegating, lens: false, size: "single" }),
    ).toBeNull();
    expect(
      lensDelegateBlock({ ...delegating, delegating: false, size: "single" }),
    ).toBeNull();
  });

  it("refuses a delegate on a task in the personal tree until a company project is picked", () => {
    // An inbox capture: it lives in my personal root, which is NOT in the
    // company list the lens serves. The assign guard would 422 this.
    expect(
      lensDelegateBlock({ ...delegating, size: "single", itemProjectId: "my-root" }),
    ).toBe("needs-company-project");
    expect(
      lensDelegateBlock({ ...delegating, size: "single" }),
    ).toBe("needs-company-project");
    // Picked one → the move and the assign go in one request.
    expect(
      lensDelegateBlock({
        ...delegating, size: "single", itemProjectId: "my-root", projectId: "proj-ops",
      }),
    ).toBeNull();
  });

  it("lets a task that already lives on a company board be delegated as-is", () => {
    expect(
      lensDelegateBlock({ ...delegating, size: "subtasks", itemProjectId: "proj-sales" }),
    ).toBeNull();
  });

  it("refuses a delegate that would also make a private project", () => {
    // Whatever project is picked: the outcome's child is mine, and the guard
    // refuses a colleague there. The server says the same in a 400.
    expect(
      lensDelegateBlock({ ...delegating, size: "project", projectId: "proj-ops" }),
    ).toBe("private-project");
  });
});

// ── 2. mapProject reads a node ───────────────────────────────

describe("mapProject reads a node", () => {

  it("reads name → outcome, lowercase status → ACTIVE, and says LOCAL", () => {
    const p = mapProject({
      id: "p1", name: "Sales Q4", status: "active", description: "the pipeline",
    });
    expect(p).toMatchObject({
      id: "p1", outcome: "Sales Q4", status: "ACTIVE", source: "LOCAL",
      purpose: "the pipeline",
    });
  });

  it("reads any other node status as DONE, so the ACTIVE filter drops it", () => {
    expect(mapProject({ id: "p2", name: "Old", status: "archived" }).status).toBe("DONE");
    expect(mapProject({ id: "p3", name: "Closed", status: "closed" }).status).toBe("DONE");
  });
});

// ── 3. The refused write ────────────────────────────────────────────────────

const ITEM: MyTask = {
  id: "t1",
  source: "LOCAL",
  title: "Draft the quote",
  disposition: "INBOX",
  isTwoMinute: false,
  isMine: true,
  assignees: [],
  subtaskCount: 0,
  createdAt: "2026-09-23T08:00:00Z",
  updatedAt: "2026-09-23T08:00:00Z",
  projectId: "my-root",
};

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
};

describe("a refused organize", () => {
  beforeEach(() => {
    vi.mocked(apiOrganize).mockReset();
    vi.mocked(fetchItems).mockReset();
    useTaskStore.setState({
      backend: "live",
      items: [ITEM],
      syncFailure: null,
      undoSnapshot: null,
    });
  });

  it("refetches the list and sets syncFailure with the server's reason", async () => {
    vi.mocked(apiOrganize).mockRejectedValue(
      new Error("This task is in a personal project, so it cannot be assigned to bob@fracktal.in."),
    );
    vi.mocked(fetchItems).mockResolvedValue([ITEM]);

    useTaskStore.getState().clarify("t1", {
      kind: "next",
      nextAction: "Draft it",
      context: "@computer",
      assignee: { name: "Bob", email: "bob@fracktal.in" },
    });
    // Optimistic: the row has already moved out of the inbox.
    expect(useTaskStore.getState().items[0].disposition).not.toBe("INBOX");

    await flush();

    // The truth came back, and the reason is on the failure the toast reads.
    expect(fetchItems).toHaveBeenCalledWith("all");
    expect(useTaskStore.getState().items[0].disposition).toBe("INBOX");
    const failure = useTaskStore.getState().syncFailure;
    expect(failure?.message).toMatch(/Couldn't organize it: .*personal project/);
    expect(typeof failure?.at).toBe("number");
  });

  it("says nothing when the write lands", async () => {
    vi.mocked(apiOrganize).mockResolvedValue({ ...ITEM, disposition: "SOMEDAY" });
    useTaskStore.getState().clarify("t1", { kind: "someday" });
    await flush();
    expect(useTaskStore.getState().syncFailure).toBeNull();
    expect(fetchItems).not.toHaveBeenCalled();
  });

  it("is cleared by the toast bridge once reported", () => {
    useTaskStore.getState().reportSyncFailure("nope");
    expect(useTaskStore.getState().syncFailure?.message).toBe("nope");
    useTaskStore.getState().clearSyncFailure();
    expect(useTaskStore.getState().syncFailure).toBeNull();
  });
});
