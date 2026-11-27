// The relays of the Undo chips (HRD-12b): the identity is the session's, the
// body names a chat and an item and nothing else, and a bad id never reaches
// the gateway. Mutation (R7): forward the whole body, and the extra-key case
// goes red.
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

let signedIn = true;
vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () =>
    signedIn ? { email: "a@example.com" } : NextResponse.json({ error: "unauthenticated" }, { status: 401 }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

import { GET } from "./route";
import { POST } from "./undo/route";

const asked: { url: string; init?: RequestInit }[] = [];

function stub(reply: () => Response) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      asked.push({ url: String(input), init });
      return reply();
    }),
  );
}

const ID = "0123456789ab";
const BASE = "http://app.test/api/auth/me/chat-decisions";
const get = (query: string) => new NextRequest(`${BASE}${query}`);
const post = (body: unknown) =>
  new NextRequest(`${BASE}/undo`, { method: "POST", body: typeof body === "string" ? body : JSON.stringify(body) });

beforeEach(() => {
  signedIn = true;
  asked.length = 0;
  vi.unstubAllGlobals();
});

describe("GET /api/auth/me/chat-decisions", () => {
  it("forwards the chat id and passes the answer through", async () => {
    stub(() => new Response(JSON.stringify({ enabled: true, items: [] }), { status: 200 }));
    const res = await GET(get("?session_id=t-1"));
    expect(res.status).toBe(200);
    expect(asked[0].url).toBe("http://gw.test/auth/me/chat-decisions?session_id=t-1");
  });
  it("refuses a bad chat id before the gateway", async () => {
    stub(() => new Response("{}"));
    expect((await GET(get("?session_id=a%20b"))).status).toBe(422);
    expect((await GET(get(""))).status).toBe(422);
    expect(asked).toHaveLength(0);
  });
  it("needs a signed-in member", async () => {
    signedIn = false;
    stub(() => new Response("{}"));
    expect((await GET(get("?session_id=t-1"))).status).toBe(401);
    expect(asked).toHaveLength(0);
  });
  it("answers 502 when the gateway cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    expect((await GET(get("?session_id=t-1"))).status).toBe(502);
  });
});

describe("POST /api/auth/me/chat-decisions/undo", () => {
  it("forwards the chat and the item only", async () => {
    stub(() => new Response(JSON.stringify({ undone: {} }), { status: 200 }));
    const res = await POST(post({ session_id: "t-1", id: ID, member: "evil@x.test", organization_id: "other" }));
    expect(res.status).toBe(200);
    expect(asked[0].url).toBe("http://gw.test/auth/me/chat-decisions/undo");
    expect(JSON.parse(asked[0].init?.body as string)).toEqual({ session_id: "t-1", id: ID });
  });
  it("refuses a bad body, a bad id and a big body before the gateway", async () => {
    stub(() => new Response("{}"));
    expect((await POST(post("not json"))).status).toBe(422);
    expect((await POST(post({ session_id: "a b", id: ID }))).status).toBe(422);
    expect((await POST(post({ session_id: "t-1", id: "ZZ" }))).status).toBe(422);
    expect((await POST(post({ session_id: "t-1", id: ID, pad: "x".repeat(2000) }))).status).toBe(413);
    expect(asked).toHaveLength(0);
  });
  it("passes a 404 through so the chip reads it as gone", async () => {
    stub(() => new Response(JSON.stringify({ detail: "no_match" }), { status: 404 }));
    expect((await POST(post({ session_id: "t-1", id: ID }))).status).toBe(404);
  });
  it("answers 502 when the gateway cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    expect((await POST(post({ session_id: "t-1", id: ID }))).status).toBe(502);
  });
});
