// `/api/settings/branding` PUT forwards the logo AND its dark-mode version.
//
// Owner request, 2026-10-09: one upload covers light and dark mode. This
// proxy used to forward `logoBase64` alone, so a dark-mode image and its
// style would have been dropped on the way to the gateway, with no error.
//
// Mutation: forward `{ logoBase64 }` only, and the first test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  NoIdentityError: class extends Error {},
  requireIdentity: async () => ({ email: "admin@example.com" }),
  unauthenticated: () => new Response(null, { status: 401 }),
  gatewayHeaders: async () => ({ Authorization: "Bearer t" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

let sent: Record<string, unknown> | null = null;

beforeEach(() => {
  sent = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      sent = JSON.parse(String(init?.body ?? "{}"));
      return new Response(JSON.stringify({ logo: null }), { status: 200 });
    }),
  );
});

const put = (body: unknown) =>
  new NextRequest("http://app.test/api/settings/branding", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

describe("PUT /api/settings/branding", () => {
  it("forwards the logo, its dark-mode version and the style", async () => {
    const { PUT } = await import("./route");
    const res = await PUT(put({ logoBase64: "AAAA", logoDarkBase64: "BBBB", darkStyle: "white" }));
    expect(res.status).toBe(200);
    expect(sent).toEqual({ logoBase64: "AAAA", logoDarkBase64: "BBBB", darkStyle: "white" });
  });

  it("forwards an old-shape upload as the same logo in both modes", async () => {
    const { PUT } = await import("./route");
    await PUT(put({ logoBase64: "AAAA" }));
    expect(sent).toEqual({ logoBase64: "AAAA", logoDarkBase64: null, darkStyle: "same" });
  });

  it("forwards nothing else the caller sends", async () => {
    const { PUT } = await import("./route");
    await PUT(put({ logoBase64: "AAAA", darkStyle: "plate", organization_id: "evil", mime: "image/svg+xml" }));
    expect(Object.keys(sent ?? {}).sort()).toEqual(["darkStyle", "logoBase64", "logoDarkBase64"]);
  });

  it("refuses a style it does not know, before the gateway", async () => {
    const { PUT } = await import("./route");
    const res = await PUT(put({ logoBase64: "AAAA", darkStyle: "neon" }));
    expect(res.status).toBe(400);
    expect(sent).toBeNull();
  });

  it("refuses a dark-mode image that is not a string", async () => {
    const { PUT } = await import("./route");
    const res = await PUT(put({ logoBase64: "AAAA", logoDarkBase64: 42, darkStyle: "white" }));
    expect(res.status).toBe(400);
  });

  it("admits two images, each at its own cap", async () => {
    const { PUT } = await import("./route");
    const big = "A".repeat(256 * 1024);
    const res = await PUT(put({ logoBase64: big, logoDarkBase64: big, darkStyle: "white" }));
    expect(res.status).toBe(200);
    expect((sent?.logoDarkBase64 as string).length).toBe(big.length);
  });

  it("refuses an image past its own cap", async () => {
    const { PUT } = await import("./route");
    const res = await PUT(put({ logoBase64: "A".repeat(256 * 1024 + 4) }));
    expect(res.status).toBe(413);
  });
});
