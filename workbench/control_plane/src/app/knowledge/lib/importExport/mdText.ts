// ── Plain text as safe Markdown (WS-61 KB-AI-1) ──────────────────────────────
//
// Pure. Text from a PDF, a deck or a sheet is not Markdown, so a `*`, a `<` or
// a leading `#` in it must not turn into markup. The page body is stored as
// Markdown, and the editor reads it later.

/** Escape the marks that Markdown reads inline. */
export function escapeInline(text: string): string {
  return text.replace(/[\\`*_[\]<>]/g, (c) => `\\${c}`);
}

/** One line of text with every mark escaped, and a line-start mark too. */
export function escapeLine(text: string): string {
  const one = escapeInline(text.replace(/\s+/g, " ").trim());
  // A line start that would be a heading, a quote, a list or a rule.
  return one
    .replace(/^(#{1,6})(\s|$)/, "\\$1$2")
    .replace(/^([-+])(\s|$)/, "\\$1$2")
    .replace(/^(\d+)([.)])(\s|$)/, "$1\\$2$3")
    .replace(/^([-=]{3,})$/, "\\$1");
}

/** A table cell: one line, with `|` escaped. */
export function escapeCell(text: string): string {
  return escapeInline(text.replace(/\s+/g, " ").trim()).replace(/\|/g, "\\|");
}

/**
 * A GFM table from rows of text. The first row is the header. Rows are padded
 * to the widest row. Returns "" for no rows.
 */
export function toTable(rows: readonly (readonly string[])[]): string {
  if (!rows.length) return "";
  const width = Math.max(...rows.map((r) => r.length));
  if (!width) return "";
  const line = (row: readonly string[]) =>
    `| ${Array.from({ length: width }, (_, i) => escapeCell(row[i] ?? "")).join(" | ")} |`;
  const head = line(rows[0]);
  const rule = `| ${Array.from({ length: width }, () => "---").join(" | ")} |`;
  return [head, rule, ...rows.slice(1).map(line)].join("\n");
}
