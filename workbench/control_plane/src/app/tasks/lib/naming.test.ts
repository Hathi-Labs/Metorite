/**
 * The app is called **My Tasks** (D73, 2026-09-23), and no string a member
 * reads in it says "GTD" or "the Tasks app".
 *
 * ## Why a source fence and not a render test
 *
 * The failure this defends is one component left behind. A render test asserts
 * one screen; a rename sweep misses the tooltip on the screen nobody rendered.
 * So this reads every component file in the two apps that share the store and
 * looks at the text a member can see: JSX text nodes and string literals that
 * sit in `title=`, `aria-label=`, `placeholder=`, `label=` and toast calls.
 *
 * Comments and identifiers are out of scope on purpose. `MyTask` is a type,
 * `my_tasks_list` is a tool name the skill owns (S9 renamed both), and a code
 * comment is not member-visible. Product copy is what the owner renamed.
 *
 * Spec: `project-docs/specs/my_tasks_cutover.md` §5 S5.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = path.resolve(__dirname, "..", "..");
const DIRS = [
  // The app root, so `page.tsx` (the toolbar title) is read — the first
  // sweep scanned only `components/` and missed it (S6b repair).
  path.join(ROOT, "tasks"),
  path.join(ROOT, "calendar"),
  path.join(ROOT, "..", "components", "tasks"),
];

function tsxFiles(dir: string): string[] {
  if (!fs.existsSync(dir)) return [];
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...tsxFiles(full));
    else if (entry.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

/** Strip `//` and `/* *\/` comments so a comment cannot trip the fence. */
function withoutComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:\\])\/\/[^\n]*/g, "$1");
}

/** The strings a member can read: attribute literals and JSX text. */
function visibleStrings(src: string): string[] {
  const found: string[] = [];
  const attr =
    /\b(?:title|aria-label|placeholder|label|hint|caveat|note)\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*`([^`]*)`\s*\})/g;
  for (const m of src.matchAll(attr)) found.push(m[1] ?? m[2] ?? m[3] ?? "");
  const jsxText = />([^<>{}]*[A-Za-z][^<>{}]*)</g;
  for (const m of src.matchAll(jsxText)) found.push(m[1]);
  const toast = /(?:flashToast|toast)\s*\(\s*(?:"([^"]*)"|'([^']*)'|`([^`]*)`)/g;
  for (const m of src.matchAll(toast)) found.push(m[1] ?? m[2] ?? m[3] ?? "");
  return found.map((s) => s.trim()).filter(Boolean);
}

const BANNED: Array<[RegExp, string]> = [
  [/\bGTD\b/, 'the method name "GTD" — say what the step does instead'],
  [/\bgtd\b/i, 'a "gtd" spelling'],
  [/\bthe Tasks app\b/, '"the Tasks app" — the app is My Tasks'],
  [/\bin Tasks\b/, '"in Tasks" — say "in My Tasks"'],
  [/\bto Tasks\b/, '"to Tasks" — say "to My Tasks"'],
  [/\bOpen Tasks\b/, '"Open Tasks" — say "Open My Tasks"'],
  // S6b repair (2026-09-23): the survivors the first sweep missed — the
  // toolbar title, the settings header and the sidebar subtitle.
  [/\bTask Manager\b/, '"Task Manager" — the app is My Tasks'],
  [/\bGetting Things Done\b/, 'the method name "Getting Things Done"'],
  // D73.9 (2026-09-23): there is no ClickUp connection (D52), so no string a
  // member reads may offer one. The status mapping went first.
  [/\bClickUp\b/, '"ClickUp" — there is no connected tool (D52, D73.9)'],
];

describe("My Tasks — the name a member reads (D73)", () => {
  const files = DIRS.flatMap(tsxFiles);

  it("scans the components of both lenses", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("no member-visible string says GTD or the old app name", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const src = withoutComments(fs.readFileSync(file, "utf8"));
      for (const text of visibleStrings(src)) {
        for (const [re, why] of BANNED) {
          if (re.test(text)) {
            offenders.push(
              `${path.relative(ROOT, file)}: ${JSON.stringify(text)} carries ${why}`,
            );
          }
        }
      }
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });
});
