// `/api/auth/me` must pass a gateway 503 on, never turn it into "nobody".
//
// 🔴 Measured on production, 2026-10-07: the email sync starved the database
// of IO, the identity read timed out, and a live member signed in as nobody in
// no org. The gateway now answers 503 for that case. This proxy used to turn
// every non-2xx into a 200 of NO_ACCESS, and `resolveAccess` reads a 200 as
// the truth, so the member lost every pane anyway.
//
// Mutation: change the 503 branch back to `NO_ACCESS, { status: 200 }`, and
// the first test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  currentIdentity: async () => ({ email: "a@example.com" }),
  headersActingAs: () => ({}),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

function stubGateway(status: number, body: unknown = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: status === 503 ? { "Retry-After": "2" } : {},
      }),
    ),
  );
}

describe("/api/auth/me under a gateway that cannot reach the directory", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("passes a 503 on, so the shell reads it as unavailable", async () => {
    stubGateway(503, { detail: "We could not reach the directory" });
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(503);
    expect(res.headers.get("Retry-After")).toBe("2");
    const body = (await res.json()) as Record<string, unknown>;
    // No access fields at all: nothing here may read as "you hold nothing".
    expect(body).not.toHaveProperty("features");
  });

  it("keeps the 200 of NO_ACCESS for any other failure", async () => {
    stubGateway(500);
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(200);
  });

  it("forwards a real answer unchanged", async () => {
    stubGateway(200, { email: "a@example.com", features: ["projects"] });
    const { GET } = await import("./route");
    const res = await GET();
    expect(res.status).toBe(200);
    expect(((await res.json()) as { features: string[] }).features).toEqual(["projects"]);
  });
});
