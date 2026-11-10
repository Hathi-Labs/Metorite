// The BFF callback of the MCP OAuth connect flow (WS-54 IN-8a).
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
  return GET(new NextRequest(`${BIND}/api/integrations/mcp/oauth/callback?${query}`));
}

const redirectTo = (location: string) =>
  new Response(null, { status: 302, headers: { location } });
const refusal = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status });

const FAILED = "/integrations?mcp_connect_error=failed";

describe("the BFF MCP OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("asks the gateway once, with a manual redirect and the session", async () => {
    stubGateway(redirectTo("/integrations?mcp_connected=notion"));
    await callback("code=c0de&state=s1.g&iss=https%3A%2F%2Fmcp.notion.com");
    expect(calls).toHaveLength(1);
    expect(calls[0].url.startsWith("http://gw.test/integrations/mcp/oauth/callback?")).toBe(true);
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe(
      "dana@example.com",
    );
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("passes only the four names on, and nothing else", async () => {
    stubGateway(redirectTo("/integrations?mcp_connected=notion"));
    await callback("code=c&state=s&error=e&iss=i&evil=1&org=other");
    const sent = new URL(calls[0].url).searchParams;
    expect([...sent.keys()].sort()).toEqual(["code", "error", "iss", "state"]);
  });

  it("passes on each Location the gateway callback can send", async () => {
    // Read from the gateway's own module, so a Location there that this
    // route would refuse fails here, and not in front of an admin.
    const gateway = readFileSync(
      new URL(
        "../../../../../../../../../apps/services/gateway/gateway/routes/integrations_mcp_oauth.py",
        import.meta.url,
      ),
      "utf8",
    );
    const constant = (name: string) => {
      const m = gateway.match(new RegExp(`^${name} = "([^"]+)"`, "m"));
      expect(m, name).not.toBeNull();
      return m![1];
    };
    for (const location of [
      `${constant("RETURN_OK")}notion`,
      `${constant("RETURN_FAILED")}server_gone`,
      `${constant("RETURN_FAILED")}access_denied`,
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("code=c&state=s");
      expect(res.headers.get("location"), location).toBe(location);
    }
  });

  it("turns an absolute or foreign Location into the generic failure", async () => {
    for (const location of [
      "https://evil.example/integrations?mcp_connected=notion",
      "//evil.example/integrations?mcp_connected=notion",
      "/integrations@evil",
      "/crm?mcp_connected=notion",
      "/integrations?mcp_connected=notion&x=1",
      "/integrations?mcp_connected=Not%20A%20Name",
      "/integrations?connected=zoho",
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("code=c&state=s");
      expect(res.headers.get("location"), location).toBe(FAILED);
    }
  });

  it("names a known refusal code, and the generic failure for any other", async () => {
    stubGateway(refusal(400, "invalid_state"));
    expect((await callback("code=c&state=bad")).headers.get("location")).toBe(
      "/integrations?mcp_connect_error=invalid_state",
    );
    stubGateway(refusal(400, "invalid_issuer"));
    expect((await callback("code=c&state=s&iss=x")).headers.get("location")).toBe(
      "/integrations?mcp_connect_error=invalid_issuer",
    );
    // The flag is off: the gateway answers 404.
    stubGateway(refusal(404, "Not Found"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(FAILED);
    stubGateway(refusal(500, "<script>alert(1)</script>"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(FAILED);
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo("/integrations?mcp_connected=notion"));
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
    expect(res.headers.get("location")).toBe(FAILED);
  });

  it("never logs the query", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/console\.|logger|\blog\(/);
  });
});
