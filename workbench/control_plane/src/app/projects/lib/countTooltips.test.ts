/**
 * A bare number in the Projects UI must say what it counts.
 *
 * Owner report, 2026-09-16, looking at the status manager: *"what are these
 * numbers here?"* The figure was `group.rows.length` — how many status lanes
 * sit in that stage — rendered as a lone "2" beside a stage name.
 *
 * ⚠️ **The ambiguity is specific to this app, and that is why the rule is.**
 * Projects renders task counts in exactly this shape — a small muted pill
 * beside a lane name — on the board, the list and the table. So a lone number
 * beside a lane name already MEANS "tasks here" everywhere else in the
 * product. The status manager used the same shape for a different quantity,
 * and there was nothing on screen to tell them apart.
 *
 * The rule: a JSX element whose entire content is a bare count expression must
 * carry a `title`, an `aria-label`, or `aria-hidden`. The third is a real
 * answer — `NotificationBell` paints an `aria-hidden` badge whose number is
 * already in the button's own `aria-label`, and labelling it twice would make
 * a screen reader say it twice.
 *
 * ⚠️ **A source scan, not a render test, and the reason is coverage.** These
 * are inline JSX attributes on a dozen elements across eight files, not a
 * function anybody can call. Rendering each one needs the whole component plus
 * its data; scanning the source asks the one question that actually matters —
 * *is there any element left that shows a number and explains nothing?* — and
 * it keeps asking it about files that do not exist yet.
 *
 * Sibling of `src/lib/sourceHygiene.test.ts`, which polices the same tree the
 * same way for the same reason.
 */

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const PROJECTS = join(__dirname, "..");

function projectSources(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) projectSources(full, found);
    else if (/\.tsx$/.test(entry)) found.push(full);
  }
  return found;
}

/**
 * A line that renders ONLY a count, with no words beside it.
 *
 * `{count} selected` and `Blocked by {n}` are deliberately NOT matched: they
 * carry their own noun, which is the whole thing a tooltip would add. The rule
 * is about numbers that stand alone, not about every number.
 */
const LONE_EXPRESSION = /^\s*\{\s*([A-Za-z_$][\w$.?[\]]*)\s*\}\s*$/;

function isBareCount(line: string): boolean {
  const lone = LONE_EXPRESSION.exec(line);
  if (!lone) return false;
  const expr = lone[1];
  return /\.length$/.test(expr) || /count$/i.test(expr);
}

/**
 * The attributes that answer "what is this number?" for some reader.
 *
 * ⚠️ `aria-hidden` matches with OR without a value, because JSX writes a true
 * boolean prop as the bare word. The first cut required `=` and so reported
 * `NotificationBell`'s deliberately hidden badge as an offender — a fence that
 * fails on correct code is one somebody eventually deletes.
 */
const EXPLAINED = /\b(?:(?:title|aria-label)\s*=|aria-hidden\b)/;

/**
 * Walk back from a bare-count line to the tag that opens it, and report
 * whether that opening tag explains itself.
 *
 * The opening tag can span several lines once it carries a className plus a
 * template literal, which is why this reads backwards to the `<` rather than
 * testing the previous line.
 */
function openingTagExplains(lines: string[], at: number): boolean {
  for (let i = at - 1; i >= 0 && i >= at - 14; i--) {
    const line = lines[i];
    if (/^\s*<[A-Za-z]/.test(line)) {
      // Found the tag. Everything from here to the bare count is its
      // attribute list.
      return EXPLAINED.test(lines.slice(i, at).join("\n"));
    }
  }
  return false;
}

describe("a bare number says what it counts", () => {
  const files = projectSources(PROJECTS);

  it("finds the Projects sources at all", () => {
    // Non-vacuity. A scan that silently walked an empty directory would pass
    // this whole suite while checking nothing — which is the exact failure
    // `sourceHygiene.test.ts` was written about.
    expect(files.length).toBeGreaterThan(20);
  });

  it("leaves no count rendered without an explanation", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const lines = readFileSync(file, "utf8").split(/\r?\n/);
      lines.forEach((line, index) => {
        if (!isBareCount(line)) return;
        if (openingTagExplains(lines, index)) return;
        offenders.push(
          `${file.slice(file.indexOf("projects"))}:${index + 1}: ${line.trim()}`
        );
      });
    }
    expect(offenders, offenders.join("\n")).toEqual([]);
  });

  it("actually recognises a bare count when it sees one", () => {
    // The regex is the whole test, so it gets its own assertions. A pattern
    // that matched nothing would make the scan above pass forever.
    expect(isBareCount("        {column.tasks.length}")).toBe(true);
    expect(isBareCount("  {count}")).toBe(true);
    expect(isBareCount("{group.rows.length}")).toBe(true);
    expect(isBareCount("{mentionCount}")).toBe(true);
  });

  it("does not call a number with a NOUN beside it bare", () => {
    // These already say what they are. Flagging them would push somebody to
    // add a tooltip that repeats the visible text, and a tooltip that says
    // what the label says is how people learn to ignore tooltips.
    expect(isBareCount("        {count} selected")).toBe(false);
    expect(isBareCount("  Blocked by {data.blocked_by.length}")).toBe(false);
    expect(isBareCount("  {unanswered.length} still to answer")).toBe(false);
    expect(isBareCount("      Apply to {count}")).toBe(false);
  });

  it("reads an explanation only from the element's OWN opening tag", () => {
    // A `title` on some ancestor twelve lines up is not this element's label,
    // and accepting one would let the rule pass on a number nested inside any
    // titled container.
    const titled = [
      '      <span',
      '        className="x"',
      '        title="1 task"',
      '      >',
      "        {rows.length}",
    ];
    expect(openingTagExplains(titled, 4)).toBe(true);

    const untitled = [
      '      <div title="an ancestor">',
      '        <span className="x">',
      "          {rows.length}",
    ];
    expect(openingTagExplains(untitled, 2)).toBe(false);
  });
});
