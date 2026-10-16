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

/** Each heading one level down: `#` to `##`, and so on. `######` stays. */
export function demoteHeadings(md: string): string {
  const lines = md.split("\n");
  let fence: string | null = null;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
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
      if (atx[2].length < 6) lines[i] = `${atx[1]}#${line.slice(atx[1].length)}`;
      continue;
    }
    // A setext `===` under a line of text is an h1. As `---` it is an h2.
    if (SETEXT_H1.test(line) && i > 0 && lines[i - 1].trim() !== "") {
      lines[i] = line.replace(/=/g, "-");
    }
  }
  return lines.join("\n");
}
