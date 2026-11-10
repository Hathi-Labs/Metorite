// The BFF confirm of the GitHub App connect (WS-54 GH-3, C12).
//
// Mutation, red here (run 2026-10-18): drop the `sameOrigin` refusal ->
// "a cross-site confirm gets 403 and goes nowhere".
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const seam = vi.hoisted(() => ({ opts: [] as unknown[] }));

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "dana@example.com" } }),
  isAuthEnabled: true,
  SESSION_SECRET: "github-bff-test-secret-at-least-32-chars!",
}));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () => ({ email: "dana@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit, opts?: unknown) => {
    seam.opts.push(opts);
    return fetch(input, init);
  },
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

async function confirm(site: string | null) {
  const { POST } = await import("./route");
  const headers: Record<string, string> = {};
  if (site) headers["sec-fetch-site"] = site;
  return POST(new NextRequest("http://localhost:3001/api/integrations/github/app/confirm", {
    method: "POST", headers,
  }));
}

describe("the BFF GitHub App confirm", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("a cross-site confirm gets 403 and goes nowhere", async () => {
    for (const site of ["cross-site", "same-site", "none", null]) {
      stubGateway(new Response("{}", { status: 200 }));
      const res = await confirm(site);
      expect(res.status).toBe(403);
      expect(await res.json()).toEqual({ detail: "cross_origin" });
      expect(calls).toHaveLength(0);
    }
  });

  it("a same-origin confirm asks the gateway once, with no body", async () => {
    stubGateway(new Response(JSON.stringify({
      status: "connected", account_login: "acme", account_type: "Organization",
      repository_selection: "selected",
    }), { status: 200 }));
    const res = await confirm("same-origin");
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      status: "connected", account_login: "acme", account_type: "Organization",
      repository_selection: "selected",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("http://gw.test/integrations/github/app/confirm");
    expect(calls[0].init?.method).toBe("POST");
    expect(calls[0].init?.body).toBeUndefined();
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("passes a short refusal code on", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "other_org" }), { status: 409 }));
    const res = await confirm("same-origin");
    expect(res.status).toBe(409);
    expect(await res.json()).toEqual({ detail: "other_org" });
  });

  it("never passes a long detail on", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "Traceback (most recent call last)" }), { status: 500 }));
    const res = await confirm("same-origin");
    expect(await res.json()).toEqual({ detail: "failed" });
  });
});
