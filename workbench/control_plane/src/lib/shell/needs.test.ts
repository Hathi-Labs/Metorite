/**
 * The needs feed in the browser (`navigation_shell.md` §7.2, NS-3).
 *
 * Three claims:
 *
 *   1. A feed with a missing or odd field reads as empty, never as a throw.
 *   2. Each one-click act runs through its OWNING app's client: a task
 *      completes through the lens, a notification is marked read through the
 *      Projects bell's route. The shell adds no write route.
 *   3. Undo of a completion follows D79: it writes the status back only when
 *      the completion moved it, and only while the row is the one we left.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { completionUndo, readFeed, runAct, undoCompletion, type NeedsItem } from "./needs";

interface Call {
  method: string;
  url: string;
  body: unknown;
  ifMatch: string | null;
}

function stub(replies: unknown[]): { calls: Call[] } {
  const calls: Call[] = [];
  let i = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      calls.push({
        method: (init?.method ?? "GET").toUpperCase(),
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
        ifMatch: headers.get("If-Match"),
      });
      const reply = replies[Math.min(i++, replies.length - 1)];
      return new Response(JSON.stringify(reply), { status: 200 });
    }),
  );
  return { calls };
}

afterEach(() => vi.unstubAllGlobals());

const TASK = {
  id: "t1",
  title: "Ship it",
  disposition: "NEXT",
  status_id: "s-open",
  status_category: "in_progress",
  updated_at: "2026-10-09T10:00:00Z",
};

const item = (extra: Partial<NeedsItem>): NeedsItem => ({
  id: "tasks:t1",
  app: "tasks",
  kind: "overdue",
  title: "Ship it",
  detail: null,
  href: "/projects?task=t1",
  at: null,
  act: "done",
  act_ref: "t1",
  ...extra,
});

describe("readFeed", () => {
  it("reads a missing field as empty", () => {
    expect(readFeed(undefined)).toEqual({ count: 0, items: [], sources: {} });
    expect(readFeed({ items: "nope" })).toEqual({ count: 0, items: [], sources: {} });
  });

  it("drops a row it cannot draw, and keeps the rest", () => {
    const feed = readFeed({
      count: 2,
      items: [item({}), { id: "x", kind: "something_new", title: "?", href: "/" }],
      sources: { tasks: "ok" },
    });
    expect(feed.items.map((i) => i.id)).toEqual(["tasks:t1"]);
    expect(feed.count).toBe(2);
    expect(feed.sources).toEqual({ tasks: "ok" });
  });
});

describe("runAct goes through the owning app", () => {
  it("completes a task through the lens, after reading the row it had", async () => {
    const { calls } = stub([TASK, {}, { ...TASK, disposition: "DONE", status_id: "s-done" }]);
    const plan = await runAct(item({}));
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "GET /api/projects/my/tasks/t1",
      "POST /api/projects/tasks/t1/complete",
      "GET /api/projects/my/tasks/t1",
    ]);
    expect(plan).toEqual({
      taskId: "t1",
      priorStatus: "s-open",
      ifMatch: "2026-10-09T10:00:00Z",
      priorDisposition: "NEXT",
    });
  });

  it("marks a notification read through the Projects bell's route, with no undo", async () => {
    const { calls } = stub([{ marked: 1 }]);
    const plan = await runAct(item({ id: "projects:n1", app: "projects", kind: "notification", act: "read", act_ref: "n1" }));
    expect(calls).toEqual([
      { method: "POST", url: "/api/projects/notifications/read", body: { ids: ["n1"] }, ifMatch: null },
    ]);
    expect(plan).toBeNull();
  });

  it("does nothing for a row with no act", async () => {
    const { calls } = stub([{}]);
    expect(await runAct(item({ act: null, act_ref: null }))).toBeNull();
    expect(calls).toEqual([]);
  });

  it("never reads the notification list, which is the bell's (seams.test.ts)", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("./needs.ts", import.meta.url), "utf8");
    expect(src).not.toMatch(/notificationsApi\.list\(/);
  });
});

describe("Undo of a completion (D79)", () => {
  it("writes the status back only when the completion moved it", () => {
    expect(completionUndo({ id: "t1", statusId: "a", disposition: "NEXT" }, { statusId: "a", updatedAt: "u" })).toEqual({
      taskId: "t1",
      priorStatus: undefined,
      ifMatch: undefined,
      priorDisposition: "NEXT",
    });
  });

  it("has nothing to give back for a task that was already done", () => {
    expect(completionUndo({ id: "t1", statusId: "a", disposition: "DONE" }, { statusId: "b", updatedAt: "u" })).toBeNull();
    expect(completionUndo(null, { statusId: "b", updatedAt: "u" })).toBeNull();
  });

  it("puts the status back under If-Match, then my disposition", async () => {
    const { calls } = stub([{}, {}, TASK]);
    await undoCompletion({ taskId: "t1", priorStatus: "s-open", ifMatch: "u1", priorDisposition: "NEXT" });
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      "PATCH /api/projects/tasks/t1",
      "PATCH /api/projects/tasks/t1/personal",
      "GET /api/projects/my/tasks/t1",
    ]);
    expect(calls[0].body).toEqual({ status_id: "s-open" });
    expect(calls[0].ifMatch).toBe("u1");
    expect(calls[1].body).toEqual({ disposition: "NEXT" });
  });
});
