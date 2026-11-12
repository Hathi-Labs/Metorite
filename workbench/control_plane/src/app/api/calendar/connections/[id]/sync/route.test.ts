// WS-CAL CE-10c: Sync now validates the id before it enters a gateway path.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const seam = vi.hoisted(() => ({ calls: [] as { path: string; init?: RequestInit }[], status: 200 }));

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "calendar-test-secret-at-least-32-characters!",
}));

vi.mock("@/lib/gateway", () => ({
  proxyToGateway: async (path: string, init?: RequestInit) => {
    seam.calls.push({ path, init });
    return new Response('{"status":"ok","added":1,"updated":0,"removed":0}', { status: seam.status });
  },
}));

import { POST } from "./route";

const ID = "3f1c2a9e-1b2d-4c3e-8f4a-5b6c7d8e9f01";
const post = (id: string, sameSite = true) =>
  POST(
    new NextRequest("http://localhost/api/calendar/connections/x/sync", {
      method: "POST",
      headers: sameSite ? { "sec-fetch-site": "same-origin" } : {},
    }),
    { params: Promise.resolve({ id }) },
  );

beforeEach(() => {
  seam.calls.length = 0;
  seam.status = 200;
});

describe("POST /api/calendar/connections/[id]/sync", () => {
  it("forwards a UUID to the gateway sync path", async () => {
    const res = await post(ID);
    expect(res.status).toBe(200);
    expect(seam.calls).toEqual([{ path: `/tasks/calendar/connections/${ID}/sync`, init: { method: "POST" } }]);
  });

  it("refuses everything that is not a UUID", async () => {
    for (const bad of ["outlook", "..", "a?b", "a/b", "", "1", "%2e%2e"]) {
      expect((await post(bad)).status, JSON.stringify(bad)).toBe(400);
    }
    expect(seam.calls).toEqual([]);
  });

  it("refuses a request from another site", async () => {
    expect((await post(ID, false)).status).toBe(403);
    expect(seam.calls).toEqual([]);
  });

  it("passes a 404 through", async () => {
    seam.status = 404;
    expect((await post(ID)).status).toBe(404);
  });
});
