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
import { describe, it, expect, afterEach } from "vitest";
import http from "node:http";
import net from "node:net";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { GATEWAY_RETRY, connectFailureCode, gatewayFetch, isReplayableBody } from "@/lib/gatewayFetch";

// Short delays keep the suite fast. The production values are pinned below.
const FAST = { firstDelayMs: 40, maxDelayMs: 80, deadlineMs: 5_000 };

const IDENTITY = {
  Authorization: "Bearer test-internal-token",
  "X-User-Email": "alice@fracktal.in",
  "X-User-Role": "employee",
};

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

  it("stops when the caller's signal aborts", async () => {
    const port = await freePort();
    const t0 = Date.now();
    await expect(
      gatewayFetch(`http://127.0.0.1:${port}/auth/me`, { signal: AbortSignal.timeout(200) }, {
        ...FAST,
        deadlineMs: 20_000,
        log: () => {},
      })
    ).rejects.toThrow();
    expect(Date.now() - t0).toBeLessThan(1_500);
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
