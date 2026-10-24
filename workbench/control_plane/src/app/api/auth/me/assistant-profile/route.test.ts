// `/api/auth/me/assistant-profile/**` forwards method, path and body (WS-63 PZ-7).
//
// Mutation: make the relay's catch answer 200 with an empty profile, and the
// 502 test fails. Make it drop the body, and the PUT test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest, NextResponse } from "next/server";

let signedIn = true;
vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () =>
    signedIn ? { email: "a@example.com" } : NextResponse.json({ error: "unauthenticated" }, { status: 401 }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const asked: { url: string; init?: RequestInit }[] = [];

function stub(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      asked.push({ url: String(input), init });
      return reply();
    }),
  );
}

const BASE = "http://app.test/api/auth/me/assistant-profile";
const GW = "http://gw.test/auth/me/assistant-profile";
const req = (path: string, method: string, body?: unknown) =>
  new NextRequest(`${BASE}${path}`, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
const ctx = (id: string) => ({ params: Promise.resolve({ id }) });

describe("/api/auth/me/assistant-profile", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    signedIn = true;
  });

  it("GET forwards and returns the profile", async () => {
    stub(() => new Response(JSON.stringify({ v: 1, learning: true }), { status: 200 }));
    const { GET } = await import("./route");
    const res = await GET(req("", "GET"));
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ v: 1, learning: true });
    expect(asked[0].url).toBe(GW);
    expect(asked[0].init?.method).toBe("GET");
  });

  it("PUT forwards the body", async () => {
    stub(() => new Response("{}", { status: 200 }));
    const { PUT } = await import("./route");
    await PUT(req("", "PUT", { key: "learning", value: false }));
    expect(asked[0].url).toBe(GW);
    expect(asked[0].init?.method).toBe("PUT");
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ key: "learning", value: false });
  });

  it("DELETE passes 204 through with no body", async () => {
    stub(() => new Response(null, { status: 204 }));
    const { DELETE } = await import("./route");
    const res = await DELETE(req("", "DELETE"));
    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
    expect(asked[0].init?.method).toBe("DELETE");
  });

  it("POST /facts and /aliases forward to their paths with 201", async () => {
    stub(() => new Response(JSON.stringify({ id: "f1" }), { status: 201 }));
    const facts = await import("./facts/route");
    const a = await facts.POST(req("/facts", "POST", { text: "x", kind: "about" }));
    expect(a.status).toBe(201);
    expect(asked[0].url).toBe(`${GW}/facts`);
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ text: "x", kind: "about" });
    const aliases = await import("./aliases/route");
    await aliases.POST(req("/aliases", "POST", { alias: "Mum", email: "m@example.com" }));
    expect(asked[1].url).toBe(`${GW}/aliases`);
    expect(asked[1].init?.method).toBe("POST");
  });

  it.each(["..", ".", "a b/1", "%2E%2E", "f1/../x", ""])(
    "an id of the wrong shape (%j) is a 404 and reaches no gateway",
    async (bad) => {
      stub(() => new Response(null, { status: 204 }));
      const f = await import("./facts/[id]/route");
      const a = await import("./aliases/[id]/route");
      expect((await f.DELETE(req("/facts/x", "DELETE"), ctx(bad))).status).toBe(404);
      expect((await a.PATCH(req("/aliases/x", "PATCH", { alias: "R" }), ctx(bad))).status).toBe(404);
      expect(asked).toEqual([]);
    },
  );

  it("PATCH and DELETE on an id put the id in the path", async () => {
    stub(() => new Response("{}", { status: 200 }));
    const f = await import("./facts/[id]/route");
    await f.PATCH(req("/facts/f1a2", "PATCH", { text: "y" }), ctx("f1a2"));
    expect(asked[0].url).toBe(`${GW}/facts/f1a2`);
    expect(asked[0].init?.method).toBe("PATCH");
    expect(JSON.parse(String(asked[0].init?.body))).toEqual({ text: "y" });
    stub(() => new Response(null, { status: 204 }));
    const r = await f.DELETE(req("/facts/f1", "DELETE"), ctx("f1"));
    expect(r.status).toBe(204);
    expect(asked[0].url).toBe(`${GW}/facts/f1`);
    const a = await import("./aliases/[id]/route");
    await a.PATCH(req("/aliases/al1", "PATCH", { alias: "Mom" }), ctx("al1"));
    expect(asked[1].url).toBe(`${GW}/aliases/al1`);
    await a.DELETE(req("/aliases/al1", "DELETE"), ctx("al1"));
    expect(asked[2].init?.method).toBe("DELETE");
  });

  it("passes 422 JSON and 404 through", async () => {
    stub(() => new Response(JSON.stringify({ detail: "fact too long" }), { status: 422 }));
    const f = await import("./facts/route");
    const res = await f.POST(req("/facts", "POST", { text: "x", kind: "about" }));
    expect(res.status).toBe(422);
    expect(await res.json()).toEqual({ detail: "fact too long" });
    stub(() => new Response(JSON.stringify({ detail: "not found" }), { status: 404 }));
    const fi = await import("./facts/[id]/route");
    const nf = await fi.DELETE(req("/facts/zz", "DELETE"), ctx("zz"));
    expect(nf.status).toBe(404);
  });

  it("answers 502 when the gateway cannot be reached, never an empty profile", async () => {
    stub(() => {
      throw new TypeError("fetch failed");
    });
    const { GET } = await import("./route");
    const res = await GET(req("", "GET"));
    expect(res.status).toBe(502);
    expect(await res.json()).not.toHaveProperty("facts");
  });

  it("answers 401 without an identity and calls no gateway", async () => {
    signedIn = false;
    stub(() => new Response("{}", { status: 200 }));
    const { GET, PUT, DELETE } = await import("./route");
    expect((await GET(req("", "GET"))).status).toBe(401);
    expect((await PUT(req("", "PUT", {}))).status).toBe(401);
    expect((await DELETE(req("", "DELETE"))).status).toBe(401);
    expect(asked).toHaveLength(0);
  });
});
