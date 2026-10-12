/**
 * My Tasks binds no letter shortcuts (owner, 2026-10-13).
 *
 * The app had a `?` sheet and about twenty single-key bindings: `c` capture,
 * `u` undo, `g <letter>` jumps, and the Inbox's and Clarify's triage keys
 * `e x t s r 2 m o ]`. The owner found them unnecessary and complicated, and
 * every one of them was also a button. They were removed with the sheet.
 *
 * What stays is what a member brings from every other application: arrows
 * and Enter (the shared cursor), Escape, ⌘K search and Ctrl+Z undo.
 *
 * This walks every source file under `app/tasks` and fails on a comparison of
 * `key` with one printable character, or a `case` of one. Space is not one:
 * it presses a focused card, as it presses any button. A text field that
 * reads one character it was typed (`#` opening the project picker) is not a
 * shortcut, so the walk skips a handler on an input by name (`ALLOWED`).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const ROOT = fileURLToPath(new URL("..", import.meta.url));

/** `file: character` pairs that are typed into a field, not shortcuts. */
const ALLOWED = new Set<string>([]);

function sources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...sources(full));
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

const ONE_KEY = [
  // e.key === "c", event.key == 'u', key.toLowerCase() === "t"
  /\bkey(?:\.toLowerCase\(\))?\s*={2,3}\s*["']([^"'\\\s])["']/g,
  // case "s":  — a switch on the key
  /\bcase\s+["']([^"'\\\s])["']\s*:/g,
];

describe("My Tasks binds no letter shortcuts", () => {
  const files = sources(ROOT);

  it("walks the app", () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it("no source compares a key with one character", () => {
    const hits: string[] = [];
    for (const file of files) {
      const rel = relative(ROOT, file).replace(/\\/g, "/");
      const text = readFileSync(file, "utf8");
      for (const pattern of ONE_KEY) {
        for (const m of text.matchAll(pattern)) {
          if (!ALLOWED.has(`${rel}: ${m[1]}`)) hits.push(`${rel}: ${m[0]}`);
        }
      }
    }
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
