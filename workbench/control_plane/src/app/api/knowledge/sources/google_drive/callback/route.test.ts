// The BFF callback of the Google Drive connect flow (WS-61 KC-1a).
//
// These cases RUN the handler: what goes upstream, which Location the browser
// may follow, and that the query (it holds the consent code) is never logged.
// The model is `app/api/crm/oauth/zoho/callback/route.test.ts`.
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
  return GET(new NextRequest(`${BIND}/api/knowledge/sources/google_drive/callback?${query}`));
}

const redirectTo = (location: string) =>
  new Response(null, { status: 302, headers: { location } });
const refusal = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status });

describe("the BFF Google Drive OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("asks the gateway with a manual redirect and the session", async () => {
    stubGateway(redirectTo("/knowledge?source_connected=google_drive"));
    await callback("code=c0de&state=s1.g");
    expect(calls).toHaveLength(1);
    expect(
      calls[0].url.startsWith("http://gw.test/knowledge/sources/google_drive/callback?"),
    ).toBe(true);
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe(
      "dana@example.com",
    );
  });

  it("passes only the three names on, and nothing else", async () => {
    stubGateway(redirectTo("/knowledge?source_connected=google_drive"));
    await callback("code=c&state=s&error=e&scope=x&authuser=0&prompt=consent&evil=1");
    const sent = new URL(calls[0].url).searchParams;
    expect([...sent.keys()].sort()).toEqual(["code", "error", "state"]);
  });

  it("passes on each Location the gateway callback can send", async () => {
    // Read from the gateway's own module, so a Location there that this
    // route would refuse fails here, and not in front of a member.
    const gateway = readFileSync(
      new URL(
        "../../../../../../../../../apps/services/gateway/gateway/routes/knowledge/sources.py",
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
      constant("RETURN_OK"),
      `${constant("RETURN_FAILED")}scope_missing`,
      `${constant("RETURN_FAILED")}no_refresh_token`,
      `${constant("RETURN_FAILED")}access_denied`,
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("code=c&state=s");
      expect(res.status).toBe(302);
      expect(res.headers.get("location"), location).toBe(location);
    }
  });

  it("turns an absolute or foreign Location into the generic failure", async () => {
    for (const location of [
      "https://evil.example/knowledge?source_connected=google_drive",
      "//evil.example/knowledge?source_connected=google_drive",
      "/\\evil.com",
      "/knowledge@evil",
      "/other?source_connected=google_drive",
      "/knowledge?source_connected=google_drive&x=1",
      "/knowledge?source_connected=dropbox",
      "/knowledge?source_error=<script>",
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("code=c&state=s");
      expect(res.headers.get("location"), location).toBe("/knowledge?source_error=failed");
    }
  });

  it("names a known refusal code, and the generic failure for any other", async () => {
    stubGateway(refusal(400, "invalid_state"));
    expect((await callback("code=c&state=bad")).headers.get("location")).toBe(
      "/knowledge?source_error=invalid_state",
    );
    stubGateway(refusal(503, "google_not_configured"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/knowledge?source_error=google_not_configured",
    );
    stubGateway(refusal(404, "Not Found"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/knowledge?source_error=failed",
    );
    stubGateway(refusal(500, "<script>alert(1)</script>"));
    expect((await callback("code=c&state=s")).headers.get("location")).toBe(
      "/knowledge?source_error=failed",
    );
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo("/knowledge?source_connected=google_drive"));
    const res = await callback("code=c&state=s");
    expect(res.status).toBe(401);
    expect(calls).toHaveLength(0);
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
    expect(res.headers.get("location")).toBe("/knowledge?source_error=failed");
    // The seam is kept (H-194), and the retry is off: the code is single-use.
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("never logs the query", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/console\.|logger|\blog\(/);
  });
});
