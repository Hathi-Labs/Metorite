/**
 * The page chrome (WS-61 KB-2d, `knowledge_base.md` §21.7). Fence for the
 * pure parts: the breadcrumbs and their fold, the outline and its rule of
 * two headings, the heading anchors, the counts, the byline and the
 * contributors, the page menu (a reader sees no Move, Archive or Restore),
 * the Move form, the Markdown file, the Full width switch, and the two API
 * calls that KB-2d adds.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { archivedPagesKey, listPages, restorePage } from "./api";
import { foldCrumbs, parentChain, type Crumb } from "./breadcrumbs";
import { bylineOf, contributorsOf } from "./byline";
import { parseFullWidth } from "./fullWidth";
import { archivedHref, headingHref } from "./links";
import { TOP_LEVEL, hasChildren, moveRequest, parentChoices } from "./movePlan";
import { activeHeadingIndex, formatCount, showsOutline, slugOf, textStats, withSlugs } from "./outline";
import { PAGE_MENU, markdownFileName, pageMarkdown, pageMenuItems, type PageMenuState } from "./pageMenu";
import type { PageSummary, PageVersion } from "./types";

function summary(id: string, parent: string | null, title = id, extra: Partial<PageSummary> = {}): PageSummary {
  return {
    id,
    space_id: "s1",
    parent_id: parent,
    position: 0,
    title,
    state: "published",
    author_email: "a@x.com",
    reviewers: [],
    review_at: null,
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: null,
    created_at: null,
    ...extra,
  };
}

const crumb = (id: string): Crumb => ({ id, title: id, href: `/${id}`, kind: "page" });

describe("the breadcrumbs", () => {
  const tree = [summary("h", null), summary("p", "h"), summary("t", "p"), summary("other", null)];

  it("walks the parents from the root down", () => {
    expect(parentChain(tree, "t").map((p) => p.id)).toEqual(["h", "p", "t"]);
    expect(parentChain(tree, null)).toEqual([]);
  });

  it("stops at a parent the member cannot see, and at a loop", () => {
    expect(parentChain(tree, "hidden")).toEqual([]);
    const loop = [summary("a", "b"), summary("b", "a")];
    expect(parentChain(loop, "a").map((p) => p.id)).toEqual(["b", "a"]);
  });

  it("shows a trail of three in full on a wide screen", () => {
    const trail = ["space", "p", "page"].map(crumb);
    expect(foldCrumbs(trail, false)).toEqual({ head: trail, folded: [], tail: [] });
  });

  it("folds the middle of a longer trail, and keeps the first and the last", () => {
    const trail = ["space", "h", "p", "t", "page"].map(crumb);
    const folded = foldCrumbs(trail, false);
    expect(folded.head.map((c) => c.id)).toEqual(["space"]);
    expect(folded.folded.map((c) => c.id)).toEqual(["h", "p", "t"]);
    expect(folded.tail.map((c) => c.id)).toEqual(["page"]);
  });

  it("puts every crumb in the list on a phone", () => {
    const trail = ["space", "page"].map(crumb);
    expect(foldCrumbs(trail, true)).toEqual({ head: [], folded: trail, tail: [] });
  });
});

describe("the outline", () => {
  it("hides on a page with fewer than two headings", () => {
    expect(showsOutline([])).toBe(false);
    expect(showsOutline([{}])).toBe(false);
    expect(showsOutline([{}, {}])).toBe(true);
  });

  it("gives each heading an anchor, unique on the page", () => {
    expect(slugOf("How to ask?")).toBe("how-to-ask");
    expect(slugOf("Café policy")).toBe("cafe-policy");
    expect(slugOf("!!!")).toBe("section");
    const slugs = withSlugs([
      { level: 1, text: "Scope" },
      { level: 2, text: "Scope" },
      { level: 2, text: "Scope 2" },
      { level: 2, text: "Scope" },
    ]).map((h) => h.slug);
    expect(slugs).toEqual(["scope", "scope-2", "scope-2-2", "scope-3"]);
    expect(new Set(slugs).size).toBe(slugs.length);
  });

  it("marks the last heading above the line, and the first above them all", () => {
    const tops = [100, 500, 900];
    expect(activeHeadingIndex([], 0)).toBe(-1);
    expect(activeHeadingIndex(tops, 50)).toBe(0);
    expect(activeHeadingIndex(tops, 100)).toBe(0);
    expect(activeHeadingIndex(tops, 600)).toBe(1);
    expect(activeHeadingIndex(tops, 5000)).toBe(2);
  });

  it("marks the last heading in view at the end of the scroll", () => {
    expect(activeHeadingIndex([-400, 200, 450], 120, { atEnd: true, viewBottom: 600 })).toBe(2);
    expect(activeHeadingIndex([-400, 200, 450], 120, { atEnd: false, viewBottom: 600 })).toBe(0);
  });

  it("counts the words and the characters of the text", () => {
    expect(textStats([])).toEqual({ words: 0, characters: 0 });
    expect(textStats(["Scope", "We  refund\nwithin 30 days.", "  "])).toEqual({
      words: 6,
      characters: "Scope We refund within 30 days.".length,
    });
    expect(formatCount(1204)).toBe("1,204");
  });
});

describe("the byline and the contributors", () => {
  const v = (version: number, author: string, reason = "edit", at: string | null = null): PageVersion => ({
    version,
    title: "T",
    author_email: author,
    reason,
    via: "web",
    created_at: at,
  });

  it("names the newest version's author and time", () => {
    expect(bylineOf([])).toBeNull();
    expect(bylineOf([v(3, "m@x.com", "edit", "2026-10-19T10:00:00Z"), v(1, "a@x.com", "create")])).toEqual({
      verb: "Edited",
      email: "m@x.com",
      at: "2026-10-19T10:00:00Z",
    });
    expect(bylineOf([v(1, "a@x.com", "create")])?.verb).toBe("Created");
  });

  it("lists each person once, the most recent first, with their count", () => {
    const people = contributorsOf([
      v(4, "m@x.com", "edit", "t4"),
      v(3, "r@x.com", "edit", "t3"),
      v(2, "m@x.com", "edit", "t2"),
      v(1, "a@x.com", "create", "t1"),
    ]);
    expect(people).toEqual([
      { email: "m@x.com", versions: 2, lastAt: "t4" },
      { email: "r@x.com", versions: 1, lastAt: "t3" },
      { email: "a@x.com", versions: 1, lastAt: "t1" },
    ]);
  });
});

describe("the page menu", () => {
  const state = (over: Partial<PageMenuState> = {}): PageMenuState => ({
    canWrite: false,
    canManage: false,
    archived: false,
    editing: false,
    fullWidth: false,
    ...over,
  });
  const ids = (s: PageMenuState) =>
    pageMenuItems(s).flatMap((row) => (row.kind === "entry" ? [row.entry.id] : ["|"]));

  it("is one array of entries, each with a unique id", () => {
    const all = PAGE_MENU.map((e) => e.id);
    expect(new Set(all).size).toBe(all.length);
    expect(all).not.toContain("history");
  });

  it("gives a reader no Move, Archive or Restore", () => {
    for (const archived of [false, true]) {
      const shown = ids(state({ archived }));
      expect(shown).not.toContain("move");
      expect(shown).not.toContain("archive");
      expect(shown).not.toContain("restore");
    }
    expect(ids(state())).toEqual(["copy-link", "copy-markdown", "download-md", "print", "|", "full-width"]);
  });

  it("gives a writer Move, and a manager Archive, with a line between groups", () => {
    expect(ids(state({ canWrite: true }))).toEqual([
      "copy-link", "copy-markdown", "download-md", "print", "|", "full-width", "|", "move",
    ]);
    expect(ids(state({ canWrite: true, canManage: true }))).toEqual([
      "copy-link", "copy-markdown", "download-md", "print", "|", "full-width", "|", "move", "|", "archive",
    ]);
  });

  it("gives a manager Restore on an archived page, and no Move or Archive", () => {
    const shown = ids(state({ canWrite: true, canManage: true, archived: true }));
    expect(shown).toContain("restore");
    expect(shown).not.toContain("move");
    expect(shown).not.toContain("archive");
  });

  it("hides Print, Move and Archive during an edit", () => {
    const shown = ids(state({ canWrite: true, canManage: true, editing: true }));
    expect(shown).not.toContain("print");
    expect(shown).not.toContain("move");
    expect(shown).not.toContain("archive");
  });

  it("marks Full width when it is on", () => {
    const row = pageMenuItems(state({ fullWidth: true })).find(
      (r) => r.kind === "entry" && r.entry.id === "full-width",
    );
    expect(row).toMatchObject({ checked: true });
  });

  it("writes the page as one Markdown file with a safe name", () => {
    expect(pageMarkdown({ title: "Refunds", body_md: "## Scope\n\nText.\n" })).toBe("# Refunds\n\n## Scope\n\nText.\n");
    expect(pageMarkdown({ title: "Empty", body_md: "  " })).toBe("# Empty\n");
    expect(markdownFileName('Q3: plan / "draft"?')).toBe("Q3 plan draft.md");
    expect(markdownFileName("  ")).toBe("Untitled page.md");
  });
});

describe("the Move form", () => {
  const pages = [summary("h", null), summary("p", "h"), summary("t", "p"), summary("x", null)];

  it("offers no parent inside the page's own subtree", () => {
    const choices = parentChoices(pages, "p").map((c) => c.id);
    expect(choices).toEqual(["h", "x"]);
    expect(parentChoices(pages, "x").map((c) => [c.id, c.depth])).toEqual([["h", 0], ["p", 1], ["t", 2]]);
  });

  it("knows a page with sub-pages", () => {
    expect(hasChildren(pages, "p")).toBe(true);
    expect(hasChildren(pages, "t")).toBe(false);
  });

  it("sends nothing when the place is the same, and the change otherwise", () => {
    const page = { space_id: "s1", parent_id: "h" };
    expect(moveRequest(page, "s1", "h")).toBeNull();
    expect(moveRequest(page, "s1", TOP_LEVEL)).toEqual({ parent_id: null });
    expect(moveRequest(page, "s1", "x")).toEqual({ parent_id: "x" });
    expect(moveRequest(page, "s2", TOP_LEVEL)).toEqual({ space_id: "s2", parent_id: null });
    expect(moveRequest({ space_id: "s1", parent_id: null }, "s1", TOP_LEVEL)).toBeNull();
  });
});

describe("Full width and the addresses", () => {
  it("reads only '1' as on", () => {
    expect(parseFullWidth("1")).toBe(true);
    expect(parseFullWidth(null)).toBe(false);
    expect(parseFullWidth("true")).toBe(false);
  });

  it("builds the Archived view and a heading address", () => {
    expect(archivedHref("s 1")).toBe("/knowledge?space=s%201&view=archived");
    expect(headingHref("p1", "how-to-ask")).toBe("/knowledge/pages/p1#how-to-ask");
  });
});

describe("the two calls of KB-2d", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stubFetch(body: unknown): Array<{ url: string; init?: RequestInit }> {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    return calls;
  }

  it("asks for the archived pages of a space with state=archived", async () => {
    const calls = stubFetch({ pages: [] });
    await listPages("s1", { state: "archived" });
    await listPages("s1");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/knowledge/pages?space=s1&state=archived",
      "/api/knowledge/pages?space=s1",
    ]);
    expect(archivedPagesKey("s1")).toBe("knowledge/pages?space=s1&state=archived");
  });

  it("restores through the restore route", async () => {
    const calls = stubFetch({ id: "p1", version: 5 });
    await restorePage("p1");
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/api/knowledge/pages/p1/restore");
    expect(calls[0].init?.method).toBe("POST");
  });
});
