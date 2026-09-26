/**
 * The Projects BFF proxy forwards `If-Match` (D-PM-20, D79).
 *
 * The gateway answers 412 when a task changed since the caller read it. My
 * Tasks' Undo sends the row's `updated_at` so it never overwrites a status a
 * teammate set after our write. A proxy that drops the header makes that
 * check silently never fire.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

afterEach(() => {
  vi.unstubAllGlobals();
});

async function patchThrough(headers: Record<string, string>): Promise<Headers> {
  const seen: { headers?: HeadersInit } = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      seen.headers = init.headers;
      return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
    }),
  );
  const { NextRequest } = await import("next/server");
  const { PATCH } = await import("@/app/api/projects/[...path]/route");
  const path = ["tasks", "t1"];
  await PATCH(
    new NextRequest("http://localhost:3001/api/projects/tasks/t1", {
      method: "PATCH",
      body: JSON.stringify({ status_id: "s1" }),
      headers: { "content-type": "application/json", ...headers },
    }),
    { params: Promise.resolve({ path }) },
  );
  return new Headers(seen.headers);
}

describe("the Projects proxy and If-Match", () => {
  it("passes the caller's If-Match to the gateway", async () => {
    const sent = await patchThrough({ "if-match": "2026-09-26T10:00:00+00:00" });
    expect(sent.get("if-match")).toBe("2026-09-26T10:00:00+00:00");
  });

  it("sends none when the caller sent none", async () => {
    const sent = await patchThrough({});
    expect(sent.get("if-match")).toBeNull();
  });
});
