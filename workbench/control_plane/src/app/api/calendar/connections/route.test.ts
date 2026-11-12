// WS-CAL CE-10c: the BFF list route forwards one fixed gateway path and keeps the status.
import { beforeEach, describe, expect, it, vi } from "vitest";

const seam = vi.hoisted(() => ({ calls: [] as { path: string; init?: RequestInit }[], status: 200 }));

vi.mock("@/lib/gateway", () => ({
  proxyToGateway: async (path: string, init?: RequestInit) => {
    seam.calls.push({ path, init });
    return new Response(seam.status === 404 ? '{"detail":"Not Found"}' : "{}", { status: seam.status });
  },
}));

import { GET } from "./route";

beforeEach(() => {
  seam.calls.length = 0;
  seam.status = 200;
});

describe("GET /api/calendar/connections", () => {
  it("forwards the fixed gateway path", async () => {
    const res = await GET();
    expect(res.status).toBe(200);
    expect(seam.calls).toEqual([{ path: "/tasks/calendar/connections", init: { method: "GET" } }]);
  });

  it("passes a 404 through, so the page can draw nothing", async () => {
    seam.status = 404;
    expect((await GET()).status).toBe(404);
  });
});
