/**
 * The needs feed in the browser (`navigation_shell.md` §7.2, NS-3).
 *
 * Three claims:
 *
 *   1. A feed with a missing or odd field reads as empty, never as a throw.
 *   2. Each one-click act runs through its OWNING app's code. A done is the
 *      My Tasks store's gesture (`completeFromHome`), so the subtask question
 *      and the store's Undo come with it. A read goes through the Projects
 *      bell's route. The shell adds no write route.
 *   3. The feed's key is in the Projects cache family, so each Projects
 *      write makes it read again.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const completeFromHome = vi.fn(async (id: string) => (id === "parent" ? "asked" : "completing"));
vi.mock("@/app/tasks/lib/completeFromHome", () => ({
  completeFromHome: (id: string) => completeFromHome(id),
}));

import { PROJECTS_CACHE } from "@/app/projects/lib/api";

import { NEEDS_CACHE, needsKey, readFeed, runAct, type NeedsItem } from "./needs";

interface Call {
  method: string;
  url: string;
  body: unknown;
}

function stub(reply: unknown): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        method: (init?.method ?? "GET").toUpperCase(),
        url: String(input),
        body: init?.body ? JSON.parse(String(init.body)) : null,
      });
      return new Response(JSON.stringify(reply), { status: 200 });
    }),
  );
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
  completeFromHome.mockClear();
});

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
  it("hands a done to the My Tasks store's own gesture, and writes nothing itself", async () => {
    const { calls } = stub({});
    expect(await runAct(item({}))).toBe("completing");
    expect(completeFromHome).toHaveBeenCalledWith("t1");
    expect(calls).toEqual([]);
  });

  it("passes on that the store ASKED, for a parent with open subtasks", async () => {
    stub({});
    expect(await runAct(item({ id: "tasks:parent", act_ref: "parent" }))).toBe("asked");
  });

  it("marks a notification read through the Projects bell's route", async () => {
    const { calls } = stub({ marked: 1 });
    const out = await runAct(
      item({ id: "projects:n1", app: "projects", kind: "notification", act: "read", act_ref: "n1" }),
    );
    expect(calls).toEqual([{ method: "POST", url: "/api/projects/notifications/read", body: { ids: ["n1"] } }]);
    expect(out).toBe("read");
    expect(completeFromHome).not.toHaveBeenCalled();
  });

  it("does nothing for a row with no act", async () => {
    const { calls } = stub({});
    expect(await runAct(item({ act: null, act_ref: null }))).toBeNull();
    expect(calls).toEqual([]);
    expect(completeFromHome).not.toHaveBeenCalled();
  });

  it("never reads the notification list, which is the bell's (seams.test.ts)", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("./needs.ts", import.meta.url), "utf8");
    expect(src).not.toMatch(/notificationsApi\.list\(/);
    // And no second completion path: no lens write is named here.
    expect(src).not.toMatch(/lensCompleteItem|lensPatchItem|lensSetStatusId/);
  });
});

describe("the feed's cache key", () => {
  it("lives in the Projects family, so a Projects write reads it again", () => {
    expect(NEEDS_CACHE.startsWith(PROJECTS_CACHE)).toBe(true);
    expect(needsKey().startsWith(PROJECTS_CACHE)).toBe(true);
  });
});
