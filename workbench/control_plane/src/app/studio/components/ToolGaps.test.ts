// The Tool gaps section of /studio (SBX-1b, task 3984). It stays dark on a
// 404 and a 403, shows one quiet error line on a miss of the network, and
// draws the counts and the newest rows on a 200. The need text of a member
// draws as text, never as HTML.
// Mutation: draw an error on a 404, and the first test fails.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { clearAll, put } from "@/lib/dataCache";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  loadToolGapsView,
  panelState,
  readToolGaps,
  TOOL_GAPS_KEY,
  TOOL_GAPS_ERROR,
  ToolGapsSection,
  type ToolGapsView,
} from "./ToolGaps";

afterEach(() => vi.unstubAllGlobals());

function answer(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status })));
}

const html = (view: ToolGapsView | null) => renderToStaticMarkup(createElement(ToolGapsSection, { view }));

const FULL = {
  days: 30,
  counts: { by_outcome: { no_match: 3, compose: 1 }, by_category: { finance: 2, crm: 2 } },
  gaps: [
    { need: "Convert a quote to INR", category: "finance", outcome: "no_match", created_at: "2026-11-25T10:00:00+00:00" },
    { need: "Merge two leads", category: "crm", outcome: "compose", created_at: "2026-11-24T09:00:00+00:00" },
  ],
};

describe("the Tool gaps read", () => {
  it("is off on a 404, so nothing draws", async () => {
    answer(404, { detail: "Not found" });
    const view = await loadToolGapsView();
    expect(view).toEqual({ kind: "off" });
    expect(html(view)).toBe("");
  });

  it("is off on a 403, so nothing draws", async () => {
    answer(403, { detail: { code: "forbidden", message: "Only a holder of studio:publish" } });
    const view = await loadToolGapsView();
    expect(view).toEqual({ kind: "off" });
    expect(html(view)).toBe("");
  });

  it("draws nothing before the answer", () => {
    expect(html(null)).toBe("");
  });

  it("reads the route through the Studio proxy", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify(FULL), { status: 200 }));
    vi.stubGlobal("fetch", f);
    await loadToolGapsView();
    expect(f).toHaveBeenCalledWith("/api/studio/tool-gaps", { cache: "no-store" });
  });

  it("shows one quiet error line on a miss of the network", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("fetch failed");
    }));
    const view = await loadToolGapsView();
    expect(view).toEqual({ kind: "error", detail: TOOL_GAPS_ERROR });
    const out = html(view);
    expect(out).toContain(TOOL_GAPS_ERROR);
    expect(out).toContain('role="alert"');
    expect(out).not.toContain("<button");
  });

  it("draws the counts by outcome and by category, and the rows, on a 200", async () => {
    answer(200, FULL);
    const view = await loadToolGapsView();
    expect(view.kind).toBe("ok");
    const out = html(view);
    expect(out).toContain("Tool gaps");
    expect(out).toContain("Last 30 days");
    expect(out).toContain("By outcome");
    expect(out).toContain("No match · 3");
    expect(out).toContain("Composed · 1");
    expect(out).toContain("By category");
    expect(out).toContain("finance · 2");
    expect(out).toContain("crm · 2");
    expect(out).toContain("Convert a quote to INR");
    expect(out).toContain("Merge two leads");
    expect(out).toContain("25 Nov 2026");
  });

  it("shows a short empty state on a 200 with no rows", async () => {
    answer(200, { days: 30, counts: { by_outcome: {}, by_category: {} }, gaps: [] });
    const out = html(await loadToolGapsView());
    expect(out).toContain("No tool gaps yet.");
    expect(out).not.toContain("By outcome");
  });

  it("draws the need of a member as text, never as HTML", async () => {
    const need = '<img src=x onerror="alert(1)"><b>bold</b>';
    answer(200, { ...FULL, gaps: [{ ...FULL.gaps[0], need }] });
    const out = html(await loadToolGapsView());
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>");
    expect(out).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&lt;b&gt;bold&lt;/b&gt;");
  });

  it("holds no raw HTML sink", () => {
    const src = readFileSync("src/app/studio/components/ToolGaps.tsx", "utf-8");
    expect(src).not.toContain("dangerouslySetInnerHTML");
    expect(src).not.toContain("innerHTML");
  });
});

describe("the Tool gaps panel", () => {
  it("reads through useCachedResource and shows a Skeleton while it loads", () => {
    const src = readFileSync("src/app/studio/components/ToolGaps.tsx", "utf-8");
    expect(src).toContain("useCachedResource<ToolGapsView>(TOOL_GAPS_KEY, readToolGaps");
    expect(src).toContain("<SkeletonRows");
    expect(src).not.toMatch(/\buseEffect\b/);
  });
});

describe("the panel stays dark until the route answered 200 once", () => {
  const ok: ToolGapsView = { kind: "ok", data: { days: 30, counts: { by_outcome: {}, by_category: {} }, gaps: [] } };
  const err: ToolGapsView = { kind: "error", detail: TOOL_GAPS_ERROR };

  it("draws no skeleton on a first read in a browser that never saw a 200", () => {
    expect(panelState(true, undefined, false)).toBe("hidden");
  });

  it("draws the skeleton on a first read after a 200 was seen", () => {
    expect(panelState(true, undefined, true)).toBe("skeleton");
  });

  it("hides a failed first read (a gateway restart) where the feature is dark", () => {
    expect(panelState(false, err, false)).toBe("hidden");
  });

  it("shows the error line where the feature was on", () => {
    expect(panelState(false, err, true)).toBe("view");
  });

  it("hides the off view, and shows the ok view", () => {
    expect(panelState(false, { kind: "off" }, true)).toBe("hidden");
    expect(panelState(false, ok, false)).toBe("view");
  });
});

describe("a failed read over good rows", () => {
  afterEach(() => clearAll());

  it("throws, so the cache keeps the rows", async () => {
    put(TOOL_GAPS_KEY, { kind: "ok", data: FULL });
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 502 })));
    await expect(readToolGaps()).rejects.toThrow(TOOL_GAPS_ERROR);
  });

  it("returns the error view when no rows were shown", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 502 })));
    await expect(readToolGaps()).resolves.toEqual({ kind: "error", detail: TOOL_GAPS_ERROR });
  });
});
