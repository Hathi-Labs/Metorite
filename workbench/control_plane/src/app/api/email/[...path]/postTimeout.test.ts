// WS-17 EM-T6e (D1, A12): the budget of a POST through the email proxy.
//
// Spec: `project-docs/specs/email_app_master_plan.md` §10.4.7, "EM-T6e".
// "Remove older mail from Metorite" runs its chunk loop inside the request,
// so its path gets 120,000 ms. Every other POST keeps its budget.
//
// R7 fence `email-storage-proxy-budget`: the rule, and the real route handler
// with the abort budget it hands to the gateway call.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AI_SLOW_POST_PATHS,
  AI_SLOW_TIMEOUT_MS,
  DEFAULT_POST_TIMEOUT_MS,
  isStorageRemoval,
  postTimeoutMs,
} from "./postTimeout";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "vj@fracktal.in" } }),
  isAuthEnabled: true,
}));

const ID = "3f2b7c1e-8a44-4f0e-9c1d-2b5e6f7a8b9c";

describe("email-storage-proxy-budget (A12)", () => {
  it("gives the removal of one mailbox 120,000 ms", () => {
    expect(AI_SLOW_TIMEOUT_MS).toBe(120_000);
    expect(postTimeoutMs(["accounts", ID, "storage", "remove-older"])).toBe(120_000);
    expect(postTimeoutMs(["accounts", "not-a-uuid-the-gateway-404s", "storage", "remove-older"])).toBe(120_000);
  });

  it("gives every other path its old budget", () => {
    expect(DEFAULT_POST_TIMEOUT_MS).toBe(30_000);
    for (const path of [
      ["accounts", ID, "storage", "older"],
      ["accounts", ID, "sync"],
      ["accounts", ID, "backfill"],
      ["accounts", "storage", "remove-older"],
      ["accounts", "", "storage", "remove-older"],
      ["messages", ID, "storage", "remove-older"],
      ["accounts", ID, "storage", "remove-older", "x"],
      ["accounts", ID, "remove-older", "storage"],
      ["accounts"],
    ]) {
      expect(isStorageRemoval(path), path.join("/")).toBe(false);
      expect(postTimeoutMs(path), path.join("/")).toBe(30_000);
    }
  });

  it("keeps the AI and agent paths at 120,000 ms", () => {
    expect(AI_SLOW_POST_PATHS.size).toBe(14);
    for (const joined of AI_SLOW_POST_PATHS) {
      expect(postTimeoutMs(joined.split("/")), joined).toBe(120_000);
    }
  });

  it("the route reads the rule from this module and keeps no copy of its own", () => {
    const route = readFileSync(join(__dirname, "route.ts"), { encoding: "utf-8" });
    expect(route).toContain('import { postTimeoutMs } from "./postTimeout";');
    expect(route).toContain("signal: AbortSignal.timeout(postTimeoutMs(path)),");
    expect(route).not.toMatch(/new Set\(|120_000\b.*POST|AI_SLOW/);
  });
});

describe("the real POST handler hands that budget to the gateway call", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  async function postThrough(path: string[]): Promise<number[]> {
    const budgets: number[] = [];
    const real = AbortSignal.timeout.bind(AbortSignal);
    vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
      budgets.push(ms);
      return real(ms);
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200, headers: { "content-type": "application/json" } })),
    );
    const { NextRequest } = await import("next/server");
    const { POST } = await import("./route");
    await POST(
      new NextRequest(`http://localhost:3001/api/email/${path.join("/")}`, {
        method: "POST",
        body: JSON.stringify({ before: "2026-09-04T00:00:00Z" }),
        headers: { "content-type": "application/json" },
      }),
      { params: Promise.resolve({ path }) },
    );
    return budgets;
  }

  it("120,000 ms for the removal", async () => {
    expect(await postThrough(["accounts", ID, "storage", "remove-older"])).toContain(120_000);
  });

  it("30,000 ms for another POST of the same mailbox", async () => {
    const budgets = await postThrough(["accounts", ID, "sync"]);
    expect(budgets).toContain(30_000);
    expect(budgets).not.toContain(120_000);
  });
});
