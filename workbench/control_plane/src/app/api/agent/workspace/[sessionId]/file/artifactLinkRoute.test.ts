// A direct open of a Markdown file shows it rendered, never as raw text
// (owner report, 2026-10-16).
//
// A member opens the address of a workspace `.md` file in a tab: from the
// address bar, a new tab or an email. The proxy route answers 303 to the
// rendered view in the Artifacts app (`artifactViewUrl`). A viewer's fetch,
// an image, a Download button (`&download=1`), a PDF (`format=pdf`) and an
// HTML file each keep the raw answer, so HANDOFF H-238 does not widen.
//
// Mutation: make `rendersAsPage` return false, and the 303 tests fail. Make
// it skip the `download` check, and the Download test fails.
import { NextRequest } from "next/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { rendersAsPage } from "@/lib/artifactKind";

vi.mock("@/lib/gateway", () => ({
  GATEWAY_URL: "http://gw.test",
  requireIdentity: async () => ({ email: "a@example.com" }),
  gatewayHeaders: async () => ({ "X-User-Email": "a@example.com" }),
  gatewayFetch: (input: RequestInfo | URL, init?: RequestInit) => fetch(input, init),
}));

const SID = "0f8fad5b-d9cb-469f-a165-70867728950e";
const MD = "# Report\n\n| a | b |\n|---|---|\n| 1 | 2 |\n";
const asked: string[] = [];

function stubGateway(body: string, type: string) {
  asked.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      asked.push(String(input));
      return new Response(body, {
        status: 200,
        headers: { "content-type": type, "content-disposition": 'inline; filename="x"' },
      });
    }),
  );
}

/** A GET of the route, with the headers a browser sends. */
async function get(query: string, headers: Record<string, string>) {
  const { GET } = await import("./route");
  const req = new NextRequest(`http://app.test/api/agent/workspace/${SID}/file?${query}`, { headers });
  return GET(req, { params: Promise.resolve({ sessionId: SID }) });
}

/** What Chrome sends for a typed address or a new tab. */
const TAB = {
  "sec-fetch-dest": "document",
  "sec-fetch-mode": "navigate",
  accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
};
/** What a viewer's `fetch()` sends. */
const FETCH = { "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", accept: "*/*" };

describe("the workspace file route — a tab gets the rendered view", () => {
  beforeEach(() => vi.unstubAllGlobals());

  it("answers a tab's open of a .md file with a 303 to the Artifacts viewer", async () => {
    stubGateway(MD, "text/markdown");
    const res = await get("path=outputs/report.md", TAB);
    expect(res.status).toBe(303);
    expect(res.headers.get("location")).toBe(
      `/artifacts?session=${SID}&file=outputs%2Freport.md`,
    );
    // It reads no file to answer: the view fetches it again, through every check.
    expect(asked).toEqual([]);
  });

  it("answers a viewer's fetch with the raw bytes", async () => {
    stubGateway(MD, "text/markdown");
    const res = await get("path=outputs/report.md", FETCH);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(MD);
    expect(res.headers.get("vary")).toContain("Sec-Fetch-Dest");
  });

  it("answers a Download button with the raw bytes, even from a tab", async () => {
    stubGateway(MD, "text/markdown");
    const res = await get("path=outputs/report.md&download=1", TAB);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe(MD);
    expect(res.headers.get("content-disposition")).toBe('inline; filename="x"');
    // `download` is the app's own mark. The gateway never sees it.
    expect(asked[0]).toBe("http://gw.test/agent/workspace/" + SID + "/file?path=outputs%2Freport.md");
  });

  it("passes a PDF request to the gateway", async () => {
    stubGateway("%PDF", "application/pdf");
    const res = await get("path=outputs/report.md&format=pdf", TAB);
    expect(res.status).toBe(200);
    expect(asked[0]).toContain("format=pdf");
  });

  it("keeps the old answer for an HTML file (H-238 does not widen)", async () => {
    stubGateway("<p>x</p>", "text/html");
    const res = await get("path=outputs/page.html", TAB);
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/html");
  });
});

describe("rendersAsPage", () => {
  const facts = (over: Partial<Parameters<typeof rendersAsPage>[1]> = {}) => ({
    dest: "document", accept: "text/html", download: false, format: null, ...over,
  });

  it("is true for a Markdown file that a tab opens", () => {
    for (const p of ["outputs/a.md", "outputs/a.markdown", "outputs/a.mdx", "a.MD"]) {
      expect(rendersAsPage(p, facts()), p).toBe(true);
    }
  });

  it("is false for every fetch that is not a tab", () => {
    for (const dest of ["empty", "image", "iframe", "embed", "object"]) {
      expect(rendersAsPage("outputs/a.md", facts({ dest })), dest).toBe(false);
    }
  });

  it("reads Accept when the browser sends no Sec-Fetch-Dest", () => {
    expect(rendersAsPage("outputs/a.md", facts({ dest: null }))).toBe(true);
    expect(rendersAsPage("outputs/a.md", facts({ dest: null, accept: "*/*" }))).toBe(false);
    expect(rendersAsPage("outputs/a.md", facts({ dest: null, accept: null }))).toBe(false);
  });

  it("is false for a download, a PDF and every other kind of file", () => {
    expect(rendersAsPage("outputs/a.md", facts({ download: true }))).toBe(false);
    expect(rendersAsPage("outputs/a.md", facts({ format: "pdf" }))).toBe(false);
    for (const p of ["outputs/a.html", "outputs/a.jsx", "outputs/a.pdf", "outputs/a.png", "outputs/a.txt"]) {
      expect(rendersAsPage(p, facts()), p).toBe(false);
    }
  });
});
