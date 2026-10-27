/**
 * The pure half of the Gaps view (WS-61 KB-9a, `knowledge_base.md` §8.4).
 *
 * The link must match the one the gateway builds for the My Day card
 * (`gaps.write_href`), so the card opens the New page form for its gap.
 */
import { describe, expect, it, vi } from "vitest";

import { askersLabel, gapCount, gapsHref, listGaps, reopenGap, titleFromQuestion } from "./gaps";

describe("the Gaps link", () => {
  it("is the view alone, or the view with the gap to write", () => {
    expect(gapsHref()).toBe("/knowledge/gaps");
    expect(gapsHref(null)).toBe("/knowledge/gaps");
    expect(gapsHref("g-1")).toBe("/knowledge/gaps?write=g-1");
    expect(gapsHref("a b/c")).toBe("/knowledge/gaps?write=a%20b%2Fc");
  });
});

describe("the title from a question", () => {
  it("drops the end mark, folds the spaces and starts with a capital", () => {
    expect(titleFromQuestion("how do we raise a credit note?")).toBe("How do we raise a credit note");
    expect(titleFromQuestion("  where is the\n travel policy ?! ")).toBe("Where is the travel policy");
  });

  it("keeps a question of marks only, and cuts at 300 characters", () => {
    expect(titleFromQuestion("???")).toBe("???");
    expect(titleFromQuestion("x".repeat(400))).toHaveLength(300);
  });
});

describe("the words", () => {
  it("counts one person and many people", () => {
    expect(askersLabel(1)).toBe("Asked by 1 person");
    expect(askersLabel(3)).toBe("Asked by 3 people");
  });

  it("shows no count for no gap, and 99+ past 99", () => {
    expect(gapCount(null)).toBeNull();
    expect(gapCount({ gaps: [], scope: "org", off: false })).toBeNull();
    const gap = { id: "g", question: "q", ask_count: 2, first_asked_at: null, last_asked_at: null,
      status: "open" as const, answered_page: null, asked_by_me: false, dismissed_at: null };
    expect(gapCount({ gaps: [gap, gap], scope: "org", off: false })).toBe("2");
    expect(gapCount({ gaps: Array.from({ length: 120 }, () => gap), scope: "org", off: false })).toBe("99+");
  });
});

describe("the list read", () => {
  it("asks for the open gaps, reads a 404 as off, and refuses an answer with no list", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ gaps: [], scope: "org" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Not Found" }), { status: 404 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "down" }), { status: 500 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "x" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      expect(await listGaps()).toEqual({ gaps: [], scope: "org", off: false });
      expect(fetchMock.mock.calls[0][0]).toBe("/api/knowledge/gaps?status=open&limit=200");
      expect(await listGaps()).toEqual({ gaps: [], scope: "mine", off: true });
      await expect(listGaps()).rejects.toThrow("down");
      await expect(listGaps()).rejects.toThrow("no list");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("the Undo of a dismiss", () => {
  it("posts to the reopen route with the id encoded", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(
      new Response(JSON.stringify({ id: "g 1", status: "open", dismissed_at: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    try {
      const out = await reopenGap("g 1");
      expect(out.status).toBe("open");
      expect(fetchMock.mock.calls[0][0]).toBe("/api/knowledge/gaps/g%201/reopen");
      expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
