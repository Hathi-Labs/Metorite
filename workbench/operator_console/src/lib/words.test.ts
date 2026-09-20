// R7's fence for the button vocabulary (`words.ts`).
//
// Two jobs. Pin the strings, so a rename is a deliberate edit somebody sees in
// review. And SCAN the Models page for the retired names, so the old
// vocabulary cannot return one component at a time — which is exactly how the
// four "Add"s and the three "fill the boxes" buttons arrived.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ADD, EDIT, LIST, REMOVE, VENDOR_PRICES } from "./words";

const MODELS_DIR = join(__dirname, "..", "app", "models");

/** Every file that puts WORDS in front of an operator on this page.
 *
 * 🔴 **`help.ts` belongs here, and leaving it out let the retired vocabulary
 * survive the rename.** Measured on the deployed bundle, 2026-09-21: the
 * buttons all read "Use the vendor's prices" while their own tooltips still
 * said "Copy the vendor's own window…" and "Fill every box below…". Hovering a
 * control to have it explained in the words it no longer uses is the same
 * defect this file exists for, one layer down. */
function modelsSource(): { file: string; text: string }[] {
  const files = readdirSync(MODELS_DIR)
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => ({ file: f, path: join(MODELS_DIR, f) }));
  files.push({ file: "lib/help.ts", path: join(__dirname, "help.ts") });
  return files.map(({ file, path }) => ({
    file,
    text: readFileSync(path, "utf-8"),
  }));
}

describe("one act, one word", () => {
  it("pins the add vocabulary", () => {
    expect(ADD.one).toBe("Add");
    expect(ADD.unpriced).toBe("Add anyway");
    expect(ADD.selected(3)).toBe("Add 3 selected");
    expect(ADD.byHandSubmit).toBe("Add");
    expect(ADD.busy).toBe("Adding…");
  });

  it("pins the remove vocabulary", () => {
    expect(REMOVE.one).toBe("Remove");
    expect(REMOVE.confirm).toBe("Remove it");
    expect(REMOVE.keep).toBe("Keep it");
  });

  it("pins the edit vocabulary, and does not call it adding", () => {
    expect(EDIT.open).toBe("Edit details");
    // 🔴 The collision this file exists to stop. "Add details" adds no model.
    expect(EDIT.open).not.toMatch(/add/i);
    expect(EDIT.close).toBe("Done");
  });

  it("gives the vendor-price copy ONE verb at all three sizes", () => {
    const verb = "Use the vendor's prices";
    expect(VENDOR_PRICES.one).toBe(verb);
    expect(VENDOR_PRICES.intoBoxes).toBe(verb);
    expect(VENDOR_PRICES.all(31)).toBe(`${verb} for all 31`);
  });

  it("keeps the list controls plain", () => {
    expect(LIST.clear).toBe("Clear filters");
    expect(LIST.showMore(12)).toBe("Show 12 more");
    expect(LIST.readyToAdd(8)).toBe("8 ready to add →");
  });
});

// 🔴 The retired names, each with the act it used to duplicate. A string here
// is banned from the Models page's own components — including its comments,
// deliberately: a comment that still says "the Copy button" sends the next
// reader looking for a control that no longer exists.
const RETIRED: [string, string][] = [
  ["Copy the vendor", "a third name for VENDOR_PRICES"],
  ["Fill from the vendor feed", "a second name for VENDOR_PRICES.one"],
  ["from the feed`", "a second name for VENDOR_PRICES.all"],
  ["+ Add details", "EDIT.open, wearing the add verb"],
  ["Add details", "EDIT.open, wearing the add verb"],
  ["or enter by hand", "a third label on the EDIT toggle"],
  ["+ Add", "ADD.one, with a stray glyph"],
];

describe("the retired vocabulary cannot come back", () => {
  it("scans a real directory, and the tooltip dictionary with it", () => {
    const files = modelsSource();
    expect(files.length).toBeGreaterThan(5);
    expect(files.map((f) => f.file)).toContain("lib/help.ts");
  });

  it.each(RETIRED)("no Models component says %j (%s)", (banned) => {
    const offenders = modelsSource()
      .filter((s) => s.text.includes(banned))
      .map((s) => s.file);
    expect(offenders).toEqual([]);
  });

  it("no Models component hardcodes a label that words.ts owns", () => {
    // Every word below has an entry in `words.ts`. A component that spells one
    // itself is how the two drift apart.
    //
    // 🔴 **Both shapes, and the JSX one matters more.** An earlier version of
    // this test matched only `"Remove"` — a double-quoted literal — while
    // every retired label on this page was written as JSX TEXT: `>Add<`,
    // `>Clear<`, `>Save<`, `>Close<`. The scan was blind to the exact form it
    // exists to catch.
    const owned = ["Remove", "Clear filters", "Edit details", "Keep it", "Done"];
    const offenders: string[] = [];
    for (const { file, text } of modelsSource()) {
      for (const w of owned) {
        if (text.includes(`"${w}"`)) offenders.push(`${file} "${w}"`);
        // `>Remove<` and `> Remove <`, across a line break too.
        if (new RegExp(`>\s*${w}\s*<`).test(text)) {
          offenders.push(`${file} >${w}<`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
