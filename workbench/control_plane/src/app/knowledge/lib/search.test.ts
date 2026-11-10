/**
 * The Search view's rules (WS-61 KB-3b). Fence for `search.ts` and for
 * `searchHref` in `links.ts`: the query rules, the grouping, the marks, the
 * address and the stale guard.
 */
import { describe, expect, it } from "vitest";

import { KNOWLEDGE_SEARCH, pageHref, searchHref } from "./links";
import {
  SEARCH_MAX,
  groupHits,
  highlight,
  highlightWords,
  isAbort,
  isCurrent,
  normalizeQuery,
  queryState,
  resultCount,
} from "./search";
import type { SearchHit } from "./types";

function hit(over: Partial<SearchHit> & Pick<SearchHit, "page_id" | "score">): SearchHit {
  return {
    space_id: "s-company",
    space_name: "Company",
    title: over.page_id,
    heading_path: "",
    text: "",
    updated_at: null,
    href: `/knowledge/pages/${over.page_id}`,
    ...over,
  };
}

describe("the query rules", () => {
  it("collapse white space as the gateway does", () => {
    expect(normalizeQuery("  refund \t  window\n")).toBe("refund window");
    expect(normalizeQuery("   ")).toBe("");
  });

  it("send nothing below 2 characters", () => {
    expect(queryState("")).toBe("empty");
    expect(queryState("   ")).toBe("empty");
    expect(queryState("r")).toBe("short");
    expect(queryState("  r  ")).toBe("short");
    expect(queryState("re")).toBe("ok");
  });

  it("count white space inside the words, not at the ends", () => {
    // "a b" is 3 characters after the collapse, so it goes.
    expect(queryState(" a   b ")).toBe("ok");
  });

  it("send nothing above 300 characters, which the gateway refuses", () => {
    expect(queryState("x".repeat(SEARCH_MAX))).toBe("ok");
    expect(queryState("x".repeat(SEARCH_MAX + 1))).toBe("long");
    // The ends do not count.
    expect(queryState(`  ${"x".repeat(SEARCH_MAX)}  `)).toBe("ok");
  });

  it("count code points, as Python's len does", () => {
    // One emoji is two UTF-16 units and one code point.
    expect(queryState("😀")).toBe("short");
  });
});

describe("the stale guard", () => {
  it("keeps an answer for the live words", () => {
    expect(isCurrent("refund", "refund")).toBe(true);
    // The gateway answers with the collapsed words.
    expect(isCurrent("refund window", "  refund   window ")).toBe(true);
  });

  it("drops an answer for older words", () => {
    // "ref" was asked first and answered last.
    expect(isCurrent("ref", "refund")).toBe(false);
    expect(isCurrent("refund", "")).toBe(false);
  });

  it("knows a cancelled request from a failure", () => {
    expect(isAbort(new DOMException("The user aborted a request.", "AbortError"))).toBe(true);
    expect(isAbort(Object.assign(new Error("x"), { name: "AbortError" }))).toBe(true);
    expect(isAbort(new Error("Knowledge answered 500"))).toBe(false);
    expect(isAbort(null)).toBe(false);
  });
});

describe("groupHits", () => {
  it("groups by space, and orders the spaces by their best score", () => {
    const groups = groupHits([
      hit({ page_id: "a", score: 0.02 }),
      hit({ page_id: "o", score: 0.05, space_id: "s-ops", space_name: "Operations" }),
      hit({ page_id: "b", score: 0.01 }),
    ]);
    expect(groups.map((g) => [g.spaceName, g.pages.map((p) => p.pageId)])).toEqual([
      ["Operations", ["o"]],
      ["Company", ["a", "b"]],
    ]);
  });

  it("shows a page once, with at most two snippets and their heading paths", () => {
    const groups = groupHits([
      hit({ page_id: "a", score: 0.03, heading_path: "Refunds > Window", text: "Thirty days." }),
      hit({ page_id: "a", score: 0.02, heading_path: "Refunds > Exceptions", text: "Custom prints." }),
      hit({ page_id: "a", score: 0.01, heading_path: "Refunds > Shipping", text: "We pay." }),
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].pages).toHaveLength(1);
    expect(groups[0].pages[0].snippets).toEqual([
      { headingPath: "Refunds > Window", text: "Thirty days." },
      { headingPath: "Refunds > Exceptions", text: "Custom prints." },
    ]);
  });

  it("drops the same snippet twice", () => {
    const groups = groupHits([
      hit({ page_id: "a", score: 0.03, heading_path: "A", text: "Same." }),
      hit({ page_id: "a", score: 0.02, heading_path: "A", text: "Same." }),
      hit({ page_id: "a", score: 0.01, heading_path: "B", text: "Other." }),
    ]);
    expect(groups[0].pages[0].snippets.map((s) => s.headingPath)).toEqual(["A", "B"]);
  });

  it("orders the pages in a space by their best hit, not by their first", () => {
    const groups = groupHits([
      hit({ page_id: "a", score: 0.02 }),
      hit({ page_id: "b", score: 0.01 }),
      hit({ page_id: "b", score: 0.04 }),
    ]);
    expect(groups[0].pages.map((p) => [p.pageId, p.best])).toEqual([
      ["b", 0.04],
      ["a", 0.02],
    ]);
    expect(groups[0].best).toBe(0.04);
  });

  it("keeps the gateway's order on a tie", () => {
    const groups = groupHits([
      hit({ page_id: "x", score: 0.02, space_id: "s1", space_name: "One" }),
      hit({ page_id: "y", score: 0.02, space_id: "s2", space_name: "Two" }),
    ]);
    expect(groups.map((g) => g.spaceName)).toEqual(["One", "Two"]);
  });

  it("gives no group for no hits, and counts the rest in words", () => {
    expect(groupHits([])).toEqual([]);
    const one = groupHits([hit({ page_id: "a", score: 1 })]);
    expect(resultCount(one)).toBe("1 page in 1 space");
    const more = groupHits([
      hit({ page_id: "a", score: 1 }),
      hit({ page_id: "a", score: 0.5 }),
      hit({ page_id: "b", score: 1 }),
      hit({ page_id: "o", score: 1, space_id: "s-ops", space_name: "Operations" }),
    ]);
    expect(resultCount(more)).toBe("3 pages in 2 spaces");
  });
});

describe("highlight", () => {
  const marked = (text: string, q: string) =>
    highlight(text, q)
      .filter((s) => s.match)
      .map((s) => s.text);

  it("marks each word of the query, with no case", () => {
    expect(marked("We refund within 30 days of the Window.", "refund window")).toEqual(["refund", "Window"]);
  });

  it("keeps every character of the text, in order", () => {
    const text = "Refund: a refund within the window.";
    expect(
      highlight(text, "refund window")
        .map((s) => s.text)
        .join(""),
    ).toBe(text);
  });

  it("matches a regex character as itself, and never throws", () => {
    expect(marked("Use a+b, not ab or aab.", "a+b")).toEqual(["a+b"]);
    expect(marked("The (draft) page.", "(draft)")).toEqual(["(draft)"]);
    expect(marked("Costs $5.00 [net].", "$5.00 [net]")).toEqual(["$5.00", "[net]"]);
    expect(() => highlight("x", "a|b ( [ \\ ^")).not.toThrow();
    expect(marked("a|b here", "a|b")).toEqual(["a|b"]);
  });

  it("prefers the longer word where two overlap", () => {
    expect(marked("refunds", "re refund")).toEqual(["refund"]);
  });

  it("marks no excluded word, no operator and no single letter", () => {
    expect(highlightWords('refund -draft or "a window"')).toEqual(["refund", "window"]);
    expect(marked("a refund, or a draft", "refund -draft or a")).toEqual(["refund"]);
  });

  it("returns the text unmarked for no words, and nothing for no text", () => {
    expect(highlight("Refund policy", "")).toEqual([{ text: "Refund policy", match: false }]);
    expect(highlight("", "refund")).toEqual([]);
  });

  it("returns text parts, never markup", () => {
    const parts = highlight("<b>refund</b> <script>", "refund");
    expect(parts).toEqual([
      { text: "<b>", match: false },
      { text: "refund", match: true },
      { text: "</b> <script>", match: false },
    ]);
  });
});

describe("searchHref", () => {
  const back = (href: string) => new URL(href, "https://app.example").searchParams.get("q");

  it("is the bare view for no words", () => {
    expect(searchHref()).toBe(KNOWLEDGE_SEARCH);
    expect(searchHref("")).toBe("/knowledge/search");
    expect(searchHref("   ")).toBe("/knowledge/search");
  });

  it("round-trips the words through the address", () => {
    for (const q of ["refund", "refund window", "a+b & c=d", "50% off?", "ZOHO-4471", "#tag / path", "café 😀"]) {
      const href = searchHref(q);
      expect(href.startsWith("/knowledge/search?q=")).toBe(true);
      expect(back(href)).toBe(q);
    }
  });

  it("puts a hit on the page's own address", () => {
    expect(pageHref("4f0c")).toBe("/knowledge/pages/4f0c");
  });
});
