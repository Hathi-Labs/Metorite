/**
 * The emoji data of Knowledge (WS-61 KB-2f). Fence for `emoji.ts`: the
 * contract with KB-2i (`searchEmoji(query, limit)`), the rank of a match,
 * and that every emoji of the data fits the 16 characters of
 * `kb_pages.icon` (migration 287).
 */
import { describe, expect, it } from "vitest";

import {
  GROUP_LOOK,
  buildGroups,
  emojiGroups,
  loadEmoji,
  normalizeEmojiQuery,
  rankEmoji,
  searchEmoji,
} from "./emoji";

const SMALL = buildGroups(
  [
    {
      name: "Smileys & Emotion",
      slug: "smileys_emotion",
      emojis: [
        { emoji: "😀", name: "grinning face", slug: "grinning_face" },
        { emoji: "😄", name: "grinning face with smiling eyes", slug: "x" },
        { emoji: "🙂", name: "slightly smiling face", slug: "y" },
      ],
    },
    {
      name: "Objects",
      slug: "objects",
      emojis: [{ emoji: "📘", name: "blue book", slug: "blue_book" }],
    },
  ],
  { "😀": ["grinning_face", "happy", "smile"], "📘": ["blue_book", "manual"] },
);

describe("emoji data", () => {
  it("builds each group with its label, its icon and the keywords", () => {
    expect(SMALL.map((g) => [g.id, g.label, g.icon])).toEqual([
      ["smileys_emotion", "Smileys", "Smile"],
      ["objects", "Objects", "Lightbulb"],
    ]);
    expect(SMALL[0].emojis[0].keywords).toEqual(["grinning face", "happy", "smile"]);
    expect(SMALL[0].emojis[2].keywords).toEqual([]);
  });

  it("reads a query as the search does", () => {
    expect(normalizeEmojiQuery("  :Blue_Book: ")).toBe("blue book");
    expect(normalizeEmojiQuery("::")).toBe("");
  });

  it("ranks the whole name, then a start, then a keyword, then a part", () => {
    expect(rankEmoji(SMALL, "blue book").map((e) => e.char)).toEqual(["📘"]);
    expect(rankEmoji(SMALL, "grin").map((e) => e.char)).toEqual(["😀", "😄"]);
    expect(rankEmoji(SMALL, "smiling").map((e) => e.char)).toEqual(["😄", "🙂"]);
    expect(rankEmoji(SMALL, "manual").map((e) => e.char)).toEqual(["📘"]);
    expect(rankEmoji(SMALL, "happy").map((e) => e.char)).toEqual(["😀"]);
    expect(rankEmoji(SMALL, "")).toEqual([]);
    expect(rankEmoji(SMALL, "grin", 1)).toHaveLength(1);
  });
});

describe("the contract with KB-2i", () => {
  it("answers [] before the data loads, and the matches after", async () => {
    // The data is not in memory before the first load in this process.
    if (!emojiGroups()) expect(searchEmoji("smile")).toEqual([]);
    await loadEmoji();
    const found = searchEmoji("thumbs up", 5);
    expect(found[0]).toEqual({ char: "👍", name: "thumbs up" });
    expect(found.length).toBeLessThanOrEqual(5);
    expect(searchEmoji(":rocket:", 1)).toEqual([{ char: "🚀", name: "rocket" }]);
    // A keyword of emojilib finds an emoji its name does not hold.
    expect(searchEmoji("happy", 50).map((m) => m.char)).toContain("😀");
  });

  it("loads every group of the data, each with our label", async () => {
    const groups = await loadEmoji();
    expect(groups.map((g) => g.id)).toEqual(Object.keys(GROUP_LOOK));
    expect(groups.reduce((n, g) => n + g.emojis.length, 0)).toBeGreaterThan(1500);
  });

  it("keeps every emoji within the 16 characters of kb_pages.icon", async () => {
    const groups = await loadEmoji();
    const longest = Math.max(...groups.flatMap((g) => g.emojis.map((e) => [...e.char].length)));
    expect(longest).toBeLessThanOrEqual(16);
  });
});
