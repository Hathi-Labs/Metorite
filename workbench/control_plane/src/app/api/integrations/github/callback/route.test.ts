// The BFF callback of the GitHub App (WS-54 GH-3).
//
// Mutations, red here (run 2026-10-18):
//  B1 forward every query name -> "passes only the four names on".
//  B2 pass any Location on -> "refuses a Location the gateway cannot send".
//  B3 (fix round 1, item 9) drop an empty `state` -> "an empty state goes on, an absent one does not".
import { readFileSync } from "node:fs";
import { NextRequest, NextResponse } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

const identity = vi.hoisted(() => ({ signedIn: true }));
const seam = vi.hoisted(() => ({ opts: [] as unknown[] }));

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () =>
    identity.signedIn
      ? { email: "dana@example.com" }
      : NextResponse.json({ error: "Sign in to continue" }, { status: 401 }),
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

async function callback(query: string) {
  const { GET } = await import("./route");
  return GET(new NextRequest(`http://localhost:3001/api/integrations/github/callback?${query}`));
}

const redirectTo = (location: string) => new Response(null, { status: 302, headers: { location } });
const FAILED = "/integrations?view=connections&github=failed";

describe("the BFF GitHub App callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("asks the gateway once, with a manual redirect and the session", async () => {
    stubGateway(redirectTo("/integrations?view=connections&github=confirm&account=acme"));
    const res = await callback("code=c0de&installation_id=12&setup_action=install&state=s.t");
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/integrations?view=connections&github=confirm&account=acme");
    expect(calls).toHaveLength(1);
    expect(calls[0].url.startsWith("http://gw.test/integrations/github/app/callback?")).toBe(true);
    expect(calls[0].init?.redirect).toBe("manual");
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("passes only the four names on", async () => {
    stubGateway(redirectTo(FAILED));
    await callback("code=c&installation_id=1&setup_action=install&state=s&org=evil&member=x");
    const sent = new URL(calls[0].url).searchParams;
    expect([...sent.keys()].sort()).toEqual(["code", "installation_id", "setup_action", "state"]);
  });

  it("an empty state goes on, an absent one does not", async () => {
    stubGateway(redirectTo(FAILED));
    await callback("code=c&installation_id=1&state=");
    expect(new URL(calls[0].url).searchParams.has("state")).toBe(true);
    stubGateway(redirectTo(FAILED));
    await callback("code=c&installation_id=1");
    expect(new URL(calls[0].url).searchParams.has("state")).toBe(false);
  });

  it("passes on every code that the gateway callback can send", async () => {
    const gateway = readFileSync(
      new URL("../../../../../../../../apps/services/gateway/gateway/routes/github/connect.py", import.meta.url),
      "utf-8",
    );
    const block = gateway.match(/CODES: frozenset\[str\] = frozenset\(\{([\s\S]*?)\}\)/);
    const codes = [...(block?.[1] ?? "").matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(codes.length).toBeGreaterThan(5);
    for (const code of codes.filter((c) => c !== "confirm")) {
      stubGateway(redirectTo(`/integrations?view=connections&github=${code}`));
      const res = await callback("code=c");
      expect(res.headers.get("location")).toBe(`/integrations?view=connections&github=${code}`);
    }
  });

  it("refuses a Location the gateway cannot send", async () => {
    for (const bad of [
      "https://evil.example/integrations?view=connections&github=connected",
      "//evil.example/integrations?github=connected",
      "/integrations?view=connections&github=connected&next=https://evil.example",
      "/elsewhere?view=connections&github=connected",
      "/integrations?view=connections&github=confirm&account=<x>",
      "/integrations?view=keys&github=connected",
    ]) {
      stubGateway(redirectTo(bad));
      const res = await callback("code=c");
      expect(res.headers.get("location")).toBe(FAILED);
    }
  });

  it("a 403 reads forbidden, and any other answer reads failed", async () => {
    stubGateway(new Response(JSON.stringify({ detail: "x" }), { status: 403 }));
    expect((await callback("code=c")).headers.get("location")).toBe(
      "/integrations?view=connections&github=forbidden",
    );
    stubGateway(new Response("{}", { status: 404 }));
    expect((await callback("code=c")).headers.get("location")).toBe(FAILED);
  });

  it("a signed-out browser goes nowhere", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo(FAILED));
    const res = await callback("code=c");
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("never logs the query", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf-8");
    expect(source).not.toMatch(/console\.(log|info|warn|error)/);
  });
});
