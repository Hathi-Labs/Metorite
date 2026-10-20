// ── The outline and the details of a page (WS-61 KB-2d, §21.4 and §21.7) ───
//
// Pure. The view reads the headings that the editor drew, and these
// functions decide what the outline shows, which heading is in view, the
// anchor of each heading, and the word and character counts.

/** One heading of the body, as the editor drew it. */
export interface OutlineHeading {
  /** The stored level: `#` is 1. */
  level: number;
  text: string;
  /** The anchor of the heading in the address, unique on the page. */
  slug: string;
}

/** The fewest headings that make an outline worth showing (§21.4). */
export const MIN_OUTLINE_HEADINGS = 2;

/** True when the page has enough headings for an outline. */
export function showsOutline(headings: readonly unknown[]): boolean {
  return headings.length >= MIN_OUTLINE_HEADINGS;
}

/**
 * The anchor of a heading's text: lower case, letters and digits joined by
 * `-`. A heading with no letter or digit gets `section`.
 */
export function slugOf(text: string): string {
  const slug = text
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return slug || "section";
}

/**
 * The headings with a unique anchor each. A second heading with the same
 * text gets `-2`, a third gets `-3`, in page order.
 */
export function withSlugs(headings: ReadonlyArray<{ level: number; text: string }>): OutlineHeading[] {
  const taken = new Set<string>();
  return headings.map(({ level, text }) => {
    const base = slugOf(text);
    let slug = base;
    for (let n = 2; taken.has(slug); n++) slug = `${base}-${n}`;
    taken.add(slug);
    return { level, text: text.trim(), slug };
  });
}

/**
 * The heading in view: the last one whose top is at or above `line`.
 *
 * `tops` are the tops of the headings, in the scroller's own coordinates,
 * in page order. Above the first heading, the first one is in view. At the
 * end of the scroll, the last heading that shows is in view, so a short last
 * section can still be marked.
 */
export function activeHeadingIndex(
  tops: readonly number[],
  line: number,
  end?: { atEnd: boolean; viewBottom: number },
): number {
  if (!tops.length) return -1;
  if (end?.atEnd) {
    for (let i = tops.length - 1; i >= 0; i--) if (tops[i] < end.viewBottom) return i;
  }
  let at = 0;
  for (let i = 0; i < tops.length; i++) {
    if (tops[i] <= line) at = i;
    else break;
  }
  return at;
}

/** The counts that Details shows. */
export interface TextStats {
  words: number;
  characters: number;
}

/**
 * The words and the characters of the text of a page. `blocks` are the texts
 * of its blocks, in order. They join with one space, as the editor reads a
 * document, and a word is a run of characters with no white space.
 */
export function textStats(blocks: readonly string[]): TextStats {
  const text = blocks
    .map((b) => b.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join(" ");
  return {
    words: text ? text.split(" ").length : 0,
    characters: text.length,
  };
}

/** A count with a thousands mark: "1,204". */
export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}
