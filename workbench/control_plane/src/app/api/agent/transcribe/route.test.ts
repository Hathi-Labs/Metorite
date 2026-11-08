// `/api/agent/transcribe` (VOX-1): a body with no Content-Length is refused
// before `formData()` can buffer it.
//
// Mutation: drop the 411 check, and the first case reaches the gateway.
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

import { POST } from "./route";

const asked: string[] = [];

function stub() {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return new Response(JSON.stringify({ text: "hello" }), { status: 200 });
    }),
  );
}

function request(headers: Record<string, string>, onPull?: () => void): never {
  const body = new ReadableStream<Uint8Array>({
    pull(c) {
      onPull?.();
      c.close();
    },
  });
  return { headers: new Headers(headers), formData: vi.fn(async () => new FormData()), body } as never;
}

describe("POST /api/agent/transcribe", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    stub();
  });

  it("refuses a request with no Content-Length (411) and reads no body", async () => {
    const req = request({ "content-type": "multipart/form-data; boundary=x" });
    const res = await POST(req);
    expect(res.status).toBe(411);
    expect((req as unknown as { formData: ReturnType<typeof vi.fn> }).formData).not.toHaveBeenCalled();
    expect(asked).toEqual([]);
  });

  it("refuses a Content-Length that is not a number (411)", async () => {
    const res = await POST(request({ "content-length": "abc" }));
    expect(res.status).toBe(411);
  });

  it("refuses a declared body over the cap (413) before the gateway", async () => {
    const res = await POST(request({ "content-length": String(11 * 1024 * 1024 + 1) }));
    expect(res.status).toBe(413);
    expect(asked).toEqual([]);
  });
});
