/**
 * The history of a page (WS-61 KB-2g). Fence for `history.ts`,
 * `wordDiff.ts`, `historyDiff.ts` and `getVersion` in `api.ts`: the version
 * read, the pick of a compare, the word diff, and the marks it makes.
 */
import { getSchema } from "@tiptap/core";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgeError, getVersion } from "./api";
import { parseBody } from "./editor/markdown";
import { knowledgeExtensions } from "./editor/schema";
import { comparePair, previousVersion, togglePick, versionDate, versionKey } from "./history";
import { changeCount, compareDecorations, compareDocs, wordCounts, wordsOf } from "./historyDiff";
import type { PageVersion } from "./types";
import { diffKeys } from "./wordDiff";

afterEach(() => {
  vi.unstubAllGlobals();
});

const schema = getSchema(knowledgeExtensions());
const doc = (md: string) => schema.nodeFromJSON(parseBody(md));

/** Apply a script to `a`, to prove it yields `b`. */
function apply(a: string[], b: string[], steps: ReturnType<typeof diffKeys>["steps"]): string[] {
  const out: string[] = [];
  for (const s of steps) {
    if (s.kind === "equal") {
      expect(a[s.a]).toBe(b[s.b]);
      out.push(a[s.a]);
    } else if (s.kind === "insert") out.push(b[s.b]);
  }
  return out;
}

describe("getVersion", () => {
  it("reads one version through the BFF, never the gateway", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      calls.push(url);
      return new Response(JSON.stringify({ version: 2, body_md: "x" }), { status: 200 });
    });
    const v = await getVersion("p 1", 2);
    expect(calls).toEqual(["/api/knowledge/pages/p%201/versions/2"]);
    expect(v.version).toBe(2);
  });

  it("keeps a 404 as a 404", async () => {
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ detail: "Not found" }), { status: 404 }));
    const err = await getVersion("p1", 1).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(KnowledgeError);
    expect((err as KnowledgeError).status).toBe(404);
  });

  it("refuses a number the gateway would refuse, and sends nothing", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(getVersion("p1", 0)).rejects.toThrow(RangeError);
    await expect(getVersion("p1", 1.5)).rejects.toThrow(RangeError);
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("the pick of a compare", () => {
  const versions = [5, 4, 3, 2, 1].map((n) => ({ version: n }) as PageVersion);

  it("puts the higher number as the newer side, in either order", () => {
    expect(comparePair([2, 5])).toEqual({ newer: 5, older: 2 });
    expect(comparePair([5, 2])).toEqual({ newer: 5, older: 2 });
    expect(comparePair([5])).toBeNull();
    expect(comparePair([3, 3])).toBeNull();
  });

  it("toggles a pick and refuses a third", () => {
    expect(togglePick([], 2)).toEqual([2]);
    expect(togglePick([2], 5)).toEqual([2, 5]);
    expect(togglePick([2, 5], 3)).toEqual([2, 5]);
    expect(togglePick([2, 5], 2)).toEqual([5]);
  });

  it("compares a single view with the version before it, and the oldest with none", () => {
    expect(previousVersion(versions, 5)).toBe(4);
    expect(previousVersion(versions, 1)).toBeNull();
    expect(previousVersion(versions, 9)).toBeNull();
  });

  it("writes a date that India and the US read the same, and a key under the app prefix", () => {
    expect(versionDate("2026-10-19T08:05:00Z")).toMatch(/^19 Oct 2026, \d\d:\d\d$/);
    expect(versionDate(null)).toBe("");
    expect(versionDate("not a date")).toBe("");
    expect(versionKey("p1", 3)).toBe("knowledge/page/p1/version/3");
  });
});

describe("the word diff", () => {
  it("finds the shortest script, and the script yields the newer list", () => {
    const a = "the cat sat on the mat".split(" ");
    const b = "the dog sat on a mat today".split(" ");
    const { steps, coarse } = diffKeys(a, b);
    expect(coarse).toBe(false);
    expect(apply(a, b, steps)).toEqual(b);
    const edits = steps.filter((s) => s.kind !== "equal").length;
    expect(edits).toBe(5);
  });

  it("handles an empty side", () => {
    expect(diffKeys([], []).steps).toEqual([]);
    expect(diffKeys([], ["a"]).steps).toEqual([{ kind: "insert", b: 0 }]);
    expect(diffKeys(["a"], []).steps).toEqual([{ kind: "delete", a: 0 }]);
  });

  it("holds on random lists", () => {
    let seed = 7;
    const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648;
    for (let t = 0; t < 50; t++) {
      const a = Array.from({ length: Math.floor(rnd() * 30) }, () => "abcd"[Math.floor(rnd() * 4)]);
      const b = Array.from({ length: Math.floor(rnd() * 30) }, () => "abcd"[Math.floor(rnd() * 4)]);
      expect(apply(a, b, diffKeys(a, b).steps)).toEqual(b);
    }
  });

  it("gives one coarse change past the edit limit, and still yields the newer list", () => {
    const a = Array.from({ length: 40 }, (_, i) => `a${i}`);
    const b = Array.from({ length: 40 }, (_, i) => `b${i}`);
    const { steps, coarse } = diffKeys(["x", ...a, "y"], ["x", ...b, "y"], 10);
    expect(coarse).toBe(true);
    expect(apply(["x", ...a, "y"], ["x", ...b, "y"], steps)).toEqual(["x", ...b, "y"]);
  });
});

describe("the compare of two bodies", () => {
  it("counts the words that came and went, and marks where", () => {
    const older = doc("Refunds take seven days.\n\nAsk the desk.");
    const newer = doc("Refunds take ten days.\n\nAsk the desk.\n\nKeep the receipt.");
    const c = compareDocs(older, newer);
    expect(c.changes).toHaveLength(2);
    expect(c.changes[0]).toMatchObject({ index: 1, removed: "seven", removedWords: 1, addedWords: 1 });
    expect(newer.textBetween(c.changes[0].added[0].from, c.changes[0].added[0].to)).toBe("ten");
    expect(c.changes[1]).toMatchObject({ index: 2, removedWords: 0, addedWords: 3 });
    expect(newer.textBetween(c.changes[1].added[0].from, c.changes[1].added[0].to)).toBe("Keep the receipt.");
    expect([c.addedWords, c.removedWords]).toEqual([4, 1]);
    expect(changeCount(2)).toBe("2 changes");
    expect(wordCounts(c)).toBe("4 words added, 1 word removed");
  });

  it("does not count Markdown syntax as words, and finds no change in the same body", () => {
    const body = "# Title\n\n- **one**\n- two\n\n| a | b |\n| - | - |\n| 1 | 2 |";
    expect(compareDocs(doc(body), doc(body)).changes).toEqual([]);
    expect(wordsOf(doc("# Title")).map((w) => w.text)).toEqual(["", "Title"]);
  });

  it("counts a word made bold as a change", () => {
    const c = compareDocs(doc("a plain word"), doc("a **plain** word"));
    expect(c.changes).toHaveLength(1);
    expect(c.changes[0]).toMatchObject({ removed: "plain", addedWords: 1 });
  });

  it("marks an added image as a node, and a removed one as text only", () => {
    const withImage = doc("See ![the chart](/api/files/a.png) here.");
    const without = doc("See here.");
    const added = compareDocs(without, withImage);
    expect(added.changes[0].added[0].atom).toBe(true);
    const removed = compareDocs(withImage, without);
    expect(removed.changes[0].removed).toBe("[Image: the chart]");
  });

  it("makes one decoration for each added run and each removed run", () => {
    const newer = doc("Refunds take ten days.");
    const c = compareDocs(doc("Refunds take seven days."), newer);
    const set = compareDecorations(newer, c, 1);
    const found = set.find();
    expect(found).toHaveLength(2);
    const inline = found.find((d) => d.from !== d.to)!;
    expect(newer.textBetween(inline.from, inline.to)).toBe("ten");
    expect((inline as unknown as { type: { attrs: Record<string, string> } }).type.attrs).toMatchObject({
      class: "kb-diff-added kb-diff-current",
      "data-diff-index": "1",
    });
  });
});
