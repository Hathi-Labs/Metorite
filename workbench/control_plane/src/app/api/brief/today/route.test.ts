// `/api/brief/today` passes `kind` on, and nothing else (R11).
//
// The gateway takes the member, the tenant and the date from the session. A
// query that names a member or a date must not reach it. A refusal keeps its
// status, so a dark switch stays a 404 (WS-BRIEF BR-2c-1).
//
// Mutation: forward `req.nextUrl.search` whole, and the second test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

const seam = vi.hoisted(() => ({
  asked: [] as string[],
  reply: (): Response => new Response("{}", { status: 200 }),
}));

vi.mock("@/lib/gateway", () => ({
  proxyToGateway: async (path: string) => {
    seam.asked.push(path);
    return seam.reply();
  },
}));

const get = async (query = "") => {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://app.test/api/brief/today${query}`));
};

describe("/api/brief/today", () => {
  beforeEach(() => {
    seam.asked.length = 0;
    seam.reply = () => new Response("{}", { status: 200 });
  });

  it("forwards the kind", async () => {
    const res = await get("?kind=closeout");
    expect(res.status).toBe(200);
    expect(seam.asked).toEqual(["/brief/today?kind=closeout"]);
  });

  it("forwards no member, no org and no date", async () => {
    await get("?kind=brief&member=b@x.test&organization_id=o&local_date=2099-01-01");
    expect(seam.asked).toEqual(["/brief/today?kind=brief"]);
    await get();
    expect(seam.asked[1]).toBe("/brief/today");
  });

  it("keeps a refusal's status", async () => {
    seam.reply = () => new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 });
    const res = await get("?kind=brief");
    expect(res.status).toBe(404);
  });

  it("answers 502 when the gateway cannot be reached", async () => {
    seam.reply = () => {
      throw new TypeError("fetch failed");
    };
    const res = await get();
    expect(res.status).toBe(502);
  });
});
