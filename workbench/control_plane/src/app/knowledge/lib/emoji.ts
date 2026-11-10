// ── The emoji data of Knowledge (WS-61 KB-2f, `knowledge_base.md` §21.7) ────
//
// One source of emoji for the app: the page icon picker (KB-2f) and the `:`
// insert of the editor (KB-2i). Two MIT packages hold DATA only, no UI:
// `unicode-emoji-json` gives each emoji its name and its group, and
// `emojilib` gives the words a search matches ("happy" finds 😀). Both load
// on the first use, in one lazy chunk, so the first paint pays nothing.
//
// The contract with KB-2i is `searchEmoji(query, limit)`. It answers at once
// from the data in memory. Before the data is in memory it starts the load
// and answers `[]`. A caller that must wait calls `loadEmoji()` first, and
// then asks again.

/** One emoji: the character, its name, its group and the words it matches. */
export interface EmojiEntry {
  char: string;
  name: string;
  group: string;
  keywords: readonly string[];
}

/** One group of the picker: a tab with an icon and its emoji, in order. */
export interface EmojiGroup {
  id: string;
  label: string;
  /** A Lucide name, drawn through `<Icon>`. */
  icon: string;
  emojis: EmojiEntry[];
}

/** What `searchEmoji` returns: the contract with KB-2i. */
export interface EmojiMatch {
  char: string;
  name: string;
}

/** The label and the icon of each group of `unicode-emoji-json`, by its slug. */
export const GROUP_LOOK: Record<string, { label: string; icon: string }> = {
  smileys_emotion: { label: "Smileys", icon: "Smile" },
  people_body: { label: "People", icon: "Hand" },
  animals_nature: { label: "Nature", icon: "Leaf" },
  food_drink: { label: "Food", icon: "Apple" },
  travel_places: { label: "Travel", icon: "Plane" },
  activities: { label: "Activities", icon: "Trophy" },
  objects: { label: "Objects", icon: "Lightbulb" },
  symbols: { label: "Symbols", icon: "Hash" },
  flags: { label: "Flags", icon: "Flag" },
};

/** The shape of `unicode-emoji-json/data-by-group.json`. */
interface RawGroup {
  name: string;
  slug: string;
  emojis: Array<{ emoji: string; name: string; slug: string }>;
}

/**
 * The groups, built from the two data files. Pure, so a test hands it a
 * small set. An emoji with no keyword entry keeps the words of its name.
 */
export function buildGroups(raw: readonly RawGroup[], words: Record<string, readonly string[]>): EmojiGroup[] {
  return raw.map((group) => {
    const look = GROUP_LOOK[group.slug] ?? { label: group.name, icon: "Smile" };
    return {
      id: group.slug,
      label: look.label,
      icon: look.icon,
      emojis: group.emojis.map((e) => ({
        char: e.emoji,
        name: e.name,
        group: group.slug,
        keywords: (words[e.emoji] ?? []).map((w) => w.replace(/_/g, " ").toLowerCase()),
      })),
    };
  });
}

/** A query as the search reads it: lower case, no `:`, `_` as a space. */
export function normalizeEmojiQuery(raw: string): string {
  return raw.trim().replace(/^:+|:+$/g, "").replace(/_/g, " ").replace(/\s+/g, " ").toLowerCase();
}

/**
 * The rank of one emoji for a query, or `null` when it does not match.
 * Lower is better: the whole name, then the start of the name, then the
 * start of a word of the name, then a keyword, then any part of the name.
 */
function rankOf(entry: EmojiEntry, q: string): number | null {
  const name = entry.name.toLowerCase();
  if (name === q) return 0;
  if (name.startsWith(q)) return 1;
  if (name.split(/[\s:-]+/).some((w) => w.startsWith(q))) return 2;
  if (entry.keywords.some((k) => k === q)) return 3;
  if (entry.keywords.some((k) => k.startsWith(q))) return 4;
  if (name.includes(q)) return 5;
  return null;
}

/**
 * The emoji that match *query*, best first, at most *limit*. An empty query
 * matches nothing. A tie keeps the order of the data, which is the order of
 * the Unicode standard. Pure.
 */
export function rankEmoji(groups: readonly EmojiGroup[], query: string, limit = 24): EmojiEntry[] {
  const q = normalizeEmojiQuery(query);
  if (!q || limit <= 0) return [];
  const scored: Array<{ entry: EmojiEntry; rank: number; order: number }> = [];
  let order = 0;
  for (const group of groups) {
    for (const entry of group.emojis) {
      const rank = rankOf(entry, q);
      if (rank !== null) scored.push({ entry, rank, order });
      order += 1;
    }
  }
  scored.sort((a, b) => a.rank - b.rank || a.order - b.order);
  return scored.slice(0, limit).map((s) => s.entry);
}

let loaded: EmojiGroup[] | null = null;
let loading: Promise<EmojiGroup[]> | null = null;

/** The data, once it is in memory. `null` before `loadEmoji` resolves. */
export function emojiGroups(): EmojiGroup[] | null {
  return loaded;
}

/** Load the data once. Each later call gets the same promise. */
export function loadEmoji(): Promise<EmojiGroup[]> {
  if (loaded) return Promise.resolve(loaded);
  if (!loading) {
    loading = Promise.all([
      import("unicode-emoji-json/data-by-group.json"),
      import("emojilib"),
    ])
      .then(([groups, words]) => {
        loaded = buildGroups(
          (groups.default ?? groups) as unknown as RawGroup[],
          (words.default ?? words) as unknown as Record<string, string[]>,
        );
        return loaded;
      })
      .catch((err: unknown) => {
        // A failed load may try again on the next use.
        loading = null;
        throw err;
      });
  }
  return loading;
}

/**
 * The contract with KB-2i: the emoji that match *query*, best first.
 *
 * It answers from the data in memory. Before the data is there, it starts
 * the load and answers `[]`, so a caller that types again gets the matches.
 */
export function searchEmoji(query: string, limit = 24): EmojiMatch[] {
  if (!loaded) {
    void loadEmoji().catch(() => undefined);
    return [];
  }
  return rankEmoji(loaded, query, limit).map((e) => ({ char: e.char, name: e.name }));
}
