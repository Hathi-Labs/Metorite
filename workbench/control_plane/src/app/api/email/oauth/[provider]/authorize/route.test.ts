// The BFF authorize route forwards `login_hint` (WS-17 EM-T3b) and
// `import_months` (EM-T6d).
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

// WS-17 EM-T6d item 3: the range of the first import. The gateway is the
// authority (EM-T6a answers 400 to a bad value). This hop forwards one digit
// from 0 to 6 and drops anything else, so the connect gets the default.
describe("the authorize BFF and import_months", () => {
  it("forwards import_months=3 beside redirect_after", async () => {
    stubGateway();
    const res = await authorize("redirect_after=%2Femail&import_months=3");
    expect(res.status).toBe(302);
    const sent = new URL(upstream[0]);
    expect(sent.searchParams.get("import_months")).toBe("3");
    expect(sent.searchParams.get("redirect_after")).toBe("/email");
  });

  it("forwards each value from 0 to 6", async () => {
    stubGateway();
    for (const n of ["0", "1", "2", "3", "4", "5", "6"]) await authorize(`import_months=${n}`);
    expect(upstream.map((u) => new URL(u).searchParams.get("import_months"))).toEqual([
      "0", "1", "2", "3", "4", "5", "6",
    ]);
  });

  it("drops 7, -1 and x, and every other shape, and still starts the sign-in", async () => {
    stubGateway();
    const bad = ["7", "-1", "x", "03", "3.0", " 3", "3 ", "", "36", "+3"];
    for (const v of bad) {
      const res = await authorize(new URLSearchParams({ redirect_after: "/email", import_months: v }).toString());
      expect(res.status, v).toBe(302);
    }
    expect(upstream).toHaveLength(bad.length);
    for (const url of upstream) {
      expect(new URL(url).searchParams.has("import_months"), url).toBe(false);
    }
  });

  it("sends no import_months when the browser sent none, as a reconnect does", async () => {
    stubGateway();
    await authorize("redirect_after=%2Femail&login_hint=ravi%40contoso.test");
    expect(new URL(upstream[0]).searchParams.has("import_months")).toBe(false);
  });

  it("takes the first value when the browser sent two", async () => {
    stubGateway();
    await authorize("import_months=2&import_months=9");
    expect(new URL(upstream[0]).searchParams.getAll("import_months")).toEqual(["2"]);
  });
});
