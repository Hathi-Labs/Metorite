/**
 * The failure partition (R7 fence for LS-5 · `launch_surface.md` §8.2).
 *
 * One claim, and it is the whole of the bug this replaces:
 *
 * > A server that says "no" and a server that says nothing must not produce
 * > the same result.
 *
 * The old `fetchAccess` collapsed both into `NO_ACCESS`, and because
 * `AccessProvider` re-resolves every 120 seconds, a single 502 from a
 * restarting gateway emptied a signed-in member's sidebar mid-session. These
 * tests pin the three outcomes apart at the seam that can see the status code.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { NO_ACCESS, fetchAccess, resolveAccess } from "./access";

type FetchArgs = Parameters<typeof fetch>;

/** Install a `fetch` that answers however the test says. */
function stubFetch(impl: (...args: FetchArgs) => Promise<Response> | Response) {
  vi.stubGlobal("fetch", vi.fn(impl));
}

const payload = {
  email: "vj@fracktal.in",
  authenticated: true,
  is_active: true,
  features: ["chat", "tasks"],
  is_admin: true,
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resolveAccess reports WHETHER the answer is authoritative", () => {
  it("returns ok with the normalized payload on a 200", async () => {
    stubFetch(() => jsonResponse(payload));
    const result = await resolveAccess();
    expect(result.kind).toBe("ok");
    if (result.kind !== "ok") return;
    expect(result.access.features).toEqual(["chat", "tasks"]);
    // A gateway predating a list field must read as "nothing granted", never
    // as undefined — the normalize step, still applied.
    expect(result.access.capabilities).toEqual([]);
    expect(result.access.features_denied).toEqual([]);
  });

  it("returns unauthorized on 401 and on 403 — the server SAID no", async () => {
    for (const status of [401, 403]) {
      stubFetch(() => new Response("", { status }));
      expect((await resolveAccess()).kind).toBe("unauthorized");
    }
  });

  it("returns unavailable on a 5xx — the server said NOTHING", async () => {
    // The exact case behind "as if I am signed out". A 500 from the gateway is
    // not a revoked permission, and must not be reported as one.
    for (const status of [500, 502, 503, 504]) {
      stubFetch(() => new Response("", { status }));
      const result = await resolveAccess();
      expect(result.kind, `HTTP ${status}`).toBe("unavailable");
    }
  });

  it("returns unavailable on a network error", async () => {
    stubFetch(() => Promise.reject(new TypeError("Failed to fetch")));
    const result = await resolveAccess();
    expect(result.kind).toBe("unavailable");
    if (result.kind === "unavailable") expect(result.reason).toBe("network");
  });

  it("returns unavailable on a 200 whose body will not parse", async () => {
    // A truncated proxy response is a silence too, not an answer of "nothing
    // granted".
    stubFetch(
      () =>
        new Response("<html>gateway timeout</html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
    );
    const result = await resolveAccess();
    expect(result.kind).toBe("unavailable");
    if (result.kind === "unavailable") expect(result.reason).toBe("malformed");
  });

  it("reports our own abort as aborted, not as a failure", async () => {
    // A React effect cleanup or a StrictMode double-mount must not look like an
    // outage, or every navigation would mark the access stale.
    const controller = new AbortController();
    stubFetch(() => {
      const err = new Error("aborted");
      err.name = "AbortError";
      return Promise.reject(err);
    });
    controller.abort();
    expect((await resolveAccess(controller.signal)).kind).toBe("aborted");
  });

  it("never throws, whatever fetch does", async () => {
    stubFetch(() => {
      throw new Error("synchronous explosion");
    });
    await expect(resolveAccess()).resolves.toBeTruthy();
  });
});

describe("fetchAccess stays fail-closed for one-shot callers", () => {
  it("gives NO_ACCESS for every non-ok outcome", async () => {
    for (const make of [
      () => new Response("", { status: 401 }),
      () => new Response("", { status: 503 }),
      () => Promise.reject(new TypeError("Failed to fetch")) as never,
    ]) {
      stubFetch(make as never);
      expect(await fetchAccess()).toEqual(NO_ACCESS);
    }
  });

  it("still returns the payload on success", async () => {
    stubFetch(() => jsonResponse(payload));
    expect((await fetchAccess()).features).toEqual(["chat", "tasks"]);
  });
});
