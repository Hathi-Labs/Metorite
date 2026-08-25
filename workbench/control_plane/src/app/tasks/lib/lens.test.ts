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
 *   1. **Every `GtdItem` field is accounted for.** The test reads `types.ts`
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
  UNMAPPED,
  lensCapture,
  lensDelegateItem,
  lensEnabled,
  lensFetchItems,
  lensPatchItem,
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
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
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
  // ⚠️ the shared Priority integer — must NOT become `important`
  importance: 3,
  estimate_mins: 480,
  disposition: "NEXT",
  is_triaged: true,
  is_mine: true,
  workflow_stage: "In progress",
  subtask_count: 2,
  assignees: ["alice@fracktal.in", "bob@fracktal.in"],
  next_action: "Open the editor",
  context: "@computer",
  energy: "high",
  time_estimate_mins: 45,
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

describe("every GtdItem field has a home (§13.4a)", () => {
  const types = readFileSync(
    fileURLToPath(new URL("./types.ts", import.meta.url)),
    "utf-8",
  );

  /** Top-level field names of `interface GtdItem`, read from the source. */
  function gtdItemFields(): string[] {
    const start = types.indexOf("export interface GtdItem {");
    expect(start).toBeGreaterThan(-1);
    const body = types.slice(start, types.indexOf("\n}", start));
    // Two spaces of indent = a field of GtdItem itself. `origin`'s nested
    // members sit at four and are deliberately not counted: `origin` is one
    // decision, not six.
    return [...body.matchAll(/^ {2}(\w+)\??:/gm)].map((m) => m[1]);
  }

  it("is mapped, or listed as deliberately unmapped with a reason", () => {
    const fields = gtdItemFields();
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
      `GtdItem field(s) ${orphans.join(", ")} are neither mapped by ` +
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

  it("names only real GtdItem fields in UNMAPPED", () => {
    const fields = new Set(gtdItemFields());
    const ghosts = Object.keys(UNMAPPED).filter((f) => !fields.has(f));
    expect(
      ghosts,
      `UNMAPPED names ${ghosts.join(", ")}, which GtdItem does not have — ` +
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

  it("does NOT read `important` from the shared Priority integer", () => {
    // D53.8's confusable pair. `pm_tasks.importance` is the team's Priority;
    // `important` is my private Eisenhower flag. Mapping one to the other
    // publishes triage nobody asked to share.
    expect(item.important).toBe(true);
    expect(ROW.importance).toBe(3);
    expect(item.important).not.toBe(ROW.importance);
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
    expect(() => splitPatch({ workflow_stage: "Done" })).toThrow(
      /cannot write `workflow_stage`/,
    );
    expect(() => splitPatch({ nonsense: 1 })).toThrow(/unknown patch key/);
  });

  it("routes assignees to the shared set", () => {
    expect(
      splitPatch({ assignees: [{ name: "Bo", email: "bo@fracktal.in" }] })
        .assignees,
    ).toEqual(["bo@fracktal.in"]);
    expect(splitPatch({ clear_assignee: true }).assignees).toEqual([]);
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

// ── 5. No spine function is left behind ─────────────────────────────────────

describe("the cutover seam is complete for this slice", () => {
  const apiSrc = readFileSync(
    fileURLToPath(new URL("./api.ts", import.meta.url)),
    "utf-8",
  );

  /**
   * The spine S3a-client slice 1 moves. §13.5's own fence — grep the whole
   * `/tasks` tree for `/api/tasks/items` — belongs to slice 2, when the AI,
   * subtask, plan and bulk routes follow; asserting it now would fail against
   * work that is deliberately still to come, and a fence that is red on
   * purpose is a fence people learn to ignore.
   *
   * This is the same shape narrowed to what HAS moved: the failure mode of
   * slice 1 is not a missing endpoint, it is one of these eight functions
   * losing its branch and quietly writing to the retired store while its
   * seven neighbours read the new one.
   */
  const SPINE = [
    "fetchItems",
    "apiCapture",
    "apiPatchItem",
    "apiArchiveItem",
    "apiDeleteItem",
    "apiRestoreItem",
    "apiPurgeItem",
    "apiDelegateItem",
  ];

  it("branches to the lens in every function this slice moved", () => {
    const missing = SPINE.filter((name) => {
      const start = apiSrc.indexOf(`export async function ${name}(`);
      if (start < 0) return true;
      // The function's own text, bounded by the next top-level `export` —
      // never a fixed character window. `apiPatchItem` declares a 35-line
      // inline patch type before its body, so a window wide enough for it
      // would reach past three of its shorter neighbours and let each of them
      // pass on somebody else's branch.
      const after = apiSrc.indexOf("\nexport ", start + 1);
      const body = apiSrc.slice(start, after < 0 ? undefined : after);
      return !body.includes("lensEnabled()");
    });
    expect(
      missing,
      `${missing.join(", ")} do not consult lensEnabled(). Under the flag ` +
        "these would keep writing gtd_items while their neighbours read " +
        "pm_tasks — two stores, one UI, and no error to say so.",
    ).toEqual([]);
  });

  it("keeps the lens off /api/tasks entirely", () => {
    const lensSrc = readFileSync(
      fileURLToPath(new URL("./lens.ts", import.meta.url)),
      "utf-8",
    );
    expect(lensSrc).not.toContain("/api/tasks");
    expect(lensSrc).not.toContain("gatewayFetch");
  });
});

// ── 6. The flag ─────────────────────────────────────────────────────────────

describe("lensEnabled", () => {
  it("is off unless explicitly turned on", () => {
    // Default OFF is load-bearing, not caution: the S3b backfill has not run,
    // so the new store answers correctly that it holds none of the old rows.
    // An early flip empties the app on a 200.
    expect(lensEnabled({})).toBe(false);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "" })).toBe(false);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "0" })).toBe(false);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "false" })).toBe(false);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "1" })).toBe(true);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "true" })).toBe(true);
    expect(lensEnabled({ NEXT_PUBLIC_TASKS_LENS: "on" })).toBe(true);
  });
});
