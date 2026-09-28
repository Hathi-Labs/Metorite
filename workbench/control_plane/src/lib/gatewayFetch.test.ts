/**
 * The workbench survives a gateway restart (H-194).
 *
 * `gatewayFetch` tries a refused connection again while the gateway restarts,
 * and it never replays a request that the gateway may have received. These
 * tests run it against a REAL local HTTP server, because the thing under test
 * is how Node's `fetch` fails on a real socket: a refusal, a reset before the
 * headers, and a stream that dies after the first byte. A fake that throws a
 * hand-made error would agree with whatever shape we guessed.
 *
 * Rules: see the header of `gatewayFetch.ts`.
 */
import { describe, it, expect, afterEach, beforeAll, beforeEach } from "vitest";
import http from "node:http";
import net from "node:net";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  GATEWAY_RETRY,
  breakerOpen,
  connectFailureCode,
  gatewayFetch,
  isReplayableBody,
  redactPath,
  resetGatewayBreaker,
  retryDeadlineMs,
} from "@/lib/gatewayFetch";

// The breaker is process state. Each test starts with every breaker closed.
beforeEach(() => resetGatewayBreaker());

// Short delays keep the suite fast. The production values are pinned below.
const FAST = { firstDelayMs: 40, maxDelayMs: 80, deadlineMs: 5_000 };

const IDENTITY = {
  Authorization: "Bearer test-internal-token",
  "X-User-Email": "alice@fracktal.in",
  "X-User-Role": "employee",
};

// The first `fetch` in a process loads undici and can take 150 ms or more.
// Tests below time a 150 ms caller timeout, so pay that cost here first.
beforeAll(async () => {
  await fetch(`http://127.0.0.1:${await freePort()}/`).catch(() => {});
});

const closers: Array<() => Promise<void>> = [];
afterEach(async () => {
  while (closers.length) await closers.pop()!();
});

/** A port with nothing listening on it, so a connect is refused. */
async function freePort(): Promise<number> {
  const probe = net.createServer();
  await new Promise<void>((r) => probe.listen(0, "127.0.0.1", r));
  const { port } = probe.address() as net.AddressInfo;
  await new Promise<void>((r) => probe.close(() => r()));
  return port;
}

interface Seen {
  method: string;
  headers: http.IncomingHttpHeaders;
  body: string;
}

/** A gateway that starts to listen on `port` after `afterMs`. */
function gatewayLater(
  port: number,
  afterMs: number,
  handler: (req: http.IncomingMessage, body: string, res: http.ServerResponse) => void
): Seen[] {
  const seen: Seen[] = [];
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      seen.push({ method: req.method ?? "", headers: req.headers, body });
      handler(req, body, res);
    });
  });
  const timer = setTimeout(() => server.listen(port, "127.0.0.1"), afterMs);
  closers.push(async () => {
    clearTimeout(timer);
    server.closeAllConnections();
    await new Promise<void>((r) => server.close(() => r()));
  });
  return seen;
}

/** A raw TCP server, for failures that a well-formed HTTP server cannot make. */
async function rawServer(onSocket: (sock: net.Socket) => void): Promise<{ port: number; connections: () => number }> {
  let count = 0;
  const sockets = new Set<net.Socket>();
  const server = net.createServer((sock) => {
    count += 1;
    sockets.add(sock);
    sock.on("error", () => {});
    onSocket(sock);
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  closers.push(async () => {
    for (const s of sockets) s.destroy();
    await new Promise<void>((r) => server.close(() => r()));
  });
  return { port: (server.address() as net.AddressInfo).port, connections: () => count };
}

function recorder() {
  const lines: string[] = [];
  return { lines, log: (l: string) => lines.push(l) };
}

describe("gatewayFetch — a refused connection is tried again", () => {
  it("a GET retries through a refusal and succeeds, with the same identity", async () => {
    const port = await freePort();
    const seen = gatewayLater(port, 250, (_req, _body, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });
    const { lines, log } = recorder();

    const res = await gatewayFetch(
      `http://127.0.0.1:${port}/auth/me?who=x`,
      { headers: IDENTITY },
      { ...FAST, log }
    );

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
    // R5: the attempt that got through carried the identity the caller gave.
    expect(seen).toHaveLength(1);
    expect(seen[0].headers.authorization).toBe(IDENTITY.Authorization);
    expect(seen[0].headers["x-user-email"]).toBe(IDENTITY["X-User-Email"]);
    expect(seen[0].headers["x-user-role"]).toBe(IDENTITY["X-User-Role"]);
    // One line when it first failed, one when it got through. The path only,
    // never the query string.
    expect(lines).toHaveLength(2);
    expect(lines[0]).toMatch(/^\[gateway\] retry: GET \/auth\/me failed with ECONNREFUSED/);
    expect(lines[1]).toMatch(/^\[gateway\] recovered: GET \/auth\/me after \d+ attempts/);
    expect(lines.join("\n")).not.toContain("who=x");
  });

  it("a POST that opts in retries on ECONNREFUSED, and the body arrives intact", async () => {
    const port = await freePort();
    const seen = gatewayLater(port, 250, (_req, body, res) => {
      res.writeHead(201, { "content-type": "application/json" });
      res.end(body);
    });
    const payload = JSON.stringify({ title: "Café ₹500 — नमस्ते", n: 1 });

    const res = await gatewayFetch(
      `http://127.0.0.1:${port}/projects/tasks`,
      {
        method: "POST",
        headers: { ...IDENTITY, "Content-Type": "application/json" },
        body: payload,
      },
      { ...FAST, retry: true, log: () => {} }
    );

    expect(res.status).toBe(201);
    expect(await res.text()).toBe(payload);
    // A refused connect never reached the gateway, so exactly one write landed.
    expect(seen).toHaveLength(1);
    expect(seen[0].method).toBe("POST");
    expect(seen[0].body).toBe(payload);
    expect(seen[0].headers["x-user-email"]).toBe(IDENTITY["X-User-Email"]);
  });
});

describe("gatewayFetch — what is never retried", () => {
  it.each(["POST", "PUT", "PATCH", "DELETE"])(
    "a %s is NOT retried unless it opts in",
    async (method) => {
      // Writes to one record can queue up during a restart and then land in
      // any order. A draft auto-save would make duplicate provider drafts.
      let calls = 0;
      const refused = (async () => {
        calls += 1;
        throw Object.assign(new TypeError("fetch failed"), {
          cause: Object.assign(new Error("refused"), { code: "ECONNREFUSED" }),
        });
      }) as unknown as typeof fetch;
      await expect(
        gatewayFetch("http://127.0.0.1:1/email/drafts", { method, body: "{}" }, {
          ...FAST,
          fetchImpl: refused,
          log: () => {},
        })
      ).rejects.toThrow("fetch failed");
      expect(calls).toBe(1);
    }
  );

  it("a POST after a partial response is NOT retried, even when it opts in", async () => {
    // The gateway read the request and began to answer, then the socket died.
    // It may have done the write, so a replay could do it twice.
    const gw = await rawServer((sock) => {
      sock.once("data", () => {
        sock.write("HTTP/1.1 200 OK\r\nContent-Ty");
        sock.destroy();
      });
    });

    await expect(
      gatewayFetch(
        `http://127.0.0.1:${gw.port}/projects/tasks`,
        { method: "POST", headers: IDENTITY, body: '{"a":1}' },
        { ...FAST, retry: true, log: () => {} }
      )
    ).rejects.toSatisfy((e: unknown) => connectFailureCode(e) === "UND_ERR_SOCKET");
    expect(gw.connections()).toBe(1);
  });

  it("the same failure on a GET IS retried, because a read is safe to replay", async () => {
    // The contrast case. Without it, the test above would also pass for a
    // function that never retries anything.
    const gw = await rawServer((sock) => {
      sock.once("data", () => {
        sock.write("HTTP/1.1 200 OK\r\nContent-Ty");
        sock.destroy();
      });
    });

    await expect(
      gatewayFetch(`http://127.0.0.1:${gw.port}/auth/me`, { headers: IDENTITY }, {
        ...FAST,
        deadlineMs: 300,
        log: () => {},
      })
    ).rejects.toThrow();
    expect(gw.connections()).toBeGreaterThan(1);
    // A gateway that accepts and then drops the socket is UP, so a give-up
    // on that error must not open the breaker (rule 9 opens on refusals only).
    expect(breakerOpen(`http://127.0.0.1:${gw.port}`)).toBe(false);
  });

  it("a 503 response is NOT retried", async () => {
    const port = await freePort();
    const seen = gatewayLater(port, 0, (_req, _body, res) => {
      res.writeHead(503, { "content-type": "application/json" });
      res.end('{"detail":"warming up"}');
    });
    await new Promise((r) => setTimeout(r, 50));

    const res = await gatewayFetch(`http://127.0.0.1:${port}/auth/me`, { headers: IDENTITY }, FAST);

    expect(res.status).toBe(503);
    expect(await res.json()).toEqual({ detail: "warming up" });
    expect(seen).toHaveLength(1);
  });

  it("a stream is not retried after the first byte", async () => {
    // Headers and one event arrive, then the gateway dies mid-stream. The
    // caller must see the first event at once and then the break. It must
    // not see a second request, which would replay the chat turn.
    const port = await freePort();
    const seen = gatewayLater(port, 0, (_req, _body, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" });
      res.write("data: 1\n\n");
      setTimeout(() => res.socket?.destroy(), 100);
    });
    await new Promise((r) => setTimeout(r, 50));

    const res = await gatewayFetch(
      `http://127.0.0.1:${port}/chat/sessions/s1/messages`,
      { method: "GET", headers: IDENTITY },
      { ...FAST, log: () => {} }
    );
    const reader = res.body!.getReader();
    const first = await reader.read();
    expect(new TextDecoder().decode(first.value)).toBe("data: 1\n\n");
    await expect(
      (async () => {
        for (;;) {
          const { done } = await reader.read();
          if (done) return;
        }
      })()
    ).rejects.toThrow();
    expect(seen).toHaveLength(1);
  });

  it("a body that cannot be replayed is NOT retried", async () => {
    let calls = 0;
    const refused = async () => {
      calls += 1;
      throw Object.assign(new TypeError("fetch failed"), {
        cause: Object.assign(new Error("connect ECONNREFUSED"), { code: "ECONNREFUSED" }),
      });
    };
    const stream = new ReadableStream({
      start(c) {
        c.enqueue(new TextEncoder().encode("once"));
        c.close();
      },
    });

    await expect(
      gatewayFetch(
        "http://127.0.0.1:1/upload",
        { method: "POST", body: stream, headers: IDENTITY },
        { ...FAST, retry: true, fetchImpl: refused as unknown as typeof fetch, log: () => {} }
      )
    ).rejects.toThrow("fetch failed");
    expect(calls).toBe(1);
  });
});

describe("gatewayFetch — the window is bounded", () => {
  it("gives up at the deadline and rethrows the refusal", async () => {
    const port = await freePort();
    const { lines, log } = recorder();
    const t0 = Date.now();

    const err = await gatewayFetch(`http://127.0.0.1:${port}/auth/me`, { headers: IDENTITY }, {
      firstDelayMs: 40,
      maxDelayMs: 80,
      deadlineMs: 400,
      log,
    }).catch((e: unknown) => e);

    const elapsed = Date.now() - t0;
    expect(connectFailureCode(err)).toBe("ECONNREFUSED");
    expect(elapsed).toBeLessThan(400 + 300);
    expect(lines[0]).toMatch(/^\[gateway\] retry:/);
    // The give-up line, then the breaker opens (rule 9).
    expect(lines.at(-2)).toMatch(/^\[gateway\] gave up: GET \/auth\/me after \d+ attempts/);
    expect(lines.at(-1)).toMatch(/^\[gateway\] down:/);
  });

  it("a caller timeout does not cut the restart window short", async () => {
    // Most routes pass `AbortSignal.timeout(5_000)` or so, and the gateway is
    // cold for 10 s or more. The timeout is a budget for the ANSWER, so it
    // must not end the retry while the gateway is still down.
    const port = await freePort();
    const seen = gatewayLater(port, 400, (_req, _body, res) => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end('{"ok":true}');
    });
    const { lines, log } = recorder();

    const res = await gatewayFetch(
      `http://127.0.0.1:${port}/chat/active-sessions`,
      { headers: IDENTITY, signal: AbortSignal.timeout(150) },
      { ...FAST, log }
    );

    expect(res.status).toBe(200);
    expect(seen).toHaveLength(1);
    expect(seen[0].headers["x-user-email"]).toBe(IDENTITY["X-User-Email"]);
    expect(lines.at(-1)).toMatch(/^\[gateway\] recovered:/);
  });

  it.each([
    ["GET", 200, 3],
    ["POST", null, 2],
  ] as const)(
    "a caller timeout during an in-flight try: %s",
    async (method, status, expectedCalls) => {
      // Try 1 is refused. The caller's timeout fires while try 2 is in flight.
      // A read may go again. A write may not, because try 2 may have connected.
      let calls = 0;
      const fake = (async (_input: RequestInfo | URL, init?: RequestInit) => {
        calls += 1;
        if (calls === 1) {
          throw Object.assign(new TypeError("fetch failed"), {
            cause: Object.assign(new Error("refused"), { code: "ECONNREFUSED" }),
          });
        }
        if (calls === 2) {
          const signal = init!.signal!;
          await new Promise((_r, reject) => signal.addEventListener("abort", () => reject(signal.reason)));
        }
        return new Response("ok", { status: 200 });
      }) as typeof fetch;

      const run = gatewayFetch(
        "http://127.0.0.1:1/auth/me",
        { method, body: method === "POST" ? "{}" : undefined, signal: AbortSignal.timeout(100) },
        { ...FAST, retry: true, fetchImpl: fake, log: () => {} }
      );

      if (status === null) {
        await expect(run).rejects.toMatchObject({ name: "TimeoutError" });
      } else {
        expect((await run).status).toBe(status);
      }
      expect(calls).toBe(expectedCalls);
    }
  );

  it("a deferred timeout still bounds a slow answer", async () => {
    // Once the gateway accepts, the try gets the caller's budget again (at
    // least 1 s). A gateway that then answers too slowly still times out.
    const port = await freePort();
    gatewayLater(port, 300, (_req, _body, res) => {
      setTimeout(() => {
        res.writeHead(200);
        res.end("late");
      }, 2_500);
    });
    const { lines, log } = recorder();
    const t0 = Date.now();

    const err = await gatewayFetch(
      `http://127.0.0.1:${port}/auth/me`,
      { headers: IDENTITY, signal: AbortSignal.timeout(150) },
      { ...FAST, log }
    ).catch((e: unknown) => e);

    expect((err as Error).name).toBe("TimeoutError");
    expect(Date.now() - t0).toBeLessThan(2_300);
    expect(lines.at(-1)).toMatch(/^\[gateway\] gave up: GET \/auth\/me .*\(TimeoutError\)$/);
  });

  it("a client abort ends the request at once, and says so in the log", async () => {
    const port = await freePort();
    const { lines, log } = recorder();
    const ctrl = new AbortController();
    setTimeout(() => ctrl.abort(), 150);
    const t0 = Date.now();

    await expect(
      gatewayFetch(`http://127.0.0.1:${port}/auth/me`, { signal: ctrl.signal }, {
        ...FAST,
        deadlineMs: 20_000,
        log,
      })
    ).rejects.toThrow();

    expect(Date.now() - t0).toBeLessThan(1_000);
    expect(lines.at(-1)).toMatch(/^\[gateway\] gave up: GET \/auth\/me .*\(aborted\)$/);
  });

  it("keeps the production window below Caddy's hold", () => {
    // `lb_try_duration` is Caddy's CONNECT hold: how long Caddy keeps trying
    // to dial the workbench while the workbench restarts. It is not a limit
    // on the answer time, because Caddy sets no response timeout. This pin
    // keeps our connect hold to the gateway no longer than Caddy's connect
    // hold to us, so a gateway restart never makes a member wait longer than
    // a workbench restart does. The same deploy restarts both.
    const caddyfile = readFileSync(
      fileURLToPath(new URL("../../../../deploy/hostinger/caddy/Caddyfile", import.meta.url)),
      "utf8"
    );
    const holds = [...caddyfile.matchAll(/lb_try_duration\s+(\d+)s/g)].map((m) => Number(m[1]) * 1000);
    expect(holds.length).toBeGreaterThan(0);
    expect(GATEWAY_RETRY.deadlineMs).toBeLessThan(Math.min(...holds));
    expect(GATEWAY_RETRY.firstDelayMs).toBeLessThanOrEqual(GATEWAY_RETRY.maxDelayMs);
  });
});

describe("turning the retry off", () => {
  const refusedFake = () => {
    let calls = 0;
    const fake = (async () => {
      calls += 1;
      throw Object.assign(new TypeError("fetch failed"), {
        cause: Object.assign(new Error("refused"), { code: "ECONNREFUSED" }),
      });
    }) as unknown as typeof fetch;
    return { fake, calls: () => calls };
  };

  it("`{ retry: false }` sends one try only", async () => {
    const f = refusedFake();
    await expect(
      gatewayFetch("http://127.0.0.1:1/chat/sessions/s/messages", { method: "POST", body: "[]" }, {
        ...FAST,
        retry: false,
        fetchImpl: f.fake,
        log: () => {},
      })
    ).rejects.toThrow("fetch failed");
    expect(f.calls()).toBe(1);
  });

  it("GATEWAY_RETRY_DEADLINE_MS can shorten the window or turn it off, never lengthen it", () => {
    expect(retryDeadlineMs(undefined)).toBe(GATEWAY_RETRY.deadlineMs);
    expect(retryDeadlineMs("")).toBe(GATEWAY_RETRY.deadlineMs);
    expect(retryDeadlineMs("junk")).toBe(GATEWAY_RETRY.deadlineMs);
    expect(retryDeadlineMs("-5")).toBe(GATEWAY_RETRY.deadlineMs);
    expect(retryDeadlineMs("0")).toBe(0);
    expect(retryDeadlineMs("4000")).toBe(4_000);
    // Capped, so the env cannot push the window past Caddy's hold.
    expect(retryDeadlineMs("99999")).toBe(GATEWAY_RETRY.deadlineMs);
  });

  it("the browser suite, which runs with no gateway, turns the retry off", () => {
    // Without this every server call in e2e/ waits the full window, and a
    // spec that reloads a page times out (measured on PR #520).
    const config = readFileSync(
      fileURLToPath(new URL("../../playwright.config.ts", import.meta.url)),
      "utf8"
    );
    expect(config).toMatch(/GATEWAY_RETRY_DEADLINE_MS:\s*"0"/);
  });
});

describe("the breaker — a gateway that stays down", () => {
  const refusingFake = () => {
    let calls = 0;
    const fake = (async () => {
      calls += 1;
      throw Object.assign(new TypeError("fetch failed"), {
        cause: Object.assign(new Error("connect ECONNREFUSED"), { code: "ECONNREFUSED" }),
      });
    }) as unknown as typeof fetch;
    return { fake, calls: () => calls };
  };
  const URL_A = "http://127.0.0.1:1/models";
  const SHORT = { firstDelayMs: 20, maxDelayMs: 40, deadlineMs: 200 };

  it("after a give-up, the next read fails fast with the original error", async () => {
    const f = refusingFake();
    const { lines, log } = recorder();
    await expect(gatewayFetch(URL_A, {}, { ...SHORT, fetchImpl: f.fake, log })).rejects.toThrow();
    const firstCalls = f.calls();
    expect(firstCalls).toBeGreaterThan(1);
    expect(breakerOpen("http://127.0.0.1:1")).toBe(true);
    expect(lines.at(-1)).toMatch(/^\[gateway\] down: http:\/\/127\.0\.0\.1:1 refused/);

    const t0 = Date.now();
    const err = await gatewayFetch(URL_A, {}, { ...SHORT, fetchImpl: f.fake, log }).catch((e: unknown) => e);
    expect(Date.now() - t0).toBeLessThan(50);
    expect(connectFailureCode(err)).toBe("ECONNREFUSED");
    expect(f.calls()).toBe(firstCalls + 1);
  });

  it("closes after the cool-down", async () => {
    const f = refusingFake();
    await gatewayFetch(URL_A, {}, { ...SHORT, breakerMs: 100, fetchImpl: f.fake, log: () => {} }).catch(() => {});
    expect(breakerOpen("http://127.0.0.1:1")).toBe(true);
    await new Promise((r) => setTimeout(r, 150));
    expect(breakerOpen("http://127.0.0.1:1")).toBe(false);

    // The window is back: the next read retries again.
    const before = f.calls();
    await gatewayFetch(URL_A, {}, { ...SHORT, breakerMs: 100, fetchImpl: f.fake, log: () => {} }).catch(() => {});
    expect(f.calls() - before).toBeGreaterThan(1);
  });

  it("closes on the first response", async () => {
    const f = refusingFake();
    await gatewayFetch(URL_A, {}, { ...SHORT, fetchImpl: f.fake, log: () => {} }).catch(() => {});
    expect(breakerOpen("http://127.0.0.1:1")).toBe(true);

    const ok = (async () => new Response("up", { status: 200 })) as unknown as typeof fetch;
    expect((await gatewayFetch(URL_A, {}, { ...SHORT, fetchImpl: ok })).status).toBe(200);
    expect(breakerOpen("http://127.0.0.1:1")).toBe(false);
  });

  it("a restart that recovers inside the window never opens the breaker", async () => {
    const port = await freePort();
    gatewayLater(port, 250, (_req, _body, res) => {
      res.writeHead(200);
      res.end("ok");
    });
    const { lines, log } = recorder();
    const res = await gatewayFetch(`http://127.0.0.1:${port}/auth/me`, {}, { ...FAST, log });
    expect(res.status).toBe(200);
    expect(breakerOpen(`http://127.0.0.1:${port}`)).toBe(false);
    expect(lines.some((l) => l.startsWith("[gateway] down"))).toBe(false);
  });

  it("a route with sequential reads waits about one window, not N", async () => {
    // `/api/models/all` makes several reads in a row. Without the breaker,
    // a down gateway would cost one full window per read.
    const f = refusingFake();
    const t0 = Date.now();
    for (let i = 0; i < 4; i += 1) {
      await gatewayFetch(`http://127.0.0.1:1/r${i}`, {}, { ...SHORT, fetchImpl: f.fake, log: () => {} }).catch(() => {});
    }
    const elapsed = Date.now() - t0;
    expect(elapsed).toBeLessThan(SHORT.deadlineMs * 2);
  });

  it("does not open for a different origin", async () => {
    const f = refusingFake();
    await gatewayFetch(URL_A, {}, { ...SHORT, fetchImpl: f.fake, log: () => {} }).catch(() => {});
    expect(breakerOpen("http://127.0.0.1:1")).toBe(true);
    expect(breakerOpen("http://127.0.0.1:2")).toBe(false);
  });
});

describe("the log carries no address or id", () => {
  it("redacts an email and an id in the path", () => {
    expect(redactPath("/memory/nithin%40hathilabs.com")).toBe("/memory/<email>");
    expect(redactPath("/memory/alice@fracktal.in/search")).toBe("/memory/<email>/search");
    expect(redactPath("/chat/sessions/3f2a9c1e-8b7d-4e6f-a1b2-c3d4e5f60718/messages")).toBe(
      "/chat/sessions/<id>/messages"
    );
    expect(redactPath("/projects/tasks/123456")).toBe("/projects/tasks/<id>");
    expect(redactPath("/agent/run/0123456789abcdef0123/cancel")).toBe("/agent/run/<id>/cancel");
    // Route words stay, so the log still says which door failed.
    expect(redactPath("/chat/active-sessions")).toBe("/chat/active-sessions");
    expect(redactPath("/projects/notifications")).toBe("/projects/notifications");
  });

  it("writes no email into a retry line", async () => {
    const port = await freePort();
    gatewayLater(port, 150, (_req, _body, res) => {
      res.writeHead(200);
      res.end("[]");
    });
    const { lines, log } = recorder();
    await gatewayFetch(`http://127.0.0.1:${port}/memory/alice%40fracktal.in?q=bob@x.io`, {}, { ...FAST, log });
    expect(lines.length).toBeGreaterThan(0);
    const all = lines.join("\n");
    expect(all).toContain("/memory/<email>");
    expect(all).not.toMatch(/alice|fracktal|bob|%40|@/);
  });
});

describe("the classifiers", () => {
  it("reads no connection code from an abort or a plain error", () => {
    expect(connectFailureCode(new Error("boom"))).toBeNull();
    expect(connectFailureCode(new DOMException("aborted", "AbortError"))).toBeNull();
    expect(connectFailureCode(new TypeError("fetch failed"))).toBeNull();
  });

  it("counts an AggregateError as refused only when every address refused", () => {
    const refused = () => Object.assign(new Error("r"), { code: "ECONNREFUSED" });
    const wrap = (errors: Error[]) =>
      Object.assign(new TypeError("fetch failed"), { cause: new AggregateError(errors) });
    expect(connectFailureCode(wrap([refused(), refused()]))).toBe("ECONNREFUSED");
    expect(
      connectFailureCode(wrap([refused(), Object.assign(new Error("t"), { code: "ETIMEDOUT" })]))
    ).toBeNull();
  });

  it("treats a stream as the one body it cannot send twice", () => {
    expect(isReplayableBody(undefined)).toBe(true);
    expect(isReplayableBody("x")).toBe(true);
    expect(isReplayableBody(Buffer.from("x"))).toBe(true);
    expect(isReplayableBody(new URLSearchParams("a=1"))).toBe(true);
    expect(isReplayableBody(new FormData())).toBe(true);
    expect(isReplayableBody(new Blob(["x"]))).toBe(true);
    expect(isReplayableBody(new ReadableStream())).toBe(false);
  });
});
