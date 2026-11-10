// The BFF disconnect of GitHub (WS-54 GH-3, §7.5).
//
// Mutation, red here (run 2026-10-18): pass any `installation_url` on ->
// "passes on only an installation page at GitHub".
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

async function disconnect(site = "same-origin") {
  const { DELETE } = await import("./route");
  return DELETE(new NextRequest("http://localhost:3001/api/integrations/github", {
    method: "DELETE", headers: { "sec-fetch-site": site },
  }));
}

describe("the BFF GitHub disconnect", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("passes on only an installation page at GitHub", async () => {
    stubGateway(new Response(JSON.stringify({
      disconnected: true,
      installation_url: "https://github.com/organizations/acme/settings/installations/12",
    }), { status: 200 }));
    expect(await (await disconnect()).json()).toEqual({
      disconnected: true,
      installation_url: "https://github.com/organizations/acme/settings/installations/12",
    });
    stubGateway(new Response(JSON.stringify({
      disconnected: true, installation_url: "https://evil.example/settings/installations/12",
    }), { status: 200 }));
    expect(await (await disconnect()).json()).toEqual({ disconnected: true, installation_url: null });
    expect(calls[0].url).toBe("http://gw.test/integrations/github");
    expect(calls[0].init?.method).toBe("DELETE");
  });

  it("a cross-site disconnect gets 403 and goes nowhere", async () => {
    stubGateway(new Response("{}", { status: 200 }));
    expect((await disconnect("cross-site")).status).toBe(403);
    expect(calls).toHaveLength(0);
  });
});
