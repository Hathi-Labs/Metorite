/**
 * My Tasks binds no letter shortcuts (owner, 2026-10-13).
 *
 * The app had a `?` sheet and about twenty single-key bindings: `c` capture,
 * `u` undo, `g <letter>` jumps, and the Inbox's and Clarify's triage keys
 * `e x t s r 2 m o ]`. The owner found them unnecessary and complicated, and
 * every one of them was also a button. They were removed with the sheet.
 *
 * What stays is what a member brings from every other application: arrows
 * and Enter (the shared cursor), Escape, ⌘K search and Ctrl+Z undo. Space is
 * not a shortcut either: it presses a focused card, as it presses any button.
 *
 * This walks every source file under `app/tasks` and fails on a keyboard
 * event's `key` or `code` compared with one printable character (either
 * order, `===` or `!==`, lower- or upper-cased), on a list of such characters
 * checked with `includes`, and on a one-character `case` in a file that
 * handles keys. `the fence catches` below pins each form, so a narrowed
 * pattern fails here first.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/** A keyboard event's key, as handlers here spell it. */
const EVENT_KEY = String.raw`\b(?:e|ev|evt|event)\.(?:key|code)(?:\.to(?:Lower|Upper)Case\(\))?`;
/** One printable character, or the `code` of a letter or digit. */
const ONE_CHAR = String.raw`["'](?:[^"'\\\s]|Key[A-Z]|Digit[0-9])["']`;

const KEY_PATTERNS: RegExp[] = [
  new RegExp(`${EVENT_KEY}\\s*[!=]==?\\s*${ONE_CHAR}`, "g"),
  new RegExp(`${ONE_CHAR}\\s*[!=]==?\\s*${EVENT_KEY}`, "g"),
  new RegExp(`\\[[^\\]]*${ONE_CHAR}[^\\]]*\\]\\.includes\\(${EVENT_KEY}\\)`, "g"),
];
/** A `case "s":` — counted only in a file that handles keys. */
const CASE_ONE = new RegExp(`\\bcase\\s+${ONE_CHAR}\\s*:`, "g");
const HANDLES_KEYS = /keydown|onKeyDown|KeyboardEvent/;

function hitsIn(text: string): string[] {
  const hits = KEY_PATTERNS.flatMap((p) => [...text.matchAll(p)].map((m) => m[0]));
  if (HANDLES_KEYS.test(text)) hits.push(...[...text.matchAll(CASE_ONE)].map((m) => m[0]));
  return hits;
}

describe("the fence catches", () => {
  it.each([
    'if (e.key === "c")',
    "if (event.key == 'u')",
    'if (e.key !== "t") return;',
    'if ("s" === e.key)',
    'if (e.key.toLowerCase() === "r")',
    'if (e.key.toUpperCase() === "C")',
    'if (e.code === "KeyC")',
    'if (e.code === "Digit2")',
    'if (["t", "s"].includes(e.key))',
    'onKeyDown={(e) => { switch (e.key) { case "m": break; } }}',
  ])("%s", (line) => {
    expect(hitsIn(line)).not.toEqual([]);
  });

  it.each([
    'if (e.key === "Escape")',
    'if (e.key === "Enter")',
    'if (e.key === " ")',
    'if (e.key === "ArrowRight")',
    'if (section.key === "a")',
    'switch (view) { case "a": break; }',
  ])("and leaves alone %s", (line) => {
    expect(hitsIn(line)).toEqual([]);
  });
});

describe("My Tasks binds no letter shortcuts", () => {
  const files = sources(ROOT);

  it("walks the app", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("no source binds a one-character key", () => {
    const hits = files.flatMap((file) =>
      hitsIn(readFileSync(file, "utf8")).map(
        (hit) => `${relative(ROOT, file).replace(/\\/g, "/")}: ${hit}`,
      ),
    );
    expect(hits).toEqual([]);
  });

  it("the shortcuts sheet and its button are gone", () => {
    for (const file of files) {
      const text = readFileSync(file, "utf8");
      expect(text, file).not.toMatch(/ShortcutsSheet|openShortcutsSheet|TasksShortcuts/);
      expect(text, file).not.toMatch(/aria-label="Keyboard shortcuts"/);
    }
  });
});
