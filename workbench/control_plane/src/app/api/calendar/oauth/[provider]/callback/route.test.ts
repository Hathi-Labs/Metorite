// The BFF callback of the calendar consent (WS-CAL CE-10c). These cases RUN the handler: the
// provider word map, what goes upstream, and which Location the browser may follow.
// The model is `app/api/knowledge/sources/google_drive/callback/route.test.ts`.
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

import { GET } from "./route";

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

const callback = (provider: string, query: string) =>
  GET(new NextRequest(`http://localhost:3001/api/calendar/oauth/x/callback?${query}`), {
    params: Promise.resolve({ provider }),
  });

const redirectTo = (location: string) => new Response(null, { status: 302, headers: { location } });
const refusal = (status: number, detail: string) => new Response(JSON.stringify({ detail }), { status });

beforeEach(() => {
  vi.unstubAllGlobals();
  identity.signedIn = true;
  seam.opts.length = 0;
});

describe("the BFF calendar OAuth callback", () => {
  it("maps microsoft to the outlook gateway word, and google to google", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=outlook"));
    await callback("microsoft", "code=c&state=s");
    expect(calls[0].url.startsWith("http://gw.test/tasks/calendar/connections/outlook/callback?")).toBe(true);
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    await callback("google", "code=c&state=s");
    expect(calls[0].url.startsWith("http://gw.test/tasks/calendar/connections/google/callback?")).toBe(true);
  });

  it("asks once, with a manual redirect and the session", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    await callback("google", "code=c&state=s");
    expect(calls).toHaveLength(1);
    expect(calls[0].init?.redirect).toBe("manual");
    expect((calls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe("dana@example.com");
    expect(seam.opts).toEqual([{ retry: false }]);
  });

  it("passes only code, state and error upstream", async () => {
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    await callback("google", "code=c&state=s&error=e&scope=x&authuser=0&evil=1");
    expect([...new URL(calls[0].url).searchParams.keys()].sort()).toEqual(["code", "error", "state"]);
  });

  it("refuses an unknown provider word before any fetch", async () => {
    for (const bad of ["outlook", "GOOGLE", "..", "a%2Fb", "x"]) {
      stubGateway(redirectTo("/calendar?calendar_connected=google"));
      const res = await callback(bad, "code=c&state=s");
      expect(res.status, bad).toBeGreaterThanOrEqual(300);
      expect(calls, bad).toHaveLength(0);
    }
  });

  it("passes on the Location shapes of the gateway", async () => {
    for (const location of [
      "/calendar?calendar_connected=google",
      "/calendar?calendar_connected=outlook",
      "/calendar?calendar_error=consent_declined",
      "/calendar?calendar_error=exchange_failed",
      "/calendar?calendar_error=scope_missing",
      "/calendar?calendar_error=no_refresh_token",
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
      "/\\evil.com",
      "/calendar@evil",
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

  it("names a refusal code of the right shape, and the generic failure for any other", async () => {
    stubGateway(refusal(400, "invalid_state"));
    expect((await callback("google", "code=c&state=bad")).headers.get("location")).toBe(
      "/calendar?calendar_error=invalid_state",
    );
    stubGateway(refusal(404, "Not Found"));
    expect((await callback("google", "code=c&state=s")).headers.get("location")).toBe(
      "/calendar?calendar_error=failed",
    );
    stubGateway(refusal(500, "<script>alert(1)</script>"));
    expect((await callback("google", "code=c&state=s")).headers.get("location")).toBe(
      "/calendar?calendar_error=failed",
    );
  });

  it("answers a signed-out caller with 401 and calls nothing", async () => {
    identity.signedIn = false;
    stubGateway(redirectTo("/calendar?calendar_connected=google"));
    expect((await callback("google", "code=c&state=s")).status).toBe(401);
    expect(calls).toHaveLength(0);
  });

  it("falls back to the generic failure when the gateway cannot be reached", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("down");
      }),
    );
    expect((await callback("google", "code=c&state=s")).headers.get("location")).toBe(
      "/calendar?calendar_error=failed",
    );
  });
});
