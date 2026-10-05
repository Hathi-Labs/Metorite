/**
 * "Metorite is updating", instead of a 502. Spec: `navigation_shell.md` §7.3.
 *
 * What these tests hold:
 *
 * 1. One failed route is NOT an update. The monitor asks `/api/health`
 *    first, and a healthy answer ends it with no message.
 * 2. A failed probe says "updating", or "offline" when the browser has no
 *    network. It polls until the gateway answers, then says "back".
 * 3. A new build after the outage is reported, so the member can reload.
 * 4. The fetch observer watches only the app's own `/api/*` calls, never the
 *    probe, never an abort, and never changes a response.
 * 5. The notice and the error pages are mounted where they must be.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it, vi } from "vitest";

import { claimAutoReload, isChunkLoadError, RELOAD_GUARD_MS } from "./chunkReload";
import {
  type ChangeInfo,
  type Health,
  type ProbeResult,
  FAST_FOR_MS,
  POLL_FAST_MS,
  POLL_SLOW_MS,
  RECOVERED_MS,
  createMonitor,
  installFetchObserver,
  isOwnApi,
  probeHealth,
} from "./serviceHealth";

const SRC = fileURLToPath(new URL("../..", import.meta.url));

/** A monitor with a hand-driven clock, timer queue and probe script. */
function rig(probes: ProbeResult[], online = true) {
  let now = 0;
  const timers: Array<{ at: number; fn: () => void; live: boolean }> = [];
  const seen: Array<[Health, ChangeInfo]> = [];
  const script = [...probes];
  const net = { online };
  const monitor = createMonitor({
    probe: async () => script.shift() ?? { up: true, build: "b1" },
    online: () => net.online,
    now: () => now,
    setTimer: (fn, ms) => {
      const t = { at: now + ms, fn, live: true };
      timers.push(t);
      return () => {
        t.live = false;
      };
    },
    onChange: (s, info) => seen.push([s, info]),
  });
  const flush = () => new Promise((r) => setTimeout(r, 0));
  async function advance(ms: number) {
    const until = now + ms;
    for (;;) {
      const next = timers
        .filter((t) => t.live && t.at <= until)
        .sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      next.live = false;
      now = next.at;
      next.fn();
      await flush();
      await flush();
    }
    now = until;
  }
  const states = () => seen.map(([s]) => s);
  return { monitor, advance, flush, seen, states, net, timers: () => timers.filter((t) => t.live) };
}

describe("the monitor", () => {
  it("says nothing when one route fails and the product is healthy", async () => {
    const r = rig([{ up: true, build: "b1" }, { up: true, build: "b1" }]);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    expect(r.states()).toEqual(["checking", "ok"]);
    expect(r.monitor.state()).toBe("ok");
  });

  it("says updating, polls, and then says back", async () => {
    const r = rig([
      { up: true, build: "b1" }, // start
      { up: false, build: null }, // the suspicion
      { up: false, build: null }, // poll 1
      { up: true, build: "b1" }, // poll 2
    ]);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    expect(r.monitor.state()).toBe("updating");
    await r.advance(POLL_FAST_MS);
    expect(r.monitor.state()).toBe("updating");
    await r.advance(POLL_FAST_MS);
    expect(r.monitor.state()).toBe("recovered");
    expect(r.seen.at(-1)?.[1].buildChanged).toBe(false);
    await r.advance(RECOVERED_MS);
    expect(r.monitor.state()).toBe("ok");
  });

  it("reports a new build after the outage", async () => {
    const r = rig([
      { up: true, build: "old" },
      { up: false, build: null },
      { up: true, build: "new" },
    ]);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    await r.advance(POLL_FAST_MS);
    expect(r.seen.at(-1)).toEqual(["recovered", { buildChanged: true }]);
  });

  it("does not claim a new build when it never knew the old one", async () => {
    const r = rig([
      { up: false, build: null }, // start: down already
      { up: false, build: null },
      { up: true, build: "new" },
    ]);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    await r.advance(POLL_FAST_MS);
    expect(r.seen.at(-1)).toEqual(["recovered", { buildChanged: false }]);
  });

  it("says offline, not updating, when the browser has no network", async () => {
    const r = rig([{ up: true, build: "b1" }, { up: false, build: null }], false);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    expect(r.monitor.state()).toBe("offline");
  });

  it("goes offline on the browser's event, and probes when it is back", async () => {
    const r = rig([{ up: true, build: "b1" }, { up: true, build: "b1" }]);
    await r.monitor.start();
    r.net.online = false;
    r.monitor.setOnline(false);
    expect(r.monitor.state()).toBe("offline");
    r.net.online = true;
    r.monitor.setOnline(true);
    await r.flush();
    expect(r.monitor.state()).toBe("recovered");
  });

  it("ignores a second suspicion while one is being checked or reported", async () => {
    const probe = vi.fn(async () => ({ up: false, build: null }) as ProbeResult);
    const monitor = createMonitor({
      probe,
      online: () => true,
      now: () => 0,
      setTimer: () => () => {},
      onChange: () => {},
    });
    monitor.suspect();
    monitor.suspect();
    monitor.suspect();
    await new Promise((r) => setTimeout(r, 0));
    expect(probe).toHaveBeenCalledTimes(1);
    monitor.suspect();
    expect(probe).toHaveBeenCalledTimes(1);
  });

  it("polls fast first and slower after a minute", async () => {
    const r = rig([{ up: true, build: "b1" }, ...Array(40).fill({ up: false, build: null })]);
    await r.monitor.start();
    r.monitor.suspect();
    await r.flush();
    expect(r.timers()[0].at).toBe(POLL_FAST_MS);
    await r.advance(FAST_FOR_MS + POLL_FAST_MS);
    const next = r.timers()[0];
    expect(next.at - FAST_FOR_MS - POLL_FAST_MS).toBeLessThanOrEqual(POLL_SLOW_MS);
    expect(next.at - FAST_FOR_MS - POLL_FAST_MS).toBeGreaterThan(POLL_FAST_MS);
  });

  it("treats a probe that throws as down", async () => {
    const monitor = createMonitor({
      probe: async () => {
        throw new TypeError("Failed to fetch");
      },
      online: () => true,
      now: () => 0,
      setTimer: () => () => {},
      onChange: () => {},
    });
    monitor.suspect();
    await new Promise((r) => setTimeout(r, 0));
    expect(monitor.state()).toBe("updating");
  });
});

describe("which requests it watches", () => {
  const origin = "https://app.metorite.com";

  it("watches the app's own API, and nothing else", () => {
    expect(isOwnApi("/api/projects/tree", origin)).toBe(true);
    expect(isOwnApi(`${origin}/api/auth/me`, origin)).toBe(true);
    expect(isOwnApi(new URL("/api/email/accounts", origin), origin)).toBe(true);
    expect(isOwnApi("/api/health", origin), "never the probe").toBe(false);
    expect(isOwnApi("/projects", origin)).toBe(false);
    expect(isOwnApi("https://example.com/api/x", origin)).toBe(false);
    expect(isOwnApi("/_next/static/chunks/a.js", origin)).toBe(false);
  });
});

describe("the fetch observer", () => {
  function fakeWindow(answer: () => Promise<Response>) {
    const calls: unknown[] = [];
    const win = {
      location: { origin: "https://app.metorite.com" },
      fetch: (async (input: RequestInfo | URL) => {
        calls.push(input);
        return answer();
      }) as typeof fetch,
    };
    return { win, calls };
  }

  it("reports a 502, 503 or 504 and passes the response through unchanged", async () => {
    for (const status of [502, 503, 504]) {
      const res = new Response("x", { status });
      const { win } = fakeWindow(async () => res);
      const onSuspect = vi.fn();
      const restore = installFetchObserver(win, onSuspect);
      expect(await win.fetch("/api/projects/tree")).toBe(res);
      expect(onSuspect).toHaveBeenCalledTimes(1);
      restore();
    }
  });

  it("does not report a 500, a 401 or a 404", async () => {
    for (const status of [500, 401, 404, 200]) {
      const { win } = fakeWindow(async () => new Response("x", { status }));
      const onSuspect = vi.fn();
      installFetchObserver(win, onSuspect);
      await win.fetch("/api/projects/tree");
      expect(onSuspect, String(status)).not.toHaveBeenCalled();
    }
  });

  it("reports a request that got no answer, and rethrows it", async () => {
    const { win } = fakeWindow(async () => {
      throw new TypeError("Failed to fetch");
    });
    const onSuspect = vi.fn();
    installFetchObserver(win, onSuspect);
    await expect(win.fetch("/api/x")).rejects.toThrow("Failed to fetch");
    expect(onSuspect).toHaveBeenCalledTimes(1);
  });

  it("never reports an abort, the probe, or another site", async () => {
    const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
    const { win } = fakeWindow(async () => {
      throw abort;
    });
    const onSuspect = vi.fn();
    installFetchObserver(win, onSuspect);
    await expect(win.fetch("/api/x")).rejects.toBe(abort);
    const down = fakeWindow(async () => new Response("x", { status: 503 }));
    installFetchObserver(down.win, onSuspect);
    await down.win.fetch("/api/health");
    await down.win.fetch("https://example.com/api/y");
    expect(onSuspect).not.toHaveBeenCalled();
  });

  it("installs once, and restores the original", async () => {
    const { win } = fakeWindow(async () => new Response("x", { status: 503 }));
    const original = win.fetch;
    const onSuspect = vi.fn();
    const restore = installFetchObserver(win, onSuspect);
    installFetchObserver(win, onSuspect); // a second install is a no-op
    await win.fetch("/api/x");
    expect(onSuspect).toHaveBeenCalledTimes(1);
    restore();
    expect(win.fetch).toBe(original);
  });
});

describe("the probe", () => {
  const answer = (body: string, status = 200, type = "application/json") =>
    (async () => new Response(body, { status, headers: { "Content-Type": type } })) as typeof fetch;

  it("reads up and the build", async () => {
    expect(await probeHealth(answer('{"gateway":"up","build":"b7"}'))).toEqual({
      up: true,
      build: "b7",
    });
    expect((await probeHealth(answer('{"gateway":"down","build":"b7"}'))).up).toBe(false);
  });

  it("reads Caddy's HTML updating page as down", async () => {
    expect((await probeHealth(answer("<!doctype html>", 503, "text/html"))).up).toBe(false);
    expect((await probeHealth(answer("<!doctype html>", 200, "text/html"))).up).toBe(false);
  });
});

describe("a tab left open across a deploy", () => {
  it("recognises a failed load of an old code file", () => {
    expect(isChunkLoadError({ name: "ChunkLoadError", message: "x" })).toBe(true);
    expect(isChunkLoadError(new Error("Loading chunk 1234 failed."))).toBe(true);
    expect(isChunkLoadError(new Error("Loading CSS chunk app-layout failed"))).toBe(true);
    expect(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: x"))).toBe(true);
    expect(isChunkLoadError(new TypeError("Importing a module script failed."))).toBe(true);
    expect(isChunkLoadError(new Error("Cannot read properties of undefined"))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
  });

  it("reloads at most once a minute, so a real bug cannot loop", () => {
    const data = new Map<string, string>();
    const store = {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => void data.set(k, v),
    };
    expect(claimAutoReload(store, 1_000_000)).toBe(true);
    expect(claimAutoReload(store, 1_000_000 + RELOAD_GUARD_MS - 1)).toBe(false);
    expect(claimAutoReload(store, 1_000_000 + RELOAD_GUARD_MS + 1)).toBe(true);
  });

  it("never reloads by itself when it cannot record the reload", () => {
    expect(claimAutoReload(null, 0)).toBe(false);
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {},
    };
    expect(claimAutoReload(broken, 0)).toBe(false);
  });
});

describe("where it is mounted", () => {
  const read = (rel: string) => readFileSync(join(SRC, rel), "utf8");

  it("mounts the notice once, inside the toast provider and outside Providers", () => {
    const layout = read("app/layout.tsx");
    expect(layout.match(/<UpdateNotice \/>/g)?.length).toBe(1);
    const at = layout.indexOf("<UpdateNotice />");
    expect(layout.lastIndexOf("<ToastProvider>", at)).toBeGreaterThan(-1);
    expect(layout.indexOf("</ToastProvider>", at)).toBeGreaterThan(at);
    expect(layout.indexOf("<Providers>")).toBeGreaterThan(at);
  });

  it("replaces React's raw error screen at both levels", () => {
    for (const file of ["app/error.tsx", "app/global-error.tsx"]) {
      expect(read(file), file).toContain("<ErrorScreen");
    }
    expect(read("app/global-error.tsx")).toContain("<html");
  });
});
