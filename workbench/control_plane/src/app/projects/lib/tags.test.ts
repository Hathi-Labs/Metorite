/**
 * Projects · tags in the browser (WS-27m).
 *
 * The registry owns identity; this owns what a text box does before the save.
 * The cases that matter are the ones where the browser could disagree with the
 * server for a moment: a differently-cased tag, a tag typed twice, and a tag
 * that is about to be created rather than picked.
 */

import { describe, expect, it } from "vitest";

import {
  MAX_TAGS_PER_TASK,
  type TagRow,
  addTag,
  byUsage,
  canMerge,
  chipClass,
  normaliseTag,
  registryOf,
  removeTag,
  suggest,
  wouldCreate,
  AUTO_TAG_HUES,
  TAG_COLORS,
  autoTagHue,
} from "./tags";

const tag = (name: string, over: Partial<TagRow> = {}): TagRow => ({
  id: `id-${name}`,
  project_id: "p1",
  name,
  color: "gray",
  ...over,
});

const TAGS = [tag("bug"), tag("Ops"), tag("needs review")];
const REGISTRY = registryOf(TAGS);

describe("normaliseTag", () => {
  it("mirrors the gateway: trimmed, internal whitespace collapsed", () => {
    // Letting the browser send "needs  review" makes the registry decide
    // something the person could have seen before pressing Enter.
    expect(normaliseTag("  needs   review ")).toBe("needs review");
  });

  it("is not a tag when there is nothing but space", () => {
    expect(normaliseTag("   ")).toBeNull();
    expect(normaliseTag("")).toBeNull();
  });
});

describe("addTag", () => {
  it("stores the registry's spelling, not the one that was typed", () => {
    // Otherwise the chip changes under the cursor on the round trip and reads
    // as the app rewriting what somebody wrote.
    expect(addTag([], "OPS", REGISTRY)).toEqual(["Ops"]);
  });

  it("keeps the typed spelling for a tag that does not exist yet", () => {
    expect(addTag([], "Regression", REGISTRY)).toEqual(["Regression"]);
  });

  it("does nothing when the tag is already on the task", () => {
    expect(addTag(["Ops"], "ops", REGISTRY)).toEqual(["Ops"]);
    expect(addTag(["bug"], "BUG", REGISTRY)).toEqual(["bug"]);
  });

  it("ignores an empty press of Enter", () => {
    expect(addTag(["bug"], "   ", REGISTRY)).toEqual(["bug"]);
  });

  it("refuses to go past the cap the server would refuse at", () => {
    // Silently dropping is right here: the alternative is a 422 for something
    // the UI could see coming.
    const full = Array.from({ length: MAX_TAGS_PER_TASK }, (_, i) => `t${i}`);
    expect(addTag(full, "one more", REGISTRY)).toHaveLength(MAX_TAGS_PER_TASK);
  });

  it("appends rather than sorting, because a tag list is arranged", () => {
    expect(addTag(["zebra"], "apple", REGISTRY)).toEqual(["zebra", "apple"]);
  });

  it("does not mutate the list it was given", () => {
    const current = ["bug"];
    addTag(current, "ops", REGISTRY);
    expect(current).toEqual(["bug"]);
  });
});

describe("removeTag", () => {
  it("comes off whatever case the chip is carrying", () => {
    // A task tagged before the registry existed may hold another spelling.
    expect(removeTag(["Bug", "ops"], "bug")).toEqual(["ops"]);
  });

  it("leaves the rest alone", () => {
    expect(removeTag(["a", "b"], "missing")).toEqual(["a", "b"]);
  });
});

describe("suggest", () => {
  it("does not offer a tag the task already wears", () => {
    // Offering it is a click that does nothing.
    expect(suggest("", TAGS, ["Ops"]).map((t) => t.name)).toEqual([
      "bug",
      "needs review",
    ]);
  });

  it("matches anywhere in the name, not only at the start", () => {
    // A tag set reads "needs review" and "blocked: review"; somebody typing
    // "review" means both.
    expect(suggest("review", TAGS, []).map((t) => t.name)).toEqual([
      "needs review",
    ]);
  });

  it("ignores case and stray whitespace", () => {
    expect(suggest("  OPS ", TAGS, []).map((t) => t.name)).toEqual(["Ops"]);
  });

  it("offers everything unchosen when nothing has been typed", () => {
    expect(suggest("", TAGS, [])).toHaveLength(3);
  });

  it("is bounded, so a big registry does not become a wall", () => {
    const many = Array.from({ length: 40 }, (_, i) => tag(`t${i}`));
    expect(suggest("t", many, []).length).toBeLessThanOrEqual(8);
  });
});

describe("wouldCreate", () => {
  it("is true only for a tag the project has never seen", () => {
    // Auto-registration is deliberate, but SILENT auto-registration is how a
    // tag set fills with typos — so the moment of creation is shown.
    expect(wouldCreate("Regression", REGISTRY)).toBe(true);
    expect(wouldCreate("bug", REGISTRY)).toBe(false);
  });

  it("is false for a differently-cased existing tag", () => {
    // "BUG" is not a new tag; it is the one already there.
    expect(wouldCreate("BUG", REGISTRY)).toBe(false);
  });

  it("is false for nothing typed", () => {
    expect(wouldCreate("   ", REGISTRY)).toBe(false);
  });
});

describe("byUsage", () => {
  it("puts the most-used first, which is what a merge decision needs", () => {
    const tags = [
      tag("rare", { task_count: 1 }),
      tag("common", { task_count: 40 }),
    ];
    expect(byUsage(tags).map((t) => t.name)).toEqual(["common", "rare"]);
  });

  it("breaks a tie by name so the list does not shuffle between renders", () => {
    const tags = [tag("zebra", { task_count: 2 }), tag("apple", { task_count: 2 })];
    expect(byUsage(tags).map((t) => t.name)).toEqual(["apple", "zebra"]);
  });

  it("treats an absent count as zero rather than as NaN", () => {
    // Named so the NAME order disagrees with the COUNT order: `NaN - 3` is
    // falsy, so a missing count silently falls through to the name tiebreak
    // and a fixture that happens to be alphabetical proves nothing.
    const tags = [tag("apple"), tag("zebra", { task_count: 3 })];
    expect(byUsage(tags).map((t) => t.name)).toEqual(["zebra", "apple"]);
  });

  it("does not reorder the array it was given", () => {
    const tags = [tag("rare", { task_count: 1 }), tag("common", { task_count: 9 })];
    byUsage(tags);
    expect(tags.map((t) => t.name)).toEqual(["rare", "common"]);
  });
});

describe("canMerge", () => {
  it("refuses a tag into itself", () => {
    const one = tag("bug");
    expect(canMerge(one, one)).toBe(false);
  });

  it("allows two different tags", () => {
    expect(canMerge(tag("bug"), tag("defect"))).toBe(true);
  });

  it("is false until both have been picked", () => {
    expect(canMerge(tag("bug"), null)).toBe(false);
    expect(canMerge(null, tag("bug"))).toBe(false);
  });
});

describe("chipClass", () => {
  it("falls back to gray for a colour the theme does not know", () => {
    // A tag coloured by an older client must still render as a chip rather
    // than as unstyled text.
    expect(chipClass("chartreuse")).toBe(chipClass("gray"));
    expect(chipClass(undefined)).toBe(chipClass("gray"));
  });

  it("never emits a colour literal", () => {
    // DESIGN_SYSTEM.md: the theme decides what red is.
    for (const color of ["gray", "red", "amber", "green", "blue", "violet"]) {
      expect(chipClass(color)).not.toMatch(/#[0-9a-f]{3,8}\b|hsl\(|rgb\(/i);
    }
  });
});

/**
 * ── The colour a new tag gets (owner, 2026-09-20) ───────────────────────
 *
 * Before this, `146_projects.sql`'s `DEFAULT 'gray'` decided, so every tag
 * the server auto-registered was gray and a project's whole tag set read as
 * one colour. The owner asked how a colour gets chosen at creation time.
 */
describe("the hue a new tag gets by default", () => {
  it("is never gray — that is the no-colour look, not a choice", () => {
    // The whole point. If gray ever re-enters the auto pool, new tags go
    // back to being indistinguishable and nothing else here would notice.
    expect(AUTO_TAG_HUES).not.toContain("gray");
    for (const name of ["bug", "ui", "urgent", "backend", "ops", "a", "zz", ""]) {
      expect(autoTagHue(name), name).not.toBe("gray");
    }
  });

  it("is stable for the same word, so a tag is one colour everywhere", () => {
    expect(autoTagHue("deploy")).toBe(autoTagHue("deploy"));
    // Two people inventing the same tag separately must not disagree.
    expect(autoTagHue("Deploy")).toBe(autoTagHue("  deploy  "));
  });

  it("is always a hue the chip vocabulary can paint", () => {
    for (const name of ["a", "bb", "ccc", "dddd", "eeeee", "ffffff", "zzzzzzz"]) {
      expect(TAG_COLORS, name).toContain(autoTagHue(name));
    }
  });

  it("spreads across the pool rather than collapsing onto one hue", () => {
    // A hash that always answered the same thing would pass every test
    // above and leave the board monochrome anyway.
    const words = ["bug", "ui", "urgent", "backend", "ops", "docs", "perf",
                   "design", "infra", "billing", "auth", "search"];
    const used = new Set(words.map(autoTagHue));
    expect(used.size, "the auto hue barely varies").toBeGreaterThan(2);
  });

  it("never indexes off the array, whatever the name", () => {
    // The unsigned guard: a signed hash gives a negative modulus, which
    // reads past the front of the array and hands back `undefined`.
    for (const name of ["\u00ff\u00ff\u00ff", "\u4f60\u597d", "z".repeat(400), "\u{1F600}"]) {
      expect(autoTagHue(name), JSON.stringify(name)).toBeTruthy();
    }
  });
});
