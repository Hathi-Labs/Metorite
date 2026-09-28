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
import { describe, it, expect, afterEach, beforeAll } from "vitest";
import http from "node:http";
import net from "node:net";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  GATEWAY_RETRY,
  connectFailureCode,
  gatewayFetch,
  isReplayableBody,
  retryDeadlineMs,
} from "@/lib/gatewayFetch";

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

  it("a POST retries on ECONNREFUSED, and the body arrives intact", async () => {
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
      { ...FAST, log: () => {} }
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
  it("a POST after a partial response is NOT retried", async () => {
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
        { ...FAST, log: () => {} }
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
        { ...FAST, fetchImpl: refused as unknown as typeof fetch, log: () => {} }
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
    expect(lines.at(-1)).toMatch(/^\[gateway\] gave up: GET \/auth\/me after \d+ attempts/);
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
        { ...FAST, fetchImpl: fake, log: () => {} }
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
    // The browser reaches the workbench through Caddy, which holds a request
    // for `lb_try_duration`. A retry window longer than that hold would still
    // be running when Caddy gives up on the member.
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
