// `POST /api/artifacts/compile` (task 2432, F1): the guards in front of
// esbuild. Each refusal must stop the request BEFORE esbuild, and the body
// cap must hold when the Content-Length is missing.
//
// Mutations, each turns a named case red:
//   - drop the declared Content-Length check -> "refuses a declared body ...".
//   - drop the cap in the streamed read -> "cuts an undeclared body ...".
//   - fail open when the gateway is down -> "fails closed ...".
import { beforeEach, describe, expect, it, vi } from "vitest";

const gatewayCalls: { url: string; init?: RequestInit }[] = [];
let gatewayAnswer: () => Promise<Response> = async () => new Response(null, { status: 204 });
let identity: unknown = { email: "a@example.com" };

vi.mock("@/lib/gateway", () => {
  class NoIdentityError extends Error {}
  return {
    GATEWAY_URL: "http://gw.test",
    NoIdentityError,
    requireIdentity: async () => identity,
    gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
    gatewayFetch: async (url: string, init?: RequestInit) => {
      gatewayCalls.push({ url, init });
      return gatewayAnswer();
    },
  };
});

const compileSpy = vi.fn();
vi.mock("@/lib/compileArtifact", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/compileArtifact")>();
  return { ...real, compileArtifact: (...args: unknown[]) => compileSpy(...args) };
});

import { NextResponse } from "next/server";

import {
  LIMIT_UNAVAILABLE,
  MAX_BODY_BYTES,
  NO_CODE,
  NOT_JSON,
  TOO_LARGE,
} from "@/lib/compileGuards";

import { POST } from "./route";

const URL_ = "http://app.test/api/artifacts/compile";

function jsonRequest(body: unknown, headers: Record<string, string> = {}): Request {
  return new Request(URL_, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

/** A body sent as a stream with no Content-Length, in chunks of `chunk` bytes. */
function streamedRequest(total: number, chunk = 64 * 1024): { req: Request; pulled: () => number } {
  let sent = 0;
  let pulls = 0;
  const stream = new ReadableStream<Uint8Array>({
    pull(c) {
      pulls += 1;
      if (sent >= total) {
        c.close();
        return;
      }
      const n = Math.min(chunk, total - sent);
      sent += n;
      c.enqueue(new Uint8Array(n).fill(0x61));
    },
  });
  const req = new Request(URL_, {
    method: "POST",
    body: stream,
    // Node's fetch needs this for a stream body.
    duplex: "half",
  } as RequestInit & { duplex: "half" });
  return { req, pulled: () => pulls };
}

async function body(res: Response): Promise<{ ok?: boolean; error?: string; code?: string }> {
  return (await res.json()) as { ok?: boolean; error?: string; code?: string };
}

describe("POST /api/artifacts/compile — the guards", () => {
  beforeEach(() => {
    gatewayCalls.length = 0;
    gatewayAnswer = async () => new Response(null, { status: 204 });
    identity = { email: "a@example.com" };
    compileSpy.mockReset();
    compileSpy.mockResolvedValue({ ok: true, js: "console.log(1)", bytes: 14 });
  });

  it("answers 401 with no member, and asks nothing of the gateway", async () => {
    identity = NextResponse.json({ error: "Sign in to continue" }, { status: 401 });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(401);
    expect(gatewayCalls).toEqual([]);
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("refuses a declared body over the cap (413) before the gateway and esbuild", async () => {
    const res = await POST(jsonRequest({ code: "x" }, { "content-length": String(MAX_BODY_BYTES + 1) }));
    expect(res.status).toBe(413);
    const b = await body(res);
    expect(b).toMatchObject({ ok: false, code: "too_large", error: TOO_LARGE });
    expect(b.error).toMatch(/Split the artifact up or trim it/);
    expect(gatewayCalls).toEqual([]);
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("cuts an undeclared body at the cap (413), and stops reading it", async () => {
    const { req, pulled } = streamedRequest(MAX_BODY_BYTES * 8);
    const res = await POST(req);
    expect(res.status).toBe(413);
    expect((await body(res)).code).toBe("too_large");
    expect(compileSpy).not.toHaveBeenCalled();
    // 8x the cap was on offer. The read stopped just past the cap.
    expect(pulled()).toBeLessThanOrEqual(MAX_BODY_BYTES / (64 * 1024) + 3);
  });

  it("takes one rate-limit slot at the gateway, as the member, before it compiles", async () => {
    const res = await POST(jsonRequest({ code: "export default () => null" }));
    expect(res.status).toBe(200);
    expect(gatewayCalls).toHaveLength(1);
    expect(gatewayCalls[0].url).toBe("http://gw.test/artifact-compile/slot");
    expect(gatewayCalls[0].init?.method).toBe("POST");
    expect((gatewayCalls[0].init?.headers as Record<string, string>)["X-User-Email"]).toBe("a@example.com");
    expect(compileSpy).toHaveBeenCalledTimes(1);
    expect(res.headers.get("etag")).toMatch(/^"[0-9a-f]{32}"$/);
  });

  it("passes the gateway's 429 on, with its words and its Retry-After, and never compiles", async () => {
    const words = "You built 200 artifacts in the last 10 minutes, which is the limit. Try again in 3 minutes.";
    gatewayAnswer = async () =>
      new Response(JSON.stringify({ detail: { code: "cap", message: words } }), {
        status: 429,
        headers: { "Retry-After": "170" },
      });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("170");
    expect(await body(res)).toMatchObject({ ok: false, code: "rate_limited", error: words });
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("fails closed (503) when the gateway does not answer, and never compiles", async () => {
    gatewayAnswer = async () => {
      throw new Error("ECONNREFUSED");
    };
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).toBe("2");
    expect(await body(res)).toMatchObject({ ok: false, error: LIMIT_UNAVAILABLE });
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("fails closed (503) on a gateway fault, and never compiles", async () => {
    gatewayAnswer = async () => new Response("boom", { status: 500 });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(503);
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("answers 400 for a body that is not JSON, and for JSON with no code string", async () => {
    const a = await POST(jsonRequest("{not json"));
    expect(a.status).toBe(400);
    expect((await body(a)).error).toBe(NOT_JSON);
    const b = await POST(jsonRequest({ code: 7 }));
    expect(b.status).toBe(400);
    expect((await body(b)).error).toBe(NO_CODE);
    expect(compileSpy).not.toHaveBeenCalled();
  });

  it("answers a full build queue with 429 and Retry-After", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "busy", error: "Metorite is building other artifacts right now. Wait a few seconds and try again." });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("5");
    expect((await body(res)).code).toBe("busy");
  });

  it("answers a timeout or a compile error as data (200, ok:false), as before", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "timeout", error: "stopped" });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(200);
    expect(await body(res)).toMatchObject({ ok: false, error: "stopped" });
  });
});
