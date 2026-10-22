/**
 * The filters of the Search view (WS-61 KB-3d). Fence for `searchFilters.ts`,
 * for `searchHref` and `pageHref` in `links.ts`, and for the request that
 * `searchKnowledge` makes: the address round trip, the Date presets and the
 * marks of a snippet.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { searchKnowledge } from "./api";
import { pageHref, searchHref } from "./links";
import { highlight } from "./search";
import {
  NO_FILTERS,
  authorsOf,
  cleanAuthor,
  filtersFromParams,
  filtersKey,
  hasFilters,
  updatedAfter,
  type SearchFilters,
} from "./searchFilters";

const ALL: SearchFilters = { space: "sp-1", author: "asha@example.com", date: "month", titleOnly: true };

describe("the address round trip", () => {
  it("writes each filter and reads it back", () => {
    const href = searchHref("refund policy", ALL);
    expect(href).toBe(
      "/knowledge/search?q=refund%20policy&space=sp-1&author=asha%40example.com&date=month&title=1",
    );
    const back = filtersFromParams(new URL(href, "http://x").searchParams);
    expect(back).toEqual(ALL);
    expect(new URL(href, "http://x").searchParams.get("q")).toBe("refund policy");
  });

  it("keeps the bare view and the words-only address as they were", () => {
    expect(searchHref()).toBe("/knowledge/search");
    expect(searchHref("  ")).toBe("/knowledge/search");
    expect(searchHref("thirty")).toBe("/knowledge/search?q=thirty");
    expect(searchHref("", ALL)).toBe(
      "/knowledge/search?space=sp-1&author=asha%40example.com&date=month&title=1",
    );
  });

  it("drops a value it does not know", () => {
    const params = new URLSearchParams("date=decade&author=not-an-email&title=yes&space=%20");
    expect(filtersFromParams(params)).toEqual(NO_FILTERS);
    expect(hasFilters(NO_FILTERS)).toBe(false);
    expect(hasFilters(ALL)).toBe(true);
    expect(filtersKey(NO_FILTERS)).toBe("");
    expect(filtersKey(ALL)).toBe("space=sp-1&author=asha@example.com&date=month&title=1");
  });

  it("takes an author email in lower case, and refuses a long one", () => {
    expect(cleanAuthor("  Asha@Example.COM ")).toBe("asha@example.com");
    expect(cleanAuthor("asha")).toBeNull();
    expect(cleanAuthor(`${"a".repeat(320)}@x.io`)).toBeNull();
  });

  it("opens a page with the words of the search", () => {
    expect(pageHref("kp-1", { q: "refund policy" })).toBe("/knowledge/pages/kp-1?q=refund%20policy");
    expect(pageHref("kp-1", { q: "  " })).toBe("/knowledge/pages/kp-1");
    expect(pageHref("kp-1")).toBe("/knowledge/pages/kp-1");
    expect(pageHref("kp-1", { edit: true })).toBe("/knowledge/pages/kp-1?edit=1");
  });
});

describe("the Date presets", () => {
  const now = new Date("2026-10-22T15:30:00Z");

  it("send the day that many days back, as a UTC date", () => {
    expect(updatedAfter("week", now)).toBe("2026-10-15");
    expect(updatedAfter("month", now)).toBe("2026-09-22");
    expect(updatedAfter("year", now)).toBe("2025-10-22");
  });

  it("send nothing for Any time", () => {
    expect(updatedAfter(null, now)).toBeNull();
  });
});

describe("the request", () => {
  afterEach(() => vi.unstubAllGlobals());

  async function urlOf(filters: SearchFilters | undefined): Promise<URL> {
    const seen: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string) => {
        seen.push(String(input));
        return new Response(JSON.stringify({ query: "x", hits: [], degraded: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }),
    );
    await searchKnowledge("refund", { filters, now: new Date("2026-10-22T00:00:00Z") });
    return new URL(seen[0], "http://x");
  }

  it("sends no filter parameter when no filter is set", async () => {
    const url = await urlOf(NO_FILTERS);
    expect([...url.searchParams.keys()]).toEqual(["q", "k"]);
  });

  it("sends one parameter for each filter that is set", async () => {
    const url = await urlOf(ALL);
    expect(url.searchParams.get("space")).toBe("sp-1");
    expect(url.searchParams.get("author")).toBe("asha@example.com");
    expect(url.searchParams.get("updated_after")).toBe("2026-09-22");
    expect(url.searchParams.get("title_only")).toBe("true");
  });
});

describe("the authors of the hits", () => {
  it("lists each once, the most frequent first", () => {
    const hits = [
      { author_email: "B@x.io" },
      { author_email: "a@x.io" },
      { author_email: "b@x.io" },
      { author_email: null },
      {},
    ];
    expect(authorsOf(hits)).toEqual(["b@x.io", "a@x.io"]);
  });
});

describe("the marked words of a snippet", () => {
  it("gives React segments for the terms, with no case", () => {
    expect(highlight("We REFUND within thirty days", "refund thirty")).toEqual([
      { text: "We ", match: false },
      { text: "REFUND", match: true },
      { text: " within ", match: false },
      { text: "thirty", match: true },
      { text: " days", match: false },
    ]);
  });

  it("escapes a special character, so the query is no pattern", () => {
    expect(highlight("a+b and (draft) and a.b", "a+b (draft)").filter((p) => p.match).map((p) => p.text)).toEqual([
      "a+b",
      "(draft)",
    ]);
    // A dot does not match any character.
    expect(highlight("axb", "a.b").some((p) => p.match)).toBe(false);
    // A broken pattern throws nothing.
    expect(() => highlight("text [x", "[x \\ *")).not.toThrow();
  });

  it("returns text and never markup", () => {
    const parts = highlight("<b>bold</b> bold", "bold");
    expect(parts.map((p) => p.text).join("")).toBe("<b>bold</b> bold");
    expect(parts.filter((p) => p.match).map((p) => p.text)).toEqual(["bold", "bold"]);
  });
});
