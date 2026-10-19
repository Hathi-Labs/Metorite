/**
 * The page icon, the star, the home lists and the space switcher (WS-61
 * KB-2f, `knowledge_base.md` §21.7). Fence for the pure parts of
 * `EmojiPicker.tsx`, `PageIcon.tsx`, `Favorites.tsx`, `KnowledgeHome.tsx`,
 * `SpaceSwitcher.tsx` and `lib/homeApi.ts`, and for the requests of the
 * home client. The emoji draws as text, never as HTML.
 */
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildGroups } from "../lib/emoji";
import {
  favoriteIds,
  homeQuery,
  listFavorites,
  listRecent,
  setPageIcon,
  starPage,
  unstarPage,
  type HomeList,
  type HomePage,
} from "../lib/homeApi";
import type { Space } from "../lib/types";
import { EMOJI_COLUMNS, pickerSections, stepEmoji } from "./EmojiPicker";
import { favoriteMenuItem, starLabel, withStar } from "./Favorites";
import { HOME_TABS, mergePages } from "./KnowledgeHome";
import { PageGlyph, iconOf } from "./PageIcon";
import { ALL_SPACES, switcherOptions } from "./SpaceSwitcher";

type Call = { url: string; init?: RequestInit };

function stubFetch(status: number, body: unknown): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return status === 204
      ? new Response(null, { status })
      : new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

function page(id: string, extra: Partial<HomePage> = {}): HomePage {
  return {
    id,
    space_id: "s",
    parent_id: null,
    position: 0,
    title: `Page ${id}`,
    icon: null,
    state: "published",
    author_email: "a@x.test",
    reviewers: [],
    review_at: null,
    source_ref: null,
    published_at: null,
    archived_at: null,
    history_from: null,
    archived_from_state: null,
    updated_at: null,
    created_at: null,
    space_name: "Handbook",
    ...extra,
  };
}

describe("the home client", () => {
  it("asks for a page of recent pages, the member's own, and the next page", async () => {
    const calls = stubFetch(200, { pages: [], next_cursor: null });
    await listRecent();
    await listRecent({ mine: true, cursor: "abc" });
    await listFavorites();
    expect(calls.map((c) => c.url)).toEqual([
      "/api/knowledge/recent?limit=20",
      "/api/knowledge/recent?limit=20&mine=true&cursor=abc",
      "/api/knowledge/favorites?limit=500",
    ]);
    expect(homeQuery({})).toBe("");
  });

  it("stars, unstars and sets the icon with the member from the session", async () => {
    const calls = stubFetch(204, null);
    await unstarPage("p/1");
    expect(calls[0]).toMatchObject({ url: "/api/knowledge/pages/p%2F1/favorite", init: { method: "DELETE" } });
    const more = stubFetch(200, { page_id: "p1", favorite: true });
    await starPage("p1");
    await setPageIcon("p1", "📘");
    await setPageIcon("p1", null);
    expect(more.map((c) => [c.url, c.init?.method, c.init?.body])).toEqual([
      ["/api/knowledge/pages/p1/favorite", "POST", undefined],
      ["/api/knowledge/pages/p1", "PATCH", JSON.stringify({ icon: "📘" })],
      ["/api/knowledge/pages/p1", "PATCH", JSON.stringify({ icon: "" })],
    ]);
  });

  it("reads the ids of the favourites", () => {
    expect([...favoriteIds({ pages: [page("a"), page("b")], next_cursor: null })]).toEqual(["a", "b"]);
    expect(favoriteIds(undefined).size).toBe(0);
  });
});

describe("the star", () => {
  it("says the act in words", () => {
    expect(starLabel(false)).toBe("Add to favourites");
    expect(starLabel(true)).toBe("Remove from favourites");
    const item = favoriteMenuItem(true, () => undefined);
    expect(item).toMatchObject({ kind: "item", label: "Remove from favourites" });
  });

  it("draws a star or an unstar at once, newest first, with no page twice", () => {
    const list: HomeList = { pages: [page("a"), page("b")], next_cursor: null };
    expect(withStar(list, page("c"), true).pages.map((p) => p.id)).toEqual(["c", "a", "b"]);
    expect(withStar(list, page("a"), true).pages.map((p) => p.id)).toEqual(["a", "b"]);
    expect(withStar(list, page("a"), false).pages.map((p) => p.id)).toEqual(["b"]);
    expect(withStar(undefined, page("a"), true).pages.map((p) => p.id)).toEqual(["a"]);
  });
});

describe("the page icon", () => {
  it("is the trimmed emoji, or none", () => {
    expect(iconOf({ icon: " 📘 " })).toBe("📘");
    expect(iconOf({ icon: "" })).toBeNull();
    expect(iconOf({})).toBeNull();
  });

  it("draws the emoji as text, and the state icon with no emoji", () => {
    const withIcon = renderToStaticMarkup(createElement(PageGlyph, { page: { icon: "<b>📘</b>" } }));
    expect(withIcon).toContain("&lt;b&gt;📘&lt;/b&gt;");
    expect(withIcon).toContain("data-page-icon");
    const without = renderToStaticMarkup(createElement(PageGlyph, { page: { icon: null, state: "draft" } }));
    expect(without).not.toContain("data-page-icon");
    expect(without).toContain("<svg");
  });
});

describe("the emoji picker", () => {
  const groups = buildGroups(
    [
      { name: "A", slug: "smileys_emotion", emojis: [{ emoji: "😀", name: "grinning face", slug: "g" }] },
      { name: "B", slug: "objects", emojis: [{ emoji: "📘", name: "blue book", slug: "b" }] },
    ],
    {},
  );

  it("shows each group, or one list of matches for a search", () => {
    expect(pickerSections(groups, "").map((s) => s.label)).toEqual(["Smileys", "Objects"]);
    const found = pickerSections(groups, "book");
    expect(found.map((s) => [s.id, s.emojis.map((e) => e.char)])).toEqual([["matches", ["📘"]]]);
  });

  it("moves by one, and by a row of eight, with no wrap at the ends", () => {
    expect(EMOJI_COLUMNS).toBe(8);
    expect(stepEmoji(0, "ArrowRight", 20)).toBe(1);
    expect(stepEmoji(0, "ArrowLeft", 20)).toBe(0);
    expect(stepEmoji(3, "ArrowDown", 20)).toBe(11);
    expect(stepEmoji(15, "ArrowDown", 20)).toBe(19);
    expect(stepEmoji(3, "ArrowUp", 20)).toBe(0);
    expect(stepEmoji(5, "End", 20)).toBe(19);
    expect(stepEmoji(5, "Home", 20)).toBe(0);
    expect(stepEmoji(5, "a", 20)).toBeNull();
    expect(stepEmoji(0, "ArrowDown", 0)).toBeNull();
  });
});

describe("the home lists", () => {
  it("has the three tabs of Docmost's home", () => {
    expect(HOME_TABS.map((t) => t.label)).toEqual(["Recently updated", "Favourites", "Created by me"]);
  });

  it("adds the pages of Load more after the first page, each once", () => {
    const merged = mergePages([page("a"), page("b")], [page("b"), page("c")]);
    expect(merged.map((p) => p.id)).toEqual(["a", "b", "c"]);
  });
});

describe("the space switcher", () => {
  it("offers the home, then each space with its page count", () => {
    const spaces = [
      { id: "s1", name: "Handbook", page_count: 3 },
      { id: "s2", name: "My notes", page_count: 1 },
    ] as Space[];
    expect(switcherOptions(spaces)).toEqual([
      { value: ALL_SPACES, label: "All spaces", hint: "Home" },
      { value: "s1", label: "Handbook", hint: "3 pages" },
      { value: "s2", label: "My notes", hint: "1 page" },
    ]);
  });
});
