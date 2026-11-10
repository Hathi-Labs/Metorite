// The BFF start of the GitHub App connect (WS-54 GH-3).
//
// Mutation, red here (run 2026-10-18): pass any gateway `url` on ->
// "a URL that is not a GitHub install link is a 502".
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "github-bff-test-secret-at-least-32-chars!",
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () => ({ email: "dana@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

let calls: { url: string; init?: RequestInit }[] = [];

function stubGateway(res: Response) {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init });
      return res;
    }),
  );
}

async function start(site = "same-origin") {
  const { POST } = await import("./route");
  return POST(new NextRequest("http://localhost:3001/api/integrations/github/app/start", {
    method: "POST", headers: { "sec-fetch-site": site },
  }));
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

describe("the BFF GitHub App start", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("passes the gateway's install link on", async () => {
    const url = "https://github.com/apps/metorite/installations/new?state=abc.def";
    stubGateway(json({ url }));
    const res = await start();
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ url });
    expect(calls[0].url).toBe("http://gw.test/integrations/github/app/start");
    expect(calls[0].init?.body).toBeUndefined();
  });

  it("a URL that is not a GitHub install link is a 502", async () => {
    for (const url of ["https://evil.example/x", "https://github.com/login?state=a", 7]) {
      stubGateway(json({ url }));
      const res = await start();
      expect(res.status).toBe(502);
    }
  });

  it("a cross-site start gets 403 and goes nowhere", async () => {
    stubGateway(json({}));
    const res = await start("cross-site");
    expect(res.status).toBe(403);
    expect(calls).toHaveLength(0);
  });

  it("the flag off reads not_available", async () => {
    stubGateway(json({ detail: "Not Found" }, 404));
    const res = await start();
    expect(res.status).toBe(404);
    expect(await res.json()).toEqual({ detail: "not_available" });
  });
});
