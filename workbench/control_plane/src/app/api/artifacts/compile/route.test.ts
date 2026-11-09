// `POST /api/artifacts/compile` (task 2432, F1 and the fix round): the guards
// in front of esbuild. Each refusal must stop the request BEFORE esbuild, and
// the body cap must hold when the Content-Length is missing.
//
// Mutations, each turns a named case red:
//   - drop the declared Content-Length check -> "refuses a declared body ...".
//   - drop the cap in the streamed read -> "cuts an undeclared body ...".
//   - fail closed when the gateway cannot count -> "lets the build go on ...".
//   - drop the timeout charge -> "charges a build that ran into the kill".
//   - drop the owner -> "tells compileArtifact who the member is".
import { beforeEach, describe, expect, it, vi } from "vitest";

const gatewayCalls: { url: string; init?: RequestInit; opts?: { retry?: boolean } }[] = [];
let gatewayAnswer: (url: string) => Promise<Response> = async () => new Response(null, { status: 204 });
let identity: unknown = { email: "a@example.com" };

vi.mock("@/lib/gateway", () => {
  class NoIdentityError extends Error {}
  return {
    GATEWAY_URL: "http://gw.test",
    NoIdentityError,
    requireIdentity: async () => identity,
    gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
    gatewayFetch: async (url: string, init?: RequestInit, opts?: { retry?: boolean }) => {
      gatewayCalls.push({ url, init, opts });
      return gatewayAnswer(url);
    },
  };
});

const compileSpy = vi.fn();
vi.mock("@/lib/compileArtifact", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/compileArtifact")>();
  return { ...real, compileArtifact: (...args: unknown[]) => compileSpy(...args) };
});

import { NextResponse } from "next/server";

import { MAX_BODY_BYTES, NO_CODE, NOT_JSON, TOO_LARGE } from "@/lib/compileGuards";

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

const slotCalls = () => gatewayCalls.filter((c) => c.url.endsWith("/artifact-compile/slot"));
const chargeCalls = () => gatewayCalls.filter((c) => c.url.endsWith("/artifact-compile/charge"));

describe("POST /api/artifacts/compile — the guards", () => {
  beforeEach(() => {
    gatewayCalls.length = 0;
    gatewayAnswer = async () => new Response(null, { status: 204 });
    identity = { email: "a@example.com" };
    compileSpy.mockReset();
    compileSpy.mockResolvedValue({ ok: true, js: "console.log(1)", bytes: 14 });
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
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
    expect(b.error).toMatch(/^The request body is larger than 1 MiB\./);
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

  it("takes one rate-limit slot at the gateway, as the member, with a bound and a safe retry", async () => {
    const res = await POST(jsonRequest({ code: "export default () => null" }));
    expect(res.status).toBe(200);
    expect(slotCalls()).toHaveLength(1);
    const call = slotCalls()[0];
    expect(call.url).toBe("http://gw.test/artifact-compile/slot");
    expect(call.init?.method).toBe("POST");
    expect((call.init?.headers as Record<string, string>)["X-User-Email"]).toBe("a@example.com");
    expect(call.init?.signal).toBeInstanceOf(AbortSignal);
    expect(call.opts).toEqual({ retry: true });
    expect(compileSpy).toHaveBeenCalledTimes(1);
    expect(res.headers.get("etag")).toMatch(/^"[0-9a-f]{32}"$/);
  });

  it("tells compileArtifact who the member is, for one build for each member", async () => {
    await POST(jsonRequest({ code: "x" }));
    expect(compileSpy).toHaveBeenCalledWith("x", {}, { owner: "a@example.com" });
  });

  it("passes the gateway's 429 on, with its words and its Retry-After, and never compiles", async () => {
    const words = "You built many artifacts in the last 10 minutes, which reached the limit. Try again in 3 minutes.";
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

  it("refuses when the gateway says 401 or 403", async () => {
    for (const status of [401, 403]) {
      compileSpy.mockClear();
      gatewayAnswer = async () => new Response(JSON.stringify({ detail: { message: "no" } }), { status });
      const res = await POST(jsonRequest({ code: "x" }));
      expect(res.status).toBe(status);
      expect(compileSpy).not.toHaveBeenCalled();
    }
  });

  it("lets the build go on, with a warning, when the gateway cannot count (fail open)", async () => {
    // The counter is for fairness. The in-process guards protect the box.
    const cases: Array<() => Promise<Response>> = [
      async () => {
        throw new Error("ECONNREFUSED");
      },
      async () => {
        throw new DOMException("The operation timed out.", "TimeoutError");
      },
      async () => new Response("boom", { status: 500 }),
      async () =>
        new Response(JSON.stringify({ detail: { code: "unavailable", message: "x" } }), { status: 503 }),
    ];
    for (const answer of cases) {
      compileSpy.mockClear();
      (console.warn as unknown as ReturnType<typeof vi.fn>).mockClear();
      gatewayAnswer = answer;
      const res = await POST(jsonRequest({ code: "x" }));
      expect(res.status).toBe(200);
      expect(compileSpy).toHaveBeenCalledTimes(1);
      expect(console.warn).toHaveBeenCalled();
    }
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

  it("answers a full build queue with 429 busy and Retry-After", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "busy", error: "Metorite is building other artifacts right now. Wait a few seconds and try again." });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("5");
    expect((await body(res)).code).toBe("busy");
  });

  it("answers a member's second build with 429 member_busy and Retry-After", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "member_busy", error: "One of your artifacts is still building." });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(429);
    expect(res.headers.get("retry-after")).toBe("2");
    expect((await body(res)).code).toBe("member_busy");
  });

  it("charges a build that ran into the kill, and answers it as data", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "timeout", error: "stopped" });
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(200);
    expect(await body(res)).toMatchObject({ ok: false, error: "stopped" });
    expect(chargeCalls()).toHaveLength(1);
    expect(JSON.parse(String(chargeCalls()[0].init?.body))).toEqual({ kind: "timeout" });
  });

  it("charges nothing for a build that did not run into the kill", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "source", error: "line 1: bad" });
    await POST(jsonRequest({ code: "x" }));
    expect(chargeCalls()).toHaveLength(0);
  });

  it("answers a killed build even when the charge fails", async () => {
    compileSpy.mockResolvedValue({ ok: false, reason: "timeout", error: "stopped" });
    gatewayAnswer = async (url) => {
      if (url.endsWith("/charge")) throw new Error("ECONNREFUSED");
      return new Response(null, { status: 204 });
    };
    const res = await POST(jsonRequest({ code: "x" }));
    expect(res.status).toBe(200);
    expect(console.warn).toHaveBeenCalled();
  });
});
