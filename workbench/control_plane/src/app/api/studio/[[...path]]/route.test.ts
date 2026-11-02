// `/api/studio/[…path]` (STU-F2b): a copy of the workflows proxy for the
// gateway prefix `/studio`, with PATCH added. It adds no header, no identity
// and no path rule.
//
// Mutations: drop `refuseUnsafePath` and the 400 test fails. Drop the PATCH
// export and the PATCH test fails. Add an `x-user-email` header and the
// source check fails.
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";

const state: { identity: unknown } = { identity: { email: "a@example.com" } };
const seen: { url: string; init: RequestInit }[] = [];
let reply: () => Response = () => new Response("{}", { status: 200 });

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () =>
    state.identity ?? NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({
    Authorization: "Bearer internal",
    ...extra,
  }),
  gatewayFetch: async (url: string, init: unknown) => {
    seen.push({ url, init: init as RequestInit });
    return reply();
  },
}));

import { DELETE, GET, PATCH, POST, PUT } from "./route";

const ctx = (path?: string[]) => ({ params: Promise.resolve({ path }) });
const req = (url: string, init?: ConstructorParameters<typeof NextRequest>[1]) => new NextRequest(url, init);

beforeEach(() => {
  seen.length = 0;
  state.identity = { email: "a@example.com" };
  reply = () => new Response("{}", { status: 200 });
});

describe("/api/studio proxy", () => {
  it("sends a GET to /studio/skills and forwards the query", async () => {
    reply = () => new Response(JSON.stringify({ skills: [] }), { status: 200 });
    const res = await GET(req("http://x/api/studio/skills?limit=5"), ctx(["skills"]));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ skills: [] });
    expect(seen[0].url).toBe("http://gw.test/studio/skills?limit=5");
    expect(seen[0].init.method).toBe("GET");
    expect(seen[0].init.body).toBeUndefined();
  });

  it("sends a POST with its JSON body", async () => {
    reply = () => new Response("{}", { status: 201 });
    const res = await POST(
      req("http://x/api/studio/skills", { method: "POST", body: JSON.stringify({ name: "A" }) }),
      ctx(["skills"]),
    );
    expect(res.status).toBe(201);
    expect(seen[0].init.method).toBe("POST");
    expect(seen[0].init.body).toBe(JSON.stringify({ name: "A" }));
    expect((seen[0].init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("sends a PATCH to the state route with its body and the same headers as POST", async () => {
    await PATCH(
      req("http://x/api/studio/skills/s1/state", { method: "PATCH", body: JSON.stringify({ state: "paused" }) }),
      ctx(["skills", "s1", "state"]),
    );
    await POST(
      req("http://x/api/studio/skills/s1/versions", { method: "POST", body: "{}" }),
      ctx(["skills", "s1", "versions"]),
    );
    expect(seen[0].url).toBe("http://gw.test/studio/skills/s1/state");
    expect(seen[0].init.method).toBe("PATCH");
    expect(seen[0].init.body).toBe(JSON.stringify({ state: "paused" }));
    expect(seen[0].init.headers).toEqual(seen[1].init.headers);
  });

  it("sends a PUT and a DELETE (DELETE with no body)", async () => {
    await PUT(req("http://x/api/studio/a", { method: "PUT", body: "{}" }), ctx(["a"]));
    await DELETE(req("http://x/api/studio/a", { method: "DELETE" }), ctx(["a"]));
    expect(seen.map((s) => s.init.method)).toEqual(["PUT", "DELETE"]);
    expect(seen[1].init.body).toBeUndefined();
  });

  it("answers 401 with no identity and calls no gateway", async () => {
    state.identity = null;
    const res = await GET(req("http://x/api/studio/skills"), ctx(["skills"]));
    expect(res.status).toBe(401);
    expect(seen).toHaveLength(0);
  });

  it("answers 400 for an unsafe path and calls no gateway", async () => {
    const res = await GET(req("http://x/api/studio/x"), ctx(["..", "internal", "drain"]));
    expect(res.status).toBe(400);
    expect(seen).toHaveLength(0);
  });

  it("passes a 409 and a 422 body through unchanged", async () => {
    const d409 = { detail: "You already have a skill with this name. Choose another name." };
    reply = () => new Response(JSON.stringify(d409), { status: 409 });
    const a = await POST(req("http://x/api/studio/skills", { method: "POST", body: "{}" }), ctx(["skills"]));
    expect(a.status).toBe(409);
    expect(await a.json()).toEqual(d409);
    const d422 = { detail: "$.actions[2]: the catalog serves no action 'x'" };
    reply = () => new Response(JSON.stringify(d422), { status: 422 });
    const b = await POST(req("http://x/api/studio/skills", { method: "POST", body: "{}" }), ctx(["skills"]));
    expect(b.status).toBe(422);
    expect(await b.json()).toEqual(d422);
  });

  it("passes a 204 through", async () => {
    reply = () => new Response(null, { status: 204 });
    const res = await DELETE(req("http://x/api/studio/a", { method: "DELETE" }), ctx(["a"]));
    expect(res.status).toBe(204);
  });

  it("answers 502 when the gateway fails", async () => {
    reply = () => {
      throw new TypeError("fetch failed");
    };
    const res = await GET(req("http://x/api/studio/skills"), ctx(["skills"]));
    expect(res.status).toBe(502);
  });

  it("adds no identity, no tenant and no header beyond gatewayHeaders (source check)", () => {
    const src = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    expect(src.toLowerCase()).not.toContain("x-user-email");
    expect(src).not.toContain("organization_id");
    expect(src).toContain("refuseUnsafePath(path)");
    expect(src.match(/gatewayHeaders\(/g)).toHaveLength(1);
  });
});
