// WS-17 EM-S2 — the pane shows text, then HTML, and the prefetch
// (`project-docs/specs/email_app_master_plan.md` §14.6.2, §14.4.2 items 4 and 5).
//
// R7 fences named here:
//   * `email-html-prefetch-bounds`: 6 rows at most in a run, 2 requests in
//     flight for ALL runs together, one id never asked twice while it is in
//     flight, an open during a prefetch makes one request, no fetch for a row
//     with `htmlRemote` false or a row the cache holds, and the 500 ms wait.
//   * `email-html-prefetch-stops`: the first 503 stops the prefetch until its
//     `Retry-After` ends, and then the same list may prefetch again. The first
//     401 stops it until the list changes. A failed row is not asked again for
//     the same list. A soft refresh clears no stop and no wait, and a remount
//     of the list keeps them.
//   * `email-html-pane`: the first paint shows the text, then the HTML goes
//     through the one render path of stored HTML. A failed fetch keeps the
//     text, with no error state. The browser proof that a `<script>` does not
//     reach the frame is `e2e/untrusted-html.spec.ts`.
//   * `email-html-flag-off`: with `EMAIL_HTML_FROM_PROVIDER` off the gateway
//     sends no `html_remote: true`, and the pane and the list behave as before.
//
// The server half (`html_remote`, the route, the 503 and the
// `email.html.provider_429` line) is `tests/unit/test_email_html_tier.py`.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { bindIdentity, clearAll, peek, put, read as readCache } from "@/lib/dataCache";
import { MessageContent, remoteBody, sanitizeEmailHtml } from "../components/MessageContent";
import { messageHtmlKey } from "./api";
import {
  PREFETCH_DEFAULT_WAIT_S,
  PREFETCH_MAX_ROWS,
  PREFETCH_PARALLEL,
  PREFETCH_STILL_MS,
  createHtmlPrefetcher,
  createPrefetchSchedule,
  defaultPrefetchDeps,
  newPrefetchState,
  openMessageHtml,
  prefetchListKey,
  prefetchTargets,
  remoteHtmlId,
  sharedHtmlPrefetcher,
  sharedPrefetchState,
  visibleRowIds,
  type PrefetchDeps,
  type PrefetchRow,
  type PrefetchState,
} from "./htmlPrefetch";

// The BFF proxy below resolves the identity before it forwards.
vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

const ROOT = join(__dirname, "..");
const read = (rel: string) => readFileSync(join(ROOT, rel), "utf-8").replace(/\r\n/g, "\n");
const codeOnly = (src: string) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

const remote = (id: string): PrefetchRow => ({ id, htmlRemote: true });
const local = (id: string): PrefetchRow => ({ id, htmlRemote: false });
const rowsOf = (...ids: string[]) => ids.map(remote);
const tick = () => new Promise((r) => setTimeout(r, 0));
const LIST = { viewAll: false, accountId: "acc1", folder: "inbox", label: null, query: "", scope: null, filters: [] };
const INBOX = prefetchListKey(LIST);
const SENT = prefetchListKey({ ...LIST, folder: "sent" });

/** An error as `gatewayFetch` in `api.ts` throws it. */
function httpError(status: number, retryAfter?: number): Error {
  const err = new Error(`Gateway error ${status}`) as Error & { status: number; retryAfter?: number };
  err.status = status;
  if (retryAfter !== undefined) err.retryAfter = retryAfter;
  return err;
}

/**
 * Effects that record each request. With `manual`, a request waits until the
 * test releases it, so the test can count what is in flight across runs.
 * Else it answers after one turn of the event loop.
 */
function harness(
  answer: (id: string) => string | null = () => "<p>x</p>",
  opts: { manual?: boolean } = {},
) {
  const kept = new Map<string, string | null>();
  const asked: string[] = [];
  const live = new Set<string>();
  let maxInFlight = 0;
  let twiceInFlight = false;
  let clock = 1_000_000;
  const waiting: Array<() => void> = [];
  const deps: PrefetchDeps = {
    fetchHtml: async (id) => {
      asked.push(id);
      if (live.has(id)) twiceInFlight = true;
      live.add(id);
      maxInFlight = Math.max(maxInFlight, live.size);
      try {
        if (opts.manual) await new Promise<void>((r) => waiting.push(r));
        else await tick();
        return answer(id);
      } finally {
        live.delete(id);
      }
    },
    held: (id) => kept.has(id),
    keep: (id, html) => kept.set(id, html),
    now: () => clock,
  };
  return {
    deps,
    kept,
    asked,
    max: () => maxInFlight,
    twice: () => twiceInFlight,
    advance: (ms: number) => {
      clock += ms;
    },
    /** Release each waiting request, and the ones they start, until none waits. */
    drain: async () => {
      for (let i = 0; i < 50 && waiting.length; i++) {
        while (waiting.length) (waiting.shift() as () => void)();
        await tick();
        await tick();
      }
    },
  };
}

/** A prefetcher with its own state, on the list INBOX. */
function prefetcher(h: ReturnType<typeof harness>, state: PrefetchState = newPrefetchState()) {
  const p = createHtmlPrefetcher(h.deps, state);
  p.listLoaded(INBOX);
  return p;
}

/** A `fetch` stub whose answers the test releases one by one. */
function deferredFetch() {
  const calls: string[] = [];
  const pending: Array<(r: Response) => void> = [];
  const fn = vi.fn((input: RequestInfo | URL) => {
    calls.push(String(input));
    return new Promise<Response>((resolve) => pending.push(resolve));
  });
  vi.stubGlobal("fetch", fn);
  const html = (id: string) =>
    new Response(JSON.stringify({ message_id: id, body_html: `<p>${id}</p>`, source: "provider" }), { status: 200 });
  return {
    calls,
    answer: async (res: Response) => {
      (pending.shift() as (r: Response) => void)(res);
      await tick();
      await tick();
    },
    html,
  };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearAll();
});

describe("email-html-prefetch-bounds", () => {
  it("asks for 6 rows at most", async () => {
    const h = harness();
    const rows = Array.from({ length: 10 }, (_, i) => remote(`m${i}`));
    const out = await prefetcher(h).run(rows);
    expect(PREFETCH_MAX_ROWS).toBe(6);
    expect(out.asked).toEqual(["m0", "m1", "m2", "m3", "m4", "m5"]);
    expect(h.asked).toHaveLength(6);
    expect([...h.kept.keys()]).toEqual(["m0", "m1", "m2", "m3", "m4", "m5"]);
  });

  it("has 2 requests in flight at most", async () => {
    const h = harness();
    await prefetcher(h).run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(PREFETCH_PARALLEL).toBe(2);
    expect(h.max()).toBe(2);
  });

  it("overlapping runs share the 2 slots, and never ask one id twice in flight", async () => {
    // A scroll, the soft refresh of 20 s and a mark-read each start a run
    // while the provider still answers the run before.
    const h = harness(undefined, { manual: true });
    const p = prefetcher(h);
    const rows = rowsOf("a", "b", "c", "d", "e", "f");
    const r1 = p.run(rows);
    await tick();
    p.listLoaded(INBOX);
    const r2 = p.run(rows);
    await tick();
    p.listLoaded(INBOX);
    const r3 = p.run(rows);
    await tick();
    expect(h.asked).toEqual(["a", "b"]);
    await h.drain();
    await Promise.all([r1, r2, r3]);
    expect(h.max()).toBe(2);
    expect(h.twice()).toBe(false);
    expect(h.asked).toEqual(["a", "b", "c", "d", "e", "f"]);
  });

  it("a newer run replaces the rows an older run still waits to ask for", async () => {
    const h = harness(undefined, { manual: true });
    const p = prefetcher(h);
    const r1 = p.run(rowsOf("a", "b", "c", "d", "e", "f"));
    await tick();
    const r2 = p.run(rowsOf("x", "y"));
    await tick();
    await h.drain();
    const [o1, o2] = await Promise.all([r1, r2]);
    // The older run asked for a and b, and stopped. c to f never went.
    expect(o1.asked).toEqual(["a", "b"]);
    expect(o2.asked).toEqual(["x", "y"]);
    expect(h.asked).toEqual(["a", "b", "x", "y"]);
  });

  it("an open during a prefetch makes one request", async () => {
    const f = deferredFetch();
    const state = newPrefetchState();
    const p = createHtmlPrefetcher(defaultPrefetchDeps(), state);
    p.listLoaded(INBOX);
    const run = p.run(rowsOf("m1"));
    // The pane opens the row while the prefetch waits on the provider. It
    // reads through dataCache, as `MessageContent` does.
    const open = readCache(messageHtmlKey("m1"), () => openMessageHtml("m1", state));
    expect(f.calls).toEqual(["/api/email/messages/m1/html?prefetch=1"]);
    await f.answer(f.html("m1"));
    expect(await open).toBe("<p>m1</p>");
    await run;
    expect(f.calls).toHaveLength(1);
    expect(peek(messageHtmlKey("m1"))?.data).toBe("<p>m1</p>");
  });

  it("an open that joined a refused prefetch asks once on its own, because an open is never refused", async () => {
    const f = deferredFetch();
    const state = newPrefetchState();
    const p = createHtmlPrefetcher(defaultPrefetchDeps(), state);
    p.listLoaded(INBOX);
    const run = p.run(rowsOf("m1"));
    const open = openMessageHtml("m1", state);
    await f.answer(new Response(JSON.stringify({ detail: "busy" }), { status: 503, headers: { "Retry-After": "30" } }));
    expect(f.calls).toEqual(["/api/email/messages/m1/html?prefetch=1", "/api/email/messages/m1/html"]);
    await f.answer(f.html("m1"));
    expect(await open).toBe("<p>m1</p>");
    expect((await run).stopped).toBe("busy");
  });

  it("the prefetch skips a row that the pane is opening", async () => {
    const f = deferredFetch();
    const state = newPrefetchState();
    const open = openMessageHtml("m2", state);
    const p = createHtmlPrefetcher(defaultPrefetchDeps(), state);
    p.listLoaded(INBOX);
    const run = p.run(rowsOf("m2"));
    expect((await run).asked).toEqual([]);
    expect(f.calls).toHaveLength(1);
    await f.answer(f.html("m2"));
    expect(await open).toBe("<p>m2</p>");
  });

  it("never asks for a row with htmlRemote false", async () => {
    const h = harness();
    const out = await prefetcher(h).run([local("a"), remote("b"), { id: "c" }, local("d"), remote("e")]);
    expect(out.asked).toEqual(["b", "e"]);
    expect(h.asked).toEqual(["b", "e"]);
  });

  it("skips a row that the cache holds, and counts only the rows it asks for", () => {
    const held = new Set(["m0", "m2"]);
    const rows = Array.from({ length: 9 }, (_, i) => remote(`m${i}`));
    expect(prefetchTargets(rows, (id) => held.has(id))).toEqual(["m1", "m3", "m4", "m5", "m6", "m7"]);
  });

  it("three quick schedules start one run, 500 ms after the last one", () => {
    vi.useFakeTimers();
    const s = createPrefetchSchedule();
    const first = vi.fn();
    const last = vi.fn();
    s.schedule(true, first);
    vi.advanceTimersByTime(200);
    s.schedule(true, first);
    vi.advanceTimersByTime(200);
    s.schedule(true, last);
    vi.advanceTimersByTime(PREFETCH_STILL_MS - 1);
    expect(first).not.toHaveBeenCalled();
    expect(last).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(first).not.toHaveBeenCalled();
    expect(last).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(5_000);
    expect(last).toHaveBeenCalledTimes(1);
    expect(PREFETCH_STILL_MS).toBe(500);
  });

  it("the list waits through the schedule, on each scroll and each new list", () => {
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("const [still] = useState(() => createPrefetchSchedule());");
    expect(list).toContain("still.schedule(anyRemote, () => void sharedHtmlPrefetcher().run(visibleRows()))");
    expect(list).toContain("onScroll={schedulePrefetch}");
    expect(list).toContain("sharedHtmlPrefetcher().listLoaded(listKey);");
    expect(list).toContain("return () => still.cancel();");
    // No prefetcher held in the component: a remount would lose its stop.
    expect(list).not.toContain("createHtmlPrefetcher(");
  });

  it("takes the rows that show in the scroll box, in the order of the list", () => {
    const boxes = [
      { id: "a", top: -80, bottom: -10 },
      { id: "b", top: -10, bottom: 60 },
      { id: "c", top: 60, bottom: 130 },
      { id: "d", top: 400, bottom: 470 },
      { id: "e", top: 470, bottom: 540 },
    ];
    expect(visibleRowIds(boxes, 0, 470)).toEqual(["b", "c", "d"]);
  });
});

describe("email-html-prefetch-stops", () => {
  it("stops on the first 503, and starts no new request", async () => {
    const h = harness((id) => {
      if (id === "m1") throw httpError(503, 30);
      return "<p>x</p>";
    });
    const out = await prefetcher(h).run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(out.stopped).toBe("busy");
    // m0 and m1 start together. m1 gives the 503, so only the slot of m0
    // may have started one more row: never all six.
    expect(h.asked.length).toBeLessThanOrEqual(3);
    expect(h.asked).not.toContain("m3");
  });

  it("a soft refresh of the same list does not lift the 503 stop inside the wait, and the prefetch resumes after it", async () => {
    let busy = true;
    const h = harness(() => {
      if (busy) throw httpError(503, 40);
      return "<p>x</p>";
    });
    const p = prefetcher(h);
    await p.run(rowsOf("a"));
    const sent = h.asked.length;
    busy = false;
    // Inside the wait: soft refreshes of the same list send nothing.
    for (const step of [1_000, 20_000, 18_999]) {
      h.advance(step);
      p.listLoaded(INBOX);
      const out = await p.run(rowsOf("b"));
      expect(out.asked).toEqual([]);
      expect(out.stopped).toBe("busy");
    }
    expect(h.asked).toHaveLength(sent);
    // The wait of 40 s ends. The same list prefetches again.
    h.advance(1);
    p.listLoaded(INBOX);
    const after = await p.run(rowsOf("b"));
    expect(after.stopped).toBeNull();
    expect(after.asked).toEqual(["b"]);
  });

  it("a list change inside the wait of Retry-After starts nothing, and after it, the prefetch goes on", async () => {
    let busy = true;
    const h = harness(() => {
      if (busy) throw httpError(503, 40);
      return "<p>x</p>";
    });
    const p = prefetcher(h);
    await p.run(rowsOf("c"));
    busy = false;
    p.listLoaded(SENT);
    h.advance(39_000);
    expect((await p.run(rowsOf("d"))).asked).toEqual([]);
    h.advance(1_000);
    expect((await p.run(rowsOf("d"))).asked).toEqual(["d"]);
  });

  it("waits 30 seconds after a 503 that sent no Retry-After", async () => {
    let busy = true;
    const h = harness(() => {
      if (busy) throw httpError(503);
      return "<p>x</p>";
    });
    const p = prefetcher(h);
    await p.run(rowsOf("a"));
    busy = false;
    p.listLoaded(SENT);
    h.advance(PREFETCH_DEFAULT_WAIT_S * 1000 - 1);
    expect((await p.run(rowsOf("b"))).asked).toEqual([]);
    h.advance(1);
    expect((await p.run(rowsOf("b"))).asked).toEqual(["b"]);
  });

  it("stops on the first 401, a soft refresh keeps the stop, and a list change clears it", async () => {
    let dead = true;
    const h = harness(() => {
      if (dead) throw httpError(401);
      return "<p>x</p>";
    });
    const p = prefetcher(h);
    const first = await p.run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(first.stopped).toBe("auth");
    expect(h.asked.length).toBeLessThanOrEqual(3);
    const before = h.asked.length;
    p.listLoaded(INBOX);
    expect((await p.run(rowsOf("x"))).asked).toEqual([]);
    expect(h.asked).toHaveLength(before);
    dead = false;
    p.listLoaded(SENT);
    expect((await p.run(rowsOf("x"))).asked).toEqual(["x"]);
  });

  it("does not ask again for a row that failed, while the list stays the same", async () => {
    let down = true;
    const h = harness((id) => {
      if (id === "b" && down) throw httpError(502);
      return `<p>${id}</p>`;
    });
    const p = prefetcher(h);
    const out = await p.run(rowsOf("a", "b", "c"));
    expect(out.stopped).toBeNull();
    expect(out.asked).toEqual(["a", "b", "c"]);
    expect(h.kept.has("b")).toBe(false);
    down = false;
    // Soft refreshes of the same list: b is not asked again.
    for (let i = 0; i < 3; i++) {
      p.listLoaded(INBOX);
      expect((await p.run(rowsOf("a", "b", "c"))).asked).toEqual([]);
    }
    // Another list may ask again.
    p.listLoaded(SENT);
    expect((await p.run(rowsOf("b"))).asked).toEqual(["b"]);
  });

  it("a remount of the list inside the wait sends nothing", async () => {
    const h = harness(() => {
      throw httpError(503, 30);
    });
    const state = newPrefetchState();
    await prefetcher(h, state).run(rowsOf("a"));
    const sent = h.asked.length;
    // The list mounts again: a new prefetcher over the same state, as
    // `sharedHtmlPrefetcher()` gives on each call.
    const again = createHtmlPrefetcher(h.deps, state);
    again.listLoaded(INBOX);
    expect((await again.run(rowsOf("b", "c"))).asked).toEqual([]);
    expect(h.asked).toHaveLength(sent);
  });

  it("the state is one per member: it survives a remount, and a clear or a new member drops it", async () => {
    const one = sharedPrefetchState();
    expect(sharedPrefetchState()).toBe(one);
    one.stop = "auth";
    expect(sharedPrefetchState().stop).toBe("auth");
    clearAll();
    expect(one.retired).toBe(true);
    const two = sharedPrefetchState();
    expect(two).not.toBe(one);
    expect(two.stop).toBeNull();
    bindIdentity("asha@fracktal.in");
    const three = sharedPrefetchState();
    expect(three).not.toBe(two);
    expect(three.owner).toBe("asha@fracktal.in");
    bindIdentity(null);
  });

  it("an answer that lands after a sign-out keeps nothing", async () => {
    const f = deferredFetch();
    const run = sharedHtmlPrefetcher().run(rowsOf("z"));
    clearAll();
    await f.answer(f.html("z"));
    await run;
    expect(peek(messageHtmlKey("z"))).toBeUndefined();
  });

  it("after a sign-out, the old state starts no new request", async () => {
    const h = harness(undefined, { manual: true });
    const p = createHtmlPrefetcher(h.deps, sharedPrefetchState());
    p.listLoaded(INBOX);
    const run = p.run(rowsOf("a", "b", "c", "d", "e", "f"));
    await tick();
    expect(h.asked).toEqual(["a", "b"]);
    clearAll();
    await h.drain();
    // a and b settle. c to f never go with the next session.
    expect(h.asked).toEqual(["a", "b"]);
    expect((await run).asked).toEqual(["a", "b"]);
  });

  it("a throw from keep counts the row as failed, with no unhandled rejection", async () => {
    const seen: unknown[] = [];
    const onRejection = (reason: unknown) => seen.push(reason);
    process.on("unhandledRejection", onRejection);
    try {
      const h = harness();
      const keep = h.deps.keep;
      h.deps.keep = (id, html) => {
        if (id === "b") throw new Error("the cache is full");
        keep(id, html);
      };
      const p = prefetcher(h);
      const out = await p.run(rowsOf("a", "b", "c"));
      expect(out.stopped).toBeNull();
      expect(out.asked).toEqual(["a", "b", "c"]);
      expect(h.kept.has("b")).toBe(false);
      // The run counts b as failed, so the same list does not ask again.
      p.listLoaded(INBOX);
      expect((await p.run(rowsOf("b"))).asked).toEqual([]);
      await tick();
      await tick();
      expect(seen).toEqual([]);
    } finally {
      process.off("unhandledRejection", onRejection);
    }
  });

  it("the list key changes with the mailbox, the folder, the label and the search, and nothing else", () => {
    const base = LIST;
    const pill = { kind: "from" as const, value: "arjun@acme.test" };
    expect(prefetchListKey({ ...base })).toBe(INBOX);
    expect(prefetchListKey({ ...base, query: "  " })).toBe(INBOX);
    expect(prefetchListKey({ ...base, filters: [pill] })).toBe(prefetchListKey({ ...base, filters: [{ ...pill }] }));
    for (const other of [
      { ...base, accountId: "acc2" },
      { ...base, folder: "sent" },
      { ...base, label: "Clients" },
      { ...base, query: "quote" },
      { ...base, viewAll: true },
      { ...base, scope: "all" },
      { ...base, filters: [pill] },
      { ...base, filters: [{ kind: "unread" as const, value: "" }] },
    ]) {
      expect(prefetchListKey(other)).not.toBe(INBOX);
    }
  });

  it("a change of pill or of search scope is a list change", async () => {
    let dead = true;
    const h = harness(() => {
      if (dead) throw httpError(401);
      return "<p>x</p>";
    });
    const p = prefetcher(h);
    expect((await p.run(rowsOf("a"))).stopped).toBe("auth");
    dead = false;
    const withPill = prefetchListKey({ ...LIST, filters: [{ kind: "unread", value: "" }] });
    p.listLoaded(withPill);
    expect((await p.run(rowsOf("a"))).asked).toEqual(["a"]);
    dead = true;
    expect((await p.run(rowsOf("b"))).stopped).toBe("auth");
    dead = false;
    p.listLoaded(prefetchListKey({ ...LIST, filters: [{ kind: "unread", value: "" }], scope: "all" }));
    expect((await p.run(rowsOf("b"))).asked).toEqual(["b"]);
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("scope: searchScope, filters: searchFilters,");
  });
});

describe("email-html-pane", () => {
  const TEXT = "Hello Priya, the quote is attached.";

  it("shows the text in the first paint, with a named loading line", () => {
    const html = renderToStaticMarkup(createElement(MessageContent, { text: TEXT, remoteId: "cold-1" }));
    expect(html).toContain(TEXT);
    expect(html).toContain('role="status"');
    expect(html).toContain("Getting the formatted message from the mail provider");
    // The loading line names itself in words, and the spinner is hidden.
    expect(html).toMatch(/<svg[^>]*aria-hidden="true"/);
  });

  it("then shows the HTML through the one render path of stored HTML", () => {
    const body = "<p>Quote</p>";
    put(messageHtmlKey("cold-2"), body);
    const fetched = renderToStaticMarkup(createElement(MessageContent, { text: TEXT, remoteId: "cold-2" }));
    const stored = renderToStaticMarkup(createElement(MessageContent, { html: body, text: TEXT }));
    expect(fetched).toBe(stored);
    expect(fetched).not.toContain(TEXT);
    expect(fetched).not.toContain('role="status"');
  });

  it("the fetched HTML reaches the frame only through sanitizeEmailHtml (source)", () => {
    // A node run has no DOM, so it cannot show the frame. The browser proof is
    // "the reading pane keeps a script of a fetched body out of the frame" in
    // `e2e/untrusted-html.spec.ts`. This holds the wiring that it relies on.
    expect(sanitizeEmailHtml("<script>x</script>", false)).toEqual({ clean: "", hasRemote: false });
    const src = codeOnly(read("components/MessageContent.tsx"));
    expect(src).toContain("return sanitizeEmailHtml(html, showImages);");
    expect(src).toContain("<body>${sanitized.clean}</body>");
    expect(src).toContain('sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"');
    expect(src).toContain("if (remote.html) return <HtmlMessage html={remote.html} />;");
    expect(src.match(/dangerouslySetInnerHTML|srcDoc=\{/g)).toEqual(["srcDoc={"]);
  });

  it("keeps the text, with no error state, when the fetch fails", () => {
    expect(remoteBody({ data: undefined, loading: false, error: "Gateway error 502" })).toEqual({
      html: null,
      loading: false,
    });
    expect(remoteBody({ data: undefined, loading: true, error: null })).toEqual({ html: null, loading: true });
    // A plain-text message (`source: none`) keeps its text too.
    expect(remoteBody({ data: null, loading: false, error: null })).toEqual({ html: null, loading: false });
    expect(remoteBody({ data: "<p>x</p>", loading: false, error: null })).toEqual({ html: "<p>x</p>", loading: false });
    const src = codeOnly(read("components/MessageContent.tsx"));
    expect(src).toContain("if (!remote.loading) return <TextMessage text={text} />;");
  });

  it("each render path of the pane passes remoteId, and one helper fetches", () => {
    for (const file of ["components/EmailDetail.tsx", "components/ConversationView.tsx", "components/EmailPreviewModal.tsx"]) {
      const src = codeOnly(read(file));
      const uses = src.match(/<MessageContent\b[^>]*>/g) ?? [];
      expect(uses.length, file).toBeGreaterThan(0);
      for (const use of uses) expect(use, file).toContain("remoteId={remoteHtmlId(");
    }
    // `fetchMessageHtml` is the one fetch of the route, and only the prefetch
    // module calls it. The pane reads through `openMessageHtml`.
    const callers = [
      "components/MessageContent.tsx", "lib/htmlPrefetch.ts", "components/EmailDetail.tsx",
      "components/ConversationView.tsx", "components/EmailPreviewModal.tsx", "components/EmailList.tsx",
      "lib/emailStore.ts", "page.tsx",
    ].filter((f) => codeOnly(read(f)).includes("fetchMessageHtml("));
    expect(callers).toEqual(["lib/htmlPrefetch.ts"]);
    expect(codeOnly(read("components/MessageContent.tsx"))).toContain("() => openMessageHtml(remoteId as string),");
    const api = codeOnly(read("lib/api.ts"));
    expect(api.match(/\/html`/g)).toHaveLength(1);
  });

  it("the prefetch keeps its answer under the key the pane reads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ message_id: "m9", body_html: "<p>a</p>", source: "provider" }), { status: 200 }),
      ),
    );
    const deps = defaultPrefetchDeps();
    expect(deps.held("m9")).toBe(false);
    deps.keep("m9", await deps.fetchHtml("m9"));
    expect(peek(messageHtmlKey("m9"))?.data).toBe("<p>a</p>");
    expect(deps.held("m9")).toBe(true);
    const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls[0][0]).toBe("/api/email/messages/m9/html?prefetch=1");
  });
});

describe("the fetch helper and the proxy", () => {
  it("asks the route, with prefetch=1 only for the prefetch", async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ message_id: "m1", body_html: "<b>hi</b>", source: "cache" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = await import("./api");
    expect(await api.fetchMessageHtml("m1")).toEqual({ messageId: "m1", bodyHtml: "<b>hi</b>", source: "cache" });
    await api.fetchMessageHtml("m1", { prefetch: true });
    const urls = (fetchSpy.mock.calls as unknown as unknown[][]).map((c) => c[0]);
    expect(urls).toEqual(["/api/email/messages/m1/html", "/api/email/messages/m1/html?prefetch=1"]);
  });

  it("a plain-text message answers no HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ message_id: "m1", body_html: null, source: "none" }), { status: 200 })),
    );
    const api = await import("./api");
    expect(await api.fetchMessageHtml("m1")).toEqual({ messageId: "m1", bodyHtml: null, source: "none" });
  });

  it("a refusal carries its status and the seconds of Retry-After", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "The database is busy." }), {
          status: 503,
          headers: { "Retry-After": "30" },
        }),
      ),
    );
    const api = await import("./api");
    await expect(api.fetchMessageHtml("m1", { prefetch: true })).rejects.toMatchObject({
      status: 503,
      retryAfter: 30,
    });
  });

  /** Run the real email proxy against a stubbed gateway answer. */
  async function relay(upstream: Response): Promise<Response> {
    vi.stubGlobal("fetch", vi.fn(async () => upstream));
    const { NextRequest } = await import("next/server");
    const { GET } = await import("@/app/api/email/[...path]/route");
    return GET(new NextRequest("http://localhost:3001/api/email/messages/m1/html?prefetch=1"), {
      params: Promise.resolve({ path: ["messages", "m1", "html"] }),
    });
  }

  it("the email proxy passes Retry-After to the browser", async () => {
    const res = await relay(
      new Response(JSON.stringify({ detail: "The mail provider asked for a pause." }), {
        status: 503,
        headers: { "content-type": "application/json", "retry-after": "45" },
      }),
    );
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).toBe("45");
  });

  it("the email proxy adds only Retry-After, and passes no other header of the gateway", async () => {
    const res = await relay(
      new Response(JSON.stringify({ detail: "busy" }), {
        status: 503,
        headers: {
          "content-type": "application/json",
          "retry-after": "45",
          "set-cookie": "sid=evil; Path=/",
          "x-provider": "graph",
          "cache-control": "public, max-age=3600",
        },
      }),
    );
    const names: string[] = [];
    res.headers.forEach((_v, k) => names.push(k));
    expect(res.headers.get("set-cookie")).toBeNull();
    expect(res.headers.get("x-provider")).toBeNull();
    expect(res.headers.get("cache-control")).toBeNull();
    expect(names.sort()).toEqual(["content-type", "retry-after"]);
    // With no Retry-After, the answer carries none.
    const plain = await relay(
      new Response(JSON.stringify({ body_html: "<p>x</p>" }), {
        status: 200,
        headers: { "content-type": "application/json", "set-cookie": "sid=evil" },
      }),
    );
    const plainNames: string[] = [];
    plain.headers.forEach((_v, k) => plainNames.push(k));
    expect(plainNames).toEqual(["content-type"]);
  });
});

describe("email-html-flag-off", () => {
  // With the flag off, `_html_remote` in the gateway is always false
  // (`core.py`). A gateway before EM-S1 sends no field at all.
  const rows = [
    { id: "a", account_id: "x", body_text: "one" },
    { id: "b", account_id: "x", body_text: "two", html_remote: false },
    { id: "c", account_id: "x", body_text: "three", html_remote: "true" },
  ];

  it("maps no row as remote", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ emails: rows, total: rows.length, page: 1, page_size: 50 }), { status: 200 }),
      ),
    );
    const api = await import("./api");
    const out = await api.listEmails({});
    expect(out.emails.map((e) => e.htmlRemote)).toEqual([false, false, false]);
    expect(out.emails.map((e) => remoteHtmlId(e))).toEqual([null, null, null]);
  });

  it("the prefetch asks for nothing, and the list starts no timer", async () => {
    const h = harness();
    const out = await prefetcher(h).run(rows.map((r) => ({ id: r.id, htmlRemote: false })));
    expect(out.asked).toEqual([]);
    expect(h.asked).toEqual([]);
    vi.useFakeTimers();
    const s = createPrefetchSchedule();
    const run = vi.fn();
    s.schedule(true, run);
    s.schedule(false, run);
    vi.advanceTimersByTime(10_000);
    expect(run).not.toHaveBeenCalled();
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("const anyRemote = emails.some((e) => e.htmlRemote === true);");
  });

  it("the pane draws exactly as before, and asks for nothing", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const before = renderToStaticMarkup(createElement(MessageContent, { text: "Hello" }));
    const now = renderToStaticMarkup(
      createElement(MessageContent, { text: "Hello", remoteId: remoteHtmlId({ id: "a", htmlRemote: false }) }),
    );
    expect(now).toBe(before);
    expect(now).toBe('<div class="text-sm text-foreground/85 leading-relaxed whitespace-pre-wrap break-words max-w-2xl">Hello</div>');
    expect(now).not.toContain('role="status"');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(peek(messageHtmlKey("a"))).toBeUndefined();
  });
});
