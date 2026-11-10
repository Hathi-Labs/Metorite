import { describe, expect, it } from "vitest";

import { relativeLink, resolveRelative, rewriteLinksToFiles, rewriteLinksToIds } from "./links";

describe("resolveRelative", () => {
  it("resolves against the folder of the file", () => {
    expect(resolveRelative("a/b/c.md", "d.md")).toBe("a/b/d.md");
    expect(resolveRelative("a/b/c.md", "../x/y.md")).toBe("a/x/y.md");
    expect(resolveRelative("c.md", "./d.md")).toBe("d.md");
  });
  it("gives null for a path above the root", () => {
    expect(resolveRelative("c.md", "../d.md")).toBeNull();
  });
});

describe("rewriting links to page addresses (import)", () => {
  const ids: Record<string, string> = { "Parent abc/Child def.md": "id-child", "Other.md": "id-other" };
  const idOf = (p: string) => ids[p];

  it("rewrites a Notion style link, URL-encoded", () => {
    const out = rewriteLinksToIds("See [the child](Parent%20abc/Child%20def.md) now.", "Home.md", idOf);
    expect(out).toEqual({ md: "See [the child](/knowledge/pages/id-child) now.", count: 1 });
  });
  it("resolves from the folder of the page", () => {
    const out = rewriteLinksToIds("[up](../Other.md#part)", "Parent abc/Child def.md", idOf);
    expect(out.md).toBe("[up](/knowledge/pages/id-other#part)");
  });
  it("leaves external, absolute, anchor, unknown and image links", () => {
    const md = "[a](https://x.example/Other.md) [b](/knowledge/pages/z) [c](#top) [d](Missing.md) ![e](Other.md) [f](mailto:a@b.c)";
    expect(rewriteLinksToIds(md, "Home.md", idOf)).toEqual({ md, count: 0 });
  });
  it("keeps the link title and skips code", () => {
    const out = rewriteLinksToIds('[o](Other.md "Hi") `[o](Other.md)`', "Home.md", idOf);
    expect(out.md).toBe('[o](/knowledge/pages/id-other "Hi") `[o](Other.md)`');
  });
});

describe("rewriting links to files (export)", () => {
  const paths = new Map([
    ["p1", "Home.md"],
    ["p2", "Home/Sub page.md"],
    ["p3", "Other (draft).md"],
  ]);
  const pathOf = (id: string) => paths.get(id);

  it("makes a relative, encoded link", () => {
    expect(rewriteLinksToFiles("[s](/knowledge/pages/p2)", "Home.md", pathOf).md).toBe("[s](Home/Sub%20page.md)");
    expect(rewriteLinksToFiles("[h](/knowledge/pages/p1#x)", "Home/Sub page.md", pathOf).md).toBe("[h](../Home.md#x)");
  });
  it("encodes parentheses so the link holds", () => {
    expect(rewriteLinksToFiles("[o](/knowledge/pages/p3)", "Home.md", pathOf).md).toBe("[o](Other%20%28draft%29.md)");
  });
  it("leaves a page outside the export as an address", () => {
    const md = "[x](/knowledge/pages/zzz) [y](https://e.example)";
    expect(rewriteLinksToFiles(md, "Home.md", pathOf)).toEqual({ md, count: 0 });
  });
});

describe("both directions agree", () => {
  it("round-trips a link through a file path and back", () => {
    const pathOf = (id: string) => ({ a: "A/One (x).md", b: "B/Two.md" })[id as "a" | "b"];
    const ids = new Map([["A/One (x).md", "a"], ["B/Two.md", "b"]]);
    const out = rewriteLinksToFiles("[t](/knowledge/pages/b)", "A/One (x).md", pathOf).md;
    expect(out).toBe("[t](../B/Two.md)");
    expect(rewriteLinksToIds(out, "A/One (x).md", (p) => ids.get(p)).md).toBe("[t](/knowledge/pages/b)");
    expect(relativeLink("A/One (x).md", "A/One (x).md")).toBe("One%20%28x%29.md");
  });
});
