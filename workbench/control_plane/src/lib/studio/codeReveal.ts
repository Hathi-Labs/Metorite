/**
 * Find an item in the pretty JSON of the Code tab (`ai_studio.md` §6.3, G3a fix 1).
 *
 * A click on an Action, a Workflow or the Skill in the Explorer must show that item. The tab
 * searches the JSON text for `needle`, after the line of the `section` key, so an action and a
 * workflow with one id do not mix. Pure.
 */
export interface Reveal {
  /** The top-level key that holds the item: `actions`, `workflows`, `skill`. */
  section: string;
  /** The text of the item, as `JSON.stringify(x, null, 2)` writes it: `"id": "weekly"`. */
  needle: string;
}

/** The 1-based line of the match, or null. */
export function findLine(text: string, at: Reveal): number | null {
  const lines = text.split("\n");
  const head = `"${at.section}"`;
  const start = lines.findIndex((l) => l.startsWith(`  ${head}`));
  if (start < 0) return null;
  for (let i = start; i < lines.length; i++) {
    if (lines[i].includes(at.needle)) return i + 1;
    // The next top-level key ends the section.
    if (i > start && /^ {2}"/.test(lines[i])) return null;
  }
  return null;
}
