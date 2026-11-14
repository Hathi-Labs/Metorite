// `/api/chat/pending-asks` fails LOUD (RESUME-0a, `chat_run_continuity.md` 4.12.2).
//
// The chat reads `[]` as "no card waits" and settles its cards. So a failure
// must never be a 200 of an empty list.
//
// Mutation: make a failure answer `NextResponse.json([])` again, and the
// failure tests fail.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const call = async (qs = "?threadId=t1") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/chat/pending-asks${qs}`));
};

describe("/api/chat/pending-asks", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("passes the list on, and an empty list stays an empty 200", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));
    const res = await call();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual([]);
  });

  it.each([500, 503, 403])("passes a gateway %i on", async (status) => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("no", { status })));
    expect((await call()).status).toBe(status);
  });

  it("answers 504 for a timeout", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new DOMException("t", "TimeoutError"); }));
    expect((await call()).status).toBe(504);
  });

  it("answers 502 for a thrown fetch", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("offline"); }));
    expect((await call()).status).toBe(502);
  });

  it("answers 400 for a missing threadId", async () => {
    expect((await call("")).status).toBe(400);
  });
});
