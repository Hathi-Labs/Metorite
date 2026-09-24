/**
 * WS-27bm S8 — the BFF in front of `POST /documents/pdf`.
 *
 * A PDF is binary. A proxy that decodes it as text breaks every byte above
 * 0x7F, and the file then opens as "damaged". So this test runs the real
 * handler against a stubbed gateway and compares BYTES, the way
 * `lib/export.test.ts` does for a CSV's BOM.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

// Every byte value, so a decode anywhere on the path shows as a mismatch.
const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0xe2, 0xe3, 0xcf, 0xd3, 0x00, 0xff, 0x80]);

afterEach(() => {
  vi.unstubAllGlobals();
});

async function post(upstream: Response, contentType = "text/html; charset=utf-8") {
  const seen: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      seen.push({ url, init });
      return upstream;
    }),
  );
  const { NextRequest } = await import("next/server");
  const { POST } = await import("./route");
  const res = await POST(
    new NextRequest("http://localhost:3001/api/documents/pdf?filename=Weekly.pdf", {
      method: "POST",
      body: "<h2>Weekly</h2>",
      headers: { "content-type": contentType },
    }),
  );
  return { res, seen };
}

describe("POST /api/documents/pdf", () => {
  it("relays the gateway's PDF byte for byte, with its filename", async () => {
    const { res, seen } = await post(
      new Response(PDF, {
        status: 200,
        headers: {
          "content-type": "application/pdf",
          "content-disposition": 'attachment; filename="Weekly.pdf"',
        },
      }),
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Weekly.pdf"');
    expect(Array.from(new Uint8Array(await res.arrayBuffer()))).toEqual(Array.from(PDF));

    // Up: the gateway's route, the filename, the caller's content type, the HTML.
    expect(seen[0].url).toMatch(/\/documents\/pdf\?filename=Weekly\.pdf$/);
    const headers = seen[0].init.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBe("text/html; charset=utf-8");
    expect(headers["X-User-Email"]).toBe("priya@fracktal.in");
    expect(new TextDecoder().decode(seen[0].init.body as ArrayBuffer)).toBe("<h2>Weekly</h2>");
  });

  it("passes the caller's content type up, so the gateway can refuse it", async () => {
    const { res, seen } = await post(
      new Response(JSON.stringify({ detail: "Send the document as text/html." }), {
        status: 415,
        headers: { "content-type": "application/json" },
      }),
      "application/json",
    );
    expect((seen[0].init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect(res.status).toBe(415);
    expect(await res.json()).toEqual({ detail: "Send the document as text/html." });
  });
});
