/**
 * WS-39 S3a-client — the Projects lens.
 *
 * Spec `task_manager_app.md` §13.5. The acceptance criterion this file exists
 * for is criterion 1's fence: *"a structural test … Structural, not exemplary
 * — the failure mode this defends is one component left behind, which an
 * example test cannot see."*
 *
 * There are two structural claims here and they are the reason the file is
 * worth more than its example cases:
 *
 *   1. **Every `MyTask` field is accounted for.** The test reads `types.ts`
 *      itself and requires each field to be either produced by `mapLensItem`
 *      or named in `UNMAPPED` with a reason. §13.4a's warning is exact — "a
 *      field with no home does not fail loudly at the cutover, it writes a 200
 *      and disappears" — and no example test can notice a field nobody
 *      remembered to write an example for.
 *
 *   2. **`UNMAPPED` cannot go stale.** An entry for a field that IS mapped is
 *      a comment claiming the opposite of the code, which is how the next
 *      reader concludes the work is unfinished and re-does it.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  OVERLAY_KEYS,
  TASK_KEYS,
  UNMAPPED,
  lensAddSubtasks,
  lensBulkArchive,
  lensBulkDispose,
  lensCapture,
  lensCaptureBatch,
  lensCreateArea,
  lensDelegateItem,
  lensDeleteArea,
  lensEstimateStats,
  lensFetchAreas,
  lensFetchItems,
  lensFetchLed,
  lensFetchProjects,
  lensFetchUntriaged,
  lensMyOverlay,
  filedByMe,
  focusPatch,
  overlayOf,
  lensMyTaskLanes,
  lensFileUnder,
  lensItemDetail,
  lensMergeInto,
  lensMoveTask,
  lensOrganize,
  lensPatchItem,
  lensPlan,
  lensRenameArea,
  lensRestoreItem,
  lensSetStage,
  lensStageAttachment,
  lensStageOptions,
  mapLensItem,
  splitPatch,
} from "./lens";

// ── A stub for the BFF ──────────────────────────────────────────────────────

type Fetch = typeof globalThis.fetch;

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/** Answers each request from `replies` in order, recording what was asked. */
function stub(replies: unknown[]): {
  calls: Call[];
  restore: () => void;
} {
  const calls: Call[] = [];
  const original = globalThis.fetch;
  let i = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({
      url: String(input),
      method: init?.method ?? "GET",
      body:
        init?.body instanceof FormData
          ? "multipart"
          : init?.body
            ? JSON.parse(String(init.body))
            : undefined,
    });
    const body = replies[Math.min(i, replies.length - 1)];
    i += 1;
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  }) as Fetch;
  return { calls, restore: () => (globalThis.fetch = original) };
}

/** A row in the shape `_project_task` emits, with everything populated. */
const ROW = {
  id: "task-1",
  title: "Ship the lens",
  description: "with the notes intact",
  project_id: "proj-1",
  parent_task_id: "task-0",
  due_at: "2026-09-10T00:00:00+00:00",
  completed_at: null,
  created_at: "2026-09-01T08:00:00+00:00",
  updated_at: "2026-09-01T09:00:00+00:00",
  archived_at: null,
  // D78. The shared integer IS Important now (3 >= IMPORTANT_AT).
  // D77. And the ONE estimate.
  importance: 3,
  estimate_mins: 480,
  start_date: "2026-09-05",
  tags: ["ops", "quote"],
  disposition: "NEXT",
  is_triaged: true,
  is_mine: true,
  workflow_stage: "In progress",
  subtask_count: 2,
  assignees: ["alice@fracktal.in", "bob@fracktal.in"],
  next_action: "Open the editor",
  context: "@computer",
  energy: "high",
  is_two_minute: false,
  defer_until: null,
  scheduled_start: "2026-09-02T09:00:00+00:00",
  scheduled_end: "2026-09-02T11:00:00+00:00",
  flexible: false,
  is_hard_date: true,
  actual_start: null,
  actual_end: null,
  important: true,
  leveraged: null,
  deep_work: true,
  kept_mine: null,
  sort_key: 1024.5,
  waiting_on: { name: "Priya", email: "priya@fracktal.in" },
  delegated_at: "2026-08-30T10:00:00+00:00",
  expected_by: null,
  last_nudged_at: null,
  clarified_at: "2026-09-01T09:00:00+00:00",
};

afterEach(() => vi.restoreAllMocks());

// ── 1. The structural fence ─────────────────────────────────────────────────

describe("every MyTask field has a home (§13.4a)", () => {
  const types = readFileSync(
    fileURLToPath(new URL("./types.ts", import.meta.url)),
    "utf-8",
  );

  /** Top-level field names of `interface MyTask`, read from the source. */
  function myTaskFields(): string[] {
    const start = types.indexOf("export interface MyTask {");
    expect(start).toBeGreaterThan(-1);
    const body = types.slice(start, types.indexOf("\n}", start));
    // Two spaces of indent = a field of MyTask itself. `origin`'s nested
    // members sit at four and are deliberately not counted: `origin` is one
    // decision, not six.
    return [...body.matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1]);
  }

  it("is mapped, or listed as deliberately unmapped with a reason", () => {
    const fields = myTaskFields();
    // Sanity: if the regex ever stops matching, an empty list would make this
    // whole file pass while checking nothing.
    expect(fields.length).toBeGreaterThan(30);
    expect(fields).toContain("disposition");

    const mapped = new Set(Object.keys(mapLensItem({})));
    const orphans = fields.filter((f) => !mapped.has(f) && !(f in UNMAPPED));

    expect(orphans, orphans.length ? errorFor(orphans) : "").toEqual([]);
  });

  function errorFor(orphans: string[]): string {
    return (
      `MyTask field(s) ${orphans.join(", ")} are neither mapped by ` +
      "mapLensItem nor listed in UNMAPPED. A field with no pm_* home does " +
      "not fail at the cutover — it writes a 200 and disappears. Give it a " +
      "home in task_manager_app.md §13.4a, or add it to UNMAPPED with the " +
      "reason it has none."
    );
  }

  it("does not carry a stale UNMAPPED entry", () => {
    const mapped = new Set(Object.keys(mapLensItem({})));
    const stale = Object.keys(UNMAPPED).filter((f) => mapped.has(f));
    expect(
      stale,
      `UNMAPPED claims ${stale.join(", ")} are not mapped, but mapLensItem ` +
        "produces them. A note that contradicts the code sends the next " +
        "reader to redo work that is done.",
    ).toEqual([]);
  });

  it("names only real MyTask fields in UNMAPPED", () => {
    const fields = new Set(myTaskFields());
    const ghosts = Object.keys(UNMAPPED).filter((f) => !fields.has(f));
    expect(
      ghosts,
      `UNMAPPED names ${ghosts.join(", ")}, which MyTask does not have — ` +
        "an excuse for a field that no longer exists is an excuse that will " +
        "cover the next one silently.",
    ).toEqual([]);
  });
});

// ── 2. The mapping's load-bearing distinctions ──────────────────────────────

describe("mapLensItem", () => {
  const item = mapLensItem(ROW);

  it("reads notes from pm_tasks.description", () => {
    expect(item.notes).toBe("with the notes intact");
  });

  it("reads `important` from the shared importance, 2 and up (D78)", () => {
    // D78 reverses D76. Important is one shared answer on the task. It is
    // `pm_tasks.importance >= IMPORTANT_AT`, and no private flag is read.
    expect(item.important).toBe(true);
    expect(mapLensItem({ ...ROW, importance: 2 }).important).toBe(true);
    expect(mapLensItem({ ...ROW, importance: 1 }).important).toBe(false);
    expect(mapLensItem({ ...ROW, importance: 0 }).important).toBe(false);
    expect(mapLensItem({ ...ROW, importance: null }).important).toBeUndefined();
  });

  it("ignores a stale overlay `important` column (D78)", () => {
    // The row still carries the old overlay field during expand/contract.
    // It must not win over the shared answer.
    expect(mapLensItem({ ...ROW, importance: 1, important: true }).important).toBe(false);
    expect(mapLensItem({ ...ROW, importance: 3, important: false }).important).toBe(true);
  });

  it("reads `leveraged` from the task (D78)", () => {
    expect(mapLensItem({ ...ROW, leveraged: true }).leveraged).toBe(true);
    expect(mapLensItem({ ...ROW, leveraged: false }).leveraged).toBe(false);
  });

  it("reads the ONE estimate off the task, never the overlay (D77)", () => {
    expect(item.timeEstimateMins).toBe(480);
    expect(
      mapLensItem({ ...ROW, estimate_mins: null, time_estimate_mins: 45 })
        .timeEstimateMins,
    ).toBeUndefined();
  });

  it("carries the shared start date and tags (D77)", () => {
    expect(item.startDate).toBe("2026-09-05");
    expect(item.tags).toEqual(["ops", "quote"]);
    expect(mapLensItem({ ...ROW, tags: undefined }).tags).toEqual([]);
  });

  it("keeps `never stated` distinct from `false`", () => {
    // Migrations 187/188 chose nullable columns for this. `leveraged: null`
    // means undecided; collapsing it to false answers a question the member
    // has not been asked, and the matrix then shows a judgement they never made.
    expect(item.leveraged).toBeUndefined();
    expect(item.keptMine).toBeUndefined();
    expect(item.deepWork).toBe(true);
  });

  it("treats an unset `flexible` as flexible, and an explicit false as fixed", () => {
    expect(item.flexible).toBe(false);
    expect(mapLensItem({ ...ROW, flexible: null }).flexible).toBe(true);
  });

  it("turns bare assignee emails into people, primary first", () => {
    expect(item.assignees?.map((p) => p.email)).toEqual([
      "alice@fracktal.in",
      "bob@fracktal.in",
    ]);
    expect(item.assignee?.email).toBe("alice@fracktal.in");
  });

  it("carries the three facts the server projection had to add", () => {
    expect(item.isMine).toBe(true);
    expect(item.workflowStage).toBe("In progress");
    expect(item.subtaskCount).toBe(2);
  });

  it("reads waiting_on as a person, not as a JSON string", () => {
    expect(item.waitingOn).toEqual({
      name: "Priya",
      email: "priya@fracktal.in",
    });
  });

  it("leaves expectedBy unset when nobody promised", () => {
    // The explicit-promise rule, settled 2026-08-02 and preserved verbatim by
    // §13.4: NULL means no promise, and the overdue line falls back to the
    // task's own due_at read LIVE. A mapper that defaulted it to dueAt would
    // freeze a snapshot and re-break the bug that fix closed.
    expect(item.expectedBy).toBeUndefined();
    expect(item.dueAt).toBe("2026-09-10T00:00:00+00:00");
  });

  it("says every task is ours (D52)", () => {
    expect(item.source).toBe("LOCAL");
  });
});

// ── 3. Splitting a write ────────────────────────────────────────────────────

describe("splitPatch", () => {
  it("sends shared facts to the task and private ones to the overlay", () => {
    const split = splitPatch({
      title: "New title",
      notes: "New body",
      disposition: "NEXT",
      context: "@calls",
    });
    expect(split.task).toEqual({ title: "New title", description: "New body" });
    expect(split.personal).toEqual({ disposition: "NEXT", context: "@calls" });
  });

  it("refuses a key it cannot place rather than dropping it", () => {
    // The whole point of the slice. A dropped key resolves the promise and
    // changes nothing, which is indistinguishable from a save that worked.
    expect(() => splitPatch({ provider_status: "x" })).toThrow(
      /cannot write `provider_status`/,
    );
    expect(() => splitPatch({ nonsense: 1 })).toThrow(/unknown patch key/);
  });

  it("routes workflow_stage to a NAME resolution, not to either table (§4.6)", () => {
    // S6a. It used to throw; now it is the one key that is neither a task
    // column nor an overlay column — a lane name the project resolves.
    const split = splitPatch({ workflow_stage: "Doing", title: "x" });
    expect(split.stage).toBe("Doing");
    expect(split.task).toEqual({ title: "x" });
    expect(split.personal).toEqual({});
  });

  it("routes assignees to the shared set", () => {
    expect(
      splitPatch({ assignees: [{ name: "Bo", email: "bo@fracktal.in" }] })
        .assignees,
    ).toEqual(["bo@fracktal.in"]);
    expect(splitPatch({ clear_assignee: true }).assignees).toEqual([]);
  });

  it("sends the estimate, start date, Important and Leveraged to the TASK (D77, D78)", () => {
    const split = splitPatch({
      time_estimate_mins: 30,
      start_date: "2026-10-01",
      important: true,
      leveraged: true,
    });
    expect(split.task).toEqual({
      estimate_mins: 30,
      start_date: "2026-10-01",
      importance: 2,
      leveraged: true,
    });
    expect(split.personal).toEqual({});
  });

  it("writes Important as the shared integer, 2 or 0 (D78)", () => {
    expect(splitPatch({ important: true })).toMatchObject({
      task: { importance: 2 },
      personal: {},
    });
    expect(splitPatch({ important: false })).toMatchObject({
      task: { importance: 0 },
      personal: {},
    });
    expect(splitPatch({ leveraged: true })).toMatchObject({
      task: { leveraged: true },
      personal: {},
    });
    expect(splitPatch({ leveraged: false })).toMatchObject({
      task: { leveraged: false },
      personal: {},
    });
  });

  it("never writes Important, Leveraged or the estimate to the overlay (D77, D78)", () => {
    // The fence the gateway's 422 mirrors. None of these is an overlay key.
    expect(OVERLAY_KEYS).not.toContain("important");
    expect(OVERLAY_KEYS).not.toContain("leveraged");
    expect(OVERLAY_KEYS).not.toContain("time_estimate_mins");
    expect(OVERLAY_KEYS).toContain("deep_work");
    expect(Object.values(TASK_KEYS)).toContain("estimate_mins");
    expect(Object.values(TASK_KEYS)).toContain("leveraged");
    expect(splitPatch({ time_estimate_mins: 45 }).personal).toEqual({});
  });

  it("ignores undefined, so a spread patch does not clear fields", () => {
    expect(splitPatch({ title: undefined, context: "@home" })).toEqual({
      task: {},
      personal: { context: "@home" },
    });
  });
});

// ── 4. The request shapes ───────────────────────────────────────────────────

describe("the lens talks to /api/projects, never /api/tasks", () => {
  it("pages the inbox to exhaustion", async () => {
    // The legacy list endpoint was unbounded; this one caps at 100. Taking
    // page one would show a member 100 of their 340 tasks, with no error and
    // no empty state to give it away.
    const page1 = { rows: Array.from({ length: 100 }, () => ROW), total: 150 };
    const page2 = { rows: Array.from({ length: 50 }, () => ROW), total: 150 };
    const { calls, restore } = stub([page1, page2]);
    try {
      const items = await lensFetchItems("all");
      expect(items).toHaveLength(150);
      expect(calls).toHaveLength(2);
      expect(calls[0].url).toContain("/api/projects/my/inbox");
      expect(calls[0].url).toContain("page=1");
      expect(calls[1].url).toContain("page=2");
      expect(calls.every((c) => !c.url.includes("/api/tasks"))).toBe(true);
    } finally {
      restore();
    }
  });

  it("asks for archived rows only in the archive view", async () => {
    for (const [view, wanted] of [
      ["all", false],
      ["done", false],
      ["archive", true],
    ] as const) {
      const { calls, restore } = stub([{ rows: [], total: 0 }]);
      try {
        await lensFetchItems(view);
        expect(calls[0].url.includes("include_archived=true")).toBe(wanted);
      } finally {
        restore();
      }
    }
  });

  it("leaves the reopen to the gateway: one overlay write, no lane write (D77)", async () => {
    // `personal.reopen_if_closed` reopens a closed task on every overlay
    // door, the bulk one the checkbox takes included. A second, client-side
    // reopen would be a second rule.
    const { calls, restore } = stub([{}, ROW]);
    try {
      await lensPatchItem("task-1", { disposition: "NEXT" });
    } finally {
      restore();
    }
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "PATCH /api/projects/tasks/task-1/personal",
      "GET /api/projects/my/tasks/task-1",
    ]);
  });

  it("restores a deleted closed task as DONE, so Undo never reopens it (D77)", async () => {
    const { calls, restore } = stub([{ ...ROW, status_category: "done" }, {}, ROW]);
    try {
      await lensRestoreItem("task-1");
    } finally {
      restore();
    }
    expect(calls[1]).toMatchObject({ method: "PATCH", body: { disposition: "DONE" } });
  });

  it("completes through /complete so the board moves too (§13.5 #4)", async () => {
    // `disposition: "DONE"` on the overlay alone would mark it done in MY list
    // and leave it open on the company board — the drift one store exists to
    // prevent.
    const { calls, restore } = stub([ROW]);
    try {
      await lensPatchItem("task-1", { disposition: "DONE", context: "@home" });
      const paths = calls.map((c) => `${c.method} ${c.url}`);
      expect(paths.some((p) => p.endsWith("/tasks/task-1/complete"))).toBe(true);
      // …and the rest of the edit still lands, without DONE riding along on it.
      const overlay = calls.find((c) => c.url.endsWith("/personal"));
      expect(overlay?.body).toEqual({ context: "@home" });
    } finally {
      restore();
    }
  });

  it("captures the notes, which the old CaptureIn had nowhere to put", async () => {
    const { calls, restore } = stub([ROW]);
    try {
      await lensCapture("A thought", "and its body");
      expect(calls[0].method).toBe("POST");
      expect(calls[0].url).toContain("/api/projects/my/tasks");
      expect(calls[0].body).toMatchObject({
        title: "A thought",
        notes: "and its body",
      });
    } finally {
      restore();
    }
  });

  it("delegates without inventing a promise", async () => {
    const { calls, restore } = stub([ROW]);
    try {
      await lensDelegateItem("task-1", {
        assignee: { name: "Priya", email: "priya@fracktal.in" },
        due_at: "2026-09-20T00:00:00+00:00",
      });
      const overlay = calls.find((c) => c.url.endsWith("/personal"));
      const body = overlay?.body as Record<string, unknown>;
      expect(body.disposition).toBe("WAITING");
      expect(body.waiting_on).toEqual({
        name: "Priya",
        email: "priya@fracktal.in",
      });
      // 188's CHECK: a chase with no since-when has no age to scan.
      expect(body.delegated_at).toBeTruthy();
      // But the deadline is NOT a promise. Copying due_at here is the exact
      // bug the 2026-08-02 fix closed at four insert sites.
      expect(body).not.toHaveProperty("expected_by");
    } finally {
      restore();
    }
  });
});

// ── 5. The client names no retired door (S8 PR 1) ─────────────────────────

describe("api.ts reaches the one store only", () => {
  const apiSrc = readFileSync(
    fileURLToPath(new URL("./api.ts", import.meta.url)),
    "utf-8",
  );
  const lensSrc = readFileSync(
    fileURLToPath(new URL("./lens.ts", import.meta.url)),
    "utf-8",
  );

  /** Every literal path `gatewayFetch` is handed, as `/tasks` sees it. */
  const gatewayPaths = [
    ...apiSrc.matchAll(/gatewayFetch\s*(?:<[^>]*>)?\s*\(\s*([`'"])(.*?)\1/g),
  ].map((m) => m[2]);

  /**
   * The `/tasks` routes S8 PR 1 deleted, or that only ever read the old store.
   * The prefixes are matched on the literal part of each path, so an id or a
   * query string cannot hide one.
   */
  const RETIRED = [
    /^\/items(?:\?|$|\/batch|\/bulk|\/\$\{[^}]+\}(?:$|\/(?:detail|stage-options|organize|subtasks|merge-into|file-under|archive|restore|purge|delegate|push)))/,
    /^\/hierarchy/,
    /^\/spaces/,
    /^\/folders/,
    /^\/local-projects/,
    /^\/accounts/,
    /^\/providers/,
    /^\/sync/,
    /^\/status-catalog/,
    /^\/projects/,
    /^\/contexts/,
    /^\/calendar(?:\?|$|\/plan$|\/replan$|\/rollover$|\/estimate-stats)/,
  ];

  it("reads every gatewayFetch path (a blind regex passes everything)", () => {
    expect(gatewayPaths.length).toBeGreaterThan(8);
    expect(gatewayPaths).toContain("/settings");
    expect(gatewayPaths).toContain("/items/${id}/clarify${q}");
  });

  it("names none of the retired paths", () => {
    const hits = gatewayPaths.filter((p) => RETIRED.some((re) => re.test(p)));
    expect(hits, `retired /tasks paths still called: ${hits.join(", ")}`).toEqual([]);
  });

  it("the fence can see: each retired shape trips it", () => {
    for (const p of [
      "/items", "/items?view=all", "/items/batch", "/items/${id}",
      "/items/${id}/organize", "/hierarchy", "/status-catalog", "/projects",
      "/calendar/plan", "/calendar/estimate-stats", "/sync",
    ]) {
      expect(RETIRED.some((re) => re.test(p)), p).toBe(true);
    }
    for (const p of [
      "/items/${id}/clarify${q}", "/items/${id}/enrich",
      "/items/${id}/suggest-title${q}", "/calendar/day-state",
      "/calendar/${kind}", "/settings", "/people", "/ai/atomize", "/plan",
    ]) {
      expect(RETIRED.some((re) => re.test(p)), p).toBe(false);
    }
  });

  it("has no flag left to consult", () => {
    expect(apiSrc).not.toContain("lensEnabled");
    expect(apiSrc).not.toContain("NEXT_PUBLIC_TASKS_LENS");
    expect(lensSrc).not.toMatch(/export function lensEnabled/);
    expect(apiSrc).not.toContain("/api/tasks/attachments");
  });

  it("keeps the lens off /api/tasks entirely", () => {
    expect(lensSrc).not.toContain("/api/tasks");
    expect(lensSrc).not.toContain("gatewayFetch");
  });
});

// ── S6a: the CRUD tail ──────────────────────────────────────────────────────

describe("the CRUD tail (S6a)", () => {
  it("captures a batch through the one-transaction route, in order", async () => {
    const { calls, restore } = stub([
      { rows: [{ ...ROW, id: "a", title: "One" }, { ...ROW, id: "b", title: "Two" }], total: 2 },
    ]);
    try {
      const items = await lensCaptureBatch(["One", "Two"]);
      expect(items.map((i) => i.title)).toEqual(["One", "Two"]);
    } finally {
      restore();
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/projects/my/tasks/batch");
    expect(calls[0].body).toEqual({ items: [{ title: "One" }, { title: "Two" }] });
  });

  it("bulk-disposes through action=personal, and reads the selection back", async () => {
    const { calls, restore } = stub([{ requested: 2, applied: 2 }, ROW, ROW]);
    try {
      const items = await lensBulkDispose(["t1", "t2"], "SOMEDAY");
      expect(items).toHaveLength(2);
    } finally {
      restore();
    }
    expect(calls[0].url).toBe("/api/projects/tasks/bulk");
    expect(calls[0].body).toEqual({
      task_ids: ["t1", "t2"],
      action: "personal",
      personal: { disposition: "SOMEDAY" },
    });
    expect(calls.slice(1).map((c) => c.url)).toEqual([
      "/api/projects/my/tasks/t1",
      "/api/projects/my/tasks/t2",
    ]);
  });

  it("bulk DONE is N completions, never an overlay write (§13.5a #1)", async () => {
    // The bulk route refuses `disposition: "DONE"` by name; the lens does not
    // even try. Each task goes through /complete so the board moves too.
    const { calls, restore } = stub([{}, {}, ROW, ROW]);
    try {
      await lensBulkDispose(["t1", "t2"], "DONE");
    } finally {
      restore();
    }
    const writes = calls.filter((c) => c.method === "POST").map((c) => c.url);
    expect(writes).toEqual([
      "/api/projects/tasks/t1/complete",
      "/api/projects/tasks/t2/complete",
    ]);
    expect(calls.some((c) => c.url.endsWith("/bulk"))).toBe(false);
  });

  it("bulk-archives and bulk-restores through the lifecycle verbs", async () => {
    for (const [archived, action] of [[true, "archive"], [false, "unarchive"]] as const) {
      const { calls, restore } = stub([{ applied: 1 }, ROW]);
      try {
        await lensBulkArchive(["t1"], archived);
      } finally {
        restore();
      }
      expect(calls[0].body).toEqual({ task_ids: ["t1"], action });
    }
  });

  it("organizes in ONE request, dropping the connector field", async () => {
    const { calls, restore } = stub([ROW]);
    try {
      await lensOrganize("task-1", {
        kind: "next",
        next_action: "Do it",
        context: "@home",
        account_id: "acct-from-a-retired-connector",
        subtasks: ["a", "b"],
      });
    } finally {
      restore();
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toBe("/api/projects/my/tasks/task-1/organize");
    expect(calls[0].body).toEqual({
      kind: "next",
      next_action: "Do it",
      context: "@home",
      subtasks: ["a", "b"],
    });
  });

  it("honours a lane name on organize the §4.6 way, against the task's project", async () => {
    const { calls, restore } = stub([
      ROW,                                              // organize → project_id proj-1
      [{ id: "s-todo", name: "Todo" }, { id: "s-doing", name: "Doing" }],
      {},                                               // PATCH status_id
      ROW,                                              // read back
    ]);
    try {
      await lensOrganize("task-1", { kind: "next", next_action: "x", status: "doing" });
    } finally {
      restore();
    }
    expect(calls[1].url).toContain("nodes/proj-1/statuses");
    expect(calls[2].method).toBe("PATCH");
    expect(calls[2].body).toEqual({ status_id: "s-doing" });
  });

  it("resolves workflow_stage to a status_id, and refuses an unknown name with the list", async () => {
    const lanes = [{ id: "s1", name: "Todo" }, { id: "s2", name: "Blocked" }];
    const a = stub([ROW, lanes, {}, ROW]);
    try {
      await lensPatchItem("task-1", { workflow_stage: "Blocked" });
    } finally {
      a.restore();
    }
    const patch = a.calls.find((c) => c.method === "PATCH" && c.url.endsWith("/tasks/task-1"));
    expect(patch?.body).toEqual({ status_id: "s2" });

    const b = stub([lanes]);
    try {
      await expect(lensSetStage("task-1", "proj-1", "Shipped")).rejects.toThrow(
        /no status named "Shipped".*Todo, Blocked/,
      );
    } finally {
      b.restore();
    }
    // Refused BEFORE any write: a wrong name must not land in a default lane.
    expect(b.calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("composes the detail panel from timeline, attachments and children", async () => {
    const { calls, restore } = stub([
      { rows: [
        { id: "c2", type: "comment", body: "later", created_by: "bo@x", created_at: "2026-09-02T00:00:00Z" },
        { id: "c1", type: "comment", body: "first", created_by: "al@x", created_at: "2026-09-01T00:00:00Z" },
      ], total: 2 },
      { rows: [{ attachment_id: "a1", kind: "image", name: "p.png", url: "/api/projects/attachments/a1/p.png", mime: "image/png", size: 12 }], total: 1 },
      { rows: [{ id: "s1", title: "Step", completed_at: "2026-09-01T00:00:00Z", assignees: ["al@x"] }], total: 1 },
    ]);
    let detail;
    try {
      detail = await lensItemDetail("task-1");
    } finally {
      restore();
    }
    expect(calls.map((c) => c.url)).toEqual([
      "/api/projects/tasks/task-1/timeline?kind=comments&page_size=100",
      "/api/projects/tasks/task-1/attachments",
      "/api/projects/tasks?parent_task_id=task-1&page_size=100",
    ]);
    // Oldest first — a thread reads down, whatever order the timeline serves.
    expect(detail.comments.map((c) => c.text)).toEqual(["first", "later"]);
    expect(detail.attachments[0]).toMatchObject({ kind: "image", name: "p.png", attachmentId: "a1" });
    expect(detail.subtasks[0]).toMatchObject({ providerTaskId: "s1", statusType: "done" });
    expect(detail.subtasks[0].assignees[0].email).toBe("al@x");
  });

  it("adds subtasks in the parent's project, assigned to ME, in order", async () => {
    const original = globalThis.fetch;
    const { calls, restore } = stub([
      { id: "task-1", project_id: "proj-1" },   // GET tasks/task-1
      { email: "me@fracktal.in" },              // /api/auth/me
      { id: "c1" }, {},                         // POST tasks, PUT assignees
      { id: "c2" }, {},
      { rows: [{ id: "c1", title: "a" }, { id: "c2", title: "b", completed_at: "x" }], total: 2 },
    ]);
    let items: Awaited<ReturnType<typeof lensAddSubtasks>> = [];
    try {
      items = await lensAddSubtasks("task-1", ["a", "b"]);
    } finally {
      restore();
      globalThis.fetch = original;
    }
    const creates = calls.filter((c) => c.method === "POST");
    expect(creates.map((c) => c.body)).toEqual([
      { project_id: "proj-1", parent_task_id: "task-1", title: "a" },
      { project_id: "proj-1", parent_task_id: "task-1", title: "b" },
    ]);
    const assigns = calls.filter((c) => c.method === "PUT");
    expect(assigns.map((c) => c.url)).toEqual([
      "/api/projects/tasks/c1/assignees",
      "/api/projects/tasks/c2/assignees",
    ]);
    expect(assigns[0].body).toEqual({ assignees: ["me@fracktal.in"] });
    // The checklist reads DONE off completed_at — the one fact a
    // project-shaped row holds.
    expect(items.map((i) => i.disposition)).toEqual(["INBOX", "DONE"]);
  });

  it("merges INTO the survivor and files UNDER the parent, answering with each", async () => {
    const a = stub([{ id: "target" }, { ...ROW, id: "target" }]);
    try {
      expect((await lensMergeInto("dup", "target")).id).toBe("target");
    } finally {
      a.restore();
    }
    expect(a.calls[0].url).toBe("/api/projects/tasks/target/merge");
    expect(a.calls[0].body).toEqual({ sources: ["dup"] });

    const b = stub([{ id: "step" }, { ...ROW, id: "parent" }]);
    try {
      expect((await lensFileUnder("step", "parent")).id).toBe("parent");
    } finally {
      b.restore();
    }
    expect(b.calls[0].url).toBe("/api/projects/tasks/step/move");
    expect(b.calls[0].body).toEqual({ parent_task_id: "parent" });
  });

  it("holds a picked file until the task exists, then uploads it AFTER the create", async () => {
    const file = new File(["bytes"], "notes.txt", { type: "text/plain" });
    const staged = lensStageAttachment(file);
    expect(staged.file).toBe(file);
    expect(staged.kind).toBe("file");
    expect(staged.name).toBe("notes.txt");

    const { calls, restore } = stub([
      { id: "task-9" },                                 // POST my/tasks
      { attachment_id: "a1", name: "notes.txt", url: "/api/projects/attachments/a1/notes.txt" },
      ROW,                                              // read back
    ]);
    try {
      await lensCapture("A thought", undefined, [
        staged,
        { kind: "link", name: "example.com", url: "https://example.com" },
      ]);
    } finally {
      restore();
    }
    expect(calls[0].url).toBe("/api/projects/my/tasks");
    // The link rides in the notes: it has no row of its own under one store.
    expect(String((calls[0].body as { notes: string }).notes)).toContain("https://example.com");
    expect(calls[1].url).toBe("/api/projects/tasks/task-9/attachments");
    expect(calls[1].method).toBe("POST");
    expect(calls[1].body).toBe("multipart"); // a FormData, not JSON
  });

});

// ── S6b: Areas ──────────────────────────────────────────────────────────────

describe("Areas (S6b)", () => {
  it("lists my Areas from /my/areas, with the open count", async () => {
    const { calls, restore } = stub([
      { rows: [{ id: "a1", name: "Home", archived: false, open_tasks: 3 }], total: 1 },
    ]);
    let areas;
    try {
      areas = await lensFetchAreas();
    } finally {
      restore();
    }
    expect(calls[0].url).toBe("/api/projects/my/areas");
    expect(calls[0].method).toBe("GET");
    expect(areas).toEqual([{ id: "a1", name: "Home", archived: false, openTasks: 3 }]);
  });

  it("mints and renames through the my/areas doors", async () => {
    const a = stub([{ id: "a2", name: "Garden", archived: false }]);
    try {
      expect((await lensCreateArea("Garden")).openTasks).toBe(0);
    } finally {
      a.restore();
    }
    expect(a.calls[0]).toMatchObject({
      method: "POST", url: "/api/projects/my/areas", body: { name: "Garden" },
    });

    const b = stub([{ id: "a2", name: "Yard", archived: false }]);
    try {
      expect((await lensRenameArea("a2", "Yard")).name).toBe("Yard");
    } finally {
      b.restore();
    }
    expect(b.calls[0]).toMatchObject({
      method: "PATCH", url: "/api/projects/my/areas/a2", body: { name: "Yard" },
    });
  });

  it("reports which of the two things a delete did", async () => {
    // "archived, 3 tasks kept" and "deleted" are different promises, and the
    // caller has to say the right one. The mapping must not flatten them.
    const a = stub([{ id: "a1", outcome: "archived", tasks: 3 }]);
    try {
      expect(await lensDeleteArea("a1")).toEqual({ id: "a1", outcome: "archived", tasks: 3 });
    } finally {
      a.restore();
    }
    expect(a.calls[0]).toMatchObject({ method: "DELETE", url: "/api/projects/my/areas/a1" });

    const b = stub([{ id: "a9", outcome: "deleted", tasks: 0 }]);
    try {
      expect((await lensDeleteArea("a9")).outcome).toBe("deleted");
    } finally {
      b.restore();
    }
  });
});

describe("the planner proposals", () => {
  it("go to the projects routes, and write nothing", async () => {
    for (const [kind, path] of [
      ["plan", "my/calendar/plan"],
      ["replan", "my/calendar/replan"],
      ["rollover", "my/calendar/rollover"],
    ] as const) {
      const { calls, restore } = stub([{ blocks: [], evicted: [] }]);
      try {
        await lensPlan(kind, { day_start: "x", day_end: "y" });
        expect(calls[0].url).toBe(`/api/projects/${path}`);
        expect(calls[0].method).toBe("POST");
      } finally {
        restore();
      }
    }
  });

  it("reads the estimate signal from the overlay's route", async () => {
    const { calls, restore } = stub([{ samples: 3, ratio: 1.3, over_pct: 30 }]);
    try {
      await lensEstimateStats();
      expect(calls[0].url).toBe("/api/projects/my/calendar/estimate-stats");
      expect(calls[0].method).toBe("GET");
    } finally {
      restore();
    }
  });
});

// ── Promotion (slice 5a) ────────────────────────────────────────────────────
//
// Everything migration 192 and the D62 guards built is unreachable until the
// Tasks app can call `move`. These pin the three calls that make it reachable.

describe("promotion — the lens reaching into the company board", () => {
  it("lists the COMPANY's projects, not a per-user tree", async () => {
    const { calls, restore } = stub([{ rows: [{ id: "p1", name: "Q3" }], total: 1 }]);
    try {
      await lensFetchProjects();
    } finally {
      restore();
    }
    expect(calls[0].url).toContain("nodes");
    // ⚠️ NOT `/projects` — under the old store that listed the local projects, one
    // member's private list. A promote destination can only be a real project.
    expect(calls[0].url).not.toMatch(/\/projects(\?|$)/);
  });

  it("asks for statuses by PROJECT, never by item", async () => {
    const { calls, restore } = stub([[{ name: "Todo" }, { name: "Doing" }]]);
    let names: string[] = [];
    try {
      names = await lensStageOptions("proj-9");
    } finally {
      restore();
    }
    expect(names).toEqual(["Todo", "Doing"]);
    expect(calls[0].url).toContain("nodes/proj-9/statuses");
    // Statuses are per-ROOT, so "what lanes exist" has no answer until you know
    // WHICH project. Asked of the item it can only describe where the task
    // already is — the wrong answer inside a move dialog.
    expect(calls[0].url).not.toContain("items/");
  });

  it("moves, answers required fields and assigns in ONE request", async () => {
    const { calls, restore } = stub([{ id: "task-1" }]);
    try {
      await lensMoveTask("task-1", {
        projectId: "proj-9",
        customFields: { client: "Acme" },
        assignees: ["priya@fracktal.in"],
      });
    } finally {
      restore();
    }
    // One call, not three. Two calls can fail between them and leave a task
    // promoted onto a team board and owned by nobody.
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe("POST");
    expect(calls[0].url).toContain("tasks/task-1/move");
    expect(calls[0].body).toEqual({
      project_id: "proj-9",
      custom_fields: { client: "Acme" },
      assignees: ["priya@fracktal.in"],
    });
  });

  it("distinguishes 'leave assignees alone' from 'clear them'", async () => {
    // ⚠️ `undefined` and `[]` are different requests. Collapsing them would make
    // "promote without touching who owns it" impossible to express — and the
    // server reads a missing key as leave-alone, so the difference is real all
    // the way down.
    const a = stub([{ id: "t" }]);
    try {
      await lensMoveTask("t", { projectId: "p" });
    } finally {
      a.restore();
    }
    expect(a.calls[0].body).not.toHaveProperty("assignees");

    const b = stub([{ id: "t" }]);
    try {
      await lensMoveTask("t", { projectId: "p", assignees: [] });
    } finally {
      b.restore();
    }
    expect(b.calls[0].body).toHaveProperty("assignees", []);
  });

  it("omits keys it was not given rather than sending nulls", async () => {
    const { calls, restore } = stub([{ id: "t" }]);
    try {
      await lensMoveTask("t", { projectId: "p" });
    } finally {
      restore();
    }
    expect(calls[0].body).toEqual({ project_id: "p" });
  });
});

// ── S6e: continuity with Projects ───────────────────────────────────────────

describe("continuity with Projects (S6e)", () => {
  it("reads the untriaged group off the ONE inbox door, with the flag", async () => {
    const { calls, restore } = stub([
      {
        rows: [{ ...ROW, is_triaged: false, assigned_by: "pm@fracktal.in", project_name: "Sales" }],
        total: 1,
      },
    ]);
    let rows;
    try {
      rows = await lensFetchUntriaged();
    } finally {
      restore();
    }
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toContain("/api/projects/my/inbox?untriaged=true");
    expect(rows[0]).toMatchObject({
      id: "task-1",
      isTriaged: false,
      assignedBy: "pm@fracktal.in",
      projectName: "Sales",
    });
  });

  it("maps the three continuity facts on every row", () => {
    const item = mapLensItem({ ...ROW, is_triaged: true, project_name: "Sales" });
    expect(item.isTriaged).toBe(true);
    expect(item.projectName).toBe("Sales");
    expect(item.assignedBy).toBeUndefined();
  });

  it("lists the projects I lead from /my/led, my tasks in the inbox shape", async () => {
    const { calls, restore } = stub([
      {
        rows: [
          {
            id: "p1",
            name: "Launch",
            task_prefix: "LN",
            open_tasks: 4,
            my_tasks: [{ ...ROW, project_id: "p1" }],
          },
        ],
        total: 1,
      },
    ]);
    let led;
    try {
      led = await lensFetchLed();
    } finally {
      restore();
    }
    expect(calls[0].url).toBe("/api/projects/my/led");
    expect(calls[0].method).toBe("GET");
    expect(led).toHaveLength(1);
    expect(led[0]).toMatchObject({ id: "p1", name: "Launch", taskPrefix: "LN", openTasks: 4 });
    expect(led[0].myTasks[0]).toMatchObject({ id: "task-1", projectId: "p1", context: "@computer" });
  });

  it("reads a task's lanes through the membership door, not the grant one", async () => {
    const { calls, restore } = stub([
      { rows: [{ id: "s1", name: "To do", category: "todo", position: 10 }], total: 1 },
    ]);
    let lanes;
    try {
      lanes = await lensMyTaskLanes("task-1");
    } finally {
      restore();
    }
    expect(calls[0].url).toBe("/api/projects/my/tasks/task-1/lanes");
    expect(calls[0].url).not.toContain("/nodes/");
    expect(lanes).toEqual([
      { id: "s1", project_id: "", name: "To do", color: "", position: 10, category: "todo", is_default: false },
    ]);
  });

  it("answers the viewer's own overlay for the Projects panel", async () => {
    const a = stub([
      { ...ROW, is_triaged: true, disposition: "WAITING", context: "@calls", important: true, leveraged: null, deep_work: null },
    ]);
    try {
      expect(await lensMyOverlay("task-1")).toEqual({
        disposition: "WAITING",
        context: "@calls",
        isTriaged: true,
        deepWork: undefined,
      });
      expect(a.calls[0].url).toBe("/api/projects/my/tasks/task-1");
    } finally {
      a.restore();
    }
  });

  it("answers for an UNTRIAGED task too, and the chip rule moved to filedByMe", async () => {
    // ⚠️ Changed 2026-09-23. This used to answer null here, which hid the
    // focus row on exactly the task whose focus most needs setting. The old
    // rule survives as `filedByMe`: the derived disposition of an untriaged
    // task is not "how I filed it", so the chip stays hidden.
    const b = stub([{ ...ROW, is_triaged: false, context: null }]);
    let overlay;
    try {
      overlay = await lensMyOverlay("task-1");
    } finally {
      b.restore();
    }
    expect(overlay).not.toBeNull();
    expect(filedByMe(overlay!)).toBe(false);
    expect(filedByMe({ ...overlay!, isTriaged: true })).toBe(true);
    expect(filedByMe({ ...overlay!, context: "@calls" })).toBe(true);
    expect(filedByMe(null)).toBe(false);
  });

  it("sends every focus flag in the overlay's own keys", () => {
    // 🔴 The Projects focus row shipped a dead Deep work toggle. The controls
    // say `deepWork`, `splitPatch` takes `deep_work` and throws on the other
    // spelling, so the toggle drew on, threw, re-read and drew off. Each
    // flag goes through the REAL splitter here, not a copy of its key list.
    // D78 leaves only Deep work personal. Important and Leveraged are
    // shared, and TaskBody writes them to the task.
    for (const value of [true, false]) {
      const split = splitPatch(focusPatch({ deepWork: value }));
      expect(split.personal, `deepWork=${value}`).toEqual({ deep_work: value });
      expect(split.task).toEqual({});
    }
    expect(focusPatch({})).toEqual({});
    // A shared flag passed in by mistake never reaches the overlay.
    const stray = focusPatch({ important: true, leveraged: true } as { deepWork?: boolean });
    expect(stray).toEqual({});
    // The raw control patch is exactly what used to throw.
    expect(() => splitPatch({ deepWork: true })).toThrow();
  });

  it("carries only MY flags, never the shared facts the panel already holds", () => {
    // The due date and the project priority come off the panel's live task
    // row. A copy here would go stale the moment Priority is edited beside it.
    const keys = Object.keys(overlayOf({ ...mapLensItem({ ...ROW, importance: 3 }) }));
    expect(keys.sort()).toEqual(["context", "deepWork", "disposition", "isTriaged"]);
    expect(keys).not.toContain("important");
    expect(keys).not.toContain("leveraged");
    expect(keys).not.toContain("dueAt");
  });
});
