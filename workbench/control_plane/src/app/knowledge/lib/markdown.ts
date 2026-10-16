// ── A page body under the page title (WS-61 KB-2a) ─────────────────────────
//
// Pure. The app bar holds the app's one `<h1>` (AGENTS.md rule 11), and the
// page title under it is an `<h2>`. `MarkdownBody` draws `# Heading` as an
// `<h1>`, so a page body that starts with one put a second `<h1>` on the
// screen (found in the KB-2a visual review). This moves each heading of the
// body one level down before the one renderer draws it. The stored Markdown
// does not change, and the renderer does not change.

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;
const ATX = /^(\s{0,3})(#{1,6})(?=\s|$)/;
const SETEXT_H1 = /^\s{0,3}=+\s*$/;
/**
 * The container marks before a line's content: quote marks and list
 * markers, nested. A heading may sit inside them (`> # Quoted`,
 * `- # Listed`, `1. # Num`), and the renderer draws it as an h1 too
 * (review of KB-2a, P3-a).
 */
const CONTAINER = /^(?:\s{0,3}(?:>\s?|[-*+]\s+|\d{1,9}[.)]\s+))*/;

/** Each heading one level down: `#` to `##`, and so on. `######` stays. */
export function demoteHeadings(md: string): string {
  const lines = md.split("\n");
  let fence: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const prefix = CONTAINER.exec(lines[i])?.[0] ?? "";
    const line = lines[i].slice(prefix.length);
    const open = FENCE.exec(line);
    if (fence) {
      if (open && open[1][0] === fence[0] && open[1].length >= fence.length) fence = null;
      continue;
    }
    if (open) {
      fence = open[1];
      continue;
    }
    const atx = ATX.exec(line);
    if (atx) {
      if (atx[2].length < 6) lines[i] = `${prefix}${atx[1]}#${line.slice(atx[1].length)}`;
      continue;
    }
    // A setext `===` under a line of text is an h1. As `---` it is an h2.
    if (SETEXT_H1.test(line) && i > 0) {
      const above = lines[i - 1].slice((CONTAINER.exec(lines[i - 1])?.[0] ?? "").length);
      if (above.trim() !== "") lines[i] = prefix + line.replace(/=/g, "-");
    }
  }
  return lines.join("\n");
}
