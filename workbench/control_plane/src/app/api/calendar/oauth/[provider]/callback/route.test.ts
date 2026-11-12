// The BFF callback of the calendar connect flow (WS-CAL CE-10a).
//
// These cases RUN the handler: what goes upstream, which Location the browser
// may follow, and that the query (it holds the consent code) is never logged.
// The model is `app/api/knowledge/sources/google_drive/callback/route.test.ts`.
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

async function callback(provider: string, query: string) {
  const { GET } = await import("./route");
  return GET(new NextRequest(`${BIND}/api/calendar/oauth/${provider}/callback?${query}`), {
    params: Promise.resolve({ provider }),
  });
}

const redirectTo = (location: string) =>
  new Response(null, { status: 302, headers: { location } });
const refusal = (status: number, detail: string) =>
  new Response(JSON.stringify({ detail }), { status });

describe("the BFF calendar OAuth callback", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    identity.signedIn = true;
  });

  it("maps the console word to the gateway route, manual redirect, session", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    await callback("google", "code=c0de&state=s1.g");
    expect(calls[0].url.startsWith("http://gw.test/tasks/calendar/connections/google/callback?")).toBe(
      true,
    );
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe(
      "dana@example.com",
    );
    stubGateway(redirectTo("/calendar?calendar_connected=outlook"));
    await callback("microsoft", "code=c&state=s");
    expect(calls[0].url.startsWith("http://gw.test/tasks/calendar/connections/outlook/callback?")).toBe(
      true,
    );
  });

  it("answers 404 for an unknown provider and calls nothing", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    for (const word of ["outlook", "yahoo", "__proto__", "constructor"]) {
      const res = await callback(word, "code=c&state=s");
      expect(res.status, word).toBe(404);
    }
    expect(calls).toHaveLength(0);
  });

  it("passes only the three names on, and nothing else", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    await callback("google", "code=c&state=s&error=e&scope=x&authuser=0&evil=1");
    const sent = new URL(calls[0].url).searchParams;
    expect([...sent.keys()].sort()).toEqual(["code", "error", "state"]);
  });

  it("passes on each Location the gateway callback can send", async () => {
    const gateway = readFileSync(
      new URL(
        "../../../../../../../../../apps/services/gateway/gateway/routes/tasks/calendar_connections.py",
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
      `${constant("RETURN_OK")}google`,
      `${constant("RETURN_OK")}outlook`,
      `${constant("RETURN_FAILED")}scope_missing`,
      `${constant("RETURN_FAILED")}no_refresh_token`,
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("google", "code=c&state=s");
      expect(res.status).toBe(302);
      expect(res.headers.get("location"), location).toBe(location);
    }
  });

  it("turns an absolute or foreign Location into the generic failure", async () => {
    for (const location of [
      "https://evil.example/calendar?calendar_connected=google",
      "//evil.example/calendar?calendar_connected=google",
      "/\evil.com",
      "/other?calendar_connected=google",
      "/calendar?calendar_connected=google&x=1",
      "/calendar?calendar_connected=dropbox",
      "/calendar?calendar_error=<script>",
    ]) {
      stubGateway(redirectTo(location));
      const res = await callback("google", "code=c&state=s");
      expect(res.headers.get("location"), location).toBe("/calendar?calendar_error=failed");
    }
  });

  it("names a known refusal code, and the generic failure for any other", async () => {
    stubGateway(refusal(400, "invalid_state"));
    expect((await callback("google", "code=c&state=bad")).headers.get("location")).toBe(
      "/calendar?calendar_error=invalid_state",
    );
    stubGateway(refusal(503, "outlook_not_configured"));
    expect((await callback("microsoft", "code=c&state=s")).headers.get("location")).toBe(
      "/calendar?calendar_error=outlook_not_configured",
    );
    stubGateway(refusal(500, "<script>alert(1)</script>"));
    expect((await callback("google", "code=c&state=s")).headers.get("location")).toBe(
      "/calendar?calendar_error=failed",
    );
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    const res = await callback("google", "code=c&state=s");
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
    const res = await callback("google", "code=c&state=s");
    expect(calls).toHaveLength(1);
    expect(res.headers.get("location")).toBe("/calendar?calendar_error=failed");
    expect(seam.opts.at(-1)).toEqual({ retry: false });
  });

  it("never logs the query", () => {
    const source = readFileSync(new URL("./route.ts", import.meta.url), "utf8");
    const code = source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
    expect(code).not.toMatch(/console\.|logger|\blog\(/);
  });
});
