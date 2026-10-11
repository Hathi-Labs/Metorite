// The BFF callback of the Zoho connect flow (WS-53 CRM-Z7).
//
// These cases RUN the handler: what goes upstream, which Location the browser
// may follow, and that the query (it holds the consent code) is never logged.
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

const BIND = "http://localhost:3001";
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
  return GET(new NextRequest(`${BIND}/api/crm/oauth/zoho/callback?${query}`));
}

const redirectTo = (location: string) =>
  new Response(null, { status: 302, headers: { location } });
const refusal = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status });

describe("the BFF Zoho OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("asks the gateway with a manual redirect and the session", async () => {
    stubGateway(redirectTo("/crm?connected=zoho"));
    await callback("code=c0de&state=s1.g&location=in&accounts-server=https%3A%2F%2Faccounts.zoho.in");
    expect(calls).toHaveLength(1);
    expect(calls[0].url.startsWith("http://gw.test/crm/oauth/zoho/callback?")).toBe(true);
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe(
      "dana@example.com",
    );
  });

  it("passes only the five names on, and nothing else", async () => {
    stubGateway(redirectTo("/crm?connected=zoho"));
    await callback("code=c&state=s&error=e&location=in&accounts-server=x&evil=1&admin=true");
    const sent = new URL(calls[0].url).searchParams;
    expect([...sent.keys()].sort()).toEqual([
      "accounts-server",
      "code",
      "error",
      "location",
      "state",
    ]);
  });

  it("sends a success to a relative /crm Location", async () => {
    stubGateway(redirectTo("/crm?connected=zoho"));
    const res = await callback("code=c&state=s");
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/crm?connected=zoho");
  });

  it("passes on a connect_error from the gateway", async () => {
    stubGateway(redirectTo("/crm?connect_error=access_denied"));
    const res = await callback("error=access_denied&state=s");
    expect(res.headers.get("location")).toBe("/crm?connect_error=access_denied");
  });

  it("turns an absolute or foreign Location into the generic failure", async () => {
    for (const location of [
      "https://evil.example/crm?connected=zoho",
      "//evil.example/crm?connected=zoho",
      "/\evil.com",
      "/crm@evil",
      "/other?connected=zoho",
      "/crm?connected=zoho&x=1",
      "/crm?connected=other",
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("code=c&state=s");
      expect(res.headers.get("location"), location).toBe("/crm?connect_error=failed");
    }
  });

  it("names a known refusal code, and the generic failure for any other", async () => {
    stubGateway(refusal(400, "invalid_state"));
    expect((await callback("code=c&state=bad")).headers.get("location")).toBe(
      "/crm?connect_error=invalid_state",
    );
    stubGateway(refusal(400, "untrusted_host"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/crm?connect_error=untrusted_host",
    );
    stubGateway(refusal(409, "mode_is_native"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/crm?connect_error=mode_is_native",
    );
    stubGateway(refusal(500, "<script>alert(1)</script>"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/crm?connect_error=failed",
    );
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo("/crm?connected=zoho"));
    const res = await callback("code=c&state=s");
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("fails soft when the gateway is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    const res = await callback("code=c&state=s");
    expect(res.headers.get("location")).toBe("/crm?connect_error=failed");
  });

  it("makes exactly one upstream call when the first throws", async () => {
    calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url: String(url), init });
        throw new Error("stale socket");
      }),
    );
    const res = await callback("code=c&state=s");
    expect(calls).toHaveLength(1);
    expect(res.headers.get("location")).toBe("/crm?connect_error=failed");
    // The seam is kept (H-194), and the retry is off: the code is single-use.
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("never logs the query", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/console\.|logger|\blog\(/);
  });
});
