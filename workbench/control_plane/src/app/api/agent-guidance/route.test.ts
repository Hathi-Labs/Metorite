// WS-MCP SW-31b — `POST /api/agent-guidance` forwards a save, and a bad
// `project_id` is a 422. It never becomes a save of the whole organization.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const seam = vi.hoisted(() => ({ calls: [] as { path: string; body: unknown }[] }));

vi.mock("@/lib/gateway", () => ({
  proxyToGateway: async (path: string, init?: RequestInit) => {
    seam.calls.push({ path, body: JSON.parse(String(init?.body ?? "{}")) });
    return new Response("{}", { status: 201 });
  },
}));

import { POST } from "./route";

const post = (body: unknown) =>
  POST(
    new NextRequest("http://localhost/api/agent-guidance", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
    }),
  );

beforeEach(() => {
  seam.calls.length = 0;
});

describe("POST /api/agent-guidance", () => {
  it("forwards an org save with project_id null", async () => {
    expect((await post({ project_id: null, body: "x" })).status).toBe(201);
    expect((await post({ body: "y" })).status).toBe(201);
    expect(seam.calls.map((c) => c.body)).toEqual([
      { project_id: null, body: "x" },
      { project_id: null, body: "y" },
    ]);
    expect(seam.calls.every((c) => c.path === "/agent-guidance")).toBe(true);
  });

  it("forwards a project save with its id", async () => {
    await post({ project_id: "p1", body: "x" });
    expect(seam.calls[0].body).toEqual({ project_id: "p1", body: "x" });
  });

  it("refuses a bad project_id with 422, and sends nothing", async () => {
    for (const bad of [42, "", "  ", {}, [], false]) {
      expect((await post({ project_id: bad, body: "x" })).status, JSON.stringify(bad)).toBe(422);
    }
    expect(seam.calls).toEqual([]);
  });

  it("refuses a missing body with 422", async () => {
    expect((await post({ project_id: null })).status).toBe(422);
    expect(seam.calls).toEqual([]);
  });
});
