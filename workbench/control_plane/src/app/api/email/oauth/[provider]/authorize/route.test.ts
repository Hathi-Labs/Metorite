// The BFF authorize route forwards `login_hint` (WS-17 EM-T3b).
//
// These cases RUN the handler. `test_email_oauth_authorize_wiring.py` reads
// the source for the shape. The reconnect banner sends the mailbox address
// as `login_hint`, and this route is the one hop between the two.
import { NextRequest, NextResponse } from "next/server";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  gatewayHeaders: async () => ({ "X-User-Email": "dana@example.com" }),
  requireIdentity: async () => ({ email: "dana@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

let upstream: string[] = [];

function stubGateway() {
  upstream = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      upstream.push(String(url));
      return new Response(null, {
        status: 302,
        headers: { location: "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1" },
      });
    }),
  );
}

async function authorize(query: string): Promise<NextResponse> {
  const { GET } = await import("./route");
  const req = new NextRequest(`http://localhost:3001/api/email/oauth/microsoft/authorize?${query}`);
  return GET(req, { params: Promise.resolve({ provider: "microsoft" }) });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the authorize BFF and login_hint", () => {
  it("forwards login_hint beside redirect_after", async () => {
    stubGateway();
    const res = await authorize(
      new URLSearchParams({
        redirect_after: "https://app.test/email",
        login_hint: "ravi+mail@contoso.test",
      }).toString(),
    );
    expect(res.status).toBe(302);
    const sent = new URL(upstream[0]);
    expect(sent.pathname).toBe("/email/oauth/microsoft/authorize");
    expect(sent.searchParams.get("login_hint")).toBe("ravi+mail@contoso.test");
    expect(sent.searchParams.get("redirect_after")).toBe("https://app.test/email");
  });

  it("sends no login_hint when the browser sent none or a blank one", async () => {
    stubGateway();
    await authorize("redirect_after=%2Femail");
    await authorize("redirect_after=%2Femail&login_hint=%20%20");
    for (const url of upstream) {
      expect(new URL(url).searchParams.has("login_hint")).toBe(false);
    }
  });

  it("still drops user_email", async () => {
    stubGateway();
    await authorize("login_hint=ravi%40contoso.test&user_email=mallory%40evil.test");
    expect(new URL(upstream[0]).searchParams.has("user_email")).toBe(false);
  });
});
