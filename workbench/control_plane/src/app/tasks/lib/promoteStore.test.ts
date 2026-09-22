/**
 * S6c repair — `promoteItem` after the move COMMITTED.
 *
 * `my/tasks/{id}` reads through my membership. A promote that hands the
 * task to a colleague, or from which I had already removed myself, answers
 * 404 on the read-back — and the first version reported that committed move
 * as "Couldn't move it". The store now drops the row and says so
 * (`PromoteOutcome.left`), and no `syncFailure` is raised.
 *
 * Also the pure rule the three hosts share (`promoteAllowed`) and the toast
 * copy for both outcomes (`promoteToast`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProjectsApiError } from "@/app/projects/lib/api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, apiMoveTask: vi.fn(), fetchProjects: vi.fn() };
});
vi.mock("./lens", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./lens")>();
  return { ...actual, lensGetItem: vi.fn() };
});

import { apiMoveTask, fetchProjects } from "./api";
import { lensGetItem } from "./lens";
import { promoteAllowed, promoteToast } from "./promote";
import { useTaskStore } from "./taskStore";
import type { GtdItem } from "./types";

const item = (id: string, over: Partial<GtdItem> = {}): GtdItem => ({
  id,
  source: "LOCAL",
  title: `Task ${id}`,
  disposition: "NEXT",
  isMine: true,
  createdAt: "2026-09-20T09:00:00Z",
  updatedAt: "2026-09-20T09:00:00Z",
  ...over,
});

describe("promoteItem", () => {
  beforeEach(() => {
    useTaskStore.setState({
      items: [item("t1"), item("t2")],
      projects: [{ id: "p2", source: "LOCAL", outcome: "Launch", status: "ACTIVE", hasNextAction: false }],
      syncFailure: null,
    });
    vi.mocked(apiMoveTask).mockReset();
    vi.mocked(lensGetItem).mockReset();
    vi.mocked(fetchProjects).mockReset();
  });
  afterEach(() => vi.restoreAllMocks());

  it("swaps the re-read row in when the task is still mine", async () => {
    vi.mocked(apiMoveTask).mockResolvedValue({});
    vi.mocked(lensGetItem).mockResolvedValue(item("t1", { projectId: "p2" }));

    const out = await useTaskStore.getState().promoteItem("t1", { projectId: "p2" });

    expect(out).toEqual({ left: false, item: item("t1", { projectId: "p2" }) });
    expect(useTaskStore.getState().items.find((i) => i.id === "t1")?.projectId).toBe("p2");
    expect(useTaskStore.getState().syncFailure).toBeNull();
    expect(fetchProjects).not.toHaveBeenCalled();
  });

  it("treats a 404 on the read-back as moved-and-left-my-list", async () => {
    vi.mocked(apiMoveTask).mockResolvedValue({});
    vi.mocked(lensGetItem).mockRejectedValue(
      new ProjectsApiError("Task not found", 404),
    );

    const out = await useTaskStore.getState().promoteItem("t1", {
      projectId: "p2",
      assignees: ["bob@fracktal.in"],
    });

    expect(out).toEqual({ left: true, projectId: "p2", assignees: ["bob@fracktal.in"] });
    expect(useTaskStore.getState().items.map((i) => i.id)).toEqual(["t2"]);
    expect(useTaskStore.getState().syncFailure).toBeNull();
    expect(apiMoveTask).toHaveBeenCalledWith("t1", {
      projectId: "p2",
      assignees: ["bob@fracktal.in"],
    });
  });

  it("still throws on any other read-back failure, and touches nothing", async () => {
    vi.mocked(apiMoveTask).mockResolvedValue({});
    vi.mocked(lensGetItem).mockRejectedValue(new ProjectsApiError("boom", 500));

    await expect(
      useTaskStore.getState().promoteItem("t1", { projectId: "p2" }),
    ).rejects.toThrow("boom");
    expect(useTaskStore.getState().items.map((i) => i.id)).toEqual(["t1", "t2"]);
  });

  it("touches nothing when the move itself is refused", async () => {
    vi.mocked(apiMoveTask).mockRejectedValue(
      new ProjectsApiError("This project requires 'PO'", 422),
    );

    await expect(
      useTaskStore.getState().promoteItem("t1", { projectId: "p2" }),
    ).rejects.toThrow("requires");
    expect(lensGetItem).not.toHaveBeenCalled();
    expect(useTaskStore.getState().items.map((i) => i.id)).toEqual(["t1", "t2"]);
  });
});

describe("promoteAllowed — the one rule for the door", () => {
  it("is lens-only, and never on an archived row", () => {
    expect(promoteAllowed({ archivedAt: undefined }, true)).toBe(true);
    expect(promoteAllowed({ archivedAt: undefined }, false)).toBe(false);
    expect(promoteAllowed({ archivedAt: "2026-09-01T00:00:00Z" }, true)).toBe(false);
  });
});

describe("promoteToast", () => {
  it("says where it went, and to whom when it left the list", () => {
    const left = promoteToast(
      { left: true, projectId: "p2", assignees: ["bob@fracktal.in"] },
      "Launch",
      null,
    );
    expect(left.title).toBe("Moved to Launch, handed to bob@fracktal.in.");
    expect(left.description).toContain("It left your list.");

    const stayed = promoteToast({ left: false, item: item("t1") }, "Launch", ["scratch"]);
    expect(stayed.title).toBe("Moved to Launch");
    expect(stayed.description).toContain("Dropped scratch");
  });
});
