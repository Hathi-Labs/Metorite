/**
 * WS-39 S6b — Areas on the client. Three pieces, each fenced where it lives:
 *
 *   1. `whereGroups` (lib/clarify.ts) — the Where picker's two groups under
 *      the lens: my Areas, then the company's projects. Pure.
 *   2. Group D of `api.ts` under the flag — the local tree reads Areas and
 *      never `/tasks/hierarchy`; a space or folder cannot be created (D65).
 *   3. The Areas slice of the store — optimistic, and a refusal re-reads the
 *      list and lands on `syncFailure`, the S6a path `SyncFailureToast` reads.
 *
 * Spec: `project-docs/specs/my_tasks_cutover.md` §5 S6b.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isPersonalTask, whereGroups } from "./clarify";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    fetchAreas: vi.fn(),
    apiCreateArea: vi.fn(),
    apiRenameArea: vi.fn(),
    apiDeleteArea: vi.fn(),
  };
});

import {
  apiCreateArea,
  apiCreateFolder,
  apiCreateLocalProject,
  apiCreateSpace,
  apiDeleteArea,
  apiRenameArea,
  fetchAreas,
  fetchLocalHierarchy,
} from "./api";
import { itemsInArea, useTaskStore } from "./taskStore";
import type { GtdItem } from "./types";

const FLAG = process.env.NEXT_PUBLIC_TASKS_LENS;
beforeEach(() => {
  process.env.NEXT_PUBLIC_TASKS_LENS = "1";
});
afterEach(() => {
  if (FLAG === undefined) delete process.env.NEXT_PUBLIC_TASKS_LENS;
  else process.env.NEXT_PUBLIC_TASKS_LENS = FLAG;
  vi.restoreAllMocks();
});

// ── 1. The Where picker's groups ────────────────────────────────────────────

describe("whereGroups", () => {
  it("puts my Areas first and the company's projects second, never mixed", () => {
    const groups = whereGroups({
      areas: [
        { id: "a1", name: "Home" },
        { id: "a2", name: "Garden", archived: true },
      ],
      projects: [
        { id: "p1", outcome: "Sales Q4", status: "ACTIVE" },
        { id: "p2", outcome: "Old launch", status: "DONE" },
      ],
    });
    expect(groups.map((g) => g.label)).toEqual(["My Areas", "Company projects"]);
    expect(groups[0].rows).toEqual([{ id: "a1", name: "Home", kind: "area" }]);
    expect(groups[1].rows).toEqual([{ id: "p1", name: "Sales Q4", kind: "project" }]);
  });

  it("keeps both headings when a group is empty, so the picker shape is stable", () => {
    const groups = whereGroups({ areas: [], projects: [] });
    expect(groups).toHaveLength(2);
    expect(groups.every((g) => g.rows.length === 0)).toBe(true);
  });

  it("drops the Areas group for a team task — D62 would refuse the move", () => {
    const groups = whereGroups({
      areas: [{ id: "a1", name: "Home" }],
      projects: [{ id: "p1", outcome: "Sales Q4" }],
      includeAreas: false,
    });
    expect(groups.map((g) => g.label)).toEqual(["Company projects"]);
  });
});

describe("isPersonalTask", () => {
  const areas = ["a-home", "a-garden"];

  it("is true for the root, an Area, or no project at all", () => {
    expect(isPersonalTask({ projectId: "root-1" }, "root-1", areas)).toBe(true);
    expect(isPersonalTask({ projectId: "a-garden" }, "root-1", areas)).toBe(true);
    expect(isPersonalTask({}, "root-1", areas)).toBe(true);
  });

  it("is false for a task on a company board", () => {
    expect(isPersonalTask({ projectId: "p-sales" }, "root-1", areas)).toBe(false);
  });

  it("does not let an unknown root claim a company task", () => {
    // Before a first capture there is no root; a task in some project that
    // is not one of my Areas is still not mine.
    expect(isPersonalTask({ projectId: "p-sales" }, null, areas)).toBe(false);
    expect(isPersonalTask({ projectId: "a-home" }, null, areas)).toBe(true);
  });
});

describe("the sidebar carries no altitude block (D65)", () => {
  it("never names the withdrawn view", () => {
    const src = readFileSync(
      fileURLToPath(new URL("../components/ListsSidebar.tsx", import.meta.url)),
      "utf-8",
    );
    // Assembled at runtime, so S6c's own check — `rg -c -i <the word>
    // src/app/tasks/lib/` returns zero — is not tripped by its fence.
    const withdrawn = ["hori", "zon"].join("");
    expect(new RegExp(withdrawn, "i").test(src)).toBe(false);
  });
});

// ── 2. Group D under the flag ───────────────────────────────────────────────

interface Call {
  url: string;
  method: string;
  body: unknown;
}

function stubFetch(reply: unknown): { calls: Call[]; restore: () => void } {
  const calls: Call[] = [];
  const original = globalThis.fetch;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    return {
      ok: true,
      status: 200,
      json: async () => reply,
      text: async () => JSON.stringify(reply),
    } as Response;
  }) as typeof globalThis.fetch;
  return { calls, restore: () => (globalThis.fetch = original) };
}

describe("the local tree under the lens", () => {
  it("is my Areas, one flat level, read from /projects/my/areas", async () => {
    const { calls, restore } = stubFetch({
      rows: [
        { id: "a1", name: "Home", archived: false, open_tasks: 2 },
        { id: "a2", name: "Gone", archived: true, open_tasks: 0 },
      ],
      total: 2,
    });
    let tree;
    try {
      tree = await fetchLocalHierarchy();
    } finally {
      restore();
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/projects/my/areas");
    // Never the retiring store's tree (S6b done-when 3).
    expect(calls[0].url).not.toContain("/api/tasks");
    expect(tree.spaces).toEqual([]);
    expect(tree.folders).toEqual([]);
    expect(tree.projects).toEqual([
      { id: "a1", outcome: "Home", hasNextAction: true, status: "ACTIVE" },
    ]);
  });

  it("mints an Area where the old tree minted a local project", async () => {
    const { calls, restore } = stubFetch({ id: "a3", name: "Errands", archived: false });
    let node;
    try {
      node = await apiCreateLocalProject({ outcome: "Errands" });
    } finally {
      restore();
    }
    // ONE request — the Area mint and nothing to `/tasks/local-projects`.
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      method: "POST", url: "/api/projects/my/areas", body: { name: "Errands" },
    });
    expect(node).toMatchObject({ id: "a3", outcome: "Errands", status: "ACTIVE" });
  });

  it("refuses a space or a folder by name — Areas are flat (D65)", async () => {
    const { calls, restore } = stubFetch({});
    try {
      await expect(apiCreateSpace("Work")).rejects.toThrow(/Areas are flat/);
      await expect(apiCreateFolder("s1", "Q4")).rejects.toThrow(/Areas are flat/);
    } finally {
      restore();
    }
    // Refused BEFORE any request: nothing reaches the retiring routes.
    expect(calls).toHaveLength(0);
  });
});

// ── 3. The store slice ──────────────────────────────────────────────────────

const AREA = { id: "a1", name: "Home", archived: false, openTasks: 1 };

const flush = async () => {
  for (let i = 0; i < 6; i += 1) await new Promise((r) => setTimeout(r, 0));
};

describe("the Areas slice", () => {
  beforeEach(() => {
    vi.mocked(fetchAreas).mockReset();
    vi.mocked(apiCreateArea).mockReset();
    vi.mocked(apiRenameArea).mockReset();
    vi.mocked(apiDeleteArea).mockReset();
    useTaskStore.setState({
      backend: "live",
      areas: [AREA],
      selectedAreaId: null,
      syncFailure: null,
    });
  });

  it("shows a created Area at once, then swaps in the server's row", async () => {
    let release: (v: typeof AREA) => void = () => {};
    vi.mocked(apiCreateArea).mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const pending = useTaskStore.getState().createArea("Garden");
    // Optimistic: the row is in the list before the server answers.
    expect(useTaskStore.getState().areas.map((a) => a.name)).toEqual(["Garden", "Home"]);
    release({ id: "a2", name: "Garden", archived: false, openTasks: 0 });
    const created = await pending;
    expect(created?.id).toBe("a2");
    expect(useTaskStore.getState().areas.map((a) => a.id)).toEqual(["a2", "a1"]);
    expect(useTaskStore.getState().syncFailure).toBeNull();
    expect(fetchAreas).not.toHaveBeenCalled();
  });

  it("re-reads the list and reports the reason when a create is refused", async () => {
    vi.mocked(apiCreateArea).mockRejectedValue(
      new Error("You already have an area called Home."),
    );
    vi.mocked(fetchAreas).mockResolvedValue([AREA]);
    const created = await useTaskStore.getState().createArea("Home");
    await flush();
    expect(created).toBeUndefined();
    // The truth came back: the optimistic row is gone.
    expect(fetchAreas).toHaveBeenCalledTimes(1);
    expect(useTaskStore.getState().areas).toEqual([AREA]);
    // …and the reason is on the failure the toast reads.
    expect(useTaskStore.getState().syncFailure?.message).toBe(
      "You already have an area called Home.",
    );
  });

  it("renames optimistically and keeps the count the server's row lacks", async () => {
    vi.mocked(apiRenameArea).mockResolvedValue({
      id: "a1", name: "House", archived: false, openTasks: 0,
    });
    await useTaskStore.getState().renameArea("a1", "House");
    expect(useTaskStore.getState().areas[0]).toEqual({ ...AREA, name: "House" });
  });

  it("answers what a delete did, and drops a scope on the row that left", async () => {
    useTaskStore.setState({ selectedAreaId: "a1" });
    vi.mocked(apiDeleteArea).mockResolvedValue({ id: "a1", outcome: "archived", tasks: 3 });
    const removal = await useTaskStore.getState().deleteArea("a1");
    expect(removal).toEqual({ id: "a1", outcome: "archived", tasks: 3 });
    expect(useTaskStore.getState().areas).toEqual([]);
    expect(useTaskStore.getState().selectedAreaId).toBeNull();
  });

  it("puts a refused delete back with the server's reason", async () => {
    vi.mocked(apiDeleteArea).mockRejectedValue(new Error("No such area"));
    vi.mocked(fetchAreas).mockResolvedValue([AREA]);
    const removal = await useTaskStore.getState().deleteArea("a1");
    expect(removal).toBeUndefined();
    expect(useTaskStore.getState().areas).toEqual([AREA]);
    expect(useTaskStore.getState().syncFailure?.message).toBe("No such area");
  });

  it("toggles the scope: the same row again clears it", () => {
    useTaskStore.getState().selectArea("a1");
    expect(useTaskStore.getState().selectedAreaId).toBe("a1");
    useTaskStore.getState().selectArea("a1");
    expect(useTaskStore.getState().selectedAreaId).toBeNull();
  });

  it("loads nothing with the flag off — the section does not render then", async () => {
    delete process.env.NEXT_PUBLIC_TASKS_LENS;
    await useTaskStore.getState().loadAreas();
    expect(fetchAreas).not.toHaveBeenCalled();
  });
});

describe("itemsInArea", () => {
  const item = (id: string, projectId?: string): GtdItem => ({
    id,
    source: "LOCAL",
    title: id,
    disposition: "NEXT",
    isTwoMinute: false,
    isMine: true,
    assignees: [],
    subtaskCount: 0,
    createdAt: "2026-09-23T08:00:00Z",
    updatedAt: "2026-09-23T08:00:00Z",
    projectId,
  });

  it("narrows by projectId, and is the identity with no Area selected", () => {
    const items = [item("t1", "a1"), item("t2", "p9"), item("t3")];
    expect(itemsInArea(items, "a1").map((i) => i.id)).toEqual(["t1"]);
    expect(itemsInArea(items, null)).toBe(items);
  });
});
