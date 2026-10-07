// WS-17 EM-S2 — the pane shows text, then HTML, and the prefetch
// (`project-docs/specs/email_app_master_plan.md` §14.6.2, §14.4.2 items 4 and 5).
//
// R7 fences named here:
//   * `email-html-prefetch-bounds`: 6 rows at most, 2 at one time, and no
//     fetch for a row with `htmlRemote` false or a row the cache holds.
//   * `email-html-prefetch-stops`: the first 503 stops the prefetch until the
//     next list load and its `Retry-After`. The first 401 stops it until the
//     next list load. Any other failure skips its row.
//   * `email-html-pane`: the first paint shows the text, then the HTML goes
//     through the one render path of stored HTML, and a `<script>` does not
//     reach the frame. A failed fetch keeps the text, with no error state.
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

import { clearAll, peek, put } from "@/lib/dataCache";
import { MessageContent, remoteBody, sanitizeEmailHtml } from "../components/MessageContent";
import { messageHtmlKey } from "./api";
import {
  PREFETCH_DEFAULT_WAIT_S,
  PREFETCH_MAX_ROWS,
  PREFETCH_PARALLEL,
  PREFETCH_STILL_MS,
  createHtmlPrefetcher,
  prefetchTargets,
  remoteHtmlId,
  visibleRowIds,
  type PrefetchDeps,
  type PrefetchRow,
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

/** An error as `gatewayFetch` in `api.ts` throws it. */
function httpError(status: number, retryAfter?: number): Error {
  const err = new Error(`Gateway error ${status}`) as Error & { status: number; retryAfter?: number };
  err.status = status;
  if (retryAfter !== undefined) err.retryAfter = retryAfter;
  return err;
}

/**
 * Effects that record each request. `answer` decides each one. A request
 * waits until the test calls `release`, so the test can count how many are in
 * flight at one time.
 */
function harness(answer: (id: string) => Promise<string | null> | string | null = () => "<p>x</p>") {
  const kept = new Map<string, string | null>();
  const asked: string[] = [];
  let inFlight = 0;
  let maxInFlight = 0;
  let clock = 1_000_000;
  const deps: PrefetchDeps = {
    fetchHtml: async (id) => {
      asked.push(id);
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      try {
        // One turn of the event loop, so the other worker can start too.
        await new Promise((r) => setTimeout(r, 0));
        return await answer(id);
      } finally {
        inFlight -= 1;
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
    advance: (ms: number) => {
      clock += ms;
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  clearAll();
});

describe("email-html-prefetch-bounds", () => {
  it("asks for 6 rows at most", async () => {
    const h = harness();
    const rows = Array.from({ length: 10 }, (_, i) => remote(`m${i}`));
    const out = await createHtmlPrefetcher(h.deps).run(rows);
    expect(PREFETCH_MAX_ROWS).toBe(6);
    expect(out.asked).toEqual(["m0", "m1", "m2", "m3", "m4", "m5"]);
    expect(h.asked).toHaveLength(6);
    expect([...h.kept.keys()]).toEqual(["m0", "m1", "m2", "m3", "m4", "m5"]);
  });

  it("has 2 requests in flight at most", async () => {
    const h = harness();
    await createHtmlPrefetcher(h.deps).run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(PREFETCH_PARALLEL).toBe(2);
    expect(h.max()).toBe(2);
  });

  it("never asks for a row with htmlRemote false", async () => {
    const h = harness();
    const out = await createHtmlPrefetcher(h.deps).run([
      local("a"), remote("b"), { id: "c" }, local("d"), remote("e"),
    ]);
    expect(out.asked).toEqual(["b", "e"]);
    expect(h.asked).not.toContain("a");
    expect(h.asked).not.toContain("c");
    expect(h.asked).not.toContain("d");
  });

  it("skips a row that the cache holds, and counts only the rows it asks for", () => {
    const held = new Set(["m0", "m2"]);
    const rows = Array.from({ length: 9 }, (_, i) => remote(`m${i}`));
    expect(prefetchTargets(rows, (id) => held.has(id))).toEqual(["m1", "m3", "m4", "m5", "m6", "m7"]);
  });

  it("waits 500 ms of a still list before a run", () => {
    expect(PREFETCH_STILL_MS).toBe(500);
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("setTimeout(() => {");
    expect(list).toContain("void prefetcher.run(visibleRows());");
    expect(list).toContain("}, PREFETCH_STILL_MS);");
    expect(list).toContain("onScroll={schedulePrefetch}");
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
    const p = createHtmlPrefetcher(h.deps);
    const out = await p.run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(out.stopped).toBe("busy");
    // m0 and m1 start together. m1 gives the 503, so only the worker that
    // already ran m0 may have started one more: never all six.
    expect(h.asked.length).toBeLessThanOrEqual(3);
    expect(h.asked).not.toContain("m3");
  });

  it("stays stopped until the next list load, and until Retry-After ends", async () => {
    let busy = true;
    const h = harness(() => {
      if (busy) throw httpError(503, 40);
      return "<p>x</p>";
    });
    const p = createHtmlPrefetcher(h.deps);
    await p.run([remote("a")]);
    busy = false;
    h.advance(60_000);
    // The wait is over, but there was no list load.
    expect((await p.run([remote("b")])).asked).toEqual([]);

    // A list load inside the wait of Retry-After starts nothing either.
    const p2 = createHtmlPrefetcher(h.deps);
    busy = true;
    await p2.run([remote("c")]);
    busy = false;
    p2.listLoaded();
    h.advance(39_000);
    expect((await p2.run([remote("d")])).asked).toEqual([]);
    h.advance(1_000);
    expect((await p2.run([remote("d")])).asked).toEqual(["d"]);
  });

  it("waits 30 seconds after a 503 that sent no Retry-After", async () => {
    let busy = true;
    const h = harness(() => {
      if (busy) throw httpError(503);
      return "<p>x</p>";
    });
    const p = createHtmlPrefetcher(h.deps);
    await p.run([remote("a")]);
    busy = false;
    p.listLoaded();
    h.advance(PREFETCH_DEFAULT_WAIT_S * 1000 - 1);
    expect((await p.run([remote("b")])).asked).toEqual([]);
    h.advance(1);
    expect((await p.run([remote("b")])).asked).toEqual(["b"]);
  });

  it("stops on the first 401 until the next list load", async () => {
    let dead = true;
    const h = harness(() => {
      if (dead) throw httpError(401);
      return "<p>x</p>";
    });
    const p = createHtmlPrefetcher(h.deps);
    const first = await p.run(Array.from({ length: 6 }, (_, i) => remote(`m${i}`)));
    expect(first.stopped).toBe("auth");
    expect(h.asked.length).toBeLessThanOrEqual(3);
    const before = h.asked.length;
    expect((await p.run([remote("x")])).asked).toEqual([]);
    expect(h.asked).toHaveLength(before);
    dead = false;
    p.listLoaded();
    expect((await p.run([remote("x")])).asked).toEqual(["x"]);
  });

  it("skips a row that failed for another reason, and goes on", async () => {
    const h = harness((id) => {
      if (id === "b") throw httpError(502);
      return `<p>${id}</p>`;
    });
    const out = await createHtmlPrefetcher(h.deps).run([remote("a"), remote("b"), remote("c")]);
    expect(out.stopped).toBeNull();
    expect(out.asked).toEqual(["a", "b", "c"]);
    expect(h.kept.has("b")).toBe(false);
    expect(h.kept.get("c")).toBe("<p>c</p>");
  });
});

describe("email-html-pane", () => {
  const TEXT = "Hello Priya, the quote is attached.";

  it("shows the text in the first paint, with a named loading line", () => {
    const html = renderToStaticMarkup(
      createElement(MessageContent, { text: TEXT, remoteId: "cold-1" }),
    );
    expect(html).toContain(TEXT);
    expect(html).toContain('role="status"');
    expect(html).toContain("Getting the formatted message from the mail provider");
    // The loading line names itself in words, and the spinner is hidden.
    expect(html).toMatch(/<svg[^>]*aria-hidden="true"/);
  });

  it("then shows the HTML through the one render path of stored HTML", () => {
    const body = "<p>Quote</p>";
    put(messageHtmlKey("cold-2"), body);
    const fetched = renderToStaticMarkup(
      createElement(MessageContent, { text: TEXT, remoteId: "cold-2" }),
    );
    const stored = renderToStaticMarkup(createElement(MessageContent, { html: body, text: TEXT }));
    expect(fetched).toBe(stored);
    expect(fetched).not.toContain(TEXT);
    expect(fetched).not.toContain('role="status"');
  });

  it("keeps a <script> of a fetched body out of the frame", () => {
    const attack = '<p>Quote</p><script>window.__probe="ran"</script><img src="x" onerror="alert(1)">';
    put(messageHtmlKey("cold-3"), attack);
    const out = renderToStaticMarkup(
      createElement(MessageContent, { text: TEXT, remoteId: "cold-3" }),
    );
    expect(out).not.toMatch(/<script/i);
    expect(out).not.toContain("__probe");
    expect(out).not.toContain("onerror");
    // With no DOM the sanitiser gives nothing, so nothing unsanitised can pass.
    expect(sanitizeEmailHtml(attack, false)).toEqual({ clean: "", hasRemote: false });
    // The frame takes only the output of sanitizeEmailHtml, in a sandbox with
    // no allow-scripts. The fetched HTML has no other way in.
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
    // Still loading: the text shows with the loading line.
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
    // `fetchMessageHtml` is the one fetch of the route. Only the pane and the
    // prefetch call it, and only `api.ts` builds the path.
    const callers = [
      "components/MessageContent.tsx", "lib/htmlPrefetch.ts", "components/EmailDetail.tsx",
      "components/ConversationView.tsx", "components/EmailPreviewModal.tsx", "components/EmailList.tsx",
      "lib/emailStore.ts", "page.tsx",
    ].filter((f) => codeOnly(read(f)).includes("fetchMessageHtml("));
    expect(callers.sort()).toEqual(["components/MessageContent.tsx", "lib/htmlPrefetch.ts"]);
    const api = codeOnly(read("lib/api.ts"));
    expect(api.match(/\/html`/g)).toHaveLength(1);
  });

  it("the open joins the cache of the prefetch, under one key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ message_id: "m9", body_html: "<p>a</p>", source: "provider" }), { status: 200 }),
      ),
    );
    const { defaultPrefetchDeps } = await import("./htmlPrefetch");
    const deps = defaultPrefetchDeps();
    expect(deps.held("m9")).toBe(false);
    deps.keep("m9", await deps.fetchHtml("m9"));
    expect(peek(messageHtmlKey("m9"))?.data).toBe("<p>a</p>");
    expect(deps.held("m9")).toBe(true);
    const calls = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls;
    expect(calls[0][0]).toBe("/api/email/messages/m9/html?prefetch=1");
  });
});

describe("the fetch helper", () => {
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

  it("the email proxy passes Retry-After to the browser", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "The mail provider asked for a pause." }), {
          status: 503,
          headers: { "content-type": "application/json", "retry-after": "45" },
        }),
      ),
    );
    const { NextRequest } = await import("next/server");
    const { GET } = await import("@/app/api/email/[...path]/route");
    const path = ["messages", "m1", "html"];
    const res = await GET(new NextRequest("http://localhost:3001/api/email/messages/m1/html?prefetch=1"), {
      params: Promise.resolve({ path }),
    });
    expect(res.status).toBe(503);
    expect(res.headers.get("retry-after")).toBe("45");
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
    const out = await createHtmlPrefetcher(h.deps).run(rows.map((r) => ({ id: r.id, htmlRemote: false })));
    expect(out.asked).toEqual([]);
    expect(h.asked).toEqual([]);
    const list = codeOnly(read("components/EmailList.tsx"));
    expect(list).toContain("const anyRemote = emails.some((e) => e.htmlRemote === true);");
    expect(list).toContain("if (!anyRemote) return;");
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
