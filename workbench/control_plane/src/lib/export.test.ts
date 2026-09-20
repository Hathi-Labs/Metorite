/**
 * The shared CSV download seam, and the two things that can silently undo it.
 *
 * Three halves, in ascending order of how much they are worth:
 *
 *   1. BEHAVIOUR — `filenameFromDisposition` reads the name the server chose.
 *   2. THE BYTES — every BFF proxy that fronts an export is RUN, with a BOM'd
 *      `text/csv` body upstream, and the bytes it hands back are compared to
 *      the bytes it was given. This is the half that matters. Both proxies did
 *      `await res.text()` and rebuilt the response from the decoded string —
 *      and `Response.text()` is a UTF-8 decode, which strips a leading byte
 *      order mark. The gateway emits that BOM so Excel on Windows does not read
 *      the file as the system code page; the browser never saw it. The previous
 *      version of this file *pinned that defect*: it asserted
 *      `toContain("await res.text()")` and commented that `res.text()` "keeps
 *      the bytes". It does not, and a fence that holds the bug in place is
 *      worse than no fence.
 *   3. THE INVARIANT — a static sweep for the content-type stamp. `text/csv`
 *      relabelled `application/json` makes a download arrive as `{}` with a
 *      200: a request that looks like it worked and saves no file.
 *
 * Both defects were local fixes to systemic shapes (Projects fixed the stamp in
 * WS-27ae and the CRM inherited it in WS-26i-export; the BOM went the other
 * way), which is why every check here sweeps EVERY proxy in `EXPORT_PROXIES`
 * rather than the one that happens to be under repair.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { filenameFromDisposition } from "@/lib/export";

// The proxies below resolve the identity before forwarding, and
// `gatewayHeaders()` throws without a signed-in member (see
// `src/lib/gateway.test.ts`). Somebody is signed in for every case here; who,
// is that file's subject, not this one's.
vi.mock("@/auth", () => ({
  auth: async () => ({ user: { email: "priya@fracktal.in" } }),
  isAuthEnabled: true,
}));

// ---------------------------------------------------------------------------
// 1. The filename
// ---------------------------------------------------------------------------

describe("filenameFromDisposition", () => {
  it("reads the name the SERVER chose", () => {
    expect(
      filenameFromDisposition(
        'attachment; filename="projects-tasks-p1.csv"',
        "fallback.csv"
      )
    ).toBe("projects-tasks-p1.csv");
  });

  it("reads an unquoted name too", () => {
    expect(
      filenameFromDisposition("attachment; filename=export.csv", "fallback.csv")
    ).toBe("export.csv");
  });

  it("falls back rather than downloading a file called null", () => {
    expect(filenameFromDisposition(null, "crm-leads.csv")).toBe("crm-leads.csv");
    expect(filenameFromDisposition("attachment", "crm-leads.csv")).toBe(
      "crm-leads.csv"
    );
  });

  it("takes the caller's fallback, not one app's filename", () => {
    // A default here would name the CRM's downloads after Projects the day a
    // proxy dropped the header — which is precisely the failure below.
    expect(filenameFromDisposition(null, "projects-tasks.csv")).toBe(
      "projects-tasks.csv"
    );
  });
});

// ---------------------------------------------------------------------------
// 2. The proxies a CSV has to survive
// ---------------------------------------------------------------------------

/** A proxy handler, loaded through the same `@/` specifier the app uses. */
type ProxyModule = {
  GET: (
    req: import("next/server").NextRequest,
    ctx: { params: Promise<{ path: string[] }> }
  ) => Promise<Response>;
};

/**
 * Every BFF proxy that fronts a `text/csv` export endpoint.
 *
 * The import is a literal arrow per entry rather than a computed specifier
 * because a dynamic `import(variable)` is not statically analysable and vite
 * cannot resolve it. `source` is the same file read off disk for the static
 * sweep below.
 */
const EXPORT_PROXIES: {
  name: string;
  source: string;
  load: () => Promise<ProxyModule>;
  path: string[];
}[] = [
  {
    name: "app/api/projects/[...path]/route.ts",
    source: "app/api/projects/[...path]/route.ts",
    load: () => import("@/app/api/projects/[...path]/route"),
    path: ["export", "tasks.csv"],
  },
  {
    name: "app/api/crm/[...path]/route.ts",
    source: "app/api/crm/[...path]/route.ts",
    load: () => import("@/app/api/crm/[...path]/route"),
    path: ["export", "leads.csv"],
  },
];

function proxySource(relative: string): string {
  return readFileSync(
    fileURLToPath(new URL(`./../${relative}`, import.meta.url)),
    "utf-8"
  );
}

/** `EF BB BF` + `Café,₹` + CRLF — the shape both gateways actually emit. */
const BOM = new Uint8Array([0xef, 0xbb, 0xbf]);

function csvUpstream(): ArrayBuffer {
  const body = new TextEncoder().encode("Name\r\nCafé\r\nSørensen\r\n₹1200\r\n");
  const out = new Uint8Array(BOM.length + body.length);
  out.set(BOM);
  out.set(body, BOM.length);
  return out.buffer as ArrayBuffer;
}

beforeEach(() => {
  vi.unstubAllGlobals();
});

describe.each(EXPORT_PROXIES)("$name", ({ source: relative, load, path }) => {
  const source = proxySource(relative);

  // ── The bytes, through the proxy's own tail ───────────────────────────────

  /** Run the real handler against a stubbed gateway response. */
  async function relay(upstream: Response): Promise<Response> {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => upstream)
    );
    const { NextRequest } = await import("next/server");
    const { GET } = await load();
    return GET(
      new NextRequest(`http://localhost:3001/api/x/${path.join("/")}`),
      { params: Promise.resolve({ path }) }
    );
  }

  it("hands the browser the gateway's bytes, BOM included", async () => {
    // ⚠️ THE assertion in this file. `await res.text()` + a rebuilt response
    // decoded UTF-8 and dropped the leading `EF BB BF`, so Excel on Windows
    // read the download as the system code page and "Café" arrived as "CafÃ©".
    // Byte equality — not `toContain`, not a decoded string compare — is the
    // only form of this check that can see a BOM at all, because decoding
    // either side is exactly the operation that loses it.
    const expected = csvUpstream();
    const res = await relay(
      new Response(expected, {
        status: 200,
        headers: {
          "content-type": "text/csv; charset=utf-8",
          "content-disposition": 'attachment; filename="export.csv"',
        },
      })
    );

    const relayed = new Uint8Array(await res.arrayBuffer());
    expect(Array.from(relayed.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(Array.from(relayed)).toEqual(Array.from(new Uint8Array(expected)));
  });

  it("still relays a JSON refusal from the same endpoint verbatim", async () => {
    // The other half of one code path: the export answers 422 with JSON when
    // the filter is wider than the row cap, and the app has to be able to read
    // the reason. A body path that only worked for CSV would trade one silent
    // failure for another.
    const res = await relay(
      new Response(JSON.stringify({ detail: "12000 leads match" }), {
        status: 422,
        headers: { "content-type": "application/json" },
      })
    );

    expect(res.status).toBe(422);
    expect(res.headers.get("content-type")).toContain("application/json");
    expect(await res.json()).toEqual({ detail: "12000 leads match" });
  });

  it("forwards the server's filename and its row count", async () => {
    // `X-Export-Rows` is the gateway's honest count beside the file. This proxy
    // is the only route to it, so a proxy that drops it makes the header a
    // no-op no caller can serve.
    const res = await relay(
      new Response(csvUpstream(), {
        status: 200,
        headers: {
          "content-type": "text/csv; charset=utf-8",
          "content-disposition": 'attachment; filename="crm-leads.csv"',
          "x-export-rows": "3993",
        },
      })
    );

    expect(res.headers.get("content-disposition")).toBe(
      'attachment; filename="crm-leads.csv"'
    );
    expect(res.headers.get("x-export-rows")).toBe("3993");
    expect(
      filenameFromDisposition(res.headers.get("content-disposition"), "x.csv")
    ).toBe("crm-leads.csv");
  });

  it("forwards nosniff, which is the only hop that can lose it", async () => {
    // ⚠️ **This asserts on the PROXY's response, not the gateway's.** The
    // gateway sets `X-Content-Type-Options: nosniff` beside every inline
    // `Content-Disposition`, and a backend test that reads it there passes
    // while the browser never sees it — which is exactly what happened.
    // These handlers build their headers from scratch, so a header survives
    // only if a line here copies it.
    //
    // It is load-bearing now rather than hygiene: Projects attachments serve
    // `inline` for a safelist of types, so the browser RENDERS uploaded bytes
    // on our own origin with the session attached. The safelist is what stops
    // an SVG today. `nosniff` is what stops the NEXT entry somebody adds to
    // that safelist from becoming stored XSS.
    const res = await relay(
      new Response(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), {
        status: 200,
        headers: {
          "content-type": "image/png",
          "content-disposition": 'inline; filename="shot.png"',
          "x-content-type-options": "nosniff",
        },
      })
    );

    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("content-disposition")).toBe(
      'inline; filename="shot.png"'
    );
  });

  it("answers an empty upstream body with no body and its status", async () => {
    // 204 and friends: there is nothing to guess a content type for, and
    // `NextResponse.json("")` would answer `""` where the caller expects
    // nothing. The byte-length branch has to keep behaving the way the
    // string-truthiness one did.
    const res = await relay(new Response(null, { status: 204 }));

    expect(res.status).toBe(204);
    expect(await res.text()).toBe("");
  });

  // ── The static sweep ──────────────────────────────────────────────────────

  it("passes the UPSTREAM content type through instead of stamping one", () => {
    // ⚠️ `NextResponse.json(body, …)` sets `application/json` unconditionally.
    // On a CSV that turns a download into a document: the browser renders the
    // bytes, or the client's `res.json()` yields `{}` with a 200 and nothing is
    // saved. Reading what upstream actually sent is what keeps a refusal (JSON)
    // and a file (CSV) both correct through one code path.
    expect(source).toContain('res.headers.get("content-type")');
  });

  it("never decodes the forwarded body to a string", () => {
    // The behavioural check above is the real fence; this is the shape it was
    // broken by, named so a future edit reaching for the convenient spelling
    // fails here too rather than only in the byte comparison. `res.text()` and
    // `res.json()` both decode, and decoding is what loses the BOM.
    expect(source).not.toContain("await res.text()");
    expect(source).not.toContain("await res.json()");
    expect(source).toContain("await res.arrayBuffer()");
  });
});
