// `/api/agent-kit/<client>.zip` relays one zip as bytes (KIT-4b).
//
// Only the six client ids reach the gateway. Any other name is a 404 before
// a request leaves the app.
//
// Mutation: drop the FILE check, and the traversal test fails.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async (extra: Record<string, string> = {}) => ({ ...extra, "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const asked: string[] = [];

function stubGateway(reply: () => Response) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return reply();
    }),
  );
}

async function get(file: string) {
  const { GET } = await import("./route");
  return GET(new NextRequest("http://app.test/api/agent-kit/x"), { params: Promise.resolve({ file }) });
}

describe("/api/agent-kit/[file]", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it("relays the bytes with the gateway's file name and no-store", async () => {
    const bytes = new Uint8Array([80, 75, 3, 4, 1, 2, 3]);
    stubGateway(
      () =>
        new Response(bytes, {
          status: 200,
          headers: { "content-disposition": 'attachment; filename="metorite-agent-kit-codex-v1.zip"' },
        }),
    );
    const res = await get("codex.zip");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/zip");
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(res.headers.get("content-disposition")).toContain("metorite-agent-kit-codex-v1.zip");
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(bytes);
    expect(asked).toEqual(["http://gw.test/agent-kit/codex.zip"]);
  });

  it.each(["../claude.zip", "claude.zip.zip", "Claude.zip", "claude", "claude.zip\n", "a/claude.zip", "other.zip"])(
    "answers 404 for %j before any request leaves the app",
    async (name) => {
      stubGateway(() => new Response("never", { status: 200 }));
      const res = await get(name);
      expect(res.status).toBe(404);
      expect(asked).toEqual([]);
    },
  );

  it("keeps the status of a dark gateway and echoes no input", async () => {
    stubGateway(() => new Response("{}", { status: 404 }));
    const res = await get("gemini.zip");
    expect(res.status).toBe(404);
    expect(JSON.stringify(await res.json())).not.toContain("gemini");
  });

  it("answers 502 when the gateway cannot be reached", async () => {
    stubGateway(() => {
      throw new TypeError("fetch failed");
    });
    expect((await get("gemini.zip")).status).toBe(502);
  });
});
